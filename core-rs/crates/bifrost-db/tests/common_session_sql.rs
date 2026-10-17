//! Opt-in persistence test for the held common schema/principal candidate.
//!
//! This consumes one committed synthetic Running fixture. It installs no DDL,
//! creates no roles/grants/credentials, launches nothing and never terminalizes
//! or retires that fixture. Its caller owns disposable-database teardown.
//! Run only in the approved supported isolated SQL lane, separately for direct
//! PostgreSQL and PgBouncer with distinct fresh fixtures. Ordinary `live-db`
//! remains the current-main schema/bootstrap lane and cannot run this test.
#![cfg(feature = "common-session-sql")]

use std::{path::PathBuf, time::Duration};

use bifrost_contracts::runtime::CanonicalUuid;
use bifrost_db::{
    Database, DatabaseConfig,
    common_session::{
        CancelCommit, CancelObservation, SessionError, SessionFence, observe_running_cancel,
        request_running_cancel,
    },
};
use serde::Deserialize;
use sqlx::{PgPool, Row};

type TestResult<T> = Result<T, Box<dyn std::error::Error>>;

// This local test input is not a public/runtime deserializer for SessionFence.
// It contains a private claim: never Debug/print it or serde input errors.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Fixture {
    schema: String,
    execution_id: CanonicalUuid,
    incumbent_execution_id: CanonicalUuid,
    owner_incarnation_id: CanonicalUuid,
    attempt_id: CanonicalUuid,
    claim_token: CanonicalUuid,
    worker_incarnation_id: CanonicalUuid,
    session_id: CanonicalUuid,
    supervisor_incarnation_id: CanonicalUuid,
    runtime_incarnation_id: CanonicalUuid,
    binding_sha256: String,
    channel_custody_sha256: String,
}

impl Fixture {
    fn read() -> TestResult<(SessionFence, CanonicalUuid)> {
        let path = std::env::var_os("BIFROST_COMMON_CANCEL_TEST_FIXTURE")
            .map(PathBuf::from)
            .ok_or("common SQL test requires its fresh private fixture file")?;
        let metadata = std::fs::metadata(&path)
            .map_err(|_| "common SQL test fixture is unavailable")?;
        if !metadata.is_file() || metadata.len() > 8192 {
            return Err("common SQL test fixture must be a bounded regular file".into());
        }
        let bytes = std::fs::read(path).map_err(|_| "common SQL test fixture read failed")?;
        let fixture: Self = serde_json::from_slice(&bytes)
            .map_err(|_| "common SQL test fixture is invalid")?;
        if fixture.schema != "bifrost.common-cancel-fixture/v1" {
            return Err("common SQL test fixture version differs".into());
        }
        let fence = SessionFence {
            execution_id: fixture.execution_id,
            owner_incarnation_id: fixture.owner_incarnation_id,
            attempt_id: fixture.attempt_id,
            claim_token: fixture.claim_token,
            worker_incarnation_id: fixture.worker_incarnation_id,
            session_id: fixture.session_id,
            supervisor_incarnation_id: fixture.supervisor_incarnation_id,
            runtime_incarnation_id: fixture.runtime_incarnation_id,
            binding_sha256: fixture.binding_sha256,
            channel_custody_sha256: fixture.channel_custody_sha256,
        };
        Ok((fence, fixture.incumbent_execution_id))
    }
}

#[derive(Debug, PartialEq, Eq)]
struct State {
    status: String,
    attempt_status: String,
    attempt_phase: String,
    attempt_complete: bool,
    closed: bool,
    close_reason: Option<String>,
    starts: i64,
    grants: i64,
    admissions: i64,
    reports: i64,
    resources: i64,
}

async fn database(variable: &str, principal: &str) -> TestResult<(DatabaseConfig, Database)> {
    let url = std::env::var(variable).map_err(|_| "common SQL test DSN is required")?;
    let config = DatabaseConfig::new(&url, 2, Duration::from_secs(2))?;
    let database = Database::new(&config)?;
    let expected: bool = sqlx::query_scalar(
        "SELECT current_database()='bifrost_test' AND session_user=$1 \
         AND current_user=session_user AND NOT r.rolsuper AND NOT r.rolbypassrls \
         AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND NOT r.rolreplication \
         FROM pg_catalog.pg_roles r WHERE r.rolname=session_user",
    )
    .bind(principal)
    .persistent(false)
    .fetch_one(database.pool())
    .await
    .map_err(|_| "common SQL test cannot verify its actual restricted login")?;
    if !expected {
        database.close().await;
        return Err("common SQL test requires its actual login on bifrost_test".into());
    }
    Ok((config, database))
}

async fn state(pool: &PgPool, fence: &SessionFence) -> TestResult<State> {
    let row = sqlx::query(
        "SELECT e.status::text AS status,a.status AS attempt_status,a.phase AS attempt_phase, \
         a.completed_at IS NOT NULL AS attempt_complete,s.closed_at IS NOT NULL AS closed,s.close_reason, \
         (SELECT count(*) FROM public.runtime_starts WHERE session_id=s.id) AS starts, \
         (SELECT count(*) FROM public.workflow_runtime_sdk_grants WHERE runtime_session_id=s.id) AS grants, \
         (SELECT count(*) FROM public.runtime_admissions WHERE session_id=s.id) AS admissions, \
         (SELECT count(*) FROM public.runtime_report_receipts WHERE session_id=s.id) AS reports, \
         (SELECT count(*) FROM public.runtime_resource_settlements WHERE session_id=s.id) AS resources \
         FROM public.executions e JOIN public.workflow_execution_attempts a ON a.execution_id=e.id \
         JOIN public.runtime_sessions s ON s.workflow_attempt_id=a.id AND s.execution_id=e.id \
         WHERE e.id=$1::text::uuid AND a.id=$2::text::uuid AND s.id=$3::text::uuid",
    )
    .bind(fence.execution_id.as_str())
    .bind(fence.attempt_id.as_str())
    .bind(fence.session_id.as_str())
    .persistent(false)
    .fetch_one(pool)
    .await
    .map_err(|_| "common SQL test fixture state is unavailable")?;
    Ok(State {
        status: row.try_get("status")?,
        attempt_status: row.try_get("attempt_status")?,
        attempt_phase: row.try_get("attempt_phase")?,
        attempt_complete: row.try_get("attempt_complete")?,
        closed: row.try_get("closed")?,
        close_reason: row.try_get("close_reason")?,
        starts: row.try_get("starts")?,
        grants: row.try_get("grants")?,
        admissions: row.try_get("admissions")?,
        reports: row.try_get("reports")?,
        resources: row.try_get("resources")?,
    })
}

async fn incumbent_execution_state(
    pool: &PgPool,
    fence: &SessionFence,
) -> TestResult<(String, String, String)> {
    let row = sqlx::query(
        "SELECT id::text AS id,control_owner,status::text AS status \
         FROM public.executions WHERE id=$1::text::uuid",
    )
    .bind(fence.execution_id.as_str())
    .persistent(false)
    .fetch_one(pool)
    .await
    .map_err(|_| "incumbent cannot observe the same native fixture")?;
    Ok((
        row.try_get("id")?,
        row.try_get("control_owner")?,
        row.try_get("status")?,
    ))
}

#[tokio::test]
async fn no_grant_cancel_preserves_fences_and_commits() -> TestResult<()> {
    let (mut fence, incumbent_execution_id) = Fixture::read()?;
    let (config, core) = database("BIFROST_COMMON_CORE_TEST_DATABASE_URL", "wex_core").await?;
    let (_, incumbent) = database(
        "BIFROST_COMMON_INCUMBENT_TEST_DATABASE_URL",
        "wex_incumbent",
    )
    .await?;
    assert_eq!(
        incumbent_execution_state(incumbent.pool(), &fence).await?,
        (
            fence.execution_id.as_str().to_owned(),
            "rust".to_owned(),
            "Running".to_owned(),
        ),
    );
    let before = state(core.pool(), &fence).await?;
    assert_eq!(
        before,
        State {
            status: "Running".into(),
            attempt_status: "running".into(),
            attempt_phase: "execution".into(),
            attempt_complete: false,
            closed: false,
            close_reason: None,
            starts: 1,
            grants: 0,
            admissions: 0,
            reports: 0,
            resources: 0,
        },
        "test requires a fresh committed fixture; never reset/reopen it",
    );
    assert_eq!(
        observe_running_cancel(core.pool(), &fence).await?,
        CancelObservation::NotCommitted,
    );
    // A missing native UPDATE privilege must not impersonate a working owner
    // guard. Exercise the same column through the actual incumbent login on
    // its separate committed Python-owned Running fixture, then roll back.
    let mut incumbent_writer = incumbent.pool().begin().await?;
    let touched: String = sqlx::query_scalar(
        "UPDATE public.executions SET status=status \
         WHERE id=$1::text::uuid AND control_owner='python' AND status='Running' \
         RETURNING id::text",
    )
    .bind(incumbent_execution_id.as_str())
    .persistent(false)
    .fetch_one(&mut *incumbent_writer)
    .await
    .map_err(|_| "incumbent-owned native write positive failed")?;
    assert_eq!(touched, incumbent_execution_id.as_str());
    incumbent_writer.rollback().await?;
    assert_eq!(
        request_running_cancel(incumbent.pool(), &fence).await,
        Err(SessionError::Rejected),
    );
    // The Rust entrypoint check above is not evidence of database writer
    // exclusion. Exercise direct incumbent DML too, and never print its error.
    let mut foreign_writer = incumbent.pool().begin().await?;
    let foreign_update = sqlx::query(
        "UPDATE public.executions SET status='Cancelling' WHERE id=$1::text::uuid",
    )
    .bind(fence.execution_id.as_str())
    .persistent(false)
    .execute(&mut *foreign_writer)
    .await;
    let forbidden = foreign_update
        .as_ref()
        .err()
        .and_then(sqlx::Error::as_database_error)
        .and_then(|error| error.code())
        .as_deref()
        == Some("42501");
    assert!(forbidden, "incumbent native lifecycle DML was not denied");
    foreign_writer.rollback().await?;
    let original_binding = fence.binding_sha256.clone();
    fence.binding_sha256 = if original_binding == "0".repeat(64) {
        "1".repeat(64)
    } else {
        "0".repeat(64)
    };
    assert_eq!(
        request_running_cancel(core.pool(), &fence).await,
        Err(SessionError::Rejected),
    );
    fence.binding_sha256 = original_binding;
    let mut blocker = core.pool().begin().await?;
    let locked: bool = sqlx::query_scalar(
        "SELECT pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtext( \
         'bifrost:workflow-execution:' || $1))",
    )
    .bind(fence.execution_id.as_str())
    .persistent(false)
    .fetch_one(&mut *blocker)
    .await?;
    assert!(locked, "a different writer already owns the fresh test fixture");
    assert_eq!(
        request_running_cancel(core.pool(), &fence).await,
        Err(SessionError::Contended),
    );
    blocker.rollback().await?;
    assert_eq!(state(core.pool(), &fence).await?, before);
    assert_eq!(
        request_running_cancel(core.pool(), &fence).await?,
        CancelCommit::Committed,
    );
    // An independent pool starts a fresh committed read; do not use an ORM
    // identity map or the transaction that performed the mutation as evidence.
    let reader = Database::new(&config)?;
    let after = state(reader.pool(), &fence).await?;
    // A different database or clone with the same database/login names cannot
    // qualify mixed access merely by rejecting the incumbent before any read.
    assert_eq!(
        incumbent_execution_state(incumbent.pool(), &fence).await?,
        (
            fence.execution_id.as_str().to_owned(),
            "rust".to_owned(),
            "Cancelling".to_owned(),
        ),
    );
    assert_eq!(
        after,
        State {
            status: "Cancelling".into(),
            closed: true,
            close_reason: Some("cancel_requested".into()),
            ..before
        },
    );
    assert_eq!(
        observe_running_cancel(reader.pool(), &fence).await?,
        CancelObservation::Committed,
    );
    assert_eq!(
        request_running_cancel(core.pool(), &fence).await?,
        CancelCommit::AlreadyCommitted,
    );
    assert_eq!(state(reader.pool(), &fence).await?, after);
    reader.close().await;
    incumbent.close().await;
    core.close().await;
    Ok(())
}
