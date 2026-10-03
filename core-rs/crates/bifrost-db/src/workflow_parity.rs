//! Disposable-test SQL persistence prototype, never an authorization route.
//!
//! The caller owns the transaction, disables SQL parameter logging and must
//! roll back after ANY infrastructure error (including a partial write).
//! These functions neither commit nor retry. Actor, tenant, owner, source and
//! session admission are absent and must precede any future application wiring.
//! Queued Cancel follows the logical-then-attempt UPDATE order observed from
//! Python in supported run37099087578. Actual Rust DML, failure rollback and
//! concurrent lock parity still require supported differential execution.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use bifrost_contracts::runtime::CanonicalUuid;
use bifrost_domain::workflow::{
    AttemptPhase, AttemptRunningPlan, AttemptStatus, CancelPlan, ClaimToken, DecisionError,
    InputWrite, LogicalExecutionStatus, LogicalExecutionView, TimeWrite, WorkflowAttemptView,
    plan_attempt_running, plan_cancel_state,
};
use sqlx::{Postgres, Row, Transaction, postgres::PgRow};
use thiserror::Error;

/// Mechanical test input, not a grant. No formatting or serialization traits.
pub struct SqlClaimFence(CanonicalUuid);

impl SqlClaimFence {
    pub fn new(value: CanonicalUuid) -> Self {
        Self(value)
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum SqlDecision<P> {
    Applied(P),
    Rejected(DecisionError),
}

/// Closed stages contain no SQL parameters, driver details or private tokens.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SqlStage {
    Advisory,
    ReadExecution,
    ReadAttempt,
    Decode,
    Clock,
    WriteAttempt,
    WriteExecution,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SqlFailureClass {
    Database,
    InvalidRow,
    ClockRange,
    Cardinality,
}

#[derive(Debug, Error, PartialEq, Eq)]
#[error("workflow SQL prototype failed: {stage:?}/{class:?}")]
pub struct SqlInfrastructureError {
    pub stage: SqlStage,
    pub class: SqlFailureClass,
}

fn failure(stage: SqlStage, class: SqlFailureClass) -> SqlInfrastructureError {
    SqlInfrastructureError { stage, class }
}

fn database(stage: SqlStage) -> SqlInfrastructureError {
    failure(stage, SqlFailureClass::Database)
}

fn decode() -> SqlInfrastructureError {
    failure(SqlStage::Decode, SqlFailureClass::InvalidRow)
}

fn uuid(row: &PgRow, column: &str) -> Result<CanonicalUuid, SqlInfrastructureError> {
    let value: String = row.try_get(column).map_err(|_| decode())?;
    CanonicalUuid::new(value).map_err(|_| decode())
}

fn logical_status(value: &str) -> Result<LogicalExecutionStatus, SqlInfrastructureError> {
    use LogicalExecutionStatus as S;
    Ok(match value {
        "Scheduled" => S::Scheduled,
        "Pending" => S::Pending,
        "Running" => S::Running,
        "Success" => S::Success,
        "Failed" => S::Failed,
        "Timeout" => S::Timeout,
        "Stuck" => S::Stuck,
        "CompletedWithErrors" => S::CompletedWithErrors,
        "Cancelling" => S::Cancelling,
        "Cancelled" => S::Cancelled,
        _ => return Err(decode()),
    })
}

fn attempt_status(value: &str) -> Result<AttemptStatus, SqlInfrastructureError> {
    use AttemptStatus as S;
    Ok(match value {
        "dispatching" => S::Dispatching,
        "published" => S::Published,
        "claimed" => S::Claimed,
        "running" => S::Running,
        "succeeded" => S::Succeeded,
        "failed" => S::Failed,
        "timed_out" => S::TimedOut,
        "cancelled" => S::Cancelled,
        "worker_lost" => S::WorkerLost,
        "admission_rejected" => S::AdmissionRejected,
        _ => return Err(decode()),
    })
}

fn attempt_phase(value: &str) -> Result<AttemptPhase, SqlInfrastructureError> {
    use AttemptPhase as P;
    Ok(match value {
        "dispatch" => P::Dispatch,
        "queue" => P::Queue,
        "claim" => P::Claim,
        "admission" => P::Admission,
        "execution" => P::Execution,
        "result" => P::Result,
        "terminal" => P::Terminal,
        _ => return Err(decode()),
    })
}

fn attempt(row: &PgRow) -> Result<WorkflowAttemptView, SqlInfrastructureError> {
    let token: Option<String> = row.try_get("claim_token").map_err(|_| decode())?;
    let status: String = row.try_get("status").map_err(|_| decode())?;
    let phase: String = row.try_get("phase").map_err(|_| decode())?;
    Ok(WorkflowAttemptView {
        id: uuid(row, "id")?,
        execution_id: uuid(row, "execution_id")?,
        claim_token: token
            .map(CanonicalUuid::new)
            .transpose()
            .map_err(|_| decode())?
            .map(ClaimToken::new),
        status: attempt_status(&status)?,
        phase: attempt_phase(&phase)?,
        started_at_present: row.try_get("started_at_present").map_err(|_| decode())?,
        completed_at_present: row.try_get("completed_at_present").map_err(|_| decode())?,
    })
}

// PostgreSQL interval input uses an exact integer microsecond value. Restrict
// application UTC to Python datetime's epoch..year9999 range, without float loss.
fn utc_interval(elapsed: Duration) -> Result<String, SqlInfrastructureError> {
    if elapsed.as_secs() > 253_402_300_799 {
        return Err(failure(SqlStage::Clock, SqlFailureClass::ClockRange));
    }
    Ok(format!(
        "{}.{:06} seconds",
        elapsed.as_secs(),
        elapsed.subsec_micros()
    ))
}

fn utc_now() -> Result<String, SqlInfrastructureError> {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| failure(SqlStage::Clock, SqlFailureClass::ClockRange))?;
    utc_interval(elapsed)
}

fn exactly_one(count: u64, stage: SqlStage) -> Result<(), SqlInfrastructureError> {
    if count != 1 {
        return Err(failure(stage, SqlFailureClass::Cardinality));
    }
    Ok(())
}

/// Matches both execution and private token. Only the typed attempt is locked.
pub async fn apply_running(
    tx: &mut Transaction<'_, Postgres>,
    execution_id: CanonicalUuid,
    fence: &SqlClaimFence,
    process_id: Option<&str>,
) -> Result<SqlDecision<AttemptRunningPlan>, SqlInfrastructureError> {
    let row = sqlx::query(
        "SELECT id::text, execution_id::text, claim_token::text, status, phase, \
         started_at IS NOT NULL AS started_at_present, \
         completed_at IS NOT NULL AS completed_at_present \
         FROM public.workflow_execution_attempts \
         WHERE execution_id = $1::uuid AND claim_token = $2::uuid \
         AND completed_at IS NULL FOR UPDATE",
    )
    .persistent(false)
    .bind(execution_id.as_str())
    .bind(fence.0.as_str())
    .fetch_optional(&mut **tx)
    .await
    .map_err(|_| database(SqlStage::ReadAttempt))?;
    let view = row.as_ref().map(attempt).transpose()?;
    let token = ClaimToken::new(fence.0.clone());
    let plan =
        match plan_attempt_running(view.as_ref(), &execution_id, &token, process_id.is_some()) {
            Ok(plan) => plan,
            Err(reason) => return Ok(SqlDecision::Rejected(reason)),
        };
    let now = utc_now()?;
    let result = sqlx::query(
        "UPDATE public.workflow_execution_attempts \
         SET status = 'running', phase = 'execution', \
         started_at = CASE WHEN $3 THEN timestamptz 'epoch' + $4::interval ELSE started_at END, \
         heartbeat_at = timestamptz 'epoch' + $4::interval, \
         process_id = CASE WHEN $5 THEN $6::text ELSE process_id END \
         WHERE execution_id = $1::uuid AND claim_token = $2::uuid \
         AND completed_at IS NULL AND status IN ('claimed', 'running')",
    )
    .persistent(false)
    .bind(execution_id.as_str())
    .bind(fence.0.as_str())
    .bind(plan.started_at == TimeWrite::Now)
    .bind(&now)
    .bind(plan.process_id == InputWrite::SetSupplied)
    .bind(process_id)
    .execute(&mut **tx)
    .await
    .map_err(|_| database(SqlStage::WriteAttempt))?;
    exactly_one(result.rows_affected(), SqlStage::WriteAttempt)?;
    Ok(SqlDecision::Applied(plan))
}

/// Mechanical cancellation only. No caller/tenant/owner authorization is present.
pub async fn apply_cancel(
    tx: &mut Transaction<'_, Postgres>,
    execution_id: CanonicalUuid,
) -> Result<SqlDecision<CancelPlan>, SqlInfrastructureError> {
    sqlx::query(
        "SELECT pg_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || $1::text))",
    )
    .persistent(false)
    .bind(execution_id.as_str())
    .execute(&mut **tx)
    .await
    .map_err(|_| database(SqlStage::Advisory))?;
    let row =
        sqlx::query("SELECT id::text, status::text FROM public.executions WHERE id = $1::uuid")
            .persistent(false)
            .bind(execution_id.as_str())
            .fetch_optional(&mut **tx)
            .await
            .map_err(|_| database(SqlStage::ReadExecution))?;
    let execution = row
        .as_ref()
        .map(
            |row| -> Result<LogicalExecutionView, SqlInfrastructureError> {
                let status: String = row.try_get("status").map_err(|_| decode())?;
                Ok(LogicalExecutionView {
                    id: uuid(row, "id")?,
                    status: logical_status(&status)?,
                })
            },
        )
        .transpose()?;
    // Reject missing/terminal rows before looking at attempts or sampling time.
    let initial = match plan_cancel_state(execution.as_ref(), None, &execution_id) {
        Ok(plan) => plan,
        Err(reason) => return Ok(SqlDecision::Rejected(reason)),
    };
    let queued = initial.completed_at == TimeWrite::Now;
    // Production autoflush=False: logical ORM mutation is not flushed before
    // the queued attempt lock; UTC is sampled before that potentially slow await.
    let now = if queued { Some(utc_now()?) } else { None };
    let attempt_row = if queued {
        let rows = sqlx::query(
            "SELECT id::text, execution_id::text, claim_token::text, status, phase, \
             started_at IS NOT NULL AS started_at_present, \
             completed_at IS NOT NULL AS completed_at_present \
             FROM public.workflow_execution_attempts \
             WHERE execution_id = $1::uuid AND completed_at IS NULL FOR UPDATE",
        )
        .persistent(false)
        .bind(execution_id.as_str())
        .fetch_all(&mut **tx)
        .await
        .map_err(|_| database(SqlStage::ReadAttempt))?;
        if rows.len() > 1 {
            return Err(failure(SqlStage::ReadAttempt, SqlFailureClass::Cardinality));
        }
        rows.into_iter().next()
    } else {
        None
    };
    let view = attempt_row.as_ref().map(attempt).transpose()?;
    let plan = match plan_cancel_state(execution.as_ref(), view.as_ref(), &execution_id) {
        Ok(plan) => plan,
        Err(reason) => return Ok(SqlDecision::Rejected(reason)),
    };
    // ORM flush predicates only on ID, after its earlier advisory/eligibility
    // checks. Do not invent a late logical-status fence against other writers.
    let result = if let Some(now) = now.as_ref() {
        sqlx::query(
            "UPDATE public.executions SET status = 'Cancelled'::execution_status, \
             completed_at = timestamptz 'epoch' + $2::interval \
             WHERE id = $1::uuid",
        )
        .persistent(false)
        .bind(execution_id.as_str())
        .bind(now)
        .execute(&mut **tx)
        .await
    } else {
        sqlx::query(
            "UPDATE public.executions SET status = 'Cancelling'::execution_status \
             WHERE id = $1::uuid",
        )
        .persistent(false)
        .bind(execution_id.as_str())
        .execute(&mut **tx)
        .await
    }
    .map_err(|_| database(SqlStage::WriteExecution))?;
    exactly_one(result.rows_affected(), SqlStage::WriteExecution)?;
    // Actual Python queued-cancel flush writes the logical row before the
    // attempt; the earlier attempt lock and pre-lock time sample stay intact.
    if let (Some(attempt_plan), Some(view), Some(now)) = (plan.attempt, view.as_ref(), now.as_ref())
    {
        // Values are the ratified CancelPlan. No payload, metric or start fields.
        if attempt_plan.status != AttemptStatus::Cancelled
            || attempt_plan.phase != AttemptPhase::Terminal
        {
            return Err(decode());
        }
        let result = sqlx::query(
            "UPDATE public.workflow_execution_attempts SET status = 'cancelled', phase = 'terminal', \
             failure_phase = 'cancellation', failure_code = 'cancelled_before_claim', \
             completed_at = timestamptz 'epoch' + $3::interval, \
             heartbeat_at = timestamptz 'epoch' + $3::interval \
             WHERE id = $1::uuid AND execution_id = $2::uuid AND completed_at IS NULL",
        )
        .persistent(false)
        .bind(view.id.as_str())
        .bind(execution_id.as_str())
        .bind(now)
        .execute(&mut **tx)
        .await
        .map_err(|_| database(SqlStage::WriteAttempt))?;
        exactly_one(result.rows_affected(), SqlStage::WriteAttempt)?;
    }
    Ok(SqlDecision::Applied(plan))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timestamp_precision_and_range() -> Result<(), SqlInfrastructureError> {
        assert_eq!(
            utc_interval(Duration::new(42, 123_456_999))?,
            "42.123456 seconds"
        );
        assert_eq!(utc_interval(Duration::ZERO)?, "0.000000 seconds");
        assert_eq!(
            utc_interval(Duration::new(253_402_300_799, 999_999_999))?,
            "253402300799.999999 seconds"
        );
        assert!(utc_interval(Duration::from_secs(253_402_300_800)).is_err());
        Ok(())
    }

    #[test]
    fn schema_vocabulary_fails_closed() -> Result<(), SqlInfrastructureError> {
        assert_eq!(
            logical_status("CompletedWithErrors")?,
            LogicalExecutionStatus::CompletedWithErrors
        );
        assert_eq!(
            attempt_status("admission_rejected")?,
            AttemptStatus::AdmissionRejected
        );
        assert_eq!(attempt_phase("terminal")?, AttemptPhase::Terminal);
        assert!(logical_status("running").is_err());
        assert!(attempt_status("Running").is_err());
        assert!(attempt_phase("unknown").is_err());
        Ok(())
    }

    #[test]
    fn infrastructure_errors_are_static_and_not_domain_rejections() {
        let error = database(SqlStage::ReadAttempt);
        assert_eq!(
            error.to_string(),
            "workflow SQL prototype failed: ReadAttempt/Database"
        );
        assert_eq!(
            format!("{error:?}"),
            "SqlInfrastructureError { stage: ReadAttempt, class: Database }"
        );
        assert!(exactly_one(0, SqlStage::WriteAttempt).is_err());
        assert!(exactly_one(2, SqlStage::WriteAttempt).is_err());
    }
}
