//! Isolated owner foundation. Observation is not admission or launch authority.
//! No production dispatch, grant issuance, lifecycle write or process spawning.
use sqlx::{PgPool, Row};

/// Exact retained identity supplied by the trusted coordinator, never a tenant.
#[derive(Debug, Clone)]
pub struct SessionFence {
    pub execution_id: String,
    pub owner_incarnation_id: String,
    pub attempt_id: String,
    pub claim_token: String,
    pub worker_incarnation_id: String,
    pub session_id: String,
    pub supervisor_incarnation_id: String,
    pub runtime_incarnation_id: String,
    pub binding_sha256: String,
    pub channel_custody_sha256: String,
}

impl SessionFence {
    fn valid(&self) -> bool {
        [
            &self.execution_id,
            &self.owner_incarnation_id,
            &self.attempt_id,
            &self.claim_token,
            &self.worker_incarnation_id,
            &self.session_id,
            &self.supervisor_incarnation_id,
            &self.runtime_incarnation_id,
        ]
        .into_iter()
        .all(|value| canonical_uuid(value))
            && digest(&self.binding_sha256)
            && digest(&self.channel_custody_sha256)
    }
}

fn canonical_uuid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(i, b)| {
            if matches!(i, 8 | 13 | 18 | 23) {
                b == b'-'
            } else {
                b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
            }
        })
}

fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[derive(Debug)]
pub enum ObserveError {
    InvalidFence,
    Rejected,
    Database(sqlx::Error),
    /// A failed commit observation must not be retried as an authority action.
    UncertainCommit,
}

impl From<sqlx::Error> for ObserveError {
    fn from(error: sqlx::Error) -> Self {
        Self::Database(error)
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum SessionObservation {
    Open,
    Closed,
}

/// Exercise the released source→attempt→NOWAIT secondary→session order on the
/// actual schema. Returned evidence cannot grant capabilities or permit spawn.
/// All mismatches and secondary contention roll back before any write/effect.
/// Full manifest/dependency eligibility and live process custody remain separate
/// prerequisites; matching retained strings never proves a live channel.
pub async fn observe_session(
    pool: &PgPool,
    fence: &SessionFence,
) -> Result<SessionObservation, ObserveError> {
    if !fence.valid() {
        return Err(ObserveError::InvalidFence);
    }
    let mut tx = pool.begin().await?;
    sqlx::query("SET LOCAL statement_timeout='3s'")
        .execute(&mut *tx)
        .await?;
    sqlx::query("SET LOCAL lock_timeout='1s'")
        .execute(&mut *tx)
        .await?;
    let identity =
        sqlx::query("SELECT session_user::text AS login, current_user::text AS effective")
            .fetch_one(&mut *tx)
            .await?;
    if identity.try_get::<String, _>("login")? != "wex_core"
        || identity.try_get::<String, _>("effective")? != "wex_core"
    {
        return Err(ObserveError::Rejected);
    }
    sqlx::query("SELECT pg_advisory_xact_lock_shared(hashtext('bifrost:workspace-release'))")
        .execute(&mut *tx)
        .await?;
    let attempt = sqlx::query(
        "SELECT id FROM workflow_execution_attempts \
         WHERE id=$1::text::uuid AND execution_id=$2::text::uuid \
         AND claim_token=$3::text::uuid AND worker_incarnation_id=$4::text::uuid \
         AND completed_at IS NULL AND status IN ('claimed','running') FOR UPDATE",
    )
    .bind(&fence.attempt_id)
    .bind(&fence.execution_id)
    .bind(&fence.claim_token)
    .bind(&fence.worker_incarnation_id)
    .fetch_optional(&mut *tx)
    .await?;
    if attempt.is_none() {
        return Err(ObserveError::Rejected);
    }
    let owner = sqlx::query(
        "SELECT workflow_id::text AS workflow, deployment_id::text AS deployment, artifact_id \
         FROM runtime_execution_owners WHERE execution_id=$1::text::uuid \
         AND owner_incarnation_id=$2::text::uuid FOR UPDATE NOWAIT",
    )
    .bind(&fence.execution_id)
    .bind(&fence.owner_incarnation_id)
    .fetch_optional(&mut *tx)
    .await?
    .ok_or(ObserveError::Rejected)?;
    let workflow: String = owner.try_get("workflow")?;
    let deployment: String = owner.try_get("deployment")?;
    let artifact: String = owner.try_get("artifact_id")?;
    let execution = sqlx::query(
        "SELECT id FROM executions WHERE id=$1::text::uuid \
         AND workflow_id=$2::text::uuid AND solution_deployment_id=$3::text::uuid \
         AND runtime_mode='deployment-v1' FOR UPDATE NOWAIT",
    )
    .bind(&fence.execution_id)
    .bind(&workflow)
    .bind(&deployment)
    .fetch_optional(&mut *tx)
    .await?;
    if execution.is_none() {
        return Err(ObserveError::Rejected);
    }
    let source = sqlx::query(
        "SELECT solution_id::text AS solution FROM solution_deployments \
         WHERE id=$1::text::uuid FOR UPDATE NOWAIT",
    )
    .bind(&deployment)
    .fetch_optional(&mut *tx)
    .await?
    .ok_or(ObserveError::Rejected)?;
    let solution: String = source.try_get("solution")?;
    let install = sqlx::query("SELECT id FROM solutions WHERE id=$1::text::uuid FOR UPDATE NOWAIT")
        .bind(&solution)
        .fetch_optional(&mut *tx)
        .await?;
    if install.is_none() {
        return Err(ObserveError::Rejected);
    }
    // Immutable association lookup, not acceptance of its staged bytes/evidence.
    let association = sqlx::query(
        "SELECT workflow_id FROM runtime_deployment_artifacts \
         WHERE deployment_id=$1::text::uuid AND workflow_id=$2::text::uuid \
         AND solution_id=$3::text::uuid AND artifact_id=$4",
    )
    .bind(&deployment)
    .bind(&workflow)
    .bind(&solution)
    .bind(&artifact)
    .fetch_optional(&mut *tx)
    .await?;
    if association.is_none() {
        return Err(ObserveError::Rejected);
    }
    let session = sqlx::query(
        "SELECT closed_at IS NOT NULL AS closed FROM runtime_sessions \
         WHERE id=$1::text::uuid AND execution_id=$2::text::uuid \
         AND owner_incarnation_id=$3::text::uuid AND workflow_attempt_id=$4::text::uuid \
         AND claim_token=$5::text::uuid AND worker_incarnation_id=$6::text::uuid \
         AND supervisor_incarnation_id=$7::text::uuid AND runtime_incarnation_id=$8::text::uuid \
         AND binding_sha256=$9 AND channel_custody_sha256=$10 FOR UPDATE",
    )
    .bind(&fence.session_id)
    .bind(&fence.execution_id)
    .bind(&fence.owner_incarnation_id)
    .bind(&fence.attempt_id)
    .bind(&fence.claim_token)
    .bind(&fence.worker_incarnation_id)
    .bind(&fence.supervisor_incarnation_id)
    .bind(&fence.runtime_incarnation_id)
    .bind(&fence.binding_sha256)
    .bind(&fence.channel_custody_sha256)
    .fetch_optional(&mut *tx)
    .await?
    .ok_or(ObserveError::Rejected)?;
    let closed: bool = session.try_get("closed")?;
    tx.commit()
        .await
        .map_err(|_| ObserveError::UncertainCommit)?;
    Ok(if closed {
        SessionObservation::Closed
    } else {
        SessionObservation::Open
    })
}

#[cfg(test)]
mod tests {
    use super::{canonical_uuid, digest};

    #[test]
    fn canonical_identity_rejects_alternate_spellings_and_injection() {
        assert!(canonical_uuid("11111111-1111-4111-8111-111111111111"));
        for text in [
            "AAAAAAAA-1111-4111-8111-111111111111",
            "11111111111141118111111111111111",
            "11111111-1111-4111-8111-111111111111'",
            "",
        ] {
            assert!(!canonical_uuid(text));
        }
    }

    #[test]
    fn digest_requires_exact_lowercase_sha256_encoding() {
        assert!(digest(&"a".repeat(64)));
        assert!(!digest(&"A".repeat(64)));
        assert!(!digest(&"a".repeat(63)));
        assert!(!digest(&format!("sha256:{}", "a".repeat(64))));
    }
}
