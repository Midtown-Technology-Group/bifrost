//! Isolated finite grant/provision commit; never a signer or delivery permit.
use crate::{ObserveError, SessionFence, canonical_uuid, digest, lock_session};
use serde_json::Value;
use sqlx::{PgPool, Row};

/// Trusted issuer supplies validated CRED-P1 preimages. This private request is
/// never accepted from a workflow. Full caller/source/operation preimage checks
/// and current authorization remain required before entering this transaction.
pub struct ProvisionRequest {
    pub snapshot: Value,
    pub grant_digest: String,
    pub integration_name: String,
    pub provision_id: String,
    pub delivery_id: String,
    pub frontier_sha256: String,
}

#[derive(Debug, PartialEq, Eq)]
pub enum ProvisionDecision {
    NewlyCommitted,
}

const SNAPSHOT_FIELDS: [&str; 32] = [
    "id",
    "schema_version",
    "workflow_attempt_id",
    "execution_id",
    "attempt_number",
    "claim_token_digest",
    "worker_incarnation_id",
    "supervisor_incarnation_id",
    "runtime_session_id",
    "started_at",
    "issued_at",
    "timeout_seconds",
    "credential_deadline",
    "initial_access_expires_at",
    "caller_user_id",
    "caller_organization_id",
    "effective_organization_id",
    "caller_email",
    "caller_name",
    "caller_admin",
    "caller_provider",
    "caller_external",
    "caller_snapshot_digest",
    "workflow_id",
    "solution_install_id",
    "source_kind",
    "source_id",
    "source_manifest_digest",
    "source_resolution_digest",
    "source_global_permission",
    "source_digest",
    "operations_digest",
];

fn request_valid(request: &ProvisionRequest) -> bool {
    let Some(snapshot) = request.snapshot.as_object() else {
        return false;
    };
    snapshot.len() == SNAPSHOT_FIELDS.len()
        && SNAPSHOT_FIELDS
            .iter()
            .all(|key| snapshot.contains_key(*key))
        && snapshot
            .get("id")
            .and_then(Value::as_str)
            .is_some_and(canonical_uuid)
        && canonical_uuid(&request.provision_id)
        && canonical_uuid(&request.delivery_id)
        && digest(&request.grant_digest)
        && digest(&request.frontier_sha256)
        && !request.integration_name.is_empty()
        && request.integration_name.len() <= 255
        && request.integration_name.trim() == request.integration_name
        && !request.integration_name.contains('\0')
        && [
            "credential_deadline",
            "caller_organization_id",
            "effective_organization_id",
        ]
        .iter()
        .all(|key| snapshot.get(*key).is_some_and(|value| !value.is_null()))
}

/// Birth is serialized with absent-grant close via the existing session row.
/// No conflict update, renewal or idempotent write replay is permitted. On a lost
/// commit reply, reconcile retained rows without calling this function again.
/// Even NewlyCommitted cannot authorize material delivery or physical spawn.
pub async fn record_provision_candidate(
    pool: &PgPool,
    fence: &SessionFence,
    request: &ProvisionRequest,
) -> Result<ProvisionDecision, ObserveError> {
    if !request_valid(request) {
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
    let start=sqlx::query("SELECT id::text AS id,start_message_id::text AS message FROM runtime_starts WHERE session_id=$1::text::uuid FOR UPDATE")
        .bind(&fence.session_id).fetch_optional(&mut *locked.tx).await?
        .ok_or(ObserveError::Rejected)?;
    for statement in [
        "SELECT id FROM runtime_admissions WHERE session_id=$1::text::uuid ORDER BY purpose,id FOR UPDATE",
        "SELECT id FROM workflow_runtime_sdk_grants WHERE runtime_session_id=$1::text::uuid ORDER BY id FOR UPDATE",
        "SELECT result_message_id FROM runtime_report_receipts WHERE session_id=$1::text::uuid ORDER BY result_message_id FOR UPDATE",
    ] {
        if !sqlx::query(statement)
            .bind(&fence.session_id)
            .fetch_all(&mut *locked.tx)
            .await?
            .is_empty()
        {
            return Err(ObserveError::Rejected);
        }
    }
    let mut snapshot = request.snapshot.clone();
    let object = snapshot.as_object_mut().ok_or(ObserveError::Rejected)?;
    object.insert(
        "grant_digest".into(),
        Value::String(request.grant_digest.clone()),
    );
    object.insert(
        "owner_incarnation_id".into(),
        Value::String(fence.owner_incarnation_id.clone()),
    );
    object.insert(
        "committed_start_id".into(),
        Value::String(start.try_get("id")?),
    );
    object.insert(
        "start_message_id".into(),
        Value::String(start.try_get("message")?),
    );
    let grant_id = request.snapshot["id"]
        .as_str()
        .ok_or(ObserveError::Rejected)?;
    let raw = snapshot.to_string();
    let checked = sqlx::query(CHECK_SNAPSHOT)
        .bind(&raw)
        .bind(grant_id)
        .bind(&fence.session_id)
        .bind(&request.integration_name)
        .fetch_optional(&mut *locked.tx)
        .await?
        .ok_or(ObserveError::Rejected)?;
    if !checked.try_get::<bool, _>("valid")? {
        return Err(ObserveError::Rejected);
    }
    // Grant and operation birth must roll back together with provision. The row
    // type's nullable revocation columns are absent/null; no caller-supplied owner
    // or lifecycle fields survive the closed snapshot and parent-derived fields.
    sqlx::query("INSERT INTO workflow_runtime_sdk_grants (id,schema_version,workflow_attempt_id,execution_id,attempt_number,claim_token_digest,worker_incarnation_id,supervisor_incarnation_id,runtime_session_id,started_at,issued_at,timeout_seconds,credential_deadline,initial_access_expires_at,caller_user_id,caller_organization_id,effective_organization_id,caller_email,caller_name,caller_admin,caller_provider,caller_external,caller_snapshot_digest,workflow_id,solution_install_id,source_kind,source_id,source_manifest_digest,source_resolution_digest,source_global_permission,source_digest,operations_digest,grant_digest,owner_incarnation_id,committed_start_id,start_message_id) SELECT g.id,g.schema_version,g.workflow_attempt_id,g.execution_id,g.attempt_number,g.claim_token_digest,g.worker_incarnation_id,g.supervisor_incarnation_id,g.runtime_session_id,g.started_at,g.issued_at,g.timeout_seconds,g.credential_deadline,g.initial_access_expires_at,g.caller_user_id,g.caller_organization_id,g.effective_organization_id,g.caller_email,g.caller_name,g.caller_admin,g.caller_provider,g.caller_external,g.caller_snapshot_digest,g.workflow_id,g.solution_install_id,g.source_kind,g.source_id,g.source_manifest_digest,g.source_resolution_digest,g.source_global_permission,g.source_digest,g.operations_digest,g.grant_digest,g.owner_incarnation_id,g.committed_start_id,g.start_message_id FROM jsonb_populate_record(NULL::workflow_runtime_sdk_grants,$1::jsonb) g")
        .bind(&raw).execute(&mut *locked.tx).await?;
    sqlx::query("INSERT INTO workflow_runtime_sdk_grant_operations (grant_id,ordinal,operation,integration_name,scope_kind,scope_organization_id,resolved_organization_id,solution_install_id) SELECT id,0,'integration-get',$2,'organization',effective_organization_id,effective_organization_id,solution_install_id FROM workflow_runtime_sdk_grants WHERE id=$1::text::uuid")
        .bind(grant_id).bind(&request.integration_name).execute(&mut *locked.tx).await?;
    // Recheck the fresh clock at birth; finite deadline is never moved forward.
    let born=sqlx::query("INSERT INTO runtime_admissions (id,purpose,session_id,committed_start_id,start_message_id,grant_id,delivery_id,operations_digest,expires_at,frontier_sha256,admitted_at) SELECT $2::text::uuid,'provision',runtime_session_id,committed_start_id,start_message_id,id,$3::text::uuid,operations_digest,initial_access_expires_at,$4,clock_timestamp() FROM workflow_runtime_sdk_grants WHERE id=$1::text::uuid AND clock_timestamp()<initial_access_expires_at RETURNING id")
        .bind(grant_id).bind(&request.provision_id).bind(&request.delivery_id).bind(&request.frontier_sha256)
        .fetch_optional(&mut *locked.tx).await?;
    if born.is_none() {
        return Err(ObserveError::Rejected);
    }
    locked
        .tx
        .commit()
        .await
        .map_err(|_| ObserveError::UncertainCommit)?;
    Ok(ProvisionDecision::NewlyCommitted)
}

// Fixed independent CRED-P1 grant field order, not table/serde iteration order.
const CHECK_SNAPSHOT: &str = r#"WITH g AS (SELECT * FROM jsonb_populate_record(NULL::workflow_runtime_sdk_grants,$1::jsonb))
 SELECT COALESCE(g.id=$2::text::uuid AND g.workflow_attempt_id=a.id
 AND g.execution_id=s.execution_id AND g.runtime_session_id=s.id
 AND g.owner_incarnation_id=s.owner_incarnation_id
 AND g.worker_incarnation_id=s.worker_incarnation_id
 AND g.supervisor_incarnation_id=s.supervisor_incarnation_id
 AND g.claim_token_digest=s.claim_token_digest AND g.attempt_number=a.attempt_number
 AND g.committed_start_id=st.id AND g.start_message_id=st.start_message_id
 AND g.started_at=st.started_at AND g.started_at<=g.issued_at AND g.issued_at<=clock_timestamp()
 AND g.timeout_seconds>0 AND g.timeout_seconds<=2147483647
 AND g.credential_deadline=st.deadline_utc AND g.initial_access_expires_at=st.deadline_utc
 AND st.deadline_utc<=st.started_at+g.timeout_seconds*interval '1 second'
 AND clock_timestamp()<g.initial_access_expires_at
 AND g.workflow_id=o.workflow_id AND g.source_id=o.deployment_id
 AND g.solution_install_id=d.solution_id AND g.source_manifest_digest=d.compiled_manifest_hash
 AND g.source_resolution_digest=d.resolution_map_hash AND g.source_global_permission=0
 AND g.schema_version='cred-p1/v1' AND g.source_kind='solution-deployment'
 AND g.source_digest=encode(sha256(convert_to('17:cred-p1/source/v1,' || '19:solution-deployment,' || (octet_length(g.source_id::text)::text || ':' || g.source_id::text || ',') || (octet_length(g.source_manifest_digest)::text || ':' || g.source_manifest_digest || ',') || (octet_length(g.source_resolution_digest)::text || ':' || g.source_resolution_digest || ',') || (octet_length(g.solution_install_id::text)::text || ':' || g.solution_install_id::text || ',') || '1:0,' || '1:0,','UTF8')),'hex')
 AND g.operations_digest=encode(sha256(convert_to('21:cred-p1/operations/v1,' || '1:1,' || (octet_length(('1:0,' || '15:integration-get,' || (octet_length($4::text)::text || ':' || $4::text || ',') || '12:organization,' || '1:1,' || (octet_length(g.effective_organization_id::text)::text || ':' || g.effective_organization_id::text || ',') || '1:1,' || (octet_length(g.effective_organization_id::text)::text || ':' || g.effective_organization_id::text || ',') || '1:1,' || (octet_length(g.solution_install_id::text)::text || ':' || g.solution_install_id::text || ',')))::text || ':' || ('1:0,' || '15:integration-get,' || (octet_length($4::text)::text || ':' || $4::text || ',') || '12:organization,' || '1:1,' || (octet_length(g.effective_organization_id::text)::text || ':' || g.effective_organization_id::text || ',') || '1:1,' || (octet_length(g.effective_organization_id::text)::text || ':' || g.effective_organization_id::text || ',') || '1:1,' || (octet_length(g.solution_install_id::text)::text || ':' || g.solution_install_id::text || ',')) || ','),'UTF8')),'hex')
 AND g.caller_snapshot_digest=o.caller_sha256
 AND g.caller_user_id::text=o.caller_snapshot->>'caller_user_id'
 AND g.caller_organization_id::text=o.caller_snapshot->>'caller_organization_id'
 AND g.effective_organization_id::text=o.caller_snapshot->>'effective_organization_id'
 AND g.effective_organization_id=g.caller_organization_id
 AND g.caller_email=o.caller_snapshot->>'caller_email'
 AND g.caller_name=o.caller_snapshot->>'caller_name'
 AND (g.caller_admin=1)::text=o.caller_snapshot->>'caller_admin'
 AND (g.caller_provider=1)::text=o.caller_snapshot->>'caller_provider'
 AND (g.caller_external=1)::text=o.caller_snapshot->>'caller_external'
 AND g.revoked_at IS NULL AND g.revocation_reason IS NULL
 AND g.grant_digest=encode(sha256(convert_to('16:cred-p1/grant/v1,' || octet_length(g.id::text)::text || ':' || g.id::text || ',' || octet_length(g.schema_version::text)::text || ':' || g.schema_version::text || ',' || octet_length(g.workflow_attempt_id::text)::text || ':' || g.workflow_attempt_id::text || ',' || octet_length(g.execution_id::text)::text || ':' || g.execution_id::text || ',' || octet_length(g.attempt_number::text)::text || ':' || g.attempt_number::text || ',' || octet_length(g.claim_token_digest::text)::text || ':' || g.claim_token_digest::text || ',' || octet_length(g.worker_incarnation_id::text)::text || ':' || g.worker_incarnation_id::text || ',' || octet_length(g.supervisor_incarnation_id::text)::text || ':' || g.supervisor_incarnation_id::text || ',' || octet_length(g.runtime_session_id::text)::text || ':' || g.runtime_session_id::text || ',' || octet_length((extract(epoch FROM g.started_at)*1000000)::bigint::text)::text || ':' || (extract(epoch FROM g.started_at)*1000000)::bigint::text || ',' || octet_length((extract(epoch FROM g.issued_at)*1000000)::bigint::text)::text || ':' || (extract(epoch FROM g.issued_at)*1000000)::bigint::text || ',' || octet_length(g.timeout_seconds::text)::text || ':' || g.timeout_seconds::text || ',' || '1:1,' || octet_length((extract(epoch FROM g.credential_deadline)*1000000)::bigint::text)::text || ':' || (extract(epoch FROM g.credential_deadline)*1000000)::bigint::text || ',' || octet_length((extract(epoch FROM g.initial_access_expires_at)*1000000)::bigint::text)::text || ':' || (extract(epoch FROM g.initial_access_expires_at)*1000000)::bigint::text || ',' || octet_length(g.caller_user_id::text)::text || ':' || g.caller_user_id::text || ',' || '1:1,' || octet_length(g.caller_organization_id::text)::text || ':' || g.caller_organization_id::text || ',' || '1:1,' || octet_length(g.effective_organization_id::text)::text || ':' || g.effective_organization_id::text || ',' || octet_length(g.caller_email::text)::text || ':' || g.caller_email::text || ',' || octet_length(g.caller_name::text)::text || ':' || g.caller_name::text || ',' || octet_length(g.caller_admin::text)::text || ':' || g.caller_admin::text || ',' || octet_length(g.caller_provider::text)::text || ':' || g.caller_provider::text || ',' || octet_length(g.caller_external::text)::text || ':' || g.caller_external::text || ',' || octet_length(g.caller_snapshot_digest::text)::text || ':' || g.caller_snapshot_digest::text || ',' || octet_length(g.workflow_id::text)::text || ':' || g.workflow_id::text || ',' || octet_length(g.solution_install_id::text)::text || ':' || g.solution_install_id::text || ',' || octet_length(g.source_kind::text)::text || ':' || g.source_kind::text || ',' || octet_length(g.source_id::text)::text || ':' || g.source_id::text || ',' || octet_length(g.source_manifest_digest::text)::text || ':' || g.source_manifest_digest::text || ',' || octet_length(g.source_resolution_digest::text)::text || ':' || g.source_resolution_digest::text || ',' || octet_length(g.source_global_permission::text)::text || ':' || g.source_global_permission::text || ',' || octet_length(g.source_digest::text)::text || ':' || g.source_digest::text || ',' || octet_length(g.operations_digest::text)::text || ':' || g.operations_digest::text || ',','UTF8')),'hex'),FALSE) AS valid
 FROM g JOIN runtime_sessions s ON s.id=$3::text::uuid
 JOIN runtime_starts st ON st.session_id=s.id
 JOIN workflow_execution_attempts a ON a.id=s.workflow_attempt_id
 JOIN runtime_execution_owners o ON o.execution_id=s.execution_id
 JOIN solution_deployments d ON d.id=o.deployment_id"#;

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_missing_fields_and_unbounded_or_unknown_private_data() {
        let mut request = ProvisionRequest {
            snapshot: serde_json::json!({}),
            grant_digest: "a".repeat(64),
            integration_name: "Fixture".into(),
            provision_id: "00000000-0000-0000-0000-000000000001".into(),
            delivery_id: "00000000-0000-0000-0000-000000000002".into(),
            frontier_sha256: "b".repeat(64),
        };
        assert!(!request_valid(&request));
        request.snapshot = Value::Object(
            SNAPSHOT_FIELDS
                .iter()
                .map(|field| ((*field).into(), Value::Null))
                .collect(),
        );
        assert!(!request_valid(&request));
        assert_eq!(SNAPSHOT_FIELDS.len(), 32);
        let mut sorted = SNAPSHOT_FIELDS.to_vec();
        sorted.sort();
        sorted.dedup();
        assert_eq!(sorted.len(), 32);
    }
}
