//! Isolated common release transaction. A retained row is not spawn authority.
use crate::{ObserveError, SessionFence, canonical_uuid, digest, lock_session_state};
use sqlx::{PgPool, Row};

// Both storage and issued paths use the identical common INSERT. Parameter
// locations are compile-time literals, never requester-controlled SQL text.
macro_rules! release_insert_sql {
    ($release:literal, $frontier:literal, $provision:literal) => { concat!(
        "INSERT INTO runtime_admissions ",
        "(id,purpose,session_id,committed_start_id,start_message_id,grant_id,delivery_id, ",
        "operations_digest,expires_at,provision_admission_id,provision_purpose,frontier_sha256,admitted_at) ",
        "SELECT ", $release, "::text::uuid,'release',p.session_id,p.committed_start_id,p.start_message_id, ",
        "p.grant_id,p.delivery_id,p.operations_digest,p.expires_at,p.id,'provision',", $frontier, ",clock_timestamp() ",
        "FROM runtime_admissions p JOIN runtime_starts s ON s.id=p.committed_start_id ",
        "JOIN workflow_runtime_sdk_grants g ON g.id=p.grant_id ",
        "WHERE p.id=", $provision, "::text::uuid AND p.purpose='provision' ",
        "AND g.revoked_at IS NULL AND g.initial_access_expires_at > clock_timestamp() ",
        "AND p.expires_at > clock_timestamp() ",
        "AND (s.deadline_utc IS NULL OR s.deadline_utc > clock_timestamp())"
    )};
}
const INSERT_RELEASE: &str = release_insert_sql!("$1", "$2", "$3");
const INSERT_ISSUED_RELEASE: &str = concat!(
    "WITH current_eligible AS (",
    crate::admit::eligibility_sql!(),
    ") ",
    release_insert_sql!("$6", "$7", "$8"),
    " AND EXISTS (SELECT 1 FROM current_eligible WHERE caller=$9::jsonb)"
);

pub struct ReleaseRequest {
    pub release_id: String,
    pub provision_id: String,
    pub grant_id: String,
    pub delivery_id: String,
    pub operations_sha256: String,
    pub frontier_sha256: String,
}

/// Recovery never turns an already retained release into a fresh delivery.
#[derive(Debug, PartialEq, Eq)]
pub enum ReleaseCommitObservation {
    NewlyCommitted,
    AlreadyRetained,
    NotRetained,
}

/// The trusted guardian must independently establish accepted bytes/source,
/// input, observed Start/frontier and authenticated issuer material before this
/// call, under the same source fences. Matching request strings cannot do so.
/// This isolated component proves database serialization and observed commit;
/// it cannot authenticate a live channel or authorize an arbitrary OS process.
pub async fn record_release_candidate(
    pool: &PgPool,
    fence: &SessionFence,
    request: &ReleaseRequest,
) -> Result<ReleaseCommitObservation, ObserveError> {
    release_transaction(pool, fence, request, false, None).await
}

/// Read-only reconciliation after an uncertain commit or lost reply. Absence
/// does not establish no effects and is never permission to replay or reassign.
pub async fn observe_release_candidate(
    pool: &PgPool,
    fence: &SessionFence,
    request: &ReleaseRequest,
) -> Result<ReleaseCommitObservation, ObserveError> {
    release_transaction(pool, fence, request, true, None).await
}

/// Actual issued-material path: current preimages/eligibility are checked under
/// the very transaction that commits release. Original live guardian and pipe
/// custody remain mandatory in the LiveStart caller, not supplied UUID evidence.
#[cfg(target_os = "linux")]
pub async fn record_issued_release_candidate(
    pool: &PgPool,
    fence: &SessionFence,
    request: &ReleaseRequest,
    material: &crate::issuer::IssuedMaterial,
) -> Result<ReleaseCommitObservation, ObserveError> {
    release_transaction(
        pool,
        fence,
        request,
        false,
        Some((
            material.reference(),
            material.operations_digest(),
            material.expires_at(),
        )),
    )
    .await
}

async fn release_transaction(
    pool: &PgPool,
    fence: &SessionFence,
    request: &ReleaseRequest,
    observe_only: bool,
    material: Option<(&crate::IntegrationGetRequest, &str, &str)>,
) -> Result<ReleaseCommitObservation, ObserveError> {
    if ![
        &request.release_id,
        &request.provision_id,
        &request.grant_id,
        &request.delivery_id,
    ]
    .into_iter()
    .all(|value| canonical_uuid(value))
        || !digest(&request.operations_sha256)
        || !digest(&request.frontier_sha256)
    {
        return Err(ObserveError::InvalidFence);
    }
    let mut locked = lock_session_state(pool, fence, observe_only).await?;
    if !observe_only
        && (locked.closed
            || locked.execution_status != "Running"
            || locked.attempt_status != "running"
            || locked.attempt_completed)
    {
        return Err(ObserveError::Rejected);
    }
    let mut release_eligibility = None;
    if let Some((reference, operations_digest, expires_at)) = material {
        if observe_only
            || request.grant_id != reference.grant_id
            || request.operations_sha256 != operations_digest
        {
            return Err(ObserveError::Rejected);
        }
        let checked =
            crate::sdk::authorize_issued_release_locked(&mut locked, fence, reference).await?;
        if checked.expires_at() != expires_at {
            return Err(ObserveError::Rejected);
        }
        release_eligibility = Some((checked.snapshot().clone(), checked.caller().to_string()));
    }
    let start = sqlx::query(
        "SELECT id::text AS id,start_message_id::text AS message FROM runtime_starts \
         WHERE session_id=$1::text::uuid AND execution_id=$2::text::uuid \
         AND owner_incarnation_id=$3::text::uuid AND workflow_attempt_id=$4::text::uuid \
         FOR UPDATE",
    )
    .bind(&fence.session_id)
    .bind(&fence.execution_id)
    .bind(&fence.owner_incarnation_id)
    .bind(&fence.attempt_id)
    .fetch_optional(&mut *locked.tx)
    .await?
    .ok_or(ObserveError::Rejected)?;
    let start_id: String = start.try_get("id")?;
    let start_message: String = start.try_get("message")?;
    let admissions = sqlx::query(
        "SELECT id::text AS id,purpose,grant_id::text AS grant,delivery_id::text AS delivery, \
         committed_start_id::text AS start,start_message_id::text AS message, \
         operations_digest,frontier_sha256,provision_admission_id::text AS provision \
         FROM runtime_admissions WHERE session_id=$1::text::uuid ORDER BY purpose,id FOR UPDATE",
    )
    .bind(&fence.session_id)
    .fetch_all(&mut *locked.tx)
    .await?;
    let grants = sqlx::query(
        "SELECT id::text AS id,revoked_at IS NOT NULL AS revoked,operations_digest, \
         committed_start_id::text AS start,start_message_id::text AS message \
         FROM workflow_runtime_sdk_grants WHERE runtime_session_id=$1::text::uuid \
         ORDER BY id FOR UPDATE",
    )
    .bind(&fence.session_id)
    .fetch_all(&mut *locked.tx)
    .await?;
    let receipts = sqlx::query(
        "SELECT result_message_id FROM runtime_report_receipts WHERE session_id=$1::text::uuid \
         ORDER BY result_message_id FOR UPDATE",
    )
    .bind(&fence.session_id)
    .fetch_all(&mut *locked.tx)
    .await?;
    if grants.len() != 1 || (!observe_only && !receipts.is_empty()) {
        return Err(ObserveError::Rejected);
    }
    let grant = &grants[0];
    if grant.try_get::<String, _>("id")? != request.grant_id
        || (!observe_only && grant.try_get::<bool, _>("revoked")?)
        || grant.try_get::<String, _>("start")? != start_id
        || grant.try_get::<String, _>("message")? != start_message
        || grant.try_get::<String, _>("operations_digest")? != request.operations_sha256
    {
        return Err(ObserveError::Rejected);
    }
    let mut provision = false;
    let mut retained = false;
    for row in &admissions {
        if row.try_get::<String, _>("grant")? != request.grant_id
            || row.try_get::<String, _>("delivery")? != request.delivery_id
            || row.try_get::<String, _>("start")? != start_id
            || row.try_get::<String, _>("message")? != start_message
            || row.try_get::<String, _>("operations_digest")? != request.operations_sha256
        {
            return Err(ObserveError::Rejected);
        }
        match row.try_get::<String, _>("purpose")?.as_str() {
            "provision" if !provision => {
                if row.try_get::<String, _>("id")? != request.provision_id {
                    return Err(ObserveError::Rejected);
                }
                provision = true;
            }
            "release" if !retained => {
                if row.try_get::<String, _>("id")? != request.release_id
                    || row.try_get::<String, _>("frontier_sha256")? != request.frontier_sha256
                    || row.try_get::<Option<String>, _>("provision")?.as_deref()
                        != Some(request.provision_id.as_str())
                {
                    return Err(ObserveError::Rejected);
                }
                retained = true;
            }
            _ => return Err(ObserveError::Rejected),
        }
    }
    if !provision {
        return Err(ObserveError::Rejected);
    }
    if retained {
        locked
            .tx
            .commit()
            .await
            .map_err(|_| ObserveError::UncertainCommit)?;
        return Ok(ReleaseCommitObservation::AlreadyRetained);
    }
    if observe_only {
        locked
            .tx
            .commit()
            .await
            .map_err(|_| ObserveError::UncertainCommit)?;
        return Ok(ReleaseCommitObservation::NotRetained);
    }
    // Evaluate expiry after all locks, at the actual INSERT clock. Any deadline
    // or grant expiry that passes during contention rejects without a release.
    // Static SQL only; values remain binds. Reuse the same shared predicate
    // at the write's own snapshot instead of carrying read-time authorization.
    let inserted = if let Some((snapshot, caller)) = &release_eligibility {
        let field = |name: &str| snapshot[name].as_str().ok_or(ObserveError::Rejected);
        sqlx::query(INSERT_ISSUED_RELEASE)
            .bind(field("workflow_id")?)
            .bind(field("caller_user_id")?)
            .bind(field("solution_install_id")?)
            .bind(field("effective_organization_id")?)
            .bind(field("source_id")?)
            .bind(&request.release_id)
            .bind(&request.frontier_sha256)
            .bind(&request.provision_id)
            .bind(caller)
            .execute(&mut *locked.tx)
            .await?
    } else {
        sqlx::query(INSERT_RELEASE)
            .bind(&request.release_id)
            .bind(&request.frontier_sha256)
            .bind(&request.provision_id)
            .execute(&mut *locked.tx)
            .await?
    };
    if inserted.rows_affected() != 1 {
        return Err(ObserveError::Rejected);
    }
    locked
        .tx
        .commit()
        .await
        .map_err(|_| ObserveError::UncertainCommit)?;
    Ok(ReleaseCommitObservation::NewlyCommitted)
}
