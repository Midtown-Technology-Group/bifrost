//! Provisional isolated Result transaction; no launch or custody authority.
use crate::{ObserveError, SessionFence, canonical_uuid, lock_session_state};
use bifrost_execution_wire_spike::Codec;
use serde_json::{Value, json};
use sqlx::{PgPool, Row};

pub struct ResultDecision {
    body: Value,
}

impl ResultDecision {
    /// Returned only after observed commit; outer framing/sequence belongs to the
    /// authenticated adapter session, not this database implementation.
    pub fn receipt_body(&self) -> &Value {
        &self.body
    }
}

/// The trusted owner supplies channel-bound identity and a fresh decision ID.
/// The independently tested codec validates the exact received payload before
/// SQL; its bytes, never a JSON reserialization, are the receipt preimage.
/// This function cannot establish live channel custody or accepted registration.
pub async fn accept_result(
    pool: &PgPool,
    fence: &SessionFence,
    payload: &[u8],
    decision_id: &str,
) -> Result<ResultDecision, ObserveError> {
    if !canonical_uuid(decision_id) {
        return Err(ObserveError::InvalidFence);
    }
    let decoded = Codec::new()
        .and_then(|codec| codec.decode(payload))
        .map_err(|_| ObserveError::Rejected)?;
    let frame = &decoded.frame;
    if frame["type"] != "Result" || frame["session_id"] != fence.session_id {
        return Err(ObserveError::Rejected);
    }
    let message = frame["message_id"].as_str().ok_or(ObserveError::Rejected)?;
    let start_message = frame["body"]["start_message_id"]
        .as_str()
        .ok_or(ObserveError::Rejected)?;
    let digest = decoded.payload_sha256();
    // Exact terminal identity is allowed only so a retained byte-identical
    // receipt can be read after finalization. A new report cannot replay work.
    let mut locked = lock_session_state(pool, fence, true).await?;
    let start = sqlx::query(
        "SELECT id::text AS id,start_message_id::text AS message, \
         deadline_utc IS NOT NULL AND deadline_utc <= clock_timestamp() AS expired \
         FROM runtime_starts WHERE session_id=$1::text::uuid \
         AND execution_id=$2::text::uuid AND owner_incarnation_id=$3::text::uuid \
         AND workflow_attempt_id=$4::text::uuid FOR UPDATE",
    )
    .bind(&fence.session_id)
    .bind(&fence.execution_id)
    .bind(&fence.owner_incarnation_id)
    .bind(&fence.attempt_id)
    .fetch_optional(&mut *locked.tx)
    .await?
    .ok_or(ObserveError::Rejected)?;
    if start.try_get::<String, _>("message")? != start_message {
        return Err(ObserveError::Rejected);
    }
    let start_id: String = start.try_get("id")?;
    let admissions = sqlx::query(
        "SELECT purpose,grant_id::text AS grant FROM runtime_admissions \
         WHERE session_id=$1::text::uuid ORDER BY purpose,id FOR UPDATE",
    )
    .bind(&fence.session_id)
    .fetch_all(&mut *locked.tx)
    .await?;
    let grants = sqlx::query(
        "SELECT id::text AS id,revoked_at IS NOT NULL AS revoked,revocation_reason, \
         initial_access_expires_at <= clock_timestamp() AS expired \
         FROM workflow_runtime_sdk_grants WHERE runtime_session_id=$1::text::uuid \
         ORDER BY id FOR UPDATE",
    )
    .bind(&fence.session_id)
    .fetch_all(&mut *locked.tx)
    .await?;
    let all_revoked = grants.iter().all(|row| {
        row.try_get::<bool, _>("revoked").ok() == Some(true)
            && row
                .try_get::<Option<String>, _>("revocation_reason")
                .ok()
                .flatten()
                .as_deref()
                == Some("session_closed")
    });
    let receipts = sqlx::query(
        "SELECT result_message_id::text AS message,raw_result_payload, \
         result_sha256,decision_id::text AS decision,disposition,winner \
         FROM runtime_report_receipts WHERE session_id=$1::text::uuid \
         ORDER BY result_message_id FOR UPDATE",
    )
    .bind(&fence.session_id)
    .fetch_all(&mut *locked.tx)
    .await?;
    if let Some(receipt) = receipts.first() {
        if receipts.len() != 1
            || receipt.try_get::<String, _>("message")? != message
            || receipt.try_get::<Vec<u8>, _>("raw_result_payload")? != payload
            || receipt.try_get::<String, _>("result_sha256")? != digest
        {
            return Err(ObserveError::Rejected);
        }
        let disposition: String = receipt.try_get("disposition")?;
        let winner: String = receipt.try_get("winner")?;
        let coherent = locked.closed
            && all_revoked
            && match (disposition.as_str(), winner.as_str()) {
                ("accepted", "result") => {
                    locked.close_reason.as_deref() == Some("result_committed")
                        && locked.attempt_completed
                        && matches!(
                            (
                                locked.execution_status.as_str(),
                                locked.attempt_status.as_str()
                            ),
                            ("Success", "succeeded") | ("Failed", "failed")
                        )
                }
                ("retained", "cancel") => {
                    locked.close_reason.as_deref() == Some("cancel_requested")
                        && matches!(
                            (
                                locked.execution_status.as_str(),
                                locked.attempt_status.as_str(),
                                locked.attempt_completed
                            ),
                            ("Cancelling", "running", false) | ("Cancelled", "cancelled", true)
                        )
                }
                _ => false,
            };
        if !coherent {
            return Err(ObserveError::Rejected);
        }
        if winner == "result" {
            let success = frame["body"]["outcome"] == "success";
            let expected = if success {
                frame["body"]["value"].clone()
            } else {
                json!({"error":frame["body"]["error"]})
            };
            let projection = sqlx::query("SELECT result::text AS result,error_message FROM executions WHERE id=$1::text::uuid")
                .bind(&fence.execution_id).fetch_one(&mut *locked.tx).await?;
            let result: Option<String> = projection.try_get("result")?;
            let result: Value =
                serde_json::from_str(result.as_deref().ok_or(ObserveError::Rejected)?)
                    .map_err(|_| ObserveError::Rejected)?;
            let expected_error = if success {
                None
            } else {
                frame["body"]["error"]["message"].as_str()
            };
            if result != expected
                || locked.execution_status != if success { "Success" } else { "Failed" }
                || projection
                    .try_get::<Option<String>, _>("error_message")?
                    .as_deref()
                    != expected_error
            {
                return Err(ObserveError::Rejected);
            }
        }
        let retained_decision: String = receipt.try_get("decision")?;
        locked
            .tx
            .commit()
            .await
            .map_err(|_| ObserveError::UncertainCommit)?;
        return Ok(ResultDecision {
            body: json!({"result_message_id":message,"result_sha256":digest,
                "decision_id":retained_decision,"disposition":disposition,"winner":winner}),
        });
    }
    let cancelled = locked.closed
        && locked.close_reason.as_deref() == Some("cancel_requested")
        && locked.execution_status == "Cancelling"
        && locked.attempt_status == "running"
        && !locked.attempt_completed;
    if cancelled && !all_revoked {
        return Err(ObserveError::Rejected);
    }
    let release = admissions
        .iter()
        .find(|row| row.try_get::<String, _>("purpose").ok().as_deref() == Some("release"))
        .ok_or(ObserveError::Rejected)?;
    let grant_id: String = release.try_get("grant")?;
    let grant = grants
        .iter()
        .find(|row| row.try_get::<String, _>("id").ok().as_deref() == Some(grant_id.as_str()))
        .ok_or(ObserveError::Rejected)?;
    if !cancelled {
        if locked.closed
            || locked.execution_status != "Running"
            || locked.attempt_status != "running"
            || locked.attempt_completed
            || start.try_get::<bool, _>("expired")?
        {
            return Err(ObserveError::Rejected);
        }
        if grant.try_get::<bool, _>("revoked")? || grant.try_get::<bool, _>("expired")? {
            return Err(ObserveError::Rejected);
        }
        // Refresh finite eligibility only after every common lock is held; an
        // earlier SELECT may have waited while the deadline/credential expired.
        let current = sqlx::query(
            "SELECT (s.deadline_utc IS NULL OR s.deadline_utc > clock_timestamp()) \
             AND g.initial_access_expires_at > clock_timestamp() AS current \
             FROM runtime_starts s JOIN workflow_runtime_sdk_grants g \
             ON g.committed_start_id=s.id WHERE s.id=$1::text::uuid AND g.id=$2::text::uuid",
        )
        .bind(&start_id)
        .bind(&grant_id)
        .fetch_one(&mut *locked.tx)
        .await?;
        if !current.try_get::<bool, _>("current")? {
            return Err(ObserveError::Rejected);
        }
        let success = frame["body"]["outcome"] == "success";
        let value = if success {
            frame["body"]["value"].clone()
        } else {
            json!({"error":frame["body"]["error"]})
        };
        let error = if success {
            None
        } else {
            Some(
                frame["body"]["error"]["message"]
                    .as_str()
                    .ok_or(ObserveError::Rejected)?,
            )
        };
        let updated = sqlx::query(
            "UPDATE executions SET status=$2::text::execution_status,result=$3::text::jsonb, \
             result_type='json',error_message=$4,completed_at=clock_timestamp() \
             WHERE id=$1::text::uuid AND status='Running'",
        )
        .bind(&fence.execution_id)
        .bind(if success { "Success" } else { "Failed" })
        .bind(value.to_string())
        .bind(error)
        .execute(&mut *locked.tx)
        .await?;
        if updated.rows_affected() != 1 {
            return Err(ObserveError::Rejected);
        }
        let terminalized = sqlx::query(
            "UPDATE workflow_execution_attempts SET status=$2,phase='terminal', \
             failure_phase=$3,failure_code=$4,completed_at=clock_timestamp() \
             WHERE id=$1::text::uuid AND completed_at IS NULL AND status='running'",
        )
        .bind(&fence.attempt_id)
        .bind(if success { "succeeded" } else { "failed" })
        .bind(if success { None } else { Some("result") })
        .bind(if success { None } else { Some("runtime_error") })
        .execute(&mut *locked.tx)
        .await?;
        if terminalized.rows_affected() != 1 {
            return Err(ObserveError::Rejected);
        }
        let closed = sqlx::query(
            "UPDATE runtime_sessions SET closed_at=clock_timestamp(),close_reason='result_committed' \
             WHERE id=$1::text::uuid AND closed_at IS NULL",
        )
        .bind(&fence.session_id)
        .execute(&mut *locked.tx)
        .await?;
        if closed.rows_affected() != 1 {
            return Err(ObserveError::Rejected);
        }
    }
    sqlx::query(
        "UPDATE workflow_runtime_sdk_grants SET revoked_at=clock_timestamp(), \
         revocation_reason='session_closed' WHERE runtime_session_id=$1::text::uuid AND revoked_at IS NULL",
    )
    .bind(&fence.session_id)
    .execute(&mut *locked.tx)
    .await?;
    let disposition = if cancelled { "retained" } else { "accepted" };
    let winner = if cancelled { "cancel" } else { "result" };
    sqlx::query(
        "INSERT INTO runtime_report_receipts (session_id,result_message_id,committed_start_id, \
         start_message_id,raw_result_payload,result_sha256,decision_id,disposition,winner) \
         VALUES ($1::text::uuid,$2::text::uuid,$3::text::uuid,$4::text::uuid,$5,$6,$7::text::uuid,$8,$9)",
    )
    .bind(&fence.session_id)
    .bind(message)
    .bind(&start_id)
    .bind(start_message)
    .bind(payload)
    .bind(&digest)
    .bind(decision_id)
    .bind(disposition)
    .bind(winner)
    .execute(&mut *locked.tx)
    .await?;
    locked
        .tx
        .commit()
        .await
        .map_err(|_| ObserveError::UncertainCommit)?;
    Ok(ResultDecision {
        body: json!({"result_message_id":message,"result_sha256":digest,
            "decision_id":decision_id,"disposition":disposition,"winner":winner}),
    })
}
