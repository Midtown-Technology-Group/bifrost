//! Common isolated resource settlement. No report/Stopped JSON can authorize
//! this writer: it consumes actual original guardian and source-retirement proof.
use crate::{ObserveError, archive::sha256, guardian::RetiredSession, lock_session_state};
use serde_json::{Value, json};
use sqlx::{PgPool, Row};

fn matching_result_projection(root: &str, attempt: &str) -> bool {
    matches!(
        (root, attempt),
        ("Success", "succeeded") | ("Failed", "failed")
    )
}

/// Closed, immutable Result readback only. No Clone/serde constructor and no
/// Start/provision/SDK permit. Physical recovery must still prove the original
/// owner dead and consume its exclusive original custody and resource journals.
pub struct ClosedResultRecovery {
    pub(crate) fence: crate::SessionFence,
    pub(crate) artifact_id: String,
    receipt: Value,
}

impl ClosedResultRecovery {
    pub fn receipt(&self) -> &Value {
        &self.receipt
    }
}

pub async fn observe_closed_result_recovery(
    pool: &PgPool,
    fence: &crate::SessionFence,
    decision_id: &str,
) -> Result<ClosedResultRecovery, ObserveError> {
    let (receipt, artifact_id) = read_committed_result(pool, fence, decision_id).await?;
    Ok(ClosedResultRecovery {
        fence: fence.clone(),
        artifact_id,
        receipt,
    })
}

/// Readback after a genuinely lost Result COMMIT acknowledgement. This never
/// calls accept_result, constructs a ResultReceipt frame or resumes the runtime.
/// The later settlement writer repeats the complete common lock tail.
pub async fn reconcile_committed_result(
    pool: &PgPool,
    fence: &crate::SessionFence,
    decision_id: &str,
) -> Result<Value, ObserveError> {
    Ok(read_committed_result(pool, fence, decision_id).await?.0)
}

async fn read_committed_result(
    pool: &PgPool,
    fence: &crate::SessionFence,
    decision_id: &str,
) -> Result<(Value, String), ObserveError> {
    if !crate::canonical_uuid(decision_id) {
        return Err(ObserveError::Rejected);
    }
    let mut locked = lock_session_state(pool, fence, true).await?;
    if !locked.closed
        || locked.close_reason.as_deref() != Some("result_committed")
        || !matching_result_projection(&locked.execution_status, &locked.attempt_status)
        || !locked.attempt_completed
    {
        return Err(ObserveError::Rejected);
    }
    // Immutable receipt/Start facts can be independently read under the session
    // serialization point without taking a new, reversed receipt mutation lock.
    let rows = sqlx::query("SELECT r.result_message_id::text AS message,r.result_sha256,r.decision_id::text AS decision,r.disposition,r.winner FROM runtime_report_receipts r JOIN runtime_starts s ON s.id=r.committed_start_id AND s.session_id=r.session_id AND s.start_message_id=r.start_message_id WHERE r.session_id=$1::text::uuid AND s.execution_id=$2::text::uuid AND s.owner_incarnation_id=$3::text::uuid AND s.workflow_attempt_id=$4::text::uuid")
        .bind(&fence.session_id).bind(&fence.execution_id).bind(&fence.owner_incarnation_id).bind(&fence.attempt_id)
        .fetch_all(&mut *locked.tx).await?;
    if rows.len() != 1
        || rows[0].try_get::<String, _>("decision")? != decision_id
        || rows[0].try_get::<String, _>("disposition")? != "accepted"
        || rows[0].try_get::<String, _>("winner")? != "result"
    {
        return Err(ObserveError::Rejected);
    }
    let receipt = json!({"result_message_id":rows[0].try_get::<String,_>("message")?,
        "result_sha256":rows[0].try_get::<String,_>("result_sha256")?,
        "decision_id":decision_id,"disposition":"accepted","winner":"result"});
    let artifact_id = locked.artifact_id;
    locked
        .tx
        .commit()
        .await
        .map_err(|_| ObserveError::UncertainCommit)?;
    Ok((receipt, artifact_id))
}

pub async fn settle_retired_session(
    pool: &PgPool,
    retired: RetiredSession,
) -> Result<Value, ObserveError> {
    let fence = &retired.fence;
    let raw = serde_json::to_vec(&retired.observation).map_err(|_| ObserveError::Rejected)?;
    if raw.is_empty() || raw.len() > 65536 {
        return Err(ObserveError::Rejected);
    }
    let mut locked = lock_session_state(pool, fence, true).await?;
    let cancelled = locked.execution_status == "Cancelling";
    if !locked.closed
        || (cancelled
            && (locked.close_reason.as_deref() != Some("cancel_requested")
                || locked.attempt_status != "running"
                || locked.attempt_completed))
        || (!cancelled
            && (locked.close_reason.as_deref() != Some("result_committed")
                || !matching_result_projection(&locked.execution_status, &locked.attempt_status)
                || !locked.attempt_completed))
    {
        return Err(ObserveError::Rejected);
    }
    let accepted = sqlx::query("SELECT artifact_id FROM runtime_execution_owners WHERE execution_id=$1::text::uuid AND owner_incarnation_id=$2::text::uuid")
        .bind(&fence.execution_id).bind(&fence.owner_incarnation_id)
        .fetch_one(&mut *locked.tx).await?;
    if accepted.try_get::<String, _>("artifact_id")?
        != format!(
            "sha256:{}",
            retired.observation["archive_sha256"]
                .as_str()
                .ok_or(ObserveError::Rejected)?
        )
    {
        return Err(ObserveError::Rejected);
    }
    let start =
        sqlx::query("SELECT id FROM runtime_starts WHERE session_id=$1::text::uuid FOR UPDATE")
            .bind(&fence.session_id)
            .fetch_optional(&mut *locked.tx)
            .await?;
    if start.is_none() {
        return Err(ObserveError::Rejected);
    }
    let admissions = sqlx::query("SELECT purpose FROM runtime_admissions WHERE session_id=$1::text::uuid ORDER BY purpose,id FOR UPDATE")
        .bind(&fence.session_id).fetch_all(&mut *locked.tx).await?;
    if admissions.len() != 2
        || admissions.iter().any(|row| {
            !matches!(
                row.try_get::<String, _>("purpose").ok().as_deref(),
                Some("provision" | "release")
            )
        })
    {
        return Err(ObserveError::Rejected);
    }
    let grants = sqlx::query("SELECT revoked_at IS NOT NULL AS revoked, revocation_reason FROM workflow_runtime_sdk_grants WHERE runtime_session_id=$1::text::uuid ORDER BY id FOR UPDATE")
        .bind(&fence.session_id).fetch_all(&mut *locked.tx).await?;
    if grants.len() != 1
        || !grants[0].try_get::<bool, _>("revoked")?
        || grants[0].try_get::<String, _>("revocation_reason")? != "session_closed"
    {
        return Err(ObserveError::Rejected);
    }
    let receipts = sqlx::query("SELECT disposition,winner FROM runtime_report_receipts WHERE session_id=$1::text::uuid ORDER BY result_message_id FOR UPDATE")
        .bind(&fence.session_id).fetch_all(&mut *locked.tx).await?;
    if cancelled {
        for receipt in &receipts {
            if receipt.try_get::<String, _>("disposition")? != "retained"
                || receipt.try_get::<String, _>("winner")? != "cancel"
            {
                return Err(ObserveError::Rejected);
            }
        }
    } else if receipts.len() != 1
        || receipts[0].try_get::<String, _>("disposition")? != "accepted"
        || receipts[0].try_get::<String, _>("winner")? != "result"
    {
        return Err(ObserveError::Rejected);
    }
    // Session serialization fences the absent immutable settlement. Creation is
    // INSERT-only; uncertainty consumes proof and never retries/conflict-updates.
    let existing = sqlx::query("SELECT session_id FROM runtime_resource_settlements WHERE session_id=$1::text::uuid FOR UPDATE")
        .bind(&fence.session_id).fetch_optional(&mut *locked.tx).await?;
    if existing.is_some() {
        return Err(ObserveError::Rejected);
    }
    let digest = sha256(&raw);
    sqlx::query("INSERT INTO runtime_resource_settlements (session_id,execution_id,owner_incarnation_id,workflow_attempt_id,raw_observation,observation_sha256) VALUES ($1::text::uuid,$2::text::uuid,$3::text::uuid,$4::text::uuid,$5,$6)")
        .bind(&fence.session_id).bind(&fence.execution_id).bind(&fence.owner_incarnation_id).bind(&fence.attempt_id).bind(&raw).bind(&digest)
        .execute(&mut *locked.tx).await?;
    if cancelled {
        let root = sqlx::query("UPDATE executions SET status='Cancelled',result=NULL,result_type=NULL,error_message='Execution cancelled',completed_at=clock_timestamp() WHERE id=$1::text::uuid AND status='Cancelling'")
            .bind(&fence.execution_id).execute(&mut *locked.tx).await?;
        let attempt = sqlx::query("UPDATE workflow_execution_attempts SET status='cancelled',phase='terminal',failure_phase='cancellation',failure_code='cancelled',completed_at=clock_timestamp() WHERE id=$1::text::uuid AND status='running' AND completed_at IS NULL")
            .bind(&fence.attempt_id).execute(&mut *locked.tx).await?;
        if root.rows_affected() != 1 || attempt.rows_affected() != 1 {
            return Err(ObserveError::Rejected);
        }
    }
    locked
        .tx
        .commit()
        .await
        .map_err(|_| ObserveError::UncertainCommit)?;
    Ok(
        json!({"session_id":fence.session_id,"observation_sha256":digest,
        "durable_source_settlement":true,"durable_cancelled_finalization":cancelled,
        "runtime_acceptance":false,"production_dispatch":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::matching_result_projection;

    #[test]
    fn reconciliation_rejects_crossed_or_nonterminal_outcomes() {
        assert!(matching_result_projection("Success", "succeeded"));
        assert!(matching_result_projection("Failed", "failed"));
        for (root, attempt) in [
            ("Success", "failed"),
            ("Failed", "succeeded"),
            ("Running", "succeeded"),
            ("Cancelled", "cancelled"),
            ("Success", "running"),
            ("Success", "worker_lost"),
        ] {
            assert!(!matching_result_projection(root, attempt));
        }
    }
}
