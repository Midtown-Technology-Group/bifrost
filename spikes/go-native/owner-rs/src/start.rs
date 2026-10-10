//! Isolated common Start candidate; no tenant process or SDK material handling.
use crate::{ObserveError, SessionFence, canonical_uuid, lock_session};
use bifrost_execution_wire_spike::Codec;
use serde_json::{Value, json};
use sqlx::{PgPool, Row};

pub struct StartCommit {
    body: Value,
}
impl StartCommit {
    /// Returned after observed commit only. This is not a physical launch permit.
    pub fn body(&self) -> &Value {
        &self.body
    }
}

/// Commit retained Start and the existing root/attempt Running transition
/// together. Caller must authenticate live Prepare/Prepared channel custody and
/// accepted staged artifact/build/source closure before calling this candidate.
/// Matching stored metadata alone cannot establish those external prerequisites.
/// No retry on ambiguous commit; retained Start must be independently observed.
pub async fn record_start_candidate(
    pool: &PgPool,
    fence: &SessionFence,
    prepare_payload: &[u8],
    prepared_payload: &[u8],
    start_id: &str,
    start_message_id: &str,
) -> Result<StartCommit, ObserveError> {
    if !canonical_uuid(start_id) || !canonical_uuid(start_message_id) {
        return Err(ObserveError::InvalidFence);
    }
    let codec = Codec::new().map_err(|_| ObserveError::Rejected)?;
    let prepare = codec
        .decode(prepare_payload)
        .map_err(|_| ObserveError::Rejected)?;
    let prepared = codec
        .decode(prepared_payload)
        .map_err(|_| ObserveError::Rejected)?;
    let p = &prepare.frame;
    let r = &prepared.frame;
    if p["type"] != "Prepare"
        || r["type"] != "Prepared"
        || p["session_id"] != fence.session_id
        || r["session_id"] != fence.session_id
        || r["body"]["prepare_message_id"] != p["message_id"]
        || r["body"]["artifact"] != p["body"]["artifact"]
        || p["body"]["workload"]["deadline_utc"].as_str().is_none()
        || p["message_id"] == start_message_id
        || r["message_id"] == start_message_id
    {
        return Err(ObserveError::Rejected);
    }
    let b = &p["body"]["binding"];
    for (key, expected) in [
        ("execution_id", &fence.execution_id),
        ("attempt_id", &fence.attempt_id),
        ("session_id", &fence.session_id),
        (
            "supervisor_incarnation_id",
            &fence.supervisor_incarnation_id,
        ),
        ("runtime_incarnation_id", &fence.runtime_incarnation_id),
    ] {
        if b[key] != *expected {
            return Err(ObserveError::Rejected);
        }
    }
    if b["execution_kind"] != "workflow" {
        return Err(ObserveError::Rejected);
    }
    if b["artifact_id"] != p["body"]["artifact"]["artifact_id"]
        || !context_matches_binding(&p["body"]["context"], b)
    {
        return Err(ObserveError::Rejected);
    }
    let mut locked = lock_session(pool, fence).await?;
    if locked.closed
        || locked.execution_status != "Pending"
        || locked.attempt_status != "claimed"
        || locked.attempt_completed
    {
        return Err(ObserveError::Rejected);
    }
    let identity = sqlx::query(
        "SELECT s.prepare_id::text AS prepare_id,s.prepare_sha256, \
         a.input_schema::text AS input_schema,a.artifact::text AS artifact, \
         e.parameters::text AS parameters,o.caller_snapshot::text AS caller_snapshot,wa.attempt_number, \
         o.deployment_id::text AS deployment_id,a.solution_id::text AS solution_id,o.artifact_id \
         FROM runtime_sessions s JOIN runtime_execution_owners o ON o.execution_id=s.execution_id \
         JOIN executions e ON e.id=o.execution_id \
         JOIN workflow_execution_attempts wa ON wa.id=s.workflow_attempt_id \
         JOIN runtime_deployment_artifacts a ON a.deployment_id=o.deployment_id \
         AND a.workflow_id=o.workflow_id AND a.artifact_id=o.artifact_id \
         WHERE s.id=$1::text::uuid",
    )
    .bind(&fence.session_id)
    .fetch_one(&mut *locked.tx)
    .await?;
    let parse = |key| -> Result<Value, ObserveError> {
        let raw: String = identity.try_get(key)?;
        serde_json::from_str(&raw).map_err(|_| ObserveError::Rejected)
    };
    let caller = parse("caller_snapshot")?;
    if b["effective_scope"]["kind"] != "organization"
        || b["original_caller"]["caller_id"] != caller["caller_user_id"]
        || b["original_caller"]["organization_id"] != caller["caller_organization_id"]
        || b["effective_scope"]["organization_id"] != caller["effective_organization_id"]
    {
        return Err(ObserveError::Rejected);
    }
    if p["message_id"] != identity.try_get::<String, _>("prepare_id")?
        || prepare.payload_sha256() != identity.try_get::<String, _>("prepare_sha256")?
        || p["body"]["artifact"] != parse("artifact")?
        || p["body"]["workload"]["input"] != parse("parameters")?
        || b["deployment_id"] != identity.try_get::<String, _>("deployment_id")?
        || b["solution_id"] != identity.try_get::<String, _>("solution_id")?
        || b["artifact_id"] != identity.try_get::<String, _>("artifact_id")?
        || b["attempt_number"].as_i64()
            != Some(i64::from(identity.try_get::<i32, _>("attempt_number")?))
        || !crate::schema::matches(&parse("input_schema")?, &p["body"]["workload"]["input"])
    {
        return Err(ObserveError::Rejected);
    }
    // Follow the shared session -> Start -> admission -> grant -> receipt order,
    // including rejection of any earlier Start/material/report on this session.
    for (table, column, order) in [
        ("runtime_starts", "session_id", "id"),
        ("runtime_admissions", "session_id", "purpose,id"),
        ("workflow_runtime_sdk_grants", "runtime_session_id", "id"),
        ("runtime_report_receipts", "session_id", "result_message_id"),
    ] {
        let query = format!(
            "SELECT 1 FROM {table} WHERE {column}=$1::text::uuid ORDER BY {order} FOR UPDATE"
        );
        if !sqlx::query(&query)
            .bind(&fence.session_id)
            .fetch_all(&mut *locked.tx)
            .await?
            .is_empty()
        {
            return Err(ObserveError::Rejected);
        }
    }
    let input = serde_json::to_string(&p["body"]["workload"]["input"])
        .map_err(|_| ObserveError::Rejected)?;
    let context =
        serde_json::to_string(&p["body"]["context"]).map_err(|_| ObserveError::Rejected)?;
    let deadline = p["body"]["workload"]["deadline_utc"]
        .as_str()
        .ok_or(ObserveError::Rejected)?;
    let started = sqlx::query(
        "INSERT INTO runtime_starts \
         (id,session_id,execution_id,owner_incarnation_id,workflow_attempt_id,start_message_id, \
         input_sha256,context_sha256,started_at,deadline_utc) \
         SELECT $1::text::uuid,$2::text::uuid,$3::text::uuid,$4::text::uuid,$5::text::uuid, \
         $6::text::uuid,encode(sha256(convert_to($7,'UTF8')),'hex'), \
         encode(sha256(convert_to($8,'UTF8')),'hex'),clock.now,$9::text::timestamptz \
         FROM (SELECT clock_timestamp() AS now) clock WHERE $9::text::timestamptz > clock.now \
         RETURNING floor(extract(epoch FROM deadline_utc-started_at)*1000)::bigint AS budget",
    )
    .bind(start_id)
    .bind(&fence.session_id)
    .bind(&fence.execution_id)
    .bind(&fence.owner_incarnation_id)
    .bind(&fence.attempt_id)
    .bind(start_message_id)
    .bind(input)
    .bind(context)
    .bind(deadline)
    .fetch_optional(&mut *locked.tx)
    .await?
    .ok_or(ObserveError::Rejected)?;
    let budget: i64 = started.try_get("budget")?;
    if budget <= 0 || budget > 9_007_199_254_740_991 {
        return Err(ObserveError::Rejected);
    }
    for query in [
        "UPDATE executions SET status='Running',started_at=(SELECT started_at FROM runtime_starts WHERE id=$2::text::uuid) WHERE id=$1::text::uuid AND status='Pending'",
        "UPDATE workflow_execution_attempts SET status='running',phase='execution',started_at=(SELECT started_at FROM runtime_starts WHERE id=$2::text::uuid),heartbeat_at=(SELECT started_at FROM runtime_starts WHERE id=$2::text::uuid) WHERE execution_id=$1::text::uuid AND id=$3::text::uuid AND status='claimed'",
    ] {
        let mut q = sqlx::query(query).bind(&fence.execution_id).bind(start_id);
        if query.contains("$3") {
            q = q.bind(&fence.attempt_id);
        }
        if q.execute(&mut *locked.tx).await?.rows_affected() != 1 {
            return Err(ObserveError::Rejected);
        }
    }
    locked
        .tx
        .commit()
        .await
        .map_err(|_| ObserveError::UncertainCommit)?;
    Ok(StartCommit {
        body: json!({"prepare_message_id":p["message_id"],"committed_start_id":start_id,"remaining_run_ms":budget}),
    })
}

fn context_matches_binding(context: &Value, binding: &Value) -> bool {
    [
        "execution_kind",
        "execution_id",
        "attempt_id",
        "attempt_number",
        "solution_id",
        "deployment_id",
        "artifact_id",
        "effective_scope",
    ]
    .into_iter()
    .all(|key| context[key] == binding[key])
        && context["caller_id"] == binding["original_caller"]["caller_id"]
}

#[cfg(test)]
mod tests {
    use super::context_matches_binding;
    use serde_json::json;

    #[test]
    fn tenant_context_cannot_change_any_prepared_identity_or_scope() {
        let binding = json!({"execution_kind":"workflow","execution_id":"execution",
            "attempt_id":"attempt","attempt_number":1,"solution_id":"solution",
            "deployment_id":"deployment","artifact_id":"artifact",
            "effective_scope":{"kind":"organization","organization_id":"org"},
            "original_caller":{"caller_id":"caller","organization_id":"org"}});
        let context = json!({"execution_kind":"workflow","execution_id":"execution",
            "attempt_id":"attempt","attempt_number":1,"solution_id":"solution",
            "deployment_id":"deployment","artifact_id":"artifact",
            "effective_scope":{"kind":"organization","organization_id":"org"},
            "caller_id":"caller"});
        assert!(context_matches_binding(&context, &binding));
        for key in [
            "execution_kind",
            "execution_id",
            "attempt_id",
            "attempt_number",
            "solution_id",
            "deployment_id",
            "artifact_id",
            "effective_scope",
            "caller_id",
        ] {
            let mut changed = context.clone();
            changed[key] = json!("different");
            assert!(!context_matches_binding(&changed, &binding));
        }
    }
}
