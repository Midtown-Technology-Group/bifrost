//! Isolated owner birth on existing domain tables, never production dispatch.
use crate::{ObserveError, SessionFence, canonical_uuid, schema};
use bifrost_execution_wire_spike::Codec;
use serde_json::Value;
use sqlx::{PgPool, Row};

// Shared static predicate; all request values remain bind parameters.
macro_rules! eligibility_sql { () => { r#"SELECT w.name,a.artifact::text AS artifact,a.input_schema::text AS input_schema,
 jsonb_build_object('caller_user_id',u.id::text,'caller_organization_id',u.organization_id::text,
 'effective_organization_id',u.organization_id::text,'caller_email',u.email,'caller_name',COALESCE(u.name,''),
 'caller_admin',u.is_superuser,'caller_provider',org.is_provider,
 'caller_external',u.is_external AND NOT (u.is_superuser OR org.is_provider),
 'roles',COALESCE((SELECT jsonb_agg(role_name ORDER BY role_name COLLATE "C") FROM
 (SELECT DISTINCT r.name AS role_name FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=u.id) names),'[]'::jsonb)) AS caller
 FROM workflows w JOIN users u ON u.id=$2::text::uuid
 JOIN organizations org ON org.id=u.organization_id
 JOIN runtime_deployment_artifacts a ON a.workflow_id=w.id AND a.deployment_id=$5::text::uuid
 WHERE w.id=$1::text::uuid AND w.solution_id=$3::text::uuid AND w.organization_id=$4::text::uuid
 AND u.organization_id=$4::text::uuid AND u.is_active AND w.is_active AND w.type='workflow'
 AND a.runtime_protocol='bifrost.runtime/v1' AND a.artifact->>'kind'='native-executable/v1'
 AND (u.is_superuser OR org.is_provider OR w.access_level='everyone'
 OR (w.access_level='authenticated' AND NOT u.is_external)
 OR EXISTS (SELECT 1 FROM workflow_roles wr JOIN user_roles ur ON ur.role_id=wr.role_id WHERE wr.workflow_id=w.id AND ur.user_id=u.id))"# }; }

pub struct AdmitRequest {
    pub workflow_id: String,
    /// Authenticated platform caller, supplied by trusted ingress, not input.
    pub caller_id: String,
    pub prepare_payload: Vec<u8>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum AdmitDecision {
    NewlyCommitted,
}

fn netstring(value: &str) -> String {
    format!("{}:{value},", value.len())
}

/// Trusted coordinator must first verify accepted staged bytes/build/source
/// closure and live channel custody. This transaction checks current retained
/// eligibility and creates root, owner, typed attempt and session atomically.
/// An existing execution is never adopted, cloned, reopened or reassigned.
/// Source retention uses the existing deployment FK and immutable association;
/// process/descendant/source drain acceptance remains a separate required gate.
pub async fn record_admit_candidate(
    pool: &PgPool,
    fence: &SessionFence,
    request: &AdmitRequest,
) -> Result<AdmitDecision, ObserveError> {
    if !fence.valid()
        || !canonical_uuid(&request.workflow_id)
        || !canonical_uuid(&request.caller_id)
    {
        return Err(ObserveError::InvalidFence);
    }
    let decoded = Codec::new()
        .map_err(|_| ObserveError::Rejected)?
        .decode(&request.prepare_payload)
        .map_err(|_| ObserveError::Rejected)?;
    let p = &decoded.frame;
    let b = &p["body"]["binding"];
    if p["type"] != "Prepare"
        || p["session_id"] != fence.session_id
        || b["execution_kind"] != "workflow"
        || b["attempt_number"] != 1
        || b["effective_scope"]["kind"] != "organization"
        || b["original_caller"]["caller_id"] != request.caller_id
        || !crate::start::context_matches_binding(&p["body"]["context"], b)
    {
        return Err(ObserveError::Rejected);
    }
    for (field, expected) in [
        ("execution_id", &fence.execution_id),
        ("attempt_id", &fence.attempt_id),
        ("session_id", &fence.session_id),
        (
            "supervisor_incarnation_id",
            &fence.supervisor_incarnation_id,
        ),
        ("runtime_incarnation_id", &fence.runtime_incarnation_id),
    ] {
        if b[field] != *expected {
            return Err(ObserveError::Rejected);
        }
    }
    let deployment = b["deployment_id"].as_str().ok_or(ObserveError::Rejected)?;
    let solution = b["solution_id"].as_str().ok_or(ObserveError::Rejected)?;
    let org = b["effective_scope"]["organization_id"]
        .as_str()
        .ok_or(ObserveError::Rejected)?;
    if b["original_caller"]["organization_id"] != org {
        return Err(ObserveError::Rejected);
    }
    let mut tx = pool.begin().await?;
    sqlx::query("SET LOCAL statement_timeout='3s'")
        .execute(&mut *tx)
        .await?;
    sqlx::query("SET LOCAL lock_timeout='1s'")
        .execute(&mut *tx)
        .await?;
    let login = sqlx::query("SELECT session_user::text AS login,current_user::text AS effective")
        .fetch_one(&mut *tx)
        .await?;
    if login.try_get::<String, _>("login")? != "wex_core"
        || login.try_get::<String, _>("effective")? != "wex_core"
    {
        return Err(ObserveError::Rejected);
    }
    sqlx::query("SELECT pg_advisory_xact_lock_shared(hashtext('bifrost:workspace-release'))")
        .execute(&mut *tx)
        .await?;
    // No attempt exists yet. New IDs cannot acquire or mutate an existing owner.
    for (query, id) in [
        (
            "SELECT id FROM workflow_execution_attempts WHERE id=$1::text::uuid FOR UPDATE",
            &fence.attempt_id,
        ),
        (
            "SELECT execution_id FROM runtime_execution_owners WHERE execution_id=$1::text::uuid FOR UPDATE NOWAIT",
            &fence.execution_id,
        ),
        (
            "SELECT id FROM executions WHERE id=$1::text::uuid FOR UPDATE NOWAIT",
            &fence.execution_id,
        ),
    ] {
        if sqlx::query(query)
            .bind(id)
            .fetch_optional(&mut *tx)
            .await?
            .is_some()
        {
            return Err(ObserveError::Rejected);
        }
    }
    let source=sqlx::query("SELECT state FROM solution_deployments WHERE id=$1::text::uuid AND solution_id=$2::text::uuid AND organization_id=$3::text::uuid FOR UPDATE NOWAIT")
        .bind(deployment).bind(solution).bind(org).fetch_optional(&mut *tx).await?.ok_or(ObserveError::Rejected)?;
    if !matches!(
        source.try_get::<String, _>("state")?.as_str(),
        "active" | "committed_unpushed"
    ) {
        return Err(ObserveError::Rejected);
    }
    let install=sqlx::query("SELECT id FROM solutions WHERE id=$1::text::uuid AND organization_id=$2::text::uuid AND status='active' AND active_deployment_id=$3::text::uuid AND execution_runtime_mode='deployment-v1' FOR UPDATE NOWAIT")
        .bind(solution).bind(org).bind(deployment).fetch_optional(&mut *tx).await?;
    if install.is_none() {
        return Err(ObserveError::Rejected);
    }
    let eligibility = sqlx::query(SELECT_ELIGIBILITY)
        .bind(&request.workflow_id)
        .bind(&request.caller_id)
        .bind(solution)
        .bind(org)
        .bind(deployment)
        .fetch_optional(&mut *tx)
        .await?
        .ok_or(ObserveError::Rejected)?;
    let artifact: Value = serde_json::from_str(&eligibility.try_get::<String, _>("artifact")?)
        .map_err(|_| ObserveError::Rejected)?;
    let input_schema: Value =
        serde_json::from_str(&eligibility.try_get::<String, _>("input_schema")?)
            .map_err(|_| ObserveError::Rejected)?;
    if artifact != p["body"]["artifact"]
        || b["artifact_id"] != artifact["artifact_id"]
        || !schema::matches(&input_schema, &p["body"]["workload"]["input"])
    {
        return Err(ObserveError::Rejected);
    }
    let caller: Value = serde_json::from_str(&eligibility.try_get::<String, _>("caller")?)
        .map_err(|_| ObserveError::Rejected)?;
    let roles = caller["roles"].as_array().ok_or(ObserveError::Rejected)?;
    if roles.len() > 256 {
        return Err(ObserveError::Rejected);
    }
    let mut preimage = netstring("cred-p1/caller/v1")
        + &netstring(&request.caller_id)
        + &netstring("1")
        + &netstring(org);
    for (key, limit) in [("caller_email", 320), ("caller_name", 255)] {
        let text = caller[key].as_str().ok_or(ObserveError::Rejected)?;
        if text.len() > limit || text.contains('\0') {
            return Err(ObserveError::Rejected);
        }
        preimage += &netstring(text);
    }
    for key in ["caller_admin", "caller_provider", "caller_external"] {
        let flag = caller[key].as_bool().ok_or(ObserveError::Rejected)?;
        preimage += &netstring(if flag { "1" } else { "0" });
    }
    preimage += &netstring(&roles.len().to_string());
    let mut previous: Option<&str> = None;
    for role in roles {
        let text = role.as_str().ok_or(ObserveError::Rejected)?;
        if text.len() > 255 || text.contains('\0') || previous.is_some_and(|value| value >= text) {
            return Err(ObserveError::Rejected);
        }
        preimage += &netstring(text);
        previous = Some(text);
    }
    let caller_digest: String =
        sqlx::query_scalar("SELECT encode(sha256(convert_to($1,'UTF8')),'hex')")
            .bind(preimage)
            .fetch_one(&mut *tx)
            .await?;
    // Re-read current authorization at the root INSERT. Caller/roles drift
    // between initial selection and birth denies, before any owner is assigned.
    let root = sqlx::query(INSERT_ROOT)
        .bind(&request.workflow_id)
        .bind(&request.caller_id)
        .bind(solution)
        .bind(org)
        .bind(deployment)
        .bind(&fence.execution_id)
        .bind(p["body"]["workload"]["input"].to_string())
        .bind(caller.to_string())
        .bind(
            p["body"]["workload"]["deadline_utc"]
                .as_str()
                .ok_or(ObserveError::Rejected)?,
        )
        .fetch_optional(&mut *tx)
        .await?;
    if root.is_none() {
        return Err(ObserveError::Rejected);
    }
    sqlx::query("INSERT INTO runtime_execution_owners (execution_id,owner_incarnation_id,workflow_id,deployment_id,artifact_id,caller_snapshot,caller_sha256) VALUES ($1::text::uuid,$2::text::uuid,$3::text::uuid,$4::text::uuid,$5,$6::jsonb,$7)")
        .bind(&fence.execution_id).bind(&fence.owner_incarnation_id).bind(&request.workflow_id).bind(deployment)
        .bind(artifact["artifact_id"].as_str().ok_or(ObserveError::Rejected)?).bind(caller.to_string()).bind(caller_digest)
        .execute(&mut *tx).await?;
    sqlx::query("INSERT INTO workflow_execution_attempts (id,execution_id,attempt_number,status,phase,claim_token,worker_incarnation_id,published_at,claimed_at,runtime_mode,isolated_owner) VALUES ($1::text::uuid,$2::text::uuid,1,'claimed','admission',$3::text::uuid,$4::text::uuid,clock_timestamp(),clock_timestamp(),'deployment-v1','coordinator')")
        .bind(&fence.attempt_id).bind(&fence.execution_id).bind(&fence.claim_token).bind(&fence.worker_incarnation_id).execute(&mut *tx).await?;
    sqlx::query("INSERT INTO runtime_sessions (id,execution_id,owner_incarnation_id,workflow_attempt_id,claim_token,worker_incarnation_id,supervisor_incarnation_id,runtime_incarnation_id,channel_custody_sha256,binding_sha256,prepare_id,prepare_sha256) VALUES ($1::text::uuid,$2::text::uuid,$3::text::uuid,$4::text::uuid,$5::text::uuid,$6::text::uuid,$7::text::uuid,$8::text::uuid,$9,$10,$11::text::uuid,$12)")
        .bind(&fence.session_id).bind(&fence.execution_id).bind(&fence.owner_incarnation_id).bind(&fence.attempt_id)
        .bind(&fence.claim_token).bind(&fence.worker_incarnation_id).bind(&fence.supervisor_incarnation_id).bind(&fence.runtime_incarnation_id)
        .bind(&fence.channel_custody_sha256).bind(&fence.binding_sha256).bind(p["message_id"].as_str().ok_or(ObserveError::Rejected)?)
        .bind(decoded.payload_sha256()).execute(&mut *tx).await?;
    tx.commit()
        .await
        .map_err(|_| ObserveError::UncertainCommit)?;
    Ok(AdmitDecision::NewlyCommitted)
}

pub(crate) const SELECT_ELIGIBILITY: &str = concat!(
    "SELECT name,artifact,input_schema,caller::text AS caller FROM (",
    eligibility_sql!(),
    ") eligible"
);
const INSERT_ROOT: &str = concat!(
    "WITH eligible AS (",
    eligibility_sql!(),
    ") INSERT INTO executions (id,workflow_id,solution_deployment_id,workflow_name,status,parameters,time_saved,value,executed_by,executed_by_name,organization_id,runtime_mode,attempt_tracking_version,isolated_owner) SELECT $6::text::uuid,$1::text::uuid,$5::text::uuid,name,'Pending',$7::jsonb,0,0,$2::text::uuid,caller->>'caller_name',$4::text::uuid,'deployment-v1','v1','coordinator' FROM eligible WHERE caller=$8::jsonb AND clock_timestamp()<$9::text::timestamptz RETURNING id"
);

#[cfg(test)]
mod tests {
    use super::netstring;
    #[test]
    fn caller_preimage_uses_utf8_byte_lengths() {
        assert_eq!(netstring("é"), "2:é,");
        assert_eq!(netstring(""), "0:,");
        assert_eq!(netstring("cred-p1/caller/v1"), "17:cred-p1/caller/v1,");
    }
}
