//! Internal workflow Start persistence candidate. No route, launch or wire adapter.
//!
//! Adapted from the common owner prototype at 56d1fb006d220b9dbb0747f1cca4def18677f918,
//! spikes/go-native/owner-rs/src/start.rs (AGPL-3.0-only). That source is unchanged.
//! This version uses execution-first fencing, detachable live links and the common
//! writer guard's native-update-before-Start order. SQL activation remains held.
//! Deliberately excluded from default/production builds. The candidate feature is
//! only a source-qualification gate, never permission to call this from a runtime.

use bifrost_contracts::runtime::{CanonicalUuid, MAX_FRAME_BYTES, decode_ordinary_json};
use sqlx::{PgPool, Postgres, Row, Transaction};

use crate::common_session::{
    SessionError, SessionFence, begin_fenced_core_transaction, database_error, digest,
};

/// Trusted coordinator intent, never deserialize a runtime/public request into this.
/// The caller must first authenticate live channel custody, validate the complete
/// Prepare/Prepared exchange, source/artifact/schema eligibility and deadline policy.
/// This transaction checks retained identity; it cannot prove those external facts.
/// Deliberately no Debug/Serialize: inputs/context may contain customer data.
pub struct PreparedStartIntent {
    pub committed_start_id: CanonicalUuid,
    pub start_message_id: CanonicalUuid,
    pub prepare_message_id: CanonicalUuid,
    pub prepare_sha256: String,
    pub artifact_id: String,
    pub input_json: String,
    pub context_json: String,
    /// Already validated absolute deadline; None retains native no-timeout semantics.
    pub deadline_utc: Option<String>,
}

impl PreparedStartIntent {
    fn validate(&self) -> Result<(), SessionError> {
        // These are defensive persistence bounds, not a substitute wire validator.
        if !digest(&self.prepare_sha256)
            || !self.artifact_id.strip_prefix("sha256:").is_some_and(digest)
            || self.start_message_id == self.prepare_message_id
            || self.input_json.is_empty()
            || self.context_json.is_empty()
            || self.input_json.len().saturating_add(self.context_json.len()) > MAX_FRAME_BYTES
            || self.deadline_utc.as_ref().is_some_and(|v| v.is_empty() || v.len() > 64)
        {
            return Err(SessionError::InvalidFence);
        }
        let input = decode_ordinary_json(self.input_json.as_bytes())
            .map_err(|_| SessionError::InvalidFence)?;
        let context = decode_ordinary_json(self.context_json.as_bytes())
            .map_err(|_| SessionError::InvalidFence)?;
        if !input.is_object() || !context.is_object() {
            return Err(SessionError::InvalidFence);
        }
        Ok(())
    }
}

/// Observed commit only. Not a channel-admission, delivery, process-spawn or SDK permit.
#[derive(Debug, PartialEq, Eq)]
pub struct CommittedWorkflowStart {
    pub committed_start_id: CanonicalUuid,
    pub start_message_id: CanonicalUuid,
    pub started_at_utc: String,
    /// Milliseconds are explicit; a wire adapter must not silently round to seconds.
    pub remaining_run_ms: Option<i64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StartObservation {
    /// Absence never authorizes replay, replacement, new ownership or physical spawn.
    NotRetained,
    Retained,
}

struct RetainedStart {
    id: String,
    message: String,
    consistent_payload_and_clock: Option<bool>,
    execution_status: String,
    attempt_status: String,
}

impl RetainedStart {
    fn observation(&self, intent: &PreparedStartIntent) -> Result<StartObservation, SessionError> {
        if self.id != intent.committed_start_id.as_str()
            || self.message != intent.start_message_id.as_str()
            || self.consistent_payload_and_clock != Some(true)
            || self.execution_status == "Pending"
            || self.attempt_status == "claimed"
        {
            return Err(SessionError::Rejected);
        }
        Ok(StartObservation::Retained)
    }
}

struct LockedPreparedSession<'a> {
    tx: Transaction<'a, Postgres>,
}

async fn lock_prepared_session<'a>(
    pool: &'a PgPool,
    fence: &SessionFence,
    intent: &PreparedStartIntent,
    observing: bool,
) -> Result<LockedPreparedSession<'a>, SessionError> {
    intent.validate()?;
    let mut tx = begin_fenced_core_transaction(pool, fence).await?;
    sqlx::query("SET LOCAL TIME ZONE 'UTC'")
        .persistent(false).execute(&mut *tx).await.map_err(database_error)?;
    // The common writer guard requires READ COMMITTED, including fresh trigger reads.
    let isolation: String = sqlx::query_scalar("SHOW transaction_isolation")
        .persistent(false).fetch_one(&mut *tx).await.map_err(database_error)?;
    if isolation != "read committed" {
        return Err(SessionError::Rejected);
    }
    let execution = sqlx::query(
        "SELECT e.id FROM public.executions e JOIN public.runtime_execution_owners o \
         ON o.execution_id=e.id AND o.live_execution_id=e.id \
         WHERE e.id=$1::text::uuid AND o.owner_incarnation_id=$2::text::uuid \
         AND e.control_owner='rust' AND e.runtime_mode='deployment-v1' \
         AND e.workflow_id=o.workflow_id AND e.solution_deployment_id=o.deployment_id \
         AND e.parameters=$3::text::jsonb AND ($4 OR e.status::text='Pending') \
         FOR UPDATE OF e NOWAIT",
    ).bind(fence.execution_id.as_str()).bind(fence.owner_incarnation_id.as_str())
        .bind(&intent.input_json).bind(observing).persistent(false)
        .fetch_optional(&mut *tx).await.map_err(database_error)?;
    if execution.is_none() { return Err(SessionError::Rejected); }
    let attempt = sqlx::query(
        "SELECT id FROM public.workflow_execution_attempts WHERE id=$1::text::uuid \
         AND execution_id=$2::text::uuid AND claim_token=$3::text::uuid \
         AND worker_incarnation_id=$4::text::uuid \
         AND ($5 OR (status='claimed' AND phase='claim' AND started_at IS NULL AND completed_at IS NULL)) \
         FOR UPDATE NOWAIT",
    ).bind(fence.attempt_id.as_str()).bind(fence.execution_id.as_str())
        .bind(fence.claim_token.as_str()).bind(fence.worker_incarnation_id.as_str())
        .bind(observing).persistent(false).fetch_optional(&mut *tx).await.map_err(database_error)?;
    if attempt.is_none() { return Err(SessionError::Rejected); }
    let owner = sqlx::query(
        "SELECT execution_id FROM public.runtime_execution_owners WHERE execution_id=$1::text::uuid \
         AND owner_incarnation_id=$2::text::uuid AND live_execution_id=execution_id \
         AND artifact_id=$3 FOR UPDATE NOWAIT",
    ).bind(fence.execution_id.as_str()).bind(fence.owner_incarnation_id.as_str())
        .bind(&intent.artifact_id).persistent(false)
        .fetch_optional(&mut *tx).await.map_err(database_error)?;
    if owner.is_none() { return Err(SessionError::Rejected); }
    // Core has no user/source-table reader. Immutable accepted artifact identity
    // and its still-attached native FKs are checked under its NOWAIT row lock.
    // Admission's source/manifest/schema review remains a caller prerequisite.
    let artifact = sqlx::query(
        "SELECT a.deployment_id FROM public.runtime_deployment_artifacts a \
         JOIN public.runtime_execution_owners o ON o.deployment_id=a.deployment_id \
          AND o.workflow_id=a.workflow_id AND o.artifact_id=a.artifact_id \
         WHERE o.execution_id=$1::text::uuid AND o.owner_incarnation_id=$2::text::uuid \
          AND a.live_deployment_id=a.deployment_id AND a.live_workflow_id=a.workflow_id \
          AND a.live_solution_id=a.solution_id AND a.live_reviewed_by=a.reviewed_by \
         FOR UPDATE OF a NOWAIT",
    ).bind(fence.execution_id.as_str()).bind(fence.owner_incarnation_id.as_str())
        .persistent(false).fetch_optional(&mut *tx).await.map_err(database_error)?;
    if artifact.is_none() { return Err(SessionError::Rejected); }
    let session = sqlx::query(
        "SELECT id FROM public.runtime_sessions WHERE id=$1::text::uuid \
         AND execution_id=$2::text::uuid AND owner_incarnation_id=$3::text::uuid \
         AND workflow_attempt_id=$4::text::uuid AND claim_token=$5::text::uuid \
         AND worker_incarnation_id=$6::text::uuid AND supervisor_incarnation_id=$7::text::uuid \
         AND runtime_incarnation_id=$8::text::uuid AND binding_sha256=$9 AND channel_custody_sha256=$10 \
         AND prepare_id=$11::text::uuid AND prepare_sha256=$12 \
         AND ($13 OR (closed_at IS NULL AND close_reason IS NULL)) FOR UPDATE NOWAIT",
    ).bind(fence.session_id.as_str()).bind(fence.execution_id.as_str())
        .bind(fence.owner_incarnation_id.as_str()).bind(fence.attempt_id.as_str())
        .bind(fence.claim_token.as_str()).bind(fence.worker_incarnation_id.as_str())
        .bind(fence.supervisor_incarnation_id.as_str()).bind(fence.runtime_incarnation_id.as_str())
        .bind(&fence.binding_sha256).bind(&fence.channel_custody_sha256)
        .bind(intent.prepare_message_id.as_str()).bind(&intent.prepare_sha256).bind(observing)
        .persistent(false).fetch_optional(&mut *tx).await.map_err(database_error)?;
    if session.is_none() { return Err(SessionError::Rejected); }
    let live = sqlx::query(
        "SELECT session_id FROM public.runtime_workflow_live_links \
         WHERE session_id=$1::text::uuid AND execution_id=$2::text::uuid \
          AND owner_incarnation_id=$3::text::uuid AND workflow_attempt_id=$4::text::uuid \
          AND native_attempt_id=$4::text::uuid AND claim_token=$5::text::uuid \
          AND worker_incarnation_id=$6::text::uuid FOR UPDATE NOWAIT",
    ).bind(fence.session_id.as_str()).bind(fence.execution_id.as_str())
        .bind(fence.owner_incarnation_id.as_str()).bind(fence.attempt_id.as_str())
        .bind(fence.claim_token.as_str()).bind(fence.worker_incarnation_id.as_str())
        .persistent(false).fetch_optional(&mut *tx).await.map_err(database_error)?;
    if live.is_none() { return Err(SessionError::Rejected); }
    Ok(LockedPreparedSession { tx })
}

async fn lock_later_rows(
    tx: &mut Transaction<'_, Postgres>,
    fence: &SessionFence,
    require_empty: bool,
) -> Result<(), SessionError> {
    for query in [
        "SELECT id FROM public.runtime_admissions WHERE session_id=$1::text::uuid ORDER BY purpose,id FOR UPDATE NOWAIT",
        "SELECT id FROM public.workflow_runtime_sdk_grants WHERE runtime_session_id=$1::text::uuid ORDER BY id FOR UPDATE NOWAIT",
        "SELECT result_message_id FROM public.runtime_report_receipts WHERE session_id=$1::text::uuid ORDER BY result_message_id FOR UPDATE NOWAIT",
    ] {
        let rows = sqlx::query(query).bind(fence.session_id.as_str()).persistent(false)
            .fetch_all(&mut **tx).await.map_err(database_error)?;
        if require_empty && !rows.is_empty() { return Err(SessionError::Rejected); }
    }
    Ok(())
}

/// Atomically commit native Running/execution and immutable Start, with one clock.
/// The outer coordinator must separately authorize admission and authenticate the
/// prepared live runtime. No Start wire message is generated or sent here.
/// On UnknownCommit, retain every original ID/binding and reconcile; do not retry.
#[tracing::instrument(skip_all, fields(execution_id=fence.execution_id.as_str(), session_id=fence.session_id.as_str()))]
pub async fn commit_prepared_workflow_start(
    pool: &PgPool,
    fence: &SessionFence,
    intent: &PreparedStartIntent,
) -> Result<CommittedWorkflowStart, SessionError> {
    let mut locked = lock_prepared_session(pool, fence, intent, false).await?;
    let starts = sqlx::query(
        "SELECT id FROM public.runtime_starts WHERE session_id=$1::text::uuid ORDER BY id FOR UPDATE NOWAIT",
    ).bind(fence.session_id.as_str()).persistent(false)
        .fetch_all(&mut *locked.tx).await.map_err(database_error)?;
    if !starts.is_empty() { return Err(SessionError::Rejected); }
    lock_later_rows(&mut locked.tx, fence, true).await?;
    let clock = sqlx::query(
        "SELECT now::text AS started_at, \
         CASE WHEN $1::text IS NULL THEN NULL ELSE floor(extract(epoch FROM ($1::text::timestamptz-now))*1000)::bigint END AS remaining_ms \
         FROM (SELECT clock_timestamp() AS now) clock",
    ).bind(intent.deadline_utc.as_deref()).persistent(false)
        .fetch_one(&mut *locked.tx).await.map_err(database_error)?;
    let started_at: String = clock.try_get("started_at").map_err(database_error)?;
    let remaining_run_ms: Option<i64> = clock.try_get("remaining_ms").map_err(database_error)?;
    if remaining_run_ms.is_some_and(|ms| !(1..=9_007_199_254_740_991).contains(&ms)) {
        return Err(SessionError::Rejected);
    }
    let root = sqlx::query(
        "UPDATE public.executions SET status='Running',started_at=$2::text::timestamptz \
         WHERE id=$1::text::uuid AND control_owner='rust' AND status::text='Pending'",
    ).bind(fence.execution_id.as_str()).bind(&started_at).persistent(false)
        .execute(&mut *locked.tx).await.map_err(database_error)?;
    if root.rows_affected()!=1 { return Err(SessionError::Rejected); }
    let attempt = sqlx::query(
        "UPDATE public.workflow_execution_attempts SET status='running',phase='execution', \
          started_at=$2::text::timestamptz,heartbeat_at=$2::text::timestamptz \
         WHERE id=$1::text::uuid AND execution_id=$3::text::uuid AND claim_token=$4::text::uuid \
          AND worker_incarnation_id=$5::text::uuid AND status='claimed' AND phase='claim' \
          AND started_at IS NULL AND completed_at IS NULL",
    ).bind(fence.attempt_id.as_str()).bind(&started_at).bind(fence.execution_id.as_str())
        .bind(fence.claim_token.as_str()).bind(fence.worker_incarnation_id.as_str())
        .persistent(false).execute(&mut *locked.tx).await.map_err(database_error)?;
    if attempt.rows_affected()!=1 { return Err(SessionError::Rejected); }
    // Native Running and exact attempt clock precede this guarded INSERT.
    let start = sqlx::query(
        "INSERT INTO public.runtime_starts \
         (id,session_id,execution_id,owner_incarnation_id,workflow_attempt_id,start_message_id, \
          input_sha256,context_sha256,started_at,deadline_utc) \
         VALUES ($1::text::uuid,$2::text::uuid,$3::text::uuid,$4::text::uuid,$5::text::uuid,$6::text::uuid, \
          encode(sha256(convert_to($7,'UTF8')),'hex'),encode(sha256(convert_to($8,'UTF8')),'hex'), \
          $9::text::timestamptz,$10::text::timestamptz)",
    ).bind(intent.committed_start_id.as_str()).bind(fence.session_id.as_str())
        .bind(fence.execution_id.as_str()).bind(fence.owner_incarnation_id.as_str())
        .bind(fence.attempt_id.as_str()).bind(intent.start_message_id.as_str())
        .bind(&intent.input_json).bind(&intent.context_json).bind(&started_at)
        .bind(intent.deadline_utc.as_deref()).persistent(false)
        .execute(&mut *locked.tx).await.map_err(database_error)?;
    if start.rows_affected()!=1 { return Err(SessionError::Rejected); }
    locked.tx.commit().await.map_err(|_| SessionError::UnknownCommit)?;
    Ok(CommittedWorkflowStart {
        committed_start_id: intent.committed_start_id.clone(),
        start_message_id: intent.start_message_id.clone(),
        started_at_utc: started_at,
        remaining_run_ms,
    })
}

/// Read the original uncertain decision, never grant replay or send Start.
/// Missing/detached live parents fail closed; historical-only reconciliation needs
/// a separately reviewed recovery reader, not weaker mutation eligibility here.
#[tracing::instrument(skip_all, fields(execution_id=fence.execution_id.as_str(), session_id=fence.session_id.as_str()))]
pub async fn observe_prepared_workflow_start(
    pool: &PgPool,
    fence: &SessionFence,
    intent: &PreparedStartIntent,
) -> Result<StartObservation, SessionError> {
    let mut locked=lock_prepared_session(pool,fence,intent,true).await?;
    let start = sqlx::query(
        "SELECT s.id::text AS id,s.start_message_id::text AS message, \
          e.status::text AS execution_status,a.status AS attempt_status, \
          s.input_sha256=encode(sha256(convert_to($2,'UTF8')),'hex') \
          AND s.context_sha256=encode(sha256(convert_to($3,'UTF8')),'hex') \
          AND s.deadline_utc IS NOT DISTINCT FROM $4::text::timestamptz \
          AND e.started_at=s.started_at AND a.started_at=s.started_at AS matches \
         FROM public.runtime_starts s JOIN public.executions e ON e.id=s.execution_id \
          JOIN public.workflow_execution_attempts a ON a.id=s.workflow_attempt_id \
         WHERE s.session_id=$1::text::uuid ORDER BY s.id FOR UPDATE OF s NOWAIT",
    ).bind(fence.session_id.as_str()).bind(&intent.input_json).bind(&intent.context_json)
        .bind(intent.deadline_utc.as_deref()).persistent(false)
        .fetch_optional(&mut *locked.tx).await.map_err(database_error)?;
    lock_later_rows(&mut locked.tx,fence,start.is_none()).await?;
    let observation = match start {
        Some(row) => RetainedStart {
            id: row.try_get("id").map_err(database_error)?,
            message: row.try_get("message").map_err(database_error)?,
            consistent_payload_and_clock: row.try_get("matches").map_err(database_error)?,
            execution_status: row.try_get("execution_status").map_err(database_error)?,
            attempt_status: row.try_get("attempt_status").map_err(database_error)?,
        }.observation(intent)?,
        None => {
            // Absence must be internally consistent; it is still not retry authority.
            let untouched:bool=sqlx::query_scalar(
                "SELECT e.status::text='Pending' AND a.status='claimed' AND a.phase='claim' \
                 AND a.started_at IS NULL AND a.completed_at IS NULL AND s.closed_at IS NULL \
                 FROM public.executions e JOIN public.workflow_execution_attempts a ON a.execution_id=e.id \
                  JOIN public.runtime_sessions s ON s.workflow_attempt_id=a.id \
                 WHERE e.id=$1::text::uuid AND a.id=$2::text::uuid AND s.id=$3::text::uuid",
            ).bind(fence.execution_id.as_str()).bind(fence.attempt_id.as_str()).bind(fence.session_id.as_str())
                .persistent(false).fetch_one(&mut *locked.tx).await.map_err(database_error)?;
            if !untouched { return Err(SessionError::Rejected); }
            StartObservation::NotRetained
        }
    };
    locked.tx.rollback().await.map_err(database_error)?;
    Ok(observation)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn intent() -> Result<PreparedStartIntent, bifrost_contracts::runtime::Error> {
        Ok(PreparedStartIntent {
            committed_start_id: CanonicalUuid::new("00000000-0000-0000-0000-000000000001")?,
            start_message_id: CanonicalUuid::new("00000000-0000-0000-0000-000000000002")?,
            prepare_message_id: CanonicalUuid::new("00000000-0000-0000-0000-000000000003")?,
            prepare_sha256: "a".repeat(64),
            artifact_id: format!("sha256:{}", "b".repeat(64)),
            input_json: "{}".into(),
            context_json: "{}".into(),
            deadline_utc: None,
        })
    }

    #[test]
    fn retained_start_rejects_pre_start_projection_but_survives_terminal_or_cancel() -> Result<(), Box<dyn std::error::Error>> {
        let intent = intent()?;
        for (root, attempt, expected) in [
            ("Running", "running", true),
            ("Cancelling", "running", true),
            ("Success", "success", true),
            ("Failed", "failed", true),
            ("Pending", "running", false),
            ("Running", "claimed", false),
            ("Pending", "claimed", false),
        ] {
            let mut row = RetainedStart {
                id: intent.committed_start_id.as_str().into(),
                message: intent.start_message_id.as_str().into(),
                consistent_payload_and_clock: Some(true),
                execution_status: root.into(),
                attempt_status: attempt.into(),
            };
            assert_eq!(row.observation(&intent).is_ok(), expected);
            for mismatch in [None, Some(false)] {
                row.consistent_payload_and_clock = mismatch;
                assert_eq!(row.observation(&intent), Err(SessionError::Rejected));
            }
        }
        let mut row = RetainedStart {
            id: intent.committed_start_id.as_str().into(),
            message: intent.start_message_id.as_str().into(),
            consistent_payload_and_clock: Some(true),
            execution_status: "Running".into(),
            attempt_status: "running".into(),
        };
        row.message = intent.prepare_message_id.as_str().into();
        assert_eq!(row.observation(&intent), Err(SessionError::Rejected));
        row.message = intent.start_message_id.as_str().into();
        row.id = intent.prepare_message_id.as_str().into();
        assert_eq!(row.observation(&intent), Err(SessionError::Rejected));
        Ok(())
    }

    #[test]
    fn persistence_seam_rejects_ambiguous_or_malformed_json() -> Result<(), Box<dyn std::error::Error>> {
        let mut value = intent()?;
        value.validate()?;
        for malformed in ["", "not JSON", "[]", "null", "{\"a\":1,\"a\":2}"] {
            value.context_json = malformed.into();
            assert_eq!(value.validate(), Err(SessionError::InvalidFence));
            value.context_json = "{}".into();
            value.input_json = malformed.into();
            assert_eq!(value.validate(), Err(SessionError::InvalidFence));
            value.input_json = "{}".into();
        }
        value.start_message_id = value.prepare_message_id.clone();
        assert_eq!(value.validate(), Err(SessionError::InvalidFence));
        Ok(())
    }
}
