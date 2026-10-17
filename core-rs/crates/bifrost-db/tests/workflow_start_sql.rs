//! Opt-in acceptance source for the internal Rust Start transaction.
//! Requires two fresh prepared cohorts on one separately approved disposable DB.
//! Installs no schema/roles, creates no fixture, never sends Start or runs a child.
//! This is NOT part of the frozen R1-CANCEL-SQL/v1 activation/test procedure.
//! SQL execution remains held; default tests do not run this file.
#![cfg(feature = "common-start-sql")]

use std::{path::PathBuf, time::Duration};

use bifrost_contracts::runtime::CanonicalUuid;
use bifrost_db::{
    Database, DatabaseConfig,
    common_session::{CancelCommit, SessionError, SessionFence, request_running_cancel},
    workflow_start::{
        PreparedStartIntent, StartObservation, commit_prepared_workflow_start,
        observe_prepared_workflow_start,
    },
};
use serde::Deserialize;
use sqlx::{PgPool, Row};

type TestResult<T> = Result<T, Box<dyn std::error::Error>>;

// Private synthetic test input, not a public/runtime admission deserializer.
// No Debug, no raw deserializer or SQL error output.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Cohort {
    execution_id: CanonicalUuid,
    owner_incarnation_id: CanonicalUuid,
    attempt_id: CanonicalUuid,
    claim_token: CanonicalUuid,
    worker_incarnation_id: CanonicalUuid,
    session_id: CanonicalUuid,
    supervisor_incarnation_id: CanonicalUuid,
    runtime_incarnation_id: CanonicalUuid,
    binding_sha256: String,
    channel_custody_sha256: String,
    prepare_message_id: CanonicalUuid,
    prepare_sha256: String,
    committed_start_id: CanonicalUuid,
    start_message_id: CanonicalUuid,
    artifact_id: String,
    input_json: String,
    context_json: String,
    deadline_utc: Option<String>,
}

impl Cohort {
    fn fence(&self) -> SessionFence {
        SessionFence {
            execution_id: self.execution_id.clone(),
            owner_incarnation_id: self.owner_incarnation_id.clone(),
            attempt_id: self.attempt_id.clone(),
            claim_token: self.claim_token.clone(),
            worker_incarnation_id: self.worker_incarnation_id.clone(),
            session_id: self.session_id.clone(),
            supervisor_incarnation_id: self.supervisor_incarnation_id.clone(),
            runtime_incarnation_id: self.runtime_incarnation_id.clone(),
            binding_sha256: self.binding_sha256.clone(),
            channel_custody_sha256: self.channel_custody_sha256.clone(),
        }
    }

    fn intent(&self) -> PreparedStartIntent {
        PreparedStartIntent {
            committed_start_id: self.committed_start_id.clone(),
            start_message_id: self.start_message_id.clone(),
            prepare_message_id: self.prepare_message_id.clone(),
            prepare_sha256: self.prepare_sha256.clone(),
            artifact_id: self.artifact_id.clone(),
            input_json: self.input_json.clone(),
            context_json: self.context_json.clone(),
            deadline_utc: self.deadline_utc.clone(),
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Fixtures {
    schema: String,
    finite: Cohort,
    no_timeout: Cohort,
}

impl Fixtures {
    fn read() -> TestResult<Self> {
        let path = std::env::var_os("BIFROST_COMMON_START_TEST_FIXTURES")
            .map(PathBuf::from)
            .ok_or("common Start test requires its fresh private fixtures")?;
        let metadata =
            std::fs::symlink_metadata(&path).map_err(|_| "common Start fixture unavailable")?;
        if !metadata.is_file() || metadata.len() > 65536 {
            return Err("common Start fixture must be a bounded regular file".into());
        }
        let bytes = std::fs::read(path).map_err(|_| "common Start fixture read failed")?;
        let value: Self =
            serde_json::from_slice(&bytes).map_err(|_| "common Start fixture invalid")?;
        if value.schema != "bifrost.common-start-sql-fixtures/v1"
            || value.finite.execution_id == value.no_timeout.execution_id
            || value.finite.attempt_id == value.no_timeout.attempt_id
            || value.finite.session_id == value.no_timeout.session_id
            || value.finite.committed_start_id == value.no_timeout.committed_start_id
            || value.finite.deadline_utc.is_none()
            || value.no_timeout.deadline_utc.is_some()
        {
            return Err("common Start fixtures must be distinct finite/no-timeout cohorts".into());
        }
        Ok(value)
    }
}

async fn database(variable: &str, principal: &str) -> TestResult<Database> {
    let url = std::env::var(variable).map_err(|_| "common Start test DSN required")?;
    let config = DatabaseConfig::new(&url, 2, Duration::from_secs(2))?;
    let db = Database::new(&config)?;
    let actual: bool = sqlx::query_scalar(
        "SELECT current_database()='bifrost_test' AND session_user=$1 \
         AND current_user=session_user AND NOT r.rolsuper AND NOT r.rolbypassrls \
         AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND NOT r.rolreplication \
         FROM pg_catalog.pg_roles r WHERE r.rolname=session_user",
    )
    .bind(principal)
    .persistent(false)
    .fetch_one(db.pool())
    .await
    .map_err(|_| "common Start test restricted login unverified")?;
    if !actual {
        return Err("common Start test requires genuine restricted login".into());
    }
    Ok(db)
}

#[derive(Debug, PartialEq, Eq)]
struct NativeState {
    status: String,
    phase: String,
    attempt_status: String,
    started_clock_matches: bool,
    root_started: bool,
    attempt_started: bool,
    completed: bool,
}

async fn native_state(pool: &PgPool, c: &Cohort) -> TestResult<NativeState> {
    let row = sqlx::query(
        "SELECT e.status::text AS status,a.phase,a.status AS attempt_status, \
          e.started_at IS NOT DISTINCT FROM a.started_at AS clock_matches, \
          e.started_at IS NOT NULL AS root_started,a.started_at IS NOT NULL AS attempt_started, \
          e.completed_at IS NOT NULL OR a.completed_at IS NOT NULL AS completed \
         FROM public.executions e JOIN public.workflow_execution_attempts a ON a.execution_id=e.id \
         WHERE e.id=$1::text::uuid AND a.id=$2::text::uuid",
    )
    .bind(c.execution_id.as_str())
    .bind(c.attempt_id.as_str())
    .persistent(false)
    .fetch_one(pool)
    .await
    .map_err(|_| "common Start native state unavailable")?;
    Ok(NativeState {
        status: row.try_get("status")?,
        phase: row.try_get("phase")?,
        attempt_status: row.try_get("attempt_status")?,
        started_clock_matches: row.try_get("clock_matches")?,
        root_started: row.try_get("root_started")?,
        attempt_started: row.try_get("attempt_started")?,
        completed: row.try_get("completed")?,
    })
}

fn changed_uuid(value: &CanonicalUuid) -> TestResult<CanonicalUuid> {
    let mut bytes = value.as_str().as_bytes().to_vec();
    bytes[0] = if bytes[0] == b'0' { b'1' } else { b'0' };
    Ok(CanonicalUuid::new(String::from_utf8(bytes)?)?)
}

async fn verify() -> TestResult<()> {
    let fixtures = Fixtures::read()?;
    let core = database("BIFROST_COMMON_CORE_TEST_DATABASE_URL", "wex_core").await?;
    let incumbent = database(
        "BIFROST_COMMON_INCUMBENT_TEST_DATABASE_URL",
        "wex_incumbent",
    )
    .await?;
    let a = &fixtures.finite;
    let b = &fixtures.no_timeout;
    let before_a = native_state(core.pool(), a).await?;
    let before_b = native_state(core.pool(), b).await?;
    for initial in [&before_a, &before_b] {
        assert_eq!(initial.status, "Running");
        assert_eq!(initial.attempt_status, "claimed");
        assert_eq!(initial.phase, "claim");
        assert!(!initial.root_started && !initial.attempt_started);
        assert!(!initial.completed);
    }
    let fence = a.fence();
    let intent = a.intent();
    assert_eq!(
        observe_prepared_workflow_start(core.pool(), &fence, &intent).await?,
        StartObservation::NotRetained
    );
    assert_eq!(
        commit_prepared_workflow_start(incumbent.pool(), &fence, &intent).await,
        Err(SessionError::Rejected)
    );
    for altered in [0, 1, 2] {
        let mut wrong = a.fence();
        match altered {
            0 => wrong.claim_token = changed_uuid(&wrong.claim_token)?,
            1 => wrong.owner_incarnation_id = changed_uuid(&wrong.owner_incarnation_id)?,
            _ => wrong.session_id = changed_uuid(&wrong.session_id)?,
        }
        assert_eq!(
            commit_prepared_workflow_start(core.pool(), &wrong, &intent).await,
            Err(SessionError::Rejected)
        );
        assert_eq!(native_state(core.pool(), a).await?, before_a);
    }
    // Force the shared execution fence to be held by a separate transaction;
    // the candidate must return Contended without any native projection write.
    let mut blocker = core.pool().begin().await?;
    let acquired: bool = sqlx::query_scalar(
        "SELECT pg_try_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || $1))",
    )
    .bind(fence.execution_id.as_str())
    .persistent(false)
    .fetch_one(&mut *blocker)
    .await?;
    assert!(acquired);
    assert_eq!(
        commit_prepared_workflow_start(core.pool(), &fence, &intent).await,
        Err(SessionError::Contended)
    );
    blocker.rollback().await?;
    assert_eq!(native_state(core.pool(), a).await?, before_a);
    // These contenders may serialize; exactly one commits. The deterministic
    // fence-contention case above supplies the forced-overlap observation.
    let (one, two) = tokio::join!(
        commit_prepared_workflow_start(core.pool(), &fence, &intent),
        commit_prepared_workflow_start(core.pool(), &fence, &intent)
    );
    assert_eq!(usize::from(one.is_ok()) + usize::from(two.is_ok()), 1);
    let (winner, loser) = match (one, two) {
        (Ok(win), Err(loss)) | (Err(loss), Ok(win)) => (win, loss),
        _ => return Err("common Start race did not produce one winner".into()),
    };
    assert!(matches!(
        loser,
        SessionError::Rejected | SessionError::Contended
    ));
    assert!(winner.remaining_run_ms.is_some_and(|ms| ms > 0));
    assert_eq!(
        observe_prepared_workflow_start(core.pool(), &fence, &intent).await?,
        StartObservation::Retained
    );
    let after = native_state(core.pool(), a).await?;
    assert_eq!(after.status, "Running");
    assert_eq!(after.attempt_status, "running");
    assert_eq!(after.phase, "execution");
    assert!(after.started_clock_matches && after.root_started && after.attempt_started);
    assert!(!after.completed);
    // Fresh incumbent read proves the same committed native database projection.
    assert_eq!(native_state(incumbent.pool(), a).await?, after);
    assert_eq!(
        commit_prepared_workflow_start(core.pool(), &fence, &intent).await,
        Err(SessionError::Rejected)
    );
    let mut drift = a.intent();
    drift.context_json = "{\"drift\":true}".into();
    assert_eq!(
        observe_prepared_workflow_start(core.pool(), &fence, &drift).await,
        Err(SessionError::Rejected)
    );

    // Deliberate global Start-ID collision happens AFTER native updates. The
    // INSERT must fail and the entire native transition must roll back.
    let mut collision = b.intent();
    collision.committed_start_id = a.committed_start_id.clone();
    assert_eq!(
        commit_prepared_workflow_start(core.pool(), &b.fence(), &collision).await,
        Err(SessionError::Unavailable)
    );
    assert_eq!(native_state(core.pool(), b).await?, before_b);
    assert_eq!(
        observe_prepared_workflow_start(core.pool(), &b.fence(), &b.intent()).await?,
        StartObservation::NotRetained
    );
    // The failed collision was a definite pre-commit rejection, never an ACK/
    // spawn ambiguity. Only this synthetic test then uses its original valid ID.
    let unlimited = commit_prepared_workflow_start(core.pool(), &b.fence(), &b.intent()).await?;
    assert_eq!(unlimited.remaining_run_ms, None);
    assert_eq!(native_state(incumbent.pool(), b).await?.status, "Running");
    assert_eq!(
        request_running_cancel(core.pool(), &fence).await?,
        CancelCommit::Committed
    );
    assert_eq!(
        observe_prepared_workflow_start(core.pool(), &fence, &intent).await?,
        StartObservation::Retained
    );
    assert_eq!(
        commit_prepared_workflow_start(core.pool(), &fence, &intent).await,
        Err(SessionError::Rejected)
    );
    core.close().await;
    incumbent.close().await;
    Ok(())
}

#[tokio::test]
async fn native_start_atomicity_fencing_race_and_reconciliation() {
    // A fixture/driver exception must never print private claims or SQL params.
    assert!(
        verify().await.is_ok(),
        "common Start SQL verification failed; retain private fixture/database, no retry"
    );
}
