//! Internal common-session cancellation candidate; not a public API handler.
//!
//! Adapted from the accepted common owner prototype at 56d1fb006d220b9dbb0747f1cca4def18677f918,
//! spikes/go-native/owner-rs/src/lib.rs (AGPL-3.0-only). The original remains unchanged.
//! This candidate uses execution-first locks and checks retained and live fences.
//! It creates no schema, role, credential, process or event. Routing/admission must remain
//! disabled until actual-schema mixed-writer and public cancellation/event parity pass.

use bifrost_contracts::runtime::CanonicalUuid;
use sqlx::{PgPool, Postgres, Row, Transaction};
use thiserror::Error;

/// Trusted coordinator input. Never deserialize this from a runtime/public request.
/// Deliberately lacks Debug/Serialize because it includes a private claim token.
pub struct SessionFence {
    pub execution_id: CanonicalUuid,
    pub owner_incarnation_id: CanonicalUuid,
    pub attempt_id: CanonicalUuid,
    pub claim_token: CanonicalUuid,
    pub worker_incarnation_id: CanonicalUuid,
    pub session_id: CanonicalUuid,
    pub supervisor_incarnation_id: CanonicalUuid,
    pub runtime_incarnation_id: CanonicalUuid,
    pub binding_sha256: String,
    pub channel_custody_sha256: String,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum SessionError {
    #[error("invalid retained session fence")]
    InvalidFence,
    #[error("common session operation rejected")]
    Rejected,
    #[error("common session operation contended")]
    Contended,
    #[error("common session database unavailable")]
    Unavailable,
    #[error("common session commit outcome unknown; reconcile original binding")]
    UnknownCommit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CancelObservation {
    NotCommitted,
    Committed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CancelCommit {
    Committed,
    AlreadyCommitted,
}

fn database_error(error: sqlx::Error) -> SessionError {
    match error
        .as_database_error()
        .and_then(|value| value.code())
        .as_deref()
    {
        Some("55P03" | "40P01") => SessionError::Contended,
        _ => SessionError::Unavailable,
    }
}

fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

struct Snapshot {
    status: String,
    closed: bool,
    close_reason: Option<String>,
    grants: Vec<(bool, Option<String>)>,
    reports: Vec<(String, String)>,
}

impl Snapshot {
    fn observation(&self) -> Result<CancelObservation, SessionError> {
        if self.closed {
            if self.status != "Cancelling"
                || self.close_reason.as_deref() != Some("cancel_requested")
                || self.grants.iter().any(|(revoked, reason)| {
                    !revoked
                        || !matches!(
                            reason.as_deref(),
                            Some("session_closed" | "explicit_revoke" | "supervisor_replaced")
                        )
                })
                || self
                    .reports
                    .iter()
                    .any(|(disposition, winner)| disposition != "retained" || winner != "cancel")
            {
                return Err(SessionError::Rejected);
            }
            return Ok(CancelObservation::Committed);
        }
        if self.status != "Running"
            || self.close_reason.is_some()
            || !self.reports.is_empty()
            || self
                .grants
                .iter()
                .any(|(_, reason)| reason.as_deref() == Some("session_closed"))
        {
            return Err(SessionError::Rejected);
        }
        Ok(CancelObservation::NotCommitted)
    }
}

struct LockedSession<'a> {
    tx: Transaction<'a, Postgres>,
    snapshot: Snapshot,
}

// Neither pooled connection settings nor temporary objects may choose which
// authority/catalog/lifecycle objects are consulted. All application relations
// are explicitly public-qualified; only built-in types/operators/functions use
// this trusted path. SET LOCAL also works through transaction-pooling proxies.
async fn prepare_transaction(tx: &mut Transaction<'_, Postgres>) -> Result<(), SessionError> {
    for statement in [
        "SET LOCAL search_path=pg_catalog,pg_temp",
        "SET LOCAL statement_timeout='3s'",
        "SET LOCAL lock_timeout='1s'",
    ] {
        sqlx::query(statement)
            .persistent(false)
            .execute(&mut **tx)
            .await
            .map_err(database_error)?;
    }
    Ok(())
}

async fn lock_running_session<'a>(
    pool: &'a PgPool,
    fence: &SessionFence,
) -> Result<LockedSession<'a>, SessionError> {
    if !digest(&fence.binding_sha256) || !digest(&fence.channel_custody_sha256) {
        return Err(SessionError::InvalidFence);
    }
    let mut tx = pool.begin().await.map_err(database_error)?;
    prepare_transaction(&mut tx).await?;
    // The prototype login is intentionally explicit, not a caller-selectable role/GUC.
    // This creates/grants nothing and is not a production provisioning decision.
    let principal: bool = sqlx::query_scalar(
        "SELECT session_user = 'wex_core' AND current_user = session_user \
         AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcreaterole \
         AND NOT r.rolcreatedb AND NOT r.rolreplication \
         AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid) \
         AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname='public' AND n.nspowner=r.oid) \
         AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace \
                         WHERE n.nspname='public' AND c.relowner=r.oid) \
         AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace \
                         WHERE n.nspname='public' AND p.proowner=r.oid) \
         AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace \
                         WHERE n.nspname='public' AND t.typowner=r.oid) \
         FROM pg_catalog.pg_roles r WHERE r.rolname=session_user",
    ).persistent(false).fetch_one(&mut *tx).await.map_err(database_error)?;
    if !principal {
        return Err(SessionError::Rejected);
    }
    let source_lock: bool = sqlx::query_scalar(
        "SELECT pg_try_advisory_xact_lock_shared(hashtext('bifrost:workspace-release'))",
    )
    .persistent(false)
    .fetch_one(&mut *tx)
    .await
    .map_err(database_error)?;
    if !source_lock {
        return Err(SessionError::Contended);
    }
    let execution_lock: bool = sqlx::query_scalar(
        "SELECT pg_try_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || $1))",
    )
    .bind(fence.execution_id.as_str())
    .persistent(false)
    .fetch_one(&mut *tx)
    .await
    .map_err(database_error)?;
    if !execution_lock {
        return Err(SessionError::Contended);
    }
    // Do not require current workflow/user live links to survive cancellation.
    // Their immutable admitted identity remains in the common owner/session.
    let status: String = sqlx::query_scalar(
        "SELECT e.status::text FROM public.executions e JOIN public.runtime_execution_owners o \
         ON o.execution_id=e.id WHERE e.id=$1::text::uuid \
         AND o.owner_incarnation_id=$2::text::uuid FOR UPDATE OF e NOWAIT",
    )
    .bind(fence.execution_id.as_str())
    .bind(fence.owner_incarnation_id.as_str())
    .persistent(false)
    .fetch_optional(&mut *tx)
    .await
    .map_err(database_error)?
    .ok_or(SessionError::Rejected)?;
    let attempt = sqlx::query(
        "SELECT id FROM public.workflow_execution_attempts WHERE id=$1::text::uuid \
         AND execution_id=$2::text::uuid AND claim_token=$3::text::uuid \
         AND worker_incarnation_id=$4::text::uuid AND completed_at IS NULL \
         AND status='running' AND phase='execution' FOR UPDATE NOWAIT",
    )
    .bind(fence.attempt_id.as_str())
    .bind(fence.execution_id.as_str())
    .bind(fence.claim_token.as_str())
    .bind(fence.worker_incarnation_id.as_str())
    .persistent(false)
    .fetch_optional(&mut *tx)
    .await
    .map_err(database_error)?;
    if attempt.is_none() {
        return Err(SessionError::Rejected);
    }
    let owner = sqlx::query(
        "SELECT execution_id FROM public.runtime_execution_owners WHERE execution_id=$1::text::uuid \
         AND owner_incarnation_id=$2::text::uuid AND live_execution_id=execution_id \
         FOR UPDATE NOWAIT",
    )
    .bind(fence.execution_id.as_str())
    .bind(fence.owner_incarnation_id.as_str())
    .persistent(false)
    .fetch_optional(&mut *tx)
    .await
    .map_err(database_error)?;
    if owner.is_none() {
        return Err(SessionError::Rejected);
    }
    let session = sqlx::query(
        "SELECT closed_at IS NOT NULL AS closed,close_reason FROM public.runtime_sessions \
         WHERE id=$1::text::uuid AND execution_id=$2::text::uuid \
         AND owner_incarnation_id=$3::text::uuid AND workflow_attempt_id=$4::text::uuid \
         AND claim_token=$5::text::uuid AND worker_incarnation_id=$6::text::uuid \
         AND supervisor_incarnation_id=$7::text::uuid AND runtime_incarnation_id=$8::text::uuid \
         AND binding_sha256=$9 AND channel_custody_sha256=$10 FOR UPDATE NOWAIT",
    )
    .bind(fence.session_id.as_str())
    .bind(fence.execution_id.as_str())
    .bind(fence.owner_incarnation_id.as_str())
    .bind(fence.attempt_id.as_str())
    .bind(fence.claim_token.as_str())
    .bind(fence.worker_incarnation_id.as_str())
    .bind(fence.supervisor_incarnation_id.as_str())
    .bind(fence.runtime_incarnation_id.as_str())
    .bind(&fence.binding_sha256)
    .bind(&fence.channel_custody_sha256)
    .persistent(false)
    .fetch_optional(&mut *tx)
    .await
    .map_err(database_error)?
    .ok_or(SessionError::Rejected)?;
    // Historical fences survive native deletion. Require the separately retained
    // live binding too: recreating a native UUID/fence cannot revive an old owner.
    // This is C1 storage's internal contract, not an authorization to install it.
    let live_link = sqlx::query(
        "SELECT session_id FROM public.runtime_workflow_live_links \
         WHERE session_id=$1::text::uuid AND execution_id=$2::text::uuid \
         AND owner_incarnation_id=$3::text::uuid AND workflow_attempt_id=$4::text::uuid \
         AND native_attempt_id=$4::text::uuid AND claim_token=$5::text::uuid \
         AND worker_incarnation_id=$6::text::uuid FOR UPDATE NOWAIT",
    )
    .bind(fence.session_id.as_str())
    .bind(fence.execution_id.as_str())
    .bind(fence.owner_incarnation_id.as_str())
    .bind(fence.attempt_id.as_str())
    .bind(fence.claim_token.as_str())
    .bind(fence.worker_incarnation_id.as_str())
    .persistent(false)
    .fetch_optional(&mut *tx)
    .await
    .map_err(database_error)?;
    if live_link.is_none() {
        return Err(SessionError::Rejected);
    }
    let start = sqlx::query(
        "SELECT id FROM public.runtime_starts WHERE session_id=$1::text::uuid \
         AND execution_id=$2::text::uuid AND owner_incarnation_id=$3::text::uuid \
         AND workflow_attempt_id=$4::text::uuid ORDER BY id FOR UPDATE NOWAIT",
    )
    .bind(fence.session_id.as_str())
    .bind(fence.execution_id.as_str())
    .bind(fence.owner_incarnation_id.as_str())
    .bind(fence.attempt_id.as_str())
    .persistent(false)
    .fetch_all(&mut *tx)
    .await
    .map_err(database_error)?;
    if start.len() != 1 {
        return Err(SessionError::Rejected);
    }
    sqlx::query(
        "SELECT id FROM public.runtime_admissions WHERE session_id=$1::text::uuid \
         ORDER BY purpose,id FOR UPDATE NOWAIT",
    )
    .bind(fence.session_id.as_str())
    .persistent(false)
    .fetch_all(&mut *tx)
    .await
    .map_err(database_error)?;
    let grants = sqlx::query(
        "SELECT revoked_at IS NOT NULL AS revoked,revocation_reason \
         FROM public.workflow_runtime_sdk_grants WHERE runtime_session_id=$1::text::uuid \
         ORDER BY id FOR UPDATE NOWAIT",
    )
    .bind(fence.session_id.as_str())
    .persistent(false)
    .fetch_all(&mut *tx)
    .await
    .map_err(database_error)?;
    let reports = sqlx::query(
        "SELECT disposition,winner FROM public.runtime_report_receipts WHERE session_id=$1::text::uuid \
         ORDER BY result_message_id FOR UPDATE NOWAIT",
    )
    .bind(fence.session_id.as_str())
    .persistent(false)
    .fetch_all(&mut *tx)
    .await
    .map_err(database_error)?;
    let snapshot = Snapshot {
        status,
        closed: session.try_get("closed").map_err(database_error)?,
        close_reason: session.try_get("close_reason").map_err(database_error)?,
        grants: grants
            .iter()
            .map(|row| Ok((row.try_get("revoked")?, row.try_get("revocation_reason")?)))
            .collect::<Result<_, sqlx::Error>>()
            .map_err(database_error)?,
        reports: reports
            .iter()
            .map(|row| Ok((row.try_get("disposition")?, row.try_get("winner")?)))
            .collect::<Result<_, sqlx::Error>>()
            .map_err(database_error)?,
    };
    snapshot.observation()?;
    Ok(LockedSession { tx, snapshot })
}

/// Reconcile the original cancellation after an unknown commit. No writes,
/// retry, new Start, owner transfer, physical-stop or source-release assertion.
#[tracing::instrument(skip_all, fields(execution_id = fence.execution_id.as_str(), session_id = fence.session_id.as_str()))]
pub async fn observe_running_cancel(
    pool: &PgPool,
    fence: &SessionFence,
) -> Result<CancelObservation, SessionError> {
    let locked = lock_running_session(pool, fence).await?;
    let observation = locked.snapshot.observation()?;
    // Read-only reconciliation deliberately rolls back its locking transaction.
    locked.tx.rollback().await.map_err(database_error)?;
    Ok(observation)
}

/// Atomic internal cancellation; the trusted caller must separately authorize
/// the public request. Replays below are internal reconciliation, not a change
/// to Python's public BadRequest precedence on repeated cancellation.
/// No event is emitted here: exposing a route before event parity is prohibited.
#[tracing::instrument(skip_all, fields(execution_id = fence.execution_id.as_str(), session_id = fence.session_id.as_str()))]
pub async fn request_running_cancel(
    pool: &PgPool,
    fence: &SessionFence,
) -> Result<CancelCommit, SessionError> {
    let mut locked = lock_running_session(pool, fence).await?;
    if locked.snapshot.observation()? == CancelObservation::Committed {
        locked.tx.rollback().await.map_err(database_error)?;
        return Ok(CancelCommit::AlreadyCommitted);
    }
    let updated = sqlx::query(
        "UPDATE public.executions SET status='Cancelling' WHERE id=$1::text::uuid AND status='Running'",
    )
    .bind(fence.execution_id.as_str())
    .persistent(false)
    .execute(&mut *locked.tx)
    .await
    .map_err(database_error)?;
    if updated.rows_affected() != 1 {
        return Err(SessionError::Rejected);
    }
    let closed = sqlx::query(
        "UPDATE public.runtime_sessions SET closed_at=clock_timestamp(),close_reason='cancel_requested' \
         WHERE id=$1::text::uuid AND closed_at IS NULL",
    )
    .bind(fence.session_id.as_str())
    .persistent(false)
    .execute(&mut *locked.tx)
    .await
    .map_err(database_error)?;
    if closed.rows_affected() != 1 {
        return Err(SessionError::Rejected);
    }
    sqlx::query(
        "UPDATE public.workflow_runtime_sdk_grants SET revoked_at=clock_timestamp(), \
         revocation_reason='session_closed' WHERE runtime_session_id=$1::text::uuid \
         AND revoked_at IS NULL",
    )
    .bind(fence.session_id.as_str())
    .persistent(false)
    .execute(&mut *locked.tx)
    .await
    .map_err(database_error)?;
    locked
        .tx
        .commit()
        .await
        .map_err(|_| SessionError::UnknownCommit)?;
    Ok(CancelCommit::Committed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn running() -> Snapshot {
        Snapshot {
            status: "Running".into(),
            closed: false,
            close_reason: None,
            grants: vec![],
            reports: vec![],
        }
    }

    // Uses only the ordinary isolated Alembic-migrated test database. No common
    // schema, runtime role, credential or lifecycle admission is installed.
    #[cfg(feature = "live-db")]
    #[tokio::test]
    async fn transaction_resists_temp_shadows() -> Result<(), Box<dyn std::error::Error>> {
        let url = std::env::var("BIFROST_RUST_TEST_DATABASE_URL")
            .map_err(|_| "live-db requires BIFROST_RUST_TEST_DATABASE_URL for an isolated Alembic-migrated database")?;
        let config = crate::DatabaseConfig::new(&url, 1, std::time::Duration::from_secs(2))?;
        let database = crate::Database::new(&config)?;
        let mut tx = database.pool().begin().await?;
        for statement in [
            "SET LOCAL search_path=pg_temp,public,pg_catalog",
            "CREATE TEMP TABLE pg_roles (shadow boolean) ON COMMIT DROP",
            "CREATE TEMP TABLE executions (shadow boolean) ON COMMIT DROP",
        ] {
            sqlx::query(statement)
                .persistent(false)
                .execute(&mut *tx)
                .await?;
        }
        // Establish that the hostile path actually resolves both shadow tables.
        // A test that never reproduces masking cannot establish this boundary.
        let shadowed: bool = sqlx::query_scalar(
            "SELECT pg_catalog.to_regclass('pg_roles') = pg_catalog.to_regclass('pg_temp.pg_roles') \
             AND pg_catalog.to_regclass('executions') = pg_catalog.to_regclass('pg_temp.executions')",
        )
        .persistent(false)
        .fetch_one(&mut *tx)
        .await?;
        assert!(
            shadowed,
            "temporary shadow setup did not mask intended relations"
        );
        prepare_transaction(&mut tx).await?;
        let trusted: bool = sqlx::query_scalar(
            "SELECT pg_catalog.to_regclass('pg_roles') = pg_catalog.to_regclass('pg_catalog.pg_roles') \
             AND pg_catalog.to_regclass('public.executions') IS NOT NULL \
             AND pg_catalog.to_regclass('public.executions') <> pg_catalog.to_regclass('pg_temp.executions')",
        )
        .persistent(false)
        .fetch_one(&mut *tx)
        .await?;
        assert!(
            trusted,
            "transaction did not retain trusted relation resolution"
        );
        tx.rollback().await?;
        database.close().await;
        Ok(())
    }

    #[test]
    fn cancellation_observation_requires_all_committed_projections() {
        let mut state = running();
        assert_eq!(state.observation(), Ok(CancelObservation::NotCommitted));
        state.grants.push((false, None));
        assert_eq!(state.observation(), Ok(CancelObservation::NotCommitted));
        state.closed = true;
        state.close_reason = Some("cancel_requested".into());
        assert_eq!(state.observation(), Err(SessionError::Rejected));
        state.status = "Cancelling".into();
        assert_eq!(state.observation(), Err(SessionError::Rejected));
        state.grants = vec![(true, Some("session_closed".into()))];
        assert_eq!(state.observation(), Ok(CancelObservation::Committed));
        state.reports.push(("retained".into(), "cancel".into()));
        assert_eq!(state.observation(), Ok(CancelObservation::Committed));
        state.reports[0] = ("accepted".into(), "result".into());
        assert_eq!(state.observation(), Err(SessionError::Rejected));
    }

    #[test]
    fn absent_grant_does_not_prevent_durable_cancellation() {
        let mut state = running();
        state.closed = true;
        state.close_reason = Some("cancel_requested".into());
        state.status = "Cancelling".into();
        assert_eq!(state.observation(), Ok(CancelObservation::Committed));
    }

    #[test]
    fn stale_or_partial_state_is_never_a_cancel_receipt() {
        for status in [
            "Pending",
            "Scheduled",
            "Cancelling",
            "Completed",
            "Failed",
            "Cancelled",
        ] {
            let mut state = running();
            state.status = status.into();
            assert_eq!(state.observation(), Err(SessionError::Rejected));
        }
        let mut state = running();
        state.grants.push((true, Some("session_closed".into())));
        assert_eq!(state.observation(), Err(SessionError::Rejected));
        let mut state = running();
        state.reports.push(("retained".into(), "cancel".into()));
        assert_eq!(state.observation(), Err(SessionError::Rejected));
        let mut state = running();
        state.close_reason = Some("cancel_requested".into());
        assert_eq!(state.observation(), Err(SessionError::Rejected));
    }

    #[test]
    fn unrelated_revocation_does_not_fabricate_cancellation() {
        let mut state = running();
        state.grants.push((true, Some("explicit_revoke".into())));
        assert_eq!(state.observation(), Ok(CancelObservation::NotCommitted));
        state.closed = true;
        state.close_reason = Some("cancel_requested".into());
        state.status = "Cancelling".into();
        assert_eq!(state.observation(), Ok(CancelObservation::Committed));
        state.grants[0] = (false, Some("explicit_revoke".into()));
        assert_eq!(state.observation(), Err(SessionError::Rejected));
    }

    #[test]
    fn fence_digests_reject_noncanonical_or_injected_values() {
        assert!(digest(&"a".repeat(64)));
        for value in [
            "a".repeat(63),
            "a".repeat(65),
            "A".repeat(64),
            format!("{}'", "a".repeat(63)),
        ] {
            assert!(!digest(&value));
        }
    }
}
