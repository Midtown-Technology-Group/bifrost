//! Restricted SDK admission candidate; no token issuer or HTTP router is enabled.
use crate::{ObserveError, SessionFence, canonical_uuid, digest, lock_session};
use serde_json::Value;
use sqlx::{PgPool, Row};

/// Trusted ingress supplies this only after CRED-P1 signature, issuer, audience,
/// purpose, finite expiry and closed claim-schema verification. These strings
/// alone are not a credential; tenant request data cannot supply a SessionFence.
pub struct IntegrationGetRequest {
    pub grant_id: String,
    pub grant_digest: String,
    pub integration_name: String,
    pub organization_id: String,
    pub solution_id: String,
}

pub struct IntegrationGetAdmission {
    integration_name: String,
    organization_id: String,
    solution_id: String,
}

/// Read-only finite-signing prerequisites, not a credential or release permit.
/// Caller still authenticates the original issuer and live guardian/source.
pub struct FiniteIssuanceAdmission {
    snapshot: Value,
    caller: Value,
    expires_at: String,
    grant_digest: String,
}
impl FiniteIssuanceAdmission {
    pub fn grant_digest(&self) -> &str {
        &self.grant_digest
    }
    pub fn expires_at(&self) -> &str {
        &self.expires_at
    }
    pub fn snapshot(&self) -> &Value {
        &self.snapshot
    }
    pub fn caller(&self) -> &Value {
        &self.caller
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Purpose {
    FiniteIssuance,
    ReleasedSDK,
}

struct CheckedGrant {
    capability: IntegrationGetAdmission,
    issuance: FiniteIssuanceAdmission,
}

impl IntegrationGetAdmission {
    pub fn integration_name(&self) -> &str {
        &self.integration_name
    }
    pub fn organization_id(&self) -> &str {
        &self.organization_id
    }
    pub fn solution_id(&self) -> &str {
        &self.solution_id
    }
}

/// Serialize the SDK admission with close/Cancel/Result using the common lock
/// order. It authorizes only the retained integration-get policy after release.
/// Recheck current source/workflow/caller entitlement and the unchanged caller
/// snapshot. Caller must still verify the token, complete source closure and live
/// custody, then invoke the stable capability API; this does not fetch integrations.
/// An uncertain read commit denies the call, never broadens or renews authority.
pub async fn authorize_integration_get_candidate(
    pool: &PgPool,
    fence: &SessionFence,
    request: &IntegrationGetRequest,
) -> Result<IntegrationGetAdmission, ObserveError> {
    Ok(
        authorize_candidate(pool, fence, request, Purpose::ReleasedSDK)
            .await?
            .capability,
    )
}

/// The original owner may call this after observed provision commit, before
/// finite signing/release. No release row, receipt or revoked/closed session may
/// exist. This never creates/renews a grant or permits material transmission.
pub async fn authorize_finite_issuance_candidate(
    pool: &PgPool,
    fence: &SessionFence,
    request: &IntegrationGetRequest,
) -> Result<FiniteIssuanceAdmission, ObserveError> {
    Ok(
        authorize_candidate(pool, fence, request, Purpose::FiniteIssuance)
            .await?
            .issuance,
    )
}

async fn authorize_candidate(
    pool: &PgPool,
    fence: &SessionFence,
    request: &IntegrationGetRequest,
    purpose: Purpose,
) -> Result<CheckedGrant, ObserveError> {
    if !canonical_uuid(&request.grant_id)
        || !canonical_uuid(&request.organization_id)
        || !canonical_uuid(&request.solution_id)
        || !digest(&request.grant_digest)
        || request.integration_name.is_empty()
        || request.integration_name.len() > 255
        || request.integration_name.contains('\0')
        || request.integration_name.trim() != request.integration_name
    {
        return Err(ObserveError::InvalidFence);
    }
    let mut locked = lock_session(pool, fence).await?;
    if locked.closed
        || locked.execution_status != "Running"
        || locked.attempt_status != "running"
        || locked.attempt_completed
    {
        return Err(ObserveError::Rejected);
    }
    let start = sqlx::query(
        "SELECT id::text AS id,start_message_id::text AS message \
         FROM runtime_starts WHERE session_id=$1::text::uuid FOR UPDATE",
    )
    .bind(&fence.session_id)
    .fetch_optional(&mut *locked.tx)
    .await?
    .ok_or(ObserveError::Rejected)?;
    let start_id: String = start.try_get("id")?;
    let start_message: String = start.try_get("message")?;
    let admissions = sqlx::query(
        "SELECT id::text AS id,purpose,grant_id::text AS grant, \
         committed_start_id::text AS start,start_message_id::text AS message, \
         operations_digest,delivery_id::text AS delivery, \
         provision_admission_id::text AS provision \
         FROM runtime_admissions WHERE session_id=$1::text::uuid ORDER BY purpose,id FOR UPDATE",
    )
    .bind(&fence.session_id)
    .fetch_all(&mut *locked.tx)
    .await?;
    let grants = sqlx::query(
        "SELECT id::text AS id,grant_digest,operations_digest, \
         revoked_at IS NOT NULL AS revoked,committed_start_id::text AS start, \
         start_message_id::text AS message,effective_organization_id::text AS organization, \
         solution_install_id::text AS solution,timeout_seconds, \
         workflow_id::text AS workflow,caller_user_id::text AS caller,source_id::text AS deployment, \
         to_jsonb(workflow_runtime_sdk_grants)::text AS raw_grant, \
         to_char(initial_access_expires_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS expires_wire \
         FROM workflow_runtime_sdk_grants WHERE runtime_session_id=$1::text::uuid ORDER BY id FOR UPDATE",
    ).bind(&fence.session_id).fetch_all(&mut *locked.tx).await?;
    if grants.len() != 1 || grants[0].try_get::<String, _>("id")? != request.grant_id {
        return Err(ObserveError::Rejected);
    }
    let operations=sqlx::query(
        "SELECT ordinal,operation,integration_name,scope_kind, \
         scope_organization_id::text AS scope,resolved_organization_id::text AS organization, \
         solution_install_id::text AS solution \
         FROM workflow_runtime_sdk_grant_operations WHERE grant_id=$1::text::uuid ORDER BY ordinal FOR UPDATE",
    ).bind(&request.grant_id).fetch_all(&mut *locked.tx).await?;
    let receipts = sqlx::query(
        "SELECT result_message_id FROM runtime_report_receipts \
         WHERE session_id=$1::text::uuid ORDER BY result_message_id FOR UPDATE",
    )
    .bind(&fence.session_id)
    .fetch_all(&mut *locked.tx)
    .await?;
    let admission_count = if purpose == Purpose::ReleasedSDK {
        2
    } else {
        1
    };
    if grants.len() != 1 || admissions.len() != admission_count || !receipts.is_empty() {
        return Err(ObserveError::Rejected);
    }
    let grant = &grants[0];
    let operations_digest: String = grant.try_get("operations_digest")?;
    if grant.try_get::<String, _>("id")? != request.grant_id
        || grant.try_get::<String, _>("grant_digest")? != request.grant_digest
        || grant.try_get::<bool, _>("revoked")?
        || grant.try_get::<String, _>("start")? != start_id
        || grant.try_get::<String, _>("message")? != start_message
        || grant
            .try_get::<Option<String>, _>("organization")?
            .as_deref()
            != Some(request.organization_id.as_str())
        || grant.try_get::<String, _>("solution")? != request.solution_id
        || grant.try_get::<i32, _>("timeout_seconds")? <= 0
    {
        return Err(ObserveError::Rejected);
    }
    // SQL purpose ordering is provision then release. Exact composite FKs also
    // retain expiry and delivery identity; neither row is a portable bearer.
    let provision = &admissions[0];
    if provision.try_get::<String, _>("purpose")? != "provision" {
        return Err(ObserveError::Rejected);
    }
    if purpose == Purpose::ReleasedSDK {
        let release = &admissions[1];
        if release.try_get::<String, _>("purpose")? != "release"
            || release
                .try_get::<Option<String>, _>("provision")?
                .as_deref()
                != Some(provision.try_get::<String, _>("id")?.as_str())
            || release.try_get::<String, _>("delivery")?
                != provision.try_get::<String, _>("delivery")?
        {
            return Err(ObserveError::Rejected);
        }
    }
    for admission in &admissions {
        if admission.try_get::<String, _>("grant")? != request.grant_id
            || admission.try_get::<String, _>("start")? != start_id
            || admission.try_get::<String, _>("message")? != start_message
            || admission.try_get::<String, _>("operations_digest")? != operations_digest
        {
            return Err(ObserveError::Rejected);
        }
    }
    if operations.len() != 1 {
        return Err(ObserveError::Rejected);
    }
    // Use the same current-user/role/workflow predicate as owner birth. Bind
    // only locked grant/parent facts, never request-provided caller identity.
    // This read is part of SDK admission under the common source/session order;
    // it grants no new source, role, scope or operation.
    let eligibility = sqlx::query(crate::admit::SELECT_ELIGIBILITY)
        .bind(grant.try_get::<String, _>("workflow")?)
        .bind(grant.try_get::<String, _>("caller")?)
        .bind(&request.solution_id)
        .bind(&request.organization_id)
        .bind(grant.try_get::<String, _>("deployment")?)
        .fetch_optional(&mut *locked.tx)
        .await?
        .ok_or(ObserveError::Rejected)?;
    let current_caller: Value = serde_json::from_str(&eligibility.try_get::<String, _>("caller")?)
        .map_err(|_| ObserveError::Rejected)?;
    let retained_caller: String = sqlx::query_scalar(
        "SELECT caller_snapshot::text FROM runtime_execution_owners WHERE execution_id=$1::text::uuid",
    ).bind(&fence.execution_id).fetch_one(&mut *locked.tx).await?;
    let retained_caller: Value =
        serde_json::from_str(&retained_caller).map_err(|_| ObserveError::Rejected)?;
    if current_caller != retained_caller {
        return Err(ObserveError::Rejected);
    }
    let operation = &operations[0];
    if operation.try_get::<i32, _>("ordinal")? != 0
        || operation.try_get::<String, _>("operation")? != "integration-get"
        || operation.try_get::<String, _>("integration_name")? != request.integration_name
        || operation.try_get::<String, _>("scope_kind")? != "organization"
        || operation.try_get::<Option<String>, _>("scope")?.as_deref()
            != Some(request.organization_id.as_str())
        || operation
            .try_get::<Option<String>, _>("organization")?
            .as_deref()
            != Some(request.organization_id.as_str())
        || operation
            .try_get::<Option<String>, _>("solution")?
            .as_deref()
            != Some(request.solution_id.as_str())
    {
        return Err(ObserveError::Rejected);
    }
    // Check the actual clock after acquiring every lock, not at request decode.
    let current = sqlx::query(
        "SELECT 1 FROM workflow_runtime_sdk_grants g JOIN runtime_starts s \
         ON s.id=g.committed_start_id JOIN runtime_admissions p \
         ON p.grant_id=g.id AND p.purpose='provision' LEFT JOIN runtime_admissions r \
         ON r.provision_admission_id=p.id AND r.purpose='release' \
         JOIN workflow_execution_attempts a ON a.id=g.workflow_attempt_id \
         JOIN runtime_execution_owners o ON o.execution_id=g.execution_id \
         JOIN solution_deployments d ON d.id=o.deployment_id \
         JOIN solutions sol ON sol.id=d.solution_id \
         WHERE g.id=$1::text::uuid AND g.revoked_at IS NULL \
         AND (($2 AND r.id IS NOT NULL) OR (NOT $2 AND r.id IS NULL)) \
         AND g.started_at=s.started_at AND g.attempt_number=a.attempt_number \
         AND o.caller_snapshot->>'caller_user_id'=g.caller_user_id::text \
         AND o.caller_snapshot->>'caller_organization_id'=g.caller_organization_id::text \
         AND o.caller_snapshot->>'effective_organization_id'=g.effective_organization_id::text \
         AND o.caller_snapshot->>'caller_email'=g.caller_email \
         AND o.caller_snapshot->>'caller_name'=g.caller_name \
         AND (o.caller_snapshot->>'caller_admin')::boolean=(g.caller_admin=1) \
         AND (o.caller_snapshot->>'caller_provider')::boolean=(g.caller_provider=1) \
         AND (o.caller_snapshot->>'caller_external')::boolean=(g.caller_external=1) \
         AND g.workflow_id=o.workflow_id AND g.source_kind='solution-deployment' \
         AND g.source_id=d.id AND g.source_global_permission=0 \
         AND g.source_manifest_digest=d.compiled_manifest_hash \
         AND g.source_resolution_digest=d.resolution_map_hash \
         AND d.state IN ('active','committed_unpushed') \
         AND d.organization_id=g.effective_organization_id \
         AND sol.status='active' AND sol.active_deployment_id=d.id \
         AND sol.organization_id=g.effective_organization_id \
         AND sol.execution_runtime_mode='deployment-v1' \
         AND g.credential_deadline=g.initial_access_expires_at \
         AND g.initial_access_expires_at>clock_timestamp() \
         AND s.deadline_utc IS NOT NULL AND s.deadline_utc>clock_timestamp() \
         AND g.initial_access_expires_at<=s.deadline_utc",
    )
    .bind(&request.grant_id)
    .bind(purpose == Purpose::ReleasedSDK)
    .fetch_optional(&mut *locked.tx)
    .await?;
    if current.is_none() {
        return Err(ObserveError::Rejected);
    }
    let raw_grant: Value = serde_json::from_str(&grant.try_get::<String, _>("raw_grant")?)
        .map_err(|_| ObserveError::Rejected)?;
    let mut snapshot = serde_json::Map::new();
    for field in crate::provision::SNAPSHOT_FIELDS {
        snapshot.insert(
            field.into(),
            raw_grant.get(field).ok_or(ObserveError::Rejected)?.clone(),
        );
    }
    locked
        .tx
        .commit()
        .await
        .map_err(|_| ObserveError::UncertainCommit)?;
    Ok(CheckedGrant {
        capability: IntegrationGetAdmission {
            integration_name: request.integration_name.clone(),
            organization_id: request.organization_id.clone(),
            solution_id: request.solution_id.clone(),
        },
        issuance: FiniteIssuanceAdmission {
            snapshot: Value::Object(snapshot),
            caller: current_caller,
            expires_at: grant.try_get("expires_wire")?,
            grant_digest: request.grant_digest.clone(),
        },
    })
}
