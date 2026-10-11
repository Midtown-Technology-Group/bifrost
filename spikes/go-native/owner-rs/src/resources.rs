//! Common isolated resource settlement. No report/Stopped JSON can authorize
//! this writer: it consumes actual original guardian and source-retirement proof.
use crate::{ObserveError, archive::sha256, guardian::RetiredSession, lock_session_state};
use serde_json::{Value, json};
use sqlx::{PgPool, Row};

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
                || !matches!(locked.execution_status.as_str(), "Success" | "Failed")
                || !matches!(locked.attempt_status.as_str(), "succeeded" | "failed")
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
