//! Original-guardian admission and Start integration, isolated dispatch only.
//! Source/build acceptance and caller authentication are parent prerequisites.
//! Finite issued-material release/delivery is an isolated candidate only.
//! No replay, replacement owner or production registration.

use crate::{
    AdmitRequest, FiniteIssuanceAdmission, IntegrationGetRequest, ObserveError, SessionFence,
    archive::sha256,
    authorize_finite_issuance_candidate, canonical_uuid,
    guardian::{Guardian, GuardianError, ReceivedFrame},
    record_admit_candidate, record_start_candidate,
};
use bifrost_execution_wire_spike::Codec;
use serde_json::{Value, json};
use sqlx::PgPool;
use std::time::{Duration, Instant};

pub struct BeginRequest {
    pub fence: SessionFence,
    pub workflow_id: String,
    pub caller_id: String,
    pub prepare: Value,
    pub select_message_id: String,
    pub start_id: String,
    pub start_message_id: String,
}

#[derive(Debug)]
pub enum BeginError {
    Rejected,
    Guardian(GuardianError),
    Owner(ObserveError),
    Issuer(crate::issuer::IssuerError),
    Material(crate::material::MaterialError),
}
impl From<GuardianError> for BeginError {
    fn from(error: GuardianError) -> Self {
        Self::Guardian(error)
    }
}
impl From<ObserveError> for BeginError {
    fn from(error: ObserveError) -> Self {
        Self::Owner(error)
    }
}

/// Process-local evidence, constructed only after observed Start commit and
/// one successful write on the original channel. It is not a launch permit or
/// portable/recoverable authorization, and intentionally cannot be cloned.
pub struct LiveStart {
    fence: SessionFence,
    prepare: Value,
    start: Value,
    issuance_attempted: bool,
    release_attempted: bool,
    used_message_ids: Vec<String>,
}
impl LiveStart {
    /// One process-local attempt on the original guardian. A failed/uncertain
    /// check cannot be retried or transferred to a recovered supervisor.
    /// This returns prerequisites only; signing and delivery still need their
    /// authenticated issuer and common release transaction.
    pub async fn authorize_issuance(
        &mut self,
        pool: &PgPool,
        guardian: &mut Guardian,
        request: &IntegrationGetRequest,
    ) -> Result<FiniteIssuanceAdmission, BeginError> {
        if self.issuance_attempted {
            return Err(BeginError::Rejected);
        }
        self.issuance_attempted = true;
        if guardian.verify_session(&self.fence)? != self.prepare["body"]["binding"] {
            return Err(BeginError::Rejected);
        }
        let admission = authorize_finite_issuance_candidate(pool, &self.fence, request).await?;
        if guardian.verify_session(&self.fence)? != self.prepare["body"]["binding"] {
            return Err(BeginError::Rejected);
        }
        Ok(admission)
    }

    /// Authenticate the original issuer, bind one finite response to the actual
    /// committed preimages, then recheck original guardian custody. A returned
    /// credential still cannot authorize release or delivery by itself.
    pub async fn issue_material(
        &mut self,
        pool: &PgPool,
        guardian: &mut Guardian,
        channel: &mut crate::issuer::IssuerChannel,
        request: &IntegrationGetRequest,
    ) -> Result<crate::issuer::IssuedMaterial, BeginError> {
        let admission = self.authorize_issuance(pool, guardian, request).await?;
        let material = channel.issue_once(&admission).map_err(BeginError::Issuer)?;
        if guardian.verify_session(&self.fence)? != self.prepare["body"]["binding"] {
            return Err(BeginError::Rejected);
        }
        Ok(material)
    }

    /// Consume actual issuer material and one release attempt. Only a newly
    /// observed common release commit reaches the original protocol/FIFO writes.
    /// Any failure leaves the original guardian/pipe with the caller for drain;
    /// no retained row or uncertain write may be replayed by this handle.
    pub async fn release_and_deliver(
        &mut self,
        pool: &PgPool,
        guardian: &mut Guardian,
        pipe: &mut crate::material::MaterialPipe,
        material: crate::issuer::IssuedMaterial,
        request: &crate::ReleaseRequest,
        provision_message_id: &str,
    ) -> Result<(), BeginError> {
        if self.release_attempted {
            return Err(BeginError::Rejected);
        }
        self.release_attempted = true;
        if !canonical_uuid(provision_message_id)
            || self
                .used_message_ids
                .iter()
                .any(|id| id == provision_message_id)
            || request.grant_id != material.grant_id()
            || request.operations_sha256 != material.operations_digest()
        {
            return Err(BeginError::Rejected);
        }
        if guardian.verify_session(&self.fence)? != self.prepare["body"]["binding"] {
            return Err(BeginError::Rejected);
        }
        guardian.verify_material(pipe)?;
        let provision = json!({"protocol":"bifrost.runtime/v1","type":"Provision",
            "session_id":self.fence.session_id,"message_id":provision_message_id,
            "sequence":4,"correlation_id":self.prepare["message_id"],
            "body":{"binding":self.prepare["body"]["binding"],
            "prepare_message_id":self.prepare["message_id"],
            "committed_start_id":self.start["body"]["committed_start_id"],
            "grant_id":material.grant_id(),"delivery_id":request.delivery_id,
            "expires_at":material.expires_at(),"capabilities":["integration-get"],
            "operations_digest":format!("sha256:{}", material.operations_digest())}});
        Codec::new()
            .map_err(|_| BeginError::Rejected)?
            .decode(&serde_json::to_vec(&provision).map_err(|_| BeginError::Rejected)?)
            .map_err(|_| BeginError::Rejected)?;
        let envelope = serde_json::to_vec(&json!({"version":"runtime-private-delivery/v1",
            "provision":provision,"sdk_configuration":material.configuration()}))
        .map_err(|_| BeginError::Rejected)?;
        if envelope.is_empty() || envelope.len() > crate::material::MAX_MATERIAL {
            return Err(BeginError::Rejected);
        }
        let committed =
            crate::record_issued_release_candidate(pool, &self.fence, request, &material).await?;
        if committed != crate::ReleaseCommitObservation::NewlyCommitted {
            return Err(BeginError::Rejected);
        }
        if guardian.verify_session(&self.fence)? != self.prepare["body"]["binding"] {
            return Err(BeginError::Rejected);
        }
        guardian.verify_material(pipe)?;
        guardian.send(&provision)?;
        pipe.deliver(&envelope).map_err(BeginError::Material)?;
        Ok(())
    }

    pub fn fence(&self) -> &SessionFence {
        &self.fence
    }
    pub fn prepare(&self) -> &Value {
        &self.prepare
    }
    pub fn start(&self) -> &Value {
        &self.start
    }
}

fn negotiated(offer: &Value, request: &BeginRequest) -> bool {
    let contains = |key: &str, item: &str| {
        offer["body"][key]
            .as_array()
            .is_some_and(|values| values.iter().any(|value| value == item))
    };
    let ids = [
        offer["message_id"].as_str().unwrap_or_default(),
        request.prepare["message_id"].as_str().unwrap_or_default(),
        &request.select_message_id,
        &request.start_message_id,
    ];
    offer["type"] == "Offer"
        && offer["session_id"] == request.fence.session_id
        && offer["sequence"] == 1
        && offer["correlation_id"].is_null()
        && offer["body"]["runtime_incarnation_id"] == request.fence.runtime_incarnation_id
        && contains("supported_protocols", "bifrost.runtime/v1")
        && contains("capabilities", "execution_profile/v1")
        && contains("artifact_classes", "native-executable/v1")
        && request.prepare["type"] == "Prepare"
        && request.prepare["session_id"] == request.fence.session_id
        && canonical_uuid(&request.start_id)
        && ids.iter().all(|id| canonical_uuid(id))
        && ids.iter().enumerate().all(|(i, id)| !ids[..i].contains(id))
}

fn matched_prepared(prepared: &Value, prepare: &Value, ids: &[&str]) -> bool {
    prepared["type"] == "Prepared"
        && prepared["session_id"] == prepare["session_id"]
        && prepared["sequence"] == 2
        && prepared["correlation_id"] == prepare["message_id"]
        && prepared["body"]["prepare_message_id"] == prepare["message_id"]
        && prepared["body"]["artifact"] == prepare["body"]["artifact"]
        && prepared["message_id"]
            .as_str()
            .is_some_and(|id| !ids.contains(&id))
}

/// Connect actual channel custody to the existing common owner transactions.
///
/// The parent has already verified accepted artifact bytes, source closure and
/// authenticated caller, staged the exact readonly bundle and attached this
/// original dormant guardian. It supplies its actual received Offer, not JSON
/// testimony about a runtime. The custody/binding digests are computed here;
/// supplied digest strings cannot qualify the session.
///
/// Every error leaves the borrowed guardian with its original owner for drain.
/// observe_live consumes its sole fresh observation even on later failure, so
/// another call cannot retry birth/Start or replay the write. Commit ambiguity
/// never reaches a send. Success still needs finite issuance/release, actual
/// SDK, Result/Receipt, cancellation and durable source settlement acceptance.
pub async fn admit_and_start(
    pool: &PgPool,
    guardian: &mut Guardian,
    offer: &ReceivedFrame,
    mut request: BeginRequest,
) -> Result<LiveStart, BeginError> {
    // Consume the original physical observation before any authority operation.
    let custody = guardian.observe_live(offer)?;
    if !negotiated(offer.frame(), &request) {
        return Err(BeginError::Rejected);
    }
    request.fence.channel_custody_sha256 = custody["channel_custody_sha256"]
        .as_str()
        .ok_or(BeginError::Rejected)?
        .into();
    request.fence.binding_sha256 = sha256(
        &serde_json::to_vec(&request.prepare["body"]["binding"])
            .map_err(|_| BeginError::Rejected)?,
    );
    if guardian.verify_session(&request.fence)? != request.prepare["body"]["binding"] {
        return Err(BeginError::Rejected);
    }
    let select = json!({"protocol":"bifrost.runtime/v1","type":"Select",
        "session_id":request.fence.session_id,"message_id":request.select_message_id,
        "sequence":1,"correlation_id":offer.frame()["message_id"],
        "body":{"protocol":"bifrost.runtime/v1","capability":"execution_profile/v1",
        "artifact_class":"native-executable/v1"}});
    request.prepare["sequence"] = json!(2);
    request.prepare["correlation_id"] = json!(request.select_message_id);
    let codec = Codec::new().map_err(|_| BeginError::Rejected)?;
    let prepare_payload = serde_json::to_vec(&request.prepare).map_err(|_| BeginError::Rejected)?;
    codec
        .decode(&prepare_payload)
        .map_err(|_| BeginError::Rejected)?;
    record_admit_candidate(
        pool,
        &request.fence,
        &AdmitRequest {
            workflow_id: request.workflow_id,
            caller_id: request.caller_id,
            prepare_payload: prepare_payload.clone(),
        },
    )
    .await?;
    guardian.verify_session(&request.fence)?;
    guardian.send(&select)?;
    guardian.send(&request.prepare)?;
    let prepared = guardian.receive(Duration::from_secs(5))?;
    if !matched_prepared(
        prepared.frame(),
        &request.prepare,
        &[
            offer.frame()["message_id"]
                .as_str()
                .ok_or(BeginError::Rejected)?,
            &request.select_message_id,
            request.prepare["message_id"]
                .as_str()
                .ok_or(BeginError::Rejected)?,
            &request.start_message_id,
        ],
    ) {
        return Err(BeginError::Rejected);
    }
    guardian.verify_session(&request.fence)?;
    let before_commit = Instant::now();
    let committed = record_start_candidate(
        pool,
        &request.fence,
        &prepare_payload,
        prepared.payload(),
        &request.start_id,
        &request.start_message_id,
    )
    .await?;
    guardian.verify_session(&request.fence)?;
    // Conservatively debit the whole transaction/check interval, including time
    // before its trusted clock observation. Never extend a returned run budget.
    let mut body = committed.body().clone();
    let remaining = debit_budget(
        body["remaining_run_ms"]
            .as_u64()
            .ok_or(BeginError::Rejected)?,
        before_commit.elapsed(),
    )?;
    body["remaining_run_ms"] = json!(remaining);
    let start = json!({"protocol":"bifrost.runtime/v1","type":"Start",
        "session_id":request.fence.session_id,"message_id":request.start_message_id,
        "sequence":3,"correlation_id":request.prepare["message_id"],"body":body});
    guardian.send(&start)?;
    let used_message_ids = [
        offer.frame()["message_id"]
            .as_str()
            .ok_or(BeginError::Rejected)?,
        &request.select_message_id,
        request.prepare["message_id"]
            .as_str()
            .ok_or(BeginError::Rejected)?,
        prepared.frame()["message_id"]
            .as_str()
            .ok_or(BeginError::Rejected)?,
        &request.start_message_id,
    ]
    .into_iter()
    .map(str::to_owned)
    .collect();
    Ok(LiveStart {
        fence: request.fence,
        prepare: request.prepare,
        start,
        issuance_attempted: false,
        release_attempted: false,
        used_message_ids,
    })
}

fn debit_budget(budget: u64, elapsed: Duration) -> Result<u64, BeginError> {
    let elapsed =
        u64::try_from(elapsed.as_nanos().div_ceil(1_000_000)).map_err(|_| BeginError::Rejected)?;
    budget
        .checked_sub(elapsed)
        .filter(|remaining| *remaining > 0)
        .ok_or(BeginError::Rejected)
}

#[cfg(test)]
mod tests {
    use super::{debit_budget, matched_prepared};
    use serde_json::json;
    use std::time::Duration;

    #[test]
    fn elapsed_owner_work_cannot_extend_or_reanimate_a_run_budget() {
        assert_eq!(
            debit_budget(100, Duration::from_micros(1001)).ok(),
            Some(98)
        );
        assert_eq!(debit_budget(100, Duration::ZERO).ok(), Some(100));
        assert!(debit_budget(1, Duration::from_nanos(1)).is_err());
        assert!(debit_budget(100, Duration::from_millis(100)).is_err());
        assert!(debit_budget(100, Duration::MAX).is_err());
    }

    #[test]
    fn prepared_cannot_acknowledge_another_session_message_or_artifact() {
        let prepare = json!({"session_id":"session","message_id":"prepare",
            "body":{"artifact":{"artifact_id":"accepted artifact"}}});
        let prepared = json!({"type":"Prepared","session_id":"session","sequence":2,
            "message_id":"prepared","correlation_id":"prepare",
            "body":{"prepare_message_id":"prepare","artifact":{"artifact_id":"accepted artifact"}}});
        assert!(matched_prepared(
            &prepared,
            &prepare,
            &["offer", "select", "prepare", "start"]
        ));
        for (key, changed) in [
            ("session_id", json!("other")),
            ("sequence", json!(3)),
            ("correlation_id", json!("other")),
            ("message_id", json!("start")),
            (
                "body",
                json!({"prepare_message_id":"other","artifact":{"artifact_id":"accepted artifact"}}),
            ),
            (
                "body",
                json!({"prepare_message_id":"prepare","artifact":{"artifact_id":"other"}}),
            ),
        ] {
            let mut changed_prepared = prepared.clone();
            changed_prepared[key] = changed;
            assert!(!matched_prepared(
                &changed_prepared,
                &prepare,
                &["offer", "select", "prepare", "start"]
            ));
        }
    }
}
