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


// Result-only transport. The original Running/Cancel error types above are
// intentionally unchanged, including their static Display and Debug contract.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ResultSqlStage {
    Advisory, ReadHistory, ReadExecution, ReadAttempt, ReadContext,
    Decode, Clock, WriteAttempt, WriteExecution,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ResultSqlFailureClass { Database, InvalidRow, ClockRange, Cardinality }

#[derive(Clone, Copy, PartialEq, Eq)]
enum ResultSqlCode { Check, Unique, ForeignKey, NumericRange, StatementTimeout, LockTimeout }

impl ResultSqlCode {
    fn from_code(code: Option<&str>) -> Option<Self> {
        match code {
            Some("23514") => Some(Self::Check),
            Some("23505") => Some(Self::Unique),
            Some("23503") => Some(Self::ForeignKey),
            Some("22003") => Some(Self::NumericRange),
            Some("57014") => Some(Self::StatementTimeout),
            Some("55P03") => Some(Self::LockTimeout),
            _ => None,
        }
    }
    fn as_str(self) -> &'static str {
        match self {
            Self::Check => "23514", Self::Unique => "23505", Self::ForeignKey => "23503",
            Self::NumericRange => "22003", Self::StatementTimeout => "57014", Self::LockTimeout => "55P03",
        }
    }
}

/// A transient database error supplies only an exact closed SQLSTATE.
/// No original error, message, query, source chain or formatting is retained.
pub fn result_sqlstate(error: &sqlx::Error) -> Option<&'static str> {
    let code = error.as_database_error().and_then(|value| value.code());
    ResultSqlCode::from_code(code.as_deref()).map(ResultSqlCode::as_str)
}

#[derive(PartialEq, Eq)]
pub struct ResultSqlFailure {
    stage: ResultSqlStage,
    class: ResultSqlFailureClass,
    code: Option<ResultSqlCode>,
}

impl ResultSqlFailure {
    pub fn stage(&self) -> ResultSqlStage { self.stage }
    pub fn class(&self) -> ResultSqlFailureClass { self.class }
    pub fn code(&self) -> Option<&'static str> { self.code.map(ResultSqlCode::as_str) }
    fn local(stage: ResultSqlStage, class: ResultSqlFailureClass) -> Self {
        Self { stage, class, code: None }
    }
    fn database(stage: ResultSqlStage, error: sqlx::Error) -> Self {
        Self { stage, class: ResultSqlFailureClass::Database,
            code: ResultSqlCode::from_code(result_sqlstate(&error)) }
    }
    fn shared(error: SqlInfrastructureError) -> Self {
        match (error.stage, error.class) {
            (SqlStage::Clock, SqlFailureClass::ClockRange) =>
                Self::local(ResultSqlStage::Clock, ResultSqlFailureClass::ClockRange),
            _ => Self::local(ResultSqlStage::Decode, ResultSqlFailureClass::InvalidRow),
        }
    }
}

fn result_decode() -> ResultSqlFailure {
    ResultSqlFailure::local(ResultSqlStage::Decode, ResultSqlFailureClass::InvalidRow)
}

/// Presence is not Option: supplied NULL and omitted members have different effects.
pub enum ResultPresence<T> { Absent, Null, Value(T) }

impl<T> ResultPresence<T> {
    fn supplied(&self) -> bool { !matches!(self, Self::Absent) }
    fn value(&self) -> Option<&T> { if let Self::Value(value) = self { Some(value) } else { None } }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ResultJsonRole { Result, Variables, Context }

/// Source-prepared JSON custody, never an unchecked classification or grant.
/// The reference harness separately binds exact sanitizer bytes and preparation.
pub struct PreparedJson {
    text: String,
    role: ResultJsonRole,
    result_type: &'static str,
    nonempty_object: bool,
}

fn python_whitespace(value: char) -> bool {
    matches!(value, '\u{0009}'..='\u{000d}' | '\u{001c}'..='\u{0020}' |
        '\u{0085}' | '\u{00a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' |
        '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

impl PreparedJson {
    pub fn new(raw: &str, prepared: String, role: ResultJsonRole) -> Result<Self, ResultSqlFailure> {
        if raw.len() > 65_536 || prepared.len() > 65_536 { return Err(result_decode()); }
        let original = bifrost_contracts::runtime::decode_ordinary_json(raw.as_bytes()).map_err(|_| result_decode())?;
        let value = bifrost_contracts::runtime::decode_ordinary_json(prepared.as_bytes()).map_err(|_| result_decode())?;
        // Inferred ordinary Value views require no serde dependency in this library.
        // They are validation only; the admitted original prepared text is bound unchanged.
        for root in [&original, &value] {
            let mut pending = vec![root];
            while let Some(node) = pending.pop() {
                if node.is_number() && !node.is_i64() && !node.is_u64() { return Err(result_decode()); }
                if node.as_str().is_some_and(|text| text.contains('\0')) { return Err(result_decode()); }
                if let Some(map) = node.as_object() {
                    if map.keys().any(|key| key.contains('\0')) { return Err(result_decode()); }
                    pending.extend(map.values());
                }
                if let Some(array) = node.as_array() { pending.extend(array); }
            }
        }
        let same_class = original.is_object() == value.is_object()
            && original.is_array() == value.is_array() && original.is_string() == value.is_string()
            && original.is_boolean() == value.is_boolean() && original.is_number() == value.is_number();
        if original.is_null() || value.is_null() || !same_class || (role == ResultJsonRole::Context && !value.is_object()) {
            return Err(result_decode());
        }
        let result_type = match original.as_str() {
            Some(text) if text.chars().find(|character| !python_whitespace(*character)) == Some('<') => "html",
            Some(_) => "text", None => "json",
        };
        Ok(Self { text: prepared, role, result_type, nonempty_object: value.as_object().is_some_and(|map| !map.is_empty()) })
    }
}

pub struct ResultMetrics {
    pub peak_memory_bytes: ResultPresence<i64>, pub process_rss_bytes: ResultPresence<i64>,
    pub cpu_user_seconds: ResultPresence<f64>, pub cpu_system_seconds: ResultPresence<f64>,
    pub cpu_total_seconds: ResultPresence<f64>,
}

pub struct ResultRoi {
    pub time_saved: ResultPresence<i32>,
    pub value: ResultPresence<super::workflow_numeric::NumericInput>,
}

pub struct SuccessResultFields {
    pub status: ResultPresence<String>, pub result: ResultPresence<PreparedJson>,
    pub error: ResultPresence<String>, pub error_type: ResultPresence<String>,
    pub duration_ms: ResultPresence<i32>, pub variables: ResultPresence<PreparedJson>,
    pub execution_context: ResultPresence<PreparedJson>, pub metrics: ResultPresence<ResultMetrics>,
    pub roi: ResultPresence<ResultRoi>,
}

pub struct FailureResultFields {
    pub error: ResultPresence<String>, pub error_type: ResultPresence<String>,
    pub duration_ms: ResultPresence<i32>, pub execution_context: ResultPresence<PreparedJson>,
    pub metrics: ResultPresence<ResultMetrics>,
}

enum ResultFields { Success(SuccessResultFields), Failure(FailureResultFields) }

pub struct SqlResultInput { execution_id: CanonicalUuid, fence: Option<SqlClaimFence>, fields: ResultFields }

impl SqlResultInput {
    pub fn success(execution_id: CanonicalUuid, fence: Option<SqlClaimFence>, fields: SuccessResultFields) -> Result<Self, ResultSqlFailure> {
        Self::checked(execution_id, fence, ResultFields::Success(fields))
    }
    pub fn failure(execution_id: CanonicalUuid, fence: Option<SqlClaimFence>, fields: FailureResultFields) -> Result<Self, ResultSqlFailure> {
        Self::checked(execution_id, fence, ResultFields::Failure(fields))
    }
    fn checked(execution_id: CanonicalUuid, fence: Option<SqlClaimFence>, fields: ResultFields) -> Result<Self, ResultSqlFailure> {
        let (error, kind, metrics) = match &fields {
            ResultFields::Success(value) => {
                for (field, role) in [(&value.result, ResultJsonRole::Result), (&value.variables, ResultJsonRole::Variables), (&value.execution_context, ResultJsonRole::Context)] {
                    if field.value().is_some_and(|json| json.role != role) { return Err(result_decode()); }
                }
                if value.status.value().is_some_and(|text| text.contains('\0')) { return Err(result_decode()); }
                if let Some(roi) = value.roi.value() {
                    if matches!(roi.value.value(), Some(super::workflow_numeric::NumericInput::Float(number)) if !number.is_finite()) {
                        return Err(result_decode());
                    }
                }
                (&value.error, &value.error_type, &value.metrics)
            }
            ResultFields::Failure(value) => {
                if value.execution_context.value().is_some_and(|json| json.role != ResultJsonRole::Context) { return Err(result_decode()); }
                (&value.error, &value.error_type, &value.metrics)
            },
        };
        if error.value().is_some_and(|text| text.contains('\0')) || kind.value().is_some_and(|text| text.contains('\0')) {
            return Err(result_decode());
        }
        if matches!(&fields, ResultFields::Failure(_)) && kind.value().is_some_and(|value|
            matches!(value.as_str(), "ProcessCrashError" | "WorkerShutdownError" | "OrphanedExecution")) { return Err(result_decode()); }
        if let Some(metrics) = metrics.value() {
            for field in [&metrics.cpu_user_seconds, &metrics.cpu_system_seconds, &metrics.cpu_total_seconds] {
                if field.value().is_some_and(|number| !number.is_finite()) { return Err(result_decode()); }
            }
        }
        Ok(Self { execution_id, fence, fields })
    }
}

pub struct AppliedResult {
    pub plan: bifrost_domain::workflow::FencedResultPlan,
    pub affected_execution_rows: u64,
    pub affected_attempt_rows: u64,
}

fn result_one(count: u64, stage: ResultSqlStage) -> Result<(), ResultSqlFailure> {
    if count == 1 { Ok(()) } else { Err(ResultSqlFailure::local(stage, ResultSqlFailureClass::Cardinality)) }
}

fn result_duration(value: &ResultPresence<i32>) -> Option<i32> {
    match value { ResultPresence::Absent => Some(0), ResultPresence::Null => None, ResultPresence::Value(value) => Some(*value) }
}

fn result_status(value: &ResultPresence<String>) -> LogicalExecutionStatus {
    value.value().and_then(|text| logical_status(text).ok()).unwrap_or(LogicalExecutionStatus::Success)
}

fn result_outcome(fields: &ResultFields) -> bifrost_domain::workflow::WorkflowOutcome {
    use bifrost_domain::workflow::{WorkflowOutcome, RuntimeFailureKind};
    match fields {
        ResultFields::Success(value) => WorkflowOutcome::Success(result_status(&value.status)),
        ResultFields::Failure(value) => WorkflowOutcome::Failure(match value.error_type.value().map(String::as_str) {
            Some("TimeoutError") => RuntimeFailureKind::Timeout,
            Some("CancelledError") => RuntimeFailureKind::Cancelled,
            Some("ResultPersistenceError") => RuntimeFailureKind::ResultPersistence,
            _ => RuntimeFailureKind::TenantCode,
        }),
    }
}

async fn result_context(tx: &mut Transaction<'_, Postgres>, id: &CanonicalUuid) -> Result<Option<String>, ResultSqlFailure> {
    let text: Option<String> = sqlx::query_scalar("SELECT execution_context::text FROM public.executions WHERE id = $1::uuid")
        .persistent(false).bind(id.as_str()).fetch_one(&mut **tx).await
        .map_err(|error| ResultSqlFailure::database(ResultSqlStage::ReadContext, error))?;
    if let Some(text) = text.as_ref() {
        let value = bifrost_contracts::runtime::decode_ordinary_json(text.as_bytes()).map_err(|_| result_decode())?;
        if !value.is_null() { PreparedJson::new(text, text.clone(), ResultJsonRole::Context)?; }
    }
    Ok(text.filter(|text| text != "null"))
}

/// Read-only test facts; construction cannot mutate an owned collector.
#[derive(Clone, Copy)]
pub struct ResultClockEvent { pub ordinal: u8, pub utc_us: u64, pub elapsed_ns: u64 }
#[derive(Clone, Copy)]
pub struct ResultAttemptClock {
    pub read_ack: Option<ResultClockEvent>, pub sample: Option<ResultClockEvent>,
    pub write_dispatch: Option<ResultClockEvent>, pub write_ack: Option<ResultClockEvent>,
}
#[derive(Clone, Copy)]
pub enum ResultClockUpper { ContextReadDispatch, WriteExecutionDispatch }
#[derive(Clone, Copy)]
pub struct ResultLogicalClock {
    pub status_read_ack: Option<ResultClockEvent>, pub sample: Option<ResultClockEvent>,
    pub upper: Option<ResultClockEvent>, pub upper_kind: ResultClockUpper,
    pub write_ack: Option<ResultClockEvent>,
}
#[derive(Clone, Copy)]
pub struct ResultCommitClock { pub dispatch: Option<ResultClockEvent>, pub ack: Option<ResultClockEvent> }

/// Invocation-local instrumentation, never a settlement or authority grant.
pub struct ResultClockWitness {
    origin: std::time::Instant, valid: bool, ordinal: u8,
    last: Option<ResultClockEvent>, admitted: bool, adapter_done: bool,
    attempt: Option<ResultAttemptClock>, logical: Option<ResultLogicalClock>,
    commit: Option<ResultCommitClock>,
}
impl ResultClockWitness {
    fn new() -> Self {
        Self { origin: std::time::Instant::now(), valid: true, ordinal: 0, last: None,
            admitted: false, adapter_done: false, attempt: None, logical: None, commit: None }
    }
    fn record(&mut self, utc: Duration) -> Option<ResultClockEvent> {
        const MAX: u128 = 9_007_199_254_740_991;
        let utc_us = utc.as_micros(); let elapsed_ns = self.origin.elapsed().as_nanos();
        if !self.valid || self.ordinal >= 10 || utc_us > MAX || elapsed_ns > MAX {
            self.valid = false; return None;
        }
        let (Ok(utc_us), Ok(elapsed_ns)) = (u64::try_from(utc_us), u64::try_from(elapsed_ns)) else {
            self.valid = false; return None;
        };
        if self.last.is_some_and(|previous| previous.utc_us > utc_us || previous.elapsed_ns > elapsed_ns) {
            self.valid = false; return None;
        }
        self.ordinal += 1;
        let event = ResultClockEvent { ordinal: self.ordinal, utc_us, elapsed_ns };
        self.last = Some(event); Some(event)
    }
    fn mark(&mut self) -> Option<ResultClockEvent> {
        match SystemTime::now().duration_since(UNIX_EPOCH) {
            Ok(utc) => self.record(utc), Err(_) => { self.valid = false; None }
        }
    }
    pub fn attempt(&self) -> Option<ResultAttemptClock> { self.attempt }
    pub fn logical(&self) -> Option<ResultLogicalClock> { self.logical }
    pub fn commit(&self) -> Option<ResultCommitClock> { self.commit }
    pub fn complete(&self) -> bool {
        if !self.valid { return false; }
        if !self.admitted { return self.attempt.is_none() && self.logical.is_none() && self.commit.is_none(); }
        if !self.adapter_done { return false; }
        result_clock_roles_complete(self.attempt, self.logical, self.commit)
    }
    /// Trusted caller places this immediately before its actual consumed commit.
    pub fn commit_dispatch(&mut self) {
        if !self.admitted || !self.adapter_done || self.commit.is_some() {
            self.valid = false; return;
        }
        let dispatch = self.mark();
        self.commit = Some(ResultCommitClock { dispatch, ack: None });
    }
    /// Trusted caller places this only after that commit actually returns Ok.
    pub fn commit_ack(&mut self) {
        if !self.admitted || !self.adapter_done || !self.commit.is_some_and(|value| value.dispatch.is_some() && value.ack.is_none()) {
            self.valid = false; return;
        }
        let ack = self.mark();
        if let Some(commit) = self.commit.as_mut() { commit.ack = ack; }
    }
}

/// Closed role admission shared by the owned collector and private driver controls.
/// Copy views are observations only; this cannot create an owned witness.
pub fn result_clock_roles_complete(attempt: Option<ResultAttemptClock>, logical: Option<ResultLogicalClock>, commit: Option<ResultCommitClock>) -> bool {
    let (Some(attempt), Some(commit)) = (attempt, commit) else { return false; };
    let mut events = vec![attempt.read_ack, attempt.sample, attempt.write_dispatch, attempt.write_ack];
    if let Some(logical) = logical {
        events.extend([logical.status_read_ack, logical.sample, logical.upper, logical.write_ack]);
    }
    events.extend([commit.dispatch, commit.ack]);
    let mut previous: Option<ResultClockEvent> = None;
    for (index, event) in events.into_iter().enumerate() {
        let Some(event) = event else { return false; };
        if usize::from(event.ordinal) != index + 1 || event.utc_us > 9_007_199_254_740_991 || event.elapsed_ns > 9_007_199_254_740_991 { return false; }
        if previous.is_some_and(|value| value.utc_us > event.utc_us || value.elapsed_ns > event.elapsed_ns) { return false; }
        previous = Some(event);
    }
    true
}

fn result_clock_now(witness: &mut Option<&mut ResultClockWitness>, logical: bool) -> Result<String, ResultSqlFailure> {
    let elapsed = SystemTime::now().duration_since(UNIX_EPOCH)
        .map_err(|_| ResultSqlFailure::shared(failure(SqlStage::Clock, SqlFailureClass::ClockRange)))?;
    let interval = utc_interval(elapsed).map_err(ResultSqlFailure::shared)?;
    if let Some(witness) = witness.as_deref_mut() {
        let sample = witness.record(elapsed);
        if logical { if let Some(value) = witness.logical.as_mut() { value.sample = sample; } }
        else if let Some(value) = witness.attempt.as_mut() { value.sample = sample; }
    }
    Ok(interval)
}

pub async fn apply_result_observed(tx: &mut Transaction<'_, Postgres>, input: &SqlResultInput)
    -> (Result<SqlDecision<AppliedResult>, ResultSqlFailure>, ResultClockWitness) {
    let mut witness = ResultClockWitness::new();
    let result = apply_result_inner(tx, input, Some(&mut witness)).await;
    (result, witness)
}

/// Actual selected Result writes only. Caller owns settlement and authority.
pub async fn apply_result(tx: &mut Transaction<'_, Postgres>, input: &SqlResultInput) -> Result<SqlDecision<AppliedResult>, ResultSqlFailure> {
    apply_result_inner(tx, input, None).await
}

async fn apply_result_inner(tx: &mut Transaction<'_, Postgres>, input: &SqlResultInput, mut witness: Option<&mut ResultClockWitness>) -> Result<SqlDecision<AppliedResult>, ResultSqlFailure> {
    use bifrost_domain::workflow::{AttemptHistory, FailureCode, FailurePhase, RuntimeFailureKind, WorkflowOutcome, plan_fenced_result};
    let (duration, metrics) = match &input.fields {
        ResultFields::Success(value) => (result_duration(&value.duration_ms), value.metrics.value()),
        ResultFields::Failure(value) => (result_duration(&value.duration_ms), value.metrics.value()),
    };
    let outcome = result_outcome(&input.fields);
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || $1::text))")
        .persistent(false).bind(input.execution_id.as_str()).execute(&mut **tx).await
        .map_err(|error| ResultSqlFailure::database(ResultSqlStage::Advisory, error))?;
    if input.fence.is_none() {
        let recorded: bool = sqlx::query_scalar("SELECT EXISTS (SELECT 1 FROM public.workflow_execution_attempts WHERE execution_id = $1::uuid)")
            .persistent(false).bind(input.execution_id.as_str()).fetch_one(&mut **tx).await
            .map_err(|error| ResultSqlFailure::database(ResultSqlStage::ReadHistory, error))?;
        return match plan_fenced_result(None, None, &input.execution_id, None, if recorded { AttemptHistory::Recorded } else { AttemptHistory::Unrecorded }, outcome, duration.is_some()) {
            Err(reason) => Ok(SqlDecision::Rejected(reason)), Ok(_) => Err(result_decode()),
        };
    }
    let row = sqlx::query("SELECT id::text, status::text FROM public.executions WHERE id = $1::uuid FOR UPDATE")
        .persistent(false).bind(input.execution_id.as_str()).fetch_optional(&mut **tx).await
        .map_err(|error| ResultSqlFailure::database(ResultSqlStage::ReadExecution, error))?;
    let execution = row.as_ref().map(|row| -> Result<LogicalExecutionView, ResultSqlFailure> {
        let status: String = row.try_get("status").map_err(|_| result_decode())?;
        Ok(LogicalExecutionView { id: uuid(row, "id").map_err(ResultSqlFailure::shared)?, status: logical_status(&status).map_err(ResultSqlFailure::shared)? })
    }).transpose()?;
    let Some(fence) = input.fence.as_ref() else { return Err(result_decode()); };
    let token = ClaimToken::new(fence.0.clone());
    if execution.as_ref().is_none_or(|value| !matches!(value.status, LogicalExecutionStatus::Running | LogicalExecutionStatus::Cancelling)) {
        return match plan_fenced_result(execution.as_ref(), None, &input.execution_id, Some(&token), AttemptHistory::Recorded, outcome, duration.is_some()) {
            Err(reason) => Ok(SqlDecision::Rejected(reason)), Ok(_) => Err(result_decode()),
        };
    }
    let Some(execution) = execution else { return Err(result_decode()); };
    let rows = sqlx::query("SELECT id::text, execution_id::text, claim_token::text, status, phase, started_at IS NOT NULL AS started_at_present, completed_at IS NOT NULL AS completed_at_present FROM public.workflow_execution_attempts WHERE execution_id = $1::uuid AND claim_token = $2::uuid AND completed_at IS NULL FOR UPDATE")
        .persistent(false).bind(input.execution_id.as_str()).bind(fence.0.as_str()).fetch_all(&mut **tx).await
        .map_err(|error| ResultSqlFailure::database(ResultSqlStage::ReadAttempt, error))?;
    let read_ack = witness.as_deref_mut().and_then(ResultClockWitness::mark);
    if rows.len() > 1 { return Err(ResultSqlFailure::local(ResultSqlStage::ReadAttempt, ResultSqlFailureClass::Cardinality)); }
    let view = rows.first().map(attempt).transpose().map_err(ResultSqlFailure::shared)?;
    let plan = match plan_fenced_result(Some(&execution), view.as_ref(), &input.execution_id, Some(&token), AttemptHistory::Recorded, outcome, duration.is_some()) {
        Ok(plan) => plan, Err(reason) => return Ok(SqlDecision::Rejected(reason)),
    };
    let Some(view) = view else { return Err(result_decode()); };
    if let Some(witness) = witness.as_deref_mut() {
        witness.admitted = true;
        witness.attempt = Some(ResultAttemptClock { read_ack, sample: None, write_dispatch: None, write_ack: None });
        if duration.is_some() {
            witness.logical = Some(ResultLogicalClock { status_read_ack: None, sample: None, upper: None,
                upper_kind: ResultClockUpper::WriteExecutionDispatch, write_ack: None });
        }
    }
    let now = result_clock_now(&mut witness, false)?;
    let attempt_status = match plan.attempt.status { AttemptStatus::Succeeded => "succeeded", AttemptStatus::Failed => "failed", AttemptStatus::TimedOut => "timed_out", AttemptStatus::Cancelled => "cancelled", _ => return Err(result_decode()) };
    let failure_code = match plan.attempt.failure_code { None => None, Some(FailureCode::ExecutionTimeout) => Some("execution_timeout"), Some(FailureCode::Cancelled) => Some("cancelled"), Some(FailureCode::ResultPersistFailed) => Some("result_persist_failed"), Some(FailureCode::TenantCodeError) => Some("tenant_code_error"), _ => return Err(result_decode()) };
    let failure_phase = match plan.attempt.failure_phase { None => None, Some(FailurePhase::Execution) => Some("execution"), Some(FailurePhase::Result) => Some("result"), Some(FailurePhase::Cancellation) => Some("cancellation"), _ => return Err(result_decode()) };
    let attempt_query = sqlx::query("UPDATE public.workflow_execution_attempts SET status = $2, phase = 'terminal', failure_phase = $3, failure_code = $4, duration_ms = $5, peak_memory_bytes = $6, cpu_total_seconds = $7, heartbeat_at = timestamptz 'epoch' + $8::interval, completed_at = timestamptz 'epoch' + $8::interval WHERE id = $1::uuid")
        .persistent(false).bind(view.id.as_str()).bind(attempt_status).bind(failure_phase).bind(failure_code)
        .bind(duration).bind(metrics.and_then(|value| value.peak_memory_bytes.value()).copied())
        .bind(metrics.and_then(|value| value.cpu_total_seconds.value()).copied()).bind(&now);
    if let Some(witness) = witness.as_deref_mut() {
        let dispatch = witness.mark();
        if let Some(value) = witness.attempt.as_mut() { value.write_dispatch = dispatch; }
    }
    let attempt_result = attempt_query.execute(&mut **tx).await.map_err(|error| ResultSqlFailure::database(ResultSqlStage::WriteAttempt, error))?;
    if let Some(witness) = witness.as_deref_mut() {
        let ack = witness.mark();
        if let Some(value) = witness.attempt.as_mut() { value.write_ack = ack; }
    }
    result_one(attempt_result.rows_affected(), ResultSqlStage::WriteAttempt)?;
    // Persistence failure's truthy context merge precedes the repository lock/read.
    let supplied_context = match &input.fields { ResultFields::Success(value) => value.execution_context.value(), ResultFields::Failure(value) => value.execution_context.value().filter(|context| context.nonempty_object && matches!(outcome, WorkflowOutcome::Failure(RuntimeFailureKind::ResultPersistence))) };
    let failure_existing = if matches!(&input.fields, ResultFields::Failure(_)) && supplied_context.is_some() { result_context(tx, &input.execution_id).await? } else { None };
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || $1::text))")
        .persistent(false).bind(input.execution_id.as_str()).execute(&mut **tx).await
        .map_err(|error| ResultSqlFailure::database(ResultSqlStage::Advisory, error))?;
    let current: Option<String> = sqlx::query_scalar("SELECT status::text FROM public.executions WHERE id = $1::uuid")
        .persistent(false).bind(input.execution_id.as_str()).fetch_optional(&mut **tx).await
        .map_err(|error| ResultSqlFailure::database(ResultSqlStage::ReadExecution, error))?;
    if let Some(witness) = witness.as_deref_mut() {
        if witness.logical.is_some() {
            let ack = witness.mark();
            if let Some(value) = witness.logical.as_mut() { value.status_read_ack = ack; }
        }
    }
    let cancelled = matches!(current.as_deref(), Some("Cancelling" | "Cancelled"));
    let final_status = if cancelled { LogicalExecutionStatus::Cancelled } else { plan.execution.status };
    let completed = duration.map(|_| result_clock_now(&mut witness, true)).transpose()?;
    if supplied_context.is_some() {
        if let Some(witness) = witness.as_deref_mut() {
            if witness.logical.is_some() {
                let upper = witness.mark();
                if let Some(value) = witness.logical.as_mut() { value.upper = upper; value.upper_kind = ResultClockUpper::ContextReadDispatch; }
            }
        }
    }
    let existing_context = if supplied_context.is_some() { result_context(tx, &input.execution_id).await? } else { None };
    // Missing and null are never collapsed for metric assignments.
    let (result, variables, error, time_saved, numeric) = match &input.fields {
        ResultFields::Success(value) => {
            let roi = value.roi.value();
            let time = match roi.map(|value| &value.time_saved) { Some(ResultPresence::Null) => None, Some(ResultPresence::Value(value)) => Some(*value), _ => Some(0) };
            let number = if cancelled { None } else {
                let raw = match roi.map(|value| &value.value) { Some(ResultPresence::Null) => None, Some(ResultPresence::Value(value)) => Some(match value { super::workflow_numeric::NumericInput::Signed(value) => super::workflow_numeric::NumericInput::Signed(*value), super::workflow_numeric::NumericInput::Unsigned(value) => super::workflow_numeric::NumericInput::Unsigned(*value), super::workflow_numeric::NumericInput::Float(value) => super::workflow_numeric::NumericInput::Float(*value) }), _ => Some(super::workflow_numeric::NumericInput::Float(0.0)) };
                raw.map(super::workflow_numeric::exact_numeric_text).transpose().map_err(|_| result_decode())?
            };
            (value.result.value(), value.variables.value(), value.error.value().map(String::as_str), time, number)
        }
        ResultFields::Failure(value) => (None, None, match &value.error { ResultPresence::Absent => Some("Unknown error"), ResultPresence::Null => None, ResultPresence::Value(value) => Some(value.as_str()) }, None, None),
    };
    let status_text = match final_status { LogicalExecutionStatus::Scheduled => "Scheduled", LogicalExecutionStatus::Pending => "Pending", LogicalExecutionStatus::Running => "Running", LogicalExecutionStatus::Success => "Success", LogicalExecutionStatus::Failed => "Failed", LogicalExecutionStatus::Timeout => "Timeout", LogicalExecutionStatus::Stuck => "Stuck", LogicalExecutionStatus::CompletedWithErrors => "CompletedWithErrors", LogicalExecutionStatus::Cancelling => "Cancelling", LogicalExecutionStatus::Cancelled => "Cancelled" };
    let metric_peak = metrics.map(|value| &value.peak_memory_bytes);
    let metric_rss = metrics.map(|value| &value.process_rss_bytes);
    let metric_user = metrics.map(|value| &value.cpu_user_seconds);
    let metric_system = metrics.map(|value| &value.cpu_system_seconds);
    let metric_cpu = metrics.map(|value| &value.cpu_total_seconds);
    let execution_query = sqlx::query("UPDATE public.executions SET status = $2::execution_status, result = CASE WHEN $3 THEN $4::jsonb ELSE result END, result_type = CASE WHEN $3 THEN $5::text ELSE result_type END, error_message = CASE WHEN $6 THEN $7::text ELSE error_message END, duration_ms = CASE WHEN $8 THEN $9::integer ELSE duration_ms END, completed_at = CASE WHEN $8 THEN timestamptz 'epoch' + $10::interval ELSE completed_at END, variables = CASE WHEN $11 THEN $12::jsonb ELSE variables END, execution_context = CASE WHEN $13 THEN (CASE WHEN $16::jsonb ? 'teams_action_completion' THEN (COALESCE($14::jsonb, '{}'::jsonb) || $15::jsonb) || jsonb_build_object('teams_action_completion', $16::jsonb -> 'teams_action_completion') ELSE COALESCE($14::jsonb, '{}'::jsonb) || $15::jsonb END) ELSE execution_context END, peak_memory_bytes = CASE WHEN $17 THEN $18::bigint ELSE peak_memory_bytes END, process_rss_bytes = CASE WHEN $19 THEN $20::bigint ELSE process_rss_bytes END, cpu_user_seconds = CASE WHEN $21 THEN $22::double precision ELSE cpu_user_seconds END, cpu_system_seconds = CASE WHEN $23 THEN $24::double precision ELSE cpu_system_seconds END, cpu_total_seconds = CASE WHEN $25 THEN $26::double precision ELSE cpu_total_seconds END, time_saved = CASE WHEN $27 THEN $28::integer ELSE time_saved END, value = CASE WHEN $29 THEN $30::numeric ELSE value END WHERE id = $1::uuid")
        .persistent(false).bind(input.execution_id.as_str()).bind(status_text)
        .bind(!cancelled && result.is_some()).bind(result.map(|value| value.text.as_str())).bind(result.map(|value| value.result_type))
        .bind(!cancelled && error.is_some()).bind(error).bind(duration.is_some()).bind(duration).bind(completed.as_deref())
        .bind(variables.is_some()).bind(variables.map(|value| value.text.as_str()))
        .bind(supplied_context.is_some()).bind(failure_existing.as_deref()).bind(supplied_context.map(|value| value.text.as_str())).bind(existing_context.as_deref())
        .bind(metric_peak.is_some_and(ResultPresence::supplied)).bind(metric_peak.and_then(ResultPresence::value).copied())
        .bind(metric_rss.is_some_and(ResultPresence::supplied)).bind(metric_rss.and_then(ResultPresence::value).copied())
        .bind(metric_user.is_some_and(ResultPresence::supplied)).bind(metric_user.and_then(ResultPresence::value).copied())
        .bind(metric_system.is_some_and(ResultPresence::supplied)).bind(metric_system.and_then(ResultPresence::value).copied())
        .bind(metric_cpu.is_some_and(ResultPresence::supplied)).bind(metric_cpu.and_then(ResultPresence::value).copied())
        .bind(!cancelled && time_saved.is_some()).bind(time_saved).bind(!cancelled && numeric.is_some()).bind(numeric.as_deref());
    if supplied_context.is_none() {
        if let Some(witness) = witness.as_deref_mut() {
            if witness.logical.is_some() {
                let upper = witness.mark();
                if let Some(value) = witness.logical.as_mut() { value.upper = upper; }
            }
        }
    }
    let execution_result = execution_query.execute(&mut **tx).await.map_err(|error| ResultSqlFailure::database(ResultSqlStage::WriteExecution, error))?;
    if let Some(witness) = witness.as_deref_mut() {
        if witness.logical.is_some() {
            let ack = witness.mark();
            if let Some(value) = witness.logical.as_mut() { value.write_ack = ack; }
        }
    }
    result_one(execution_result.rows_affected(), ResultSqlStage::WriteExecution)?;
    if let Some(witness) = witness.as_deref_mut() { witness.adapter_done = true; }
    Ok(SqlDecision::Applied(AppliedResult { plan, affected_execution_rows: execution_result.rows_affected(), affected_attempt_rows: attempt_result.rows_affected() }))
}


#[cfg(test)]
mod result_error_tests {
    use super::*;

    #[test]
    fn result_error_allowlist_exact() {
        for code in ["23514", "23505", "23503", "22003", "57014", "55P03"] {
            assert!(ResultSqlCode::from_code(Some(code)).map(ResultSqlCode::as_str) == Some(code));
        }
    }
    #[test]
    fn result_error_allowlist_closed() {
        for code in [Some("08P01"), Some("08p01"), Some("x22003"), Some("22003x"), Some(""), None] {
            assert!(ResultSqlCode::from_code(code).is_none());
        }
    }
    #[test]
    fn result_error_nondatabase_has_no_code() {
        let error = sqlx::Error::PoolTimedOut;
        assert!(result_sqlstate(&error).is_none());
        let failure = ResultSqlFailure::database(ResultSqlStage::WriteExecution, error);
        assert!(failure.stage() == ResultSqlStage::WriteExecution);
        assert!(failure.class() == ResultSqlFailureClass::Database && failure.code().is_none());
    }
    #[test]
    fn result_error_decode_mapping() {
        for error in [decode(), database(SqlStage::ReadAttempt)] {
            let failure = ResultSqlFailure::shared(error);
            assert!(failure.stage() == ResultSqlStage::Decode && failure.class() == ResultSqlFailureClass::InvalidRow && failure.code().is_none());
        }
    }
    #[test]
    fn result_error_clock_mapping() {
        let failure = ResultSqlFailure::shared(failure(SqlStage::Clock, SqlFailureClass::ClockRange));
        assert!(failure.stage() == ResultSqlStage::Clock && failure.class() == ResultSqlFailureClass::ClockRange && failure.code().is_none());
    }
    #[test]
    fn result_error_cardinality_mapping() {
        for count in [0, 2] {
            match result_one(count, ResultSqlStage::WriteExecution) {
                Err(error) => assert!(error.stage() == ResultSqlStage::WriteExecution && error.class() == ResultSqlFailureClass::Cardinality && error.code().is_none()),
                Ok(()) => panic!("synthetic cardinality was admitted"),
            }
        }
        assert!(result_one(1, ResultSqlStage::WriteExecution).is_ok());
    }
    #[test]
    fn checked_prepared_json_rejects_class_and_private_profile_drift() {
        assert!(PreparedJson::new("{}", "[]".to_owned(), ResultJsonRole::Result).is_err());
        assert!(PreparedJson::new("null", "null".to_owned(), ResultJsonRole::Result).is_err());
        assert!(PreparedJson::new("1.0", "1".to_owned(), ResultJsonRole::Result).is_err());
        assert!(PreparedJson::new("{\"a\":1}", "{\"a\":1,\"a\":2}".to_owned(), ResultJsonRole::Context).is_err());
        assert!(PreparedJson::new("{\"$serde_json::private::RawValue\":\"{\\\"a\\\":1}\"}", "{\"$serde_json::private::RawValue\":\"{\\\"a\\\":1}\"}".to_owned(), ResultJsonRole::Context).is_ok());
    }
    #[test]
    fn html_predicate_matches_python_whitespace_without_changing_storage() {
        let whitespace = [0x9,0xa,0xb,0xc,0xd,0x1c,0x1d,0x1e,0x1f,0x20,0x85,0xa0,0x1680,0x2000,0x2001,0x2002,0x2003,0x2004,0x2005,0x2006,0x2007,0x2008,0x2009,0x200a,0x2028,0x2029,0x202f,0x205f,0x3000];
        for scalar in whitespace {
            if let Some(character) = char::from_u32(scalar) { assert!(python_whitespace(character)); }
            else { panic!("synthetic whitespace scalar invalid"); }
        }
        for character in ['\u{200b}', '\u{feff}', '\u{180e}', '\u{2060}'] { assert!(!python_whitespace(character)); }
        match PreparedJson::new("\"  <tag>\"", "\"  <tag>\"".to_owned(), ResultJsonRole::Result) {
            Ok(value) => assert!(value.result_type == "html" && value.text == "\"  <tag>\""),
            Err(_) => panic!("synthetic HTML input rejected"),
        }
    }
}
