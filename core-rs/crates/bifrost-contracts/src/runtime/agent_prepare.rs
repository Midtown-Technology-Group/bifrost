//! Private mechanical agent facts; no source/provider/Start authority.
use super::{
    CanonicalUuid, Error, Frame, MAX_FRAME_BYTES, MAX_SAFE_INTEGER, PROTOCOL, Positive, UInt,
};
use serde::{Deserialize, Deserializer};
use serde_json::{Value, value::RawValue};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Read, Write},
};

pub const AGENT_PREPARE_PROFILE: &str = "agent_prepare_profile/v1";
trait Emit {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error>;
}
struct BoundedWriter<'a> {
    out: &'a mut Vec<u8>,
    overflow: bool,
}
impl Write for BoundedWriter<'_> {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > MAX_FRAME_BYTES.saturating_sub(self.out.len()) {
            self.overflow = true;
            return Err(std::io::Error::other("frame size bound"));
        }
        self.out.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
fn atom<T: serde::Serialize>(value: &T, out: &mut Vec<u8>) -> Result<(), Error> {
    let mut writer = BoundedWriter {
        out,
        overflow: false,
    };
    let result = serde_json::to_writer(&mut writer, value);
    if result.is_err() {
        return Err(if writer.overflow {
            Error::FrameTooLarge
        } else {
            Error::InvalidFrame
        });
    }
    Ok(())
}
impl Emit for String {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        atom(self, out)
    }
}
impl Emit for bool {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        atom(self, out)
    }
}
impl Emit for i64 {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        atom(self, out)
    }
}
impl Emit for CanonicalUuid {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        atom(self, out)
    }
}
impl Emit for Positive {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        atom(self, out)
    }
}
impl Emit for UInt {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        atom(self, out)
    }
}
impl<T: Emit> Emit for Option<T> {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        match self {
            Some(v) => v.emit(out),
            None => {
                out.extend_from_slice(b"null");
                Ok(())
            }
        }
    }
}
impl<T: Emit> Emit for Vec<T> {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'[');
        for (index, item) in self.iter().enumerate() {
            if index > 0 {
                out.push(b',');
            }
            item.emit(out)?;
        }
        out.push(b']');
        Ok(())
    }
}
impl<T: Emit> Emit for BTreeMap<String, T> {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        for (index, (key, value)) in self.iter().enumerate() {
            if index > 0 {
                out.push(b',');
            }
            atom(key, out)?;
            out.push(b':');
            value.emit(out)?;
        }
        out.push(b'}');
        Ok(())
    }
}

/// Opaque raw business JSON. No Debug, Display, Serialize or raw getter.
pub struct RawBusinessJson(Box<RawValue>);
fn deserialize_business<'de, D: Deserializer<'de>>(d: D) -> Result<RawBusinessJson, D::Error> {
    let raw = Box::<RawValue>::deserialize(d)?;
    let bytes = raw.get().as_bytes();
    if bytes.len() > MAX_FRAME_BYTES {
        return Err(serde::de::Error::custom("invalid business value"));
    }
    lexical_numbers(bytes).map_err(|_| serde::de::Error::custom("invalid business value"))?;
    let tree = super::decode_ordinary_json(bytes)
        .map_err(|_| serde::de::Error::custom("invalid business value"))?;
    validate(&tree, "business").map_err(|_| serde::de::Error::custom("invalid business value"))?;
    Ok(RawBusinessJson(raw))
}
fn deserialize_optional_business<'de, D: Deserializer<'de>>(
    d: D,
) -> Result<Option<RawBusinessJson>, D::Error> {
    let raw = Option::<Box<RawValue>>::deserialize(d)?;
    raw.map(|value| {
        AgentPrepareCodec::new()
            .parse_business_json(value.get().as_bytes())
            .map_err(|_| serde::de::Error::custom("invalid business value"))
    })
    .transpose()
}
impl Emit for RawBusinessJson {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        let bytes = self.0.get().as_bytes();
        if bytes.len() > MAX_FRAME_BYTES.saturating_sub(out.len()) {
            return Err(Error::FrameTooLarge);
        }
        out.extend_from_slice(bytes);
        Ok(())
    }
}
impl RawBusinessJson {
    pub fn kind(&self) -> &'static str {
        "object"
    }
}

pub struct RawBusinessNumber(Box<RawValue>);
fn deserialize_number<'de, D: Deserializer<'de>>(d: D) -> Result<RawBusinessNumber, D::Error> {
    let raw = Box::<RawValue>::deserialize(d)?;
    let bytes = raw.get().as_bytes();
    if bytes.len() > MAX_FRAME_BYTES {
        return Err(serde::de::Error::custom("invalid business value"));
    }
    lexical_numbers(bytes).map_err(|_| serde::de::Error::custom("invalid business value"))?;
    let tree = super::decode_ordinary_json(bytes)
        .map_err(|_| serde::de::Error::custom("invalid business value"))?;
    validate(&tree, "number").map_err(|_| serde::de::Error::custom("invalid business value"))?;
    Ok(RawBusinessNumber(raw))
}
impl Emit for RawBusinessNumber {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        let bytes = self.0.get().as_bytes();
        if bytes.len() > MAX_FRAME_BYTES.saturating_sub(out.len()) {
            return Err(Error::FrameTooLarge);
        }
        out.extend_from_slice(bytes);
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentLogical {
    kind: String,
    run_id: CanonicalUuid,
}
impl AgentLogical {
    pub fn kind(&self) -> &String {
        &self.kind
    }
    pub fn run_id(&self) -> &CanonicalUuid {
        &self.run_id
    }
}
impl Emit for AgentLogical {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"kind", out)?;
        out.push(b':');
        self.kind.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"run_id", out)?;
        out.push(b':');
        self.run_id.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentAttempt {
    kind: String,
    attempt_id: CanonicalUuid,
    attempt_number: Positive,
}
impl AgentAttempt {
    pub fn kind(&self) -> &String {
        &self.kind
    }
    pub fn attempt_id(&self) -> &CanonicalUuid {
        &self.attempt_id
    }
    pub fn attempt_number(&self) -> &Positive {
        &self.attempt_number
    }
}
impl Emit for AgentAttempt {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"kind", out)?;
        out.push(b':');
        self.kind.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"attempt_id", out)?;
        out.push(b':');
        self.attempt_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"attempt_number", out)?;
        out.push(b':');
        self.attempt_number.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentBinding {
    supervisor_incarnation_id: CanonicalUuid,
    session_id: CanonicalUuid,
    prepare_message_id: CanonicalUuid,
    process_identity: String,
    logical_job: AgentLogical,
    attempt: AgentAttempt,
    expected_artifact_id: String,
    expected_image_digest: Option<String>,
}
impl AgentBinding {
    pub fn supervisor_incarnation_id(&self) -> &CanonicalUuid {
        &self.supervisor_incarnation_id
    }
    pub fn session_id(&self) -> &CanonicalUuid {
        &self.session_id
    }
    pub fn prepare_message_id(&self) -> &CanonicalUuid {
        &self.prepare_message_id
    }
    pub fn process_identity(&self) -> &String {
        &self.process_identity
    }
    pub fn logical_job(&self) -> &AgentLogical {
        &self.logical_job
    }
    pub fn attempt(&self) -> &AgentAttempt {
        &self.attempt
    }
    pub fn expected_artifact_id(&self) -> &String {
        &self.expected_artifact_id
    }
    pub fn expected_image_digest(&self) -> &Option<String> {
        &self.expected_image_digest
    }
}
impl Emit for AgentBinding {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"supervisor_incarnation_id", out)?;
        out.push(b':');
        self.supervisor_incarnation_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"session_id", out)?;
        out.push(b':');
        self.session_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"prepare_message_id", out)?;
        out.push(b':');
        self.prepare_message_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"process_identity", out)?;
        out.push(b':');
        self.process_identity.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"logical_job", out)?;
        out.push(b':');
        self.logical_job.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"attempt", out)?;
        out.push(b':');
        self.attempt.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"expected_artifact_id", out)?;
        out.push(b':');
        self.expected_artifact_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"expected_image_digest", out)?;
        out.push(b':');
        self.expected_image_digest.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceBaseline {
    reference_source_main: String,
    authored_workspace_revision: String,
    selected_manifest_sha256: String,
    adapter_source_sha256: String,
    mechanical_contract_sha256: String,
}
impl SourceBaseline {
    pub fn reference_source_main(&self) -> &String {
        &self.reference_source_main
    }
    pub fn authored_workspace_revision(&self) -> &String {
        &self.authored_workspace_revision
    }
    pub fn selected_manifest_sha256(&self) -> &String {
        &self.selected_manifest_sha256
    }
    pub fn adapter_source_sha256(&self) -> &String {
        &self.adapter_source_sha256
    }
    pub fn mechanical_contract_sha256(&self) -> &String {
        &self.mechanical_contract_sha256
    }
}
impl Emit for SourceBaseline {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"reference_source_main", out)?;
        out.push(b':');
        self.reference_source_main.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"authored_workspace_revision", out)?;
        out.push(b':');
        self.authored_workspace_revision.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"selected_manifest_sha256", out)?;
        out.push(b':');
        self.selected_manifest_sha256.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"adapter_source_sha256", out)?;
        out.push(b':');
        self.adapter_source_sha256.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"mechanical_contract_sha256", out)?;
        out.push(b':');
        self.mechanical_contract_sha256.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerFacts {
    user_id: CanonicalUuid,
    email: String,
    name: String,
    organization_id: Option<CanonicalUuid>,
    is_superuser: bool,
    is_platform_admin: bool,
    is_external: bool,
    is_provider_org: bool,
    roles: Vec<String>,
    #[serde(default)]
    verified_role_ids: Option<Vec<CanonicalUuid>>,
}
impl CallerFacts {
    pub fn user_id(&self) -> &CanonicalUuid {
        &self.user_id
    }
    pub fn email(&self) -> &String {
        &self.email
    }
    pub fn name(&self) -> &String {
        &self.name
    }
    pub fn organization_id(&self) -> &Option<CanonicalUuid> {
        &self.organization_id
    }
    pub fn is_superuser(&self) -> &bool {
        &self.is_superuser
    }
    pub fn is_platform_admin(&self) -> &bool {
        &self.is_platform_admin
    }
    pub fn is_external(&self) -> &bool {
        &self.is_external
    }
    pub fn is_provider_org(&self) -> &bool {
        &self.is_provider_org
    }
    pub fn roles(&self) -> &Vec<String> {
        &self.roles
    }
    pub fn verified_role_ids(&self) -> &Option<Vec<CanonicalUuid>> {
        &self.verified_role_ids
    }
}
impl Emit for CallerFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"user_id", out)?;
        out.push(b':');
        self.user_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"email", out)?;
        out.push(b':');
        self.email.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"name", out)?;
        out.push(b':');
        self.name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"organization_id", out)?;
        out.push(b':');
        self.organization_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"is_superuser", out)?;
        out.push(b':');
        self.is_superuser.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"is_platform_admin", out)?;
        out.push(b':');
        self.is_platform_admin.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"is_external", out)?;
        out.push(b':');
        self.is_external.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"is_provider_org", out)?;
        out.push(b':');
        self.is_provider_org.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"roles", out)?;
        out.push(b':');
        self.roles.emit(out)?;
        if self.verified_role_ids.is_some() {
            if !first {
                out.push(b',');
            }
            first = false;
            atom(&"verified_role_ids", out)?;
            out.push(b':');
            self.verified_role_ids.emit(out)?;
        }
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EffectiveFacts {
    organization_id: Option<CanonicalUuid>,
    solution_id: Option<CanonicalUuid>,
    solution_install_id: Option<CanonicalUuid>,
    agent_id: CanonicalUuid,
    agent_run_id: CanonicalUuid,
    caller_user_id: Option<CanonicalUuid>,
    caller_email: Option<String>,
    caller_name: Option<String>,
    accounting_org_id: Option<CanonicalUuid>,
    trigger_type: String,
    trigger_source: Option<String>,
    event_delivery_id: Option<CanonicalUuid>,
    artifact_workspace_id: Option<String>,
}
impl EffectiveFacts {
    pub fn organization_id(&self) -> &Option<CanonicalUuid> {
        &self.organization_id
    }
    pub fn solution_id(&self) -> &Option<CanonicalUuid> {
        &self.solution_id
    }
    pub fn solution_install_id(&self) -> &Option<CanonicalUuid> {
        &self.solution_install_id
    }
    pub fn agent_id(&self) -> &CanonicalUuid {
        &self.agent_id
    }
    pub fn agent_run_id(&self) -> &CanonicalUuid {
        &self.agent_run_id
    }
    pub fn caller_user_id(&self) -> &Option<CanonicalUuid> {
        &self.caller_user_id
    }
    pub fn caller_email(&self) -> &Option<String> {
        &self.caller_email
    }
    pub fn caller_name(&self) -> &Option<String> {
        &self.caller_name
    }
    pub fn accounting_org_id(&self) -> &Option<CanonicalUuid> {
        &self.accounting_org_id
    }
    pub fn trigger_type(&self) -> &String {
        &self.trigger_type
    }
    pub fn trigger_source(&self) -> &Option<String> {
        &self.trigger_source
    }
    pub fn event_delivery_id(&self) -> &Option<CanonicalUuid> {
        &self.event_delivery_id
    }
    pub fn artifact_workspace_id(&self) -> &Option<String> {
        &self.artifact_workspace_id
    }
}
impl Emit for EffectiveFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"organization_id", out)?;
        out.push(b':');
        self.organization_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"solution_id", out)?;
        out.push(b':');
        self.solution_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"solution_install_id", out)?;
        out.push(b':');
        self.solution_install_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"agent_id", out)?;
        out.push(b':');
        self.agent_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"agent_run_id", out)?;
        out.push(b':');
        self.agent_run_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"caller_user_id", out)?;
        out.push(b':');
        self.caller_user_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"caller_email", out)?;
        out.push(b':');
        self.caller_email.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"caller_name", out)?;
        out.push(b':');
        self.caller_name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"accounting_org_id", out)?;
        out.push(b':');
        self.accounting_org_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"trigger_type", out)?;
        out.push(b':');
        self.trigger_type.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"trigger_source", out)?;
        out.push(b':');
        self.trigger_source.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"event_delivery_id", out)?;
        out.push(b':');
        self.event_delivery_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"artifact_workspace_id", out)?;
        out.push(b':');
        self.artifact_workspace_id.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdmissionFacts {
    admission_snapshot_id: CanonicalUuid,
    original_caller: Option<CallerFacts>,
    effective_context: EffectiveFacts,
    original_caller_provenance_sha256: String,
}
impl AdmissionFacts {
    pub fn admission_snapshot_id(&self) -> &CanonicalUuid {
        &self.admission_snapshot_id
    }
    pub fn original_caller(&self) -> &Option<CallerFacts> {
        &self.original_caller
    }
    pub fn effective_context(&self) -> &EffectiveFacts {
        &self.effective_context
    }
    pub fn original_caller_provenance_sha256(&self) -> &String {
        &self.original_caller_provenance_sha256
    }
}
impl Emit for AdmissionFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"admission_snapshot_id", out)?;
        out.push(b':');
        self.admission_snapshot_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"original_caller", out)?;
        out.push(b':');
        self.original_caller.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"effective_context", out)?;
        out.push(b':');
        self.effective_context.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"original_caller_provenance_sha256", out)?;
        out.push(b':');
        self.original_caller_provenance_sha256.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LimitFacts {
    configured_max_iterations: Positive,
    configured_max_token_budget: Positive,
    llm_max_tokens: Option<Positive>,
    parent_duration_seconds: Positive,
    parent_deadline_monotonic_ms: UInt,
}
impl LimitFacts {
    pub fn configured_max_iterations(&self) -> &Positive {
        &self.configured_max_iterations
    }
    pub fn configured_max_token_budget(&self) -> &Positive {
        &self.configured_max_token_budget
    }
    pub fn llm_max_tokens(&self) -> &Option<Positive> {
        &self.llm_max_tokens
    }
    pub fn parent_duration_seconds(&self) -> &Positive {
        &self.parent_duration_seconds
    }
    pub fn parent_deadline_monotonic_ms(&self) -> &UInt {
        &self.parent_deadline_monotonic_ms
    }
}
impl Emit for LimitFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"configured_max_iterations", out)?;
        out.push(b':');
        self.configured_max_iterations.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"configured_max_token_budget", out)?;
        out.push(b':');
        self.configured_max_token_budget.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"llm_max_tokens", out)?;
        out.push(b':');
        self.llm_max_tokens.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"parent_duration_seconds", out)?;
        out.push(b':');
        self.parent_duration_seconds.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"parent_deadline_monotonic_ms", out)?;
        out.push(b':');
        self.parent_deadline_monotonic_ms.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentFacts {
    id: CanonicalUuid,
    name: String,
    is_active: bool,
    organization_id: Option<CanonicalUuid>,
}
impl AgentFacts {
    pub fn id(&self) -> &CanonicalUuid {
        &self.id
    }
    pub fn name(&self) -> &String {
        &self.name
    }
    pub fn is_active(&self) -> &bool {
        &self.is_active
    }
    pub fn organization_id(&self) -> &Option<CanonicalUuid> {
        &self.organization_id
    }
}
impl Emit for AgentFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"id", out)?;
        out.push(b':');
        self.id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"name", out)?;
        out.push(b':');
        self.name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"is_active", out)?;
        out.push(b':');
        self.is_active.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"organization_id", out)?;
        out.push(b':');
        self.organization_id.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromptFacts {
    system_prompt: String,
    #[serde(deserialize_with = "deserialize_optional_business")]
    input_data: Option<RawBusinessJson>,
    #[serde(deserialize_with = "deserialize_optional_business")]
    output_schema: Option<RawBusinessJson>,
}
impl PromptFacts {
    pub fn system_prompt(&self) -> &String {
        &self.system_prompt
    }
    pub fn input_data(&self) -> &Option<RawBusinessJson> {
        &self.input_data
    }
    pub fn output_schema(&self) -> &Option<RawBusinessJson> {
        &self.output_schema
    }
}
impl Emit for PromptFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"system_prompt", out)?;
        out.push(b':');
        self.system_prompt.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"input_data", out)?;
        out.push(b':');
        self.input_data.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"output_schema", out)?;
        out.push(b':');
        self.output_schema.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolFacts {
    name: String,
    description: String,
    #[serde(deserialize_with = "deserialize_business")]
    parameters: RawBusinessJson,
    workflow_id: CanonicalUuid,
}
impl ToolFacts {
    pub fn name(&self) -> &String {
        &self.name
    }
    pub fn description(&self) -> &String {
        &self.description
    }
    pub fn parameters(&self) -> &RawBusinessJson {
        &self.parameters
    }
    pub fn workflow_id(&self) -> &CanonicalUuid {
        &self.workflow_id
    }
}
impl Emit for ToolFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"name", out)?;
        out.push(b':');
        self.name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"description", out)?;
        out.push(b':');
        self.description.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"parameters", out)?;
        out.push(b':');
        self.parameters.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_id", out)?;
        out.push(b':');
        self.workflow_id.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelFacts {
    slot: Positive,
    profile_id: CanonicalUuid,
    provider: String,
    model: String,
    provider_connection_id: Option<CanonicalUuid>,
    default_max_tokens: Option<Positive>,
    anthropic_prompt_cache_supported: Option<bool>,
    openai_transport: Option<String>,
}
impl ModelFacts {
    pub fn slot(&self) -> &Positive {
        &self.slot
    }
    pub fn profile_id(&self) -> &CanonicalUuid {
        &self.profile_id
    }
    pub fn provider(&self) -> &String {
        &self.provider
    }
    pub fn model(&self) -> &String {
        &self.model
    }
    pub fn provider_connection_id(&self) -> &Option<CanonicalUuid> {
        &self.provider_connection_id
    }
    pub fn default_max_tokens(&self) -> &Option<Positive> {
        &self.default_max_tokens
    }
    pub fn anthropic_prompt_cache_supported(&self) -> &Option<bool> {
        &self.anthropic_prompt_cache_supported
    }
    pub fn openai_transport(&self) -> &Option<String> {
        &self.openai_transport
    }
}
impl Emit for ModelFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"slot", out)?;
        out.push(b':');
        self.slot.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"profile_id", out)?;
        out.push(b':');
        self.profile_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"provider", out)?;
        out.push(b':');
        self.provider.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"model", out)?;
        out.push(b':');
        self.model.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"provider_connection_id", out)?;
        out.push(b':');
        self.provider_connection_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"default_max_tokens", out)?;
        out.push(b':');
        self.default_max_tokens.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"anthropic_prompt_cache_supported", out)?;
        out.push(b':');
        self.anthropic_prompt_cache_supported.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"openai_transport", out)?;
        out.push(b':');
        self.openai_transport.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EntrypointFacts {
    kind: String,
    adapter_module: String,
    adapter_function: String,
    registered_agent_id: CanonicalUuid,
}
impl EntrypointFacts {
    pub fn kind(&self) -> &String {
        &self.kind
    }
    pub fn adapter_module(&self) -> &String {
        &self.adapter_module
    }
    pub fn adapter_function(&self) -> &String {
        &self.adapter_function
    }
    pub fn registered_agent_id(&self) -> &CanonicalUuid {
        &self.registered_agent_id
    }
}
impl Emit for EntrypointFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"kind", out)?;
        out.push(b':');
        self.kind.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"adapter_module", out)?;
        out.push(b':');
        self.adapter_module.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"adapter_function", out)?;
        out.push(b':');
        self.adapter_function.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"registered_agent_id", out)?;
        out.push(b':');
        self.registered_agent_id.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StagedEntry {
    entry_id: String,
    path: String,
    kind: String,
    expected_byte_length: UInt,
    expected_digest: String,
}
impl StagedEntry {
    pub fn entry_id(&self) -> &String {
        &self.entry_id
    }
    pub fn path(&self) -> &String {
        &self.path
    }
    pub fn kind(&self) -> &String {
        &self.kind
    }
    pub fn expected_byte_length(&self) -> &UInt {
        &self.expected_byte_length
    }
    pub fn expected_digest(&self) -> &String {
        &self.expected_digest
    }
}
impl Emit for StagedEntry {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"entry_id", out)?;
        out.push(b':');
        self.entry_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"path", out)?;
        out.push(b':');
        self.path.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"kind", out)?;
        out.push(b':');
        self.kind.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"expected_byte_length", out)?;
        out.push(b':');
        self.expected_byte_length.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"expected_digest", out)?;
        out.push(b':');
        self.expected_digest.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExpectedNamespace {
    module_name: String,
    kind: String,
    staged_paths: Vec<String>,
    entry_ids: Vec<String>,
    expected_origins: Vec<String>,
}
impl ExpectedNamespace {
    pub fn module_name(&self) -> &String {
        &self.module_name
    }
    pub fn kind(&self) -> &String {
        &self.kind
    }
    pub fn staged_paths(&self) -> &Vec<String> {
        &self.staged_paths
    }
    pub fn entry_ids(&self) -> &Vec<String> {
        &self.entry_ids
    }
    pub fn expected_origins(&self) -> &Vec<String> {
        &self.expected_origins
    }
}
impl Emit for ExpectedNamespace {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"module_name", out)?;
        out.push(b':');
        self.module_name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"kind", out)?;
        out.push(b':');
        self.kind.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"staged_paths", out)?;
        out.push(b':');
        self.staged_paths.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"entry_ids", out)?;
        out.push(b':');
        self.entry_ids.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"expected_origins", out)?;
        out.push(b':');
        self.expected_origins.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareInterpreter {
    implementation: String,
    version: String,
}
impl PrepareInterpreter {
    pub fn implementation(&self) -> &String {
        &self.implementation
    }
    pub fn version(&self) -> &String {
        &self.version
    }
}
impl Emit for PrepareInterpreter {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"implementation", out)?;
        out.push(b':');
        self.implementation.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"version", out)?;
        out.push(b':');
        self.version.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareSdk {
    distribution: Option<String>,
    version: Option<String>,
}
impl PrepareSdk {
    pub fn distribution(&self) -> &Option<String> {
        &self.distribution
    }
    pub fn version(&self) -> &Option<String> {
        &self.version
    }
}
impl Emit for PrepareSdk {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"distribution", out)?;
        out.push(b':');
        self.distribution.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"version", out)?;
        out.push(b':');
        self.version.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareArtifact {
    artifact_id: String,
    image_digest: Option<String>,
    interpreter: PrepareInterpreter,
    sdk: PrepareSdk,
    requirements_lock_sha256: Option<String>,
    runtime_protocol: String,
}
impl PrepareArtifact {
    pub fn artifact_id(&self) -> &String {
        &self.artifact_id
    }
    pub fn image_digest(&self) -> &Option<String> {
        &self.image_digest
    }
    pub fn interpreter(&self) -> &PrepareInterpreter {
        &self.interpreter
    }
    pub fn sdk(&self) -> &PrepareSdk {
        &self.sdk
    }
    pub fn requirements_lock_sha256(&self) -> &Option<String> {
        &self.requirements_lock_sha256
    }
    pub fn runtime_protocol(&self) -> &String {
        &self.runtime_protocol
    }
}
impl Emit for PrepareArtifact {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"artifact_id", out)?;
        out.push(b':');
        self.artifact_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"image_digest", out)?;
        out.push(b':');
        self.image_digest.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"interpreter", out)?;
        out.push(b':');
        self.interpreter.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"sdk", out)?;
        out.push(b':');
        self.sdk.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"requirements_lock_sha256", out)?;
        out.push(b':');
        self.requirements_lock_sha256.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"runtime_protocol", out)?;
        out.push(b':');
        self.runtime_protocol.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SolutionEvidence {
    solution_id: CanonicalUuid,
    solution_deployment_id: CanonicalUuid,
    bundle_hash: String,
    compiled_manifest_hash: String,
    workflow_source_hash: String,
    git_commit_sha: Option<String>,
    workflow_organization_id: Option<String>,
    runtime_storage_prefix: String,
    workflow_portable_ref: String,
    workflow_name: String,
    workflow_function_name: String,
    workflow_path: String,
    workflow_execution_mode: String,
    workflow_type: String,
    workflow_timeout_seconds: i64,
    workflow_time_saved: i64,
    workflow_cache_ttl_seconds: i64,
    #[serde(deserialize_with = "deserialize_number")]
    workflow_value: RawBusinessNumber,
    solution_global_repo_access: bool,
    deployment_source_hashes: BTreeMap<String, String>,
    #[serde(default)]
    workflow_runtime_bounds: Option<BTreeMap<String, Positive>>,
    #[serde(default)]
    #[serde(deserialize_with = "deserialize_optional_business")]
    workflow_parameters_schema: Option<RawBusinessJson>,
}
impl SolutionEvidence {
    pub fn solution_id(&self) -> &CanonicalUuid {
        &self.solution_id
    }
    pub fn solution_deployment_id(&self) -> &CanonicalUuid {
        &self.solution_deployment_id
    }
    pub fn bundle_hash(&self) -> &String {
        &self.bundle_hash
    }
    pub fn compiled_manifest_hash(&self) -> &String {
        &self.compiled_manifest_hash
    }
    pub fn workflow_source_hash(&self) -> &String {
        &self.workflow_source_hash
    }
    pub fn git_commit_sha(&self) -> &Option<String> {
        &self.git_commit_sha
    }
    pub fn workflow_organization_id(&self) -> &Option<String> {
        &self.workflow_organization_id
    }
    pub fn runtime_storage_prefix(&self) -> &String {
        &self.runtime_storage_prefix
    }
    pub fn workflow_portable_ref(&self) -> &String {
        &self.workflow_portable_ref
    }
    pub fn workflow_name(&self) -> &String {
        &self.workflow_name
    }
    pub fn workflow_function_name(&self) -> &String {
        &self.workflow_function_name
    }
    pub fn workflow_path(&self) -> &String {
        &self.workflow_path
    }
    pub fn workflow_execution_mode(&self) -> &String {
        &self.workflow_execution_mode
    }
    pub fn workflow_type(&self) -> &String {
        &self.workflow_type
    }
    pub fn workflow_timeout_seconds(&self) -> &i64 {
        &self.workflow_timeout_seconds
    }
    pub fn workflow_time_saved(&self) -> &i64 {
        &self.workflow_time_saved
    }
    pub fn workflow_cache_ttl_seconds(&self) -> &i64 {
        &self.workflow_cache_ttl_seconds
    }
    pub fn workflow_value(&self) -> &RawBusinessNumber {
        &self.workflow_value
    }
    pub fn solution_global_repo_access(&self) -> &bool {
        &self.solution_global_repo_access
    }
    pub fn deployment_source_hashes(&self) -> &BTreeMap<String, String> {
        &self.deployment_source_hashes
    }
    pub fn workflow_runtime_bounds(&self) -> &Option<BTreeMap<String, Positive>> {
        &self.workflow_runtime_bounds
    }
    pub fn workflow_parameters_schema(&self) -> &Option<RawBusinessJson> {
        &self.workflow_parameters_schema
    }
}
impl Emit for SolutionEvidence {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"solution_id", out)?;
        out.push(b':');
        self.solution_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"solution_deployment_id", out)?;
        out.push(b':');
        self.solution_deployment_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"bundle_hash", out)?;
        out.push(b':');
        self.bundle_hash.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"compiled_manifest_hash", out)?;
        out.push(b':');
        self.compiled_manifest_hash.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_source_hash", out)?;
        out.push(b':');
        self.workflow_source_hash.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"git_commit_sha", out)?;
        out.push(b':');
        self.git_commit_sha.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_organization_id", out)?;
        out.push(b':');
        self.workflow_organization_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"runtime_storage_prefix", out)?;
        out.push(b':');
        self.runtime_storage_prefix.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_portable_ref", out)?;
        out.push(b':');
        self.workflow_portable_ref.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_name", out)?;
        out.push(b':');
        self.workflow_name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_function_name", out)?;
        out.push(b':');
        self.workflow_function_name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_path", out)?;
        out.push(b':');
        self.workflow_path.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_execution_mode", out)?;
        out.push(b':');
        self.workflow_execution_mode.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_type", out)?;
        out.push(b':');
        self.workflow_type.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_timeout_seconds", out)?;
        out.push(b':');
        self.workflow_timeout_seconds.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_time_saved", out)?;
        out.push(b':');
        self.workflow_time_saved.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_cache_ttl_seconds", out)?;
        out.push(b':');
        self.workflow_cache_ttl_seconds.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_value", out)?;
        out.push(b':');
        self.workflow_value.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"solution_global_repo_access", out)?;
        out.push(b':');
        self.solution_global_repo_access.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"deployment_source_hashes", out)?;
        out.push(b':');
        self.deployment_source_hashes.emit(out)?;
        if self.workflow_runtime_bounds.is_some() {
            if !first {
                out.push(b',');
            }
            first = false;
            atom(&"workflow_runtime_bounds", out)?;
            out.push(b':');
            self.workflow_runtime_bounds.emit(out)?;
        }
        if self.workflow_parameters_schema.is_some() {
            if !first {
                out.push(b',');
            }
            first = false;
            atom(&"workflow_parameters_schema", out)?;
            out.push(b':');
            self.workflow_parameters_schema.emit(out)?;
        }
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceEvidence {
    schema_version: String,
    workspace_release_row_id: CanonicalUuid,
    workspace_release_artifact_id: CanonicalUuid,
    workflow_id: CanonicalUuid,
    workspace_release_id: String,
    workspace_release_effective_manifest_id: String,
    workspace_release_governed_manifest_id: String,
    workspace_release_registration_manifest_id: String,
    workspace_release_runtime_storage_prefix: String,
    workspace_release_source_commit_sha: String,
    workspace_release_source_tree_sha: String,
    workspace_release_registration_state_fingerprint: String,
    workflow_name: String,
    workflow_function_name: String,
    workflow_path: String,
    workflow_source_hash: String,
    workflow_execution_mode: String,
    workflow_type: String,
    workspace_release_source_hashes: BTreeMap<String, String>,
    workflow_runtime_bounds: BTreeMap<String, Positive>,
    workflow_timeout_seconds: i64,
    workflow_time_saved: i64,
    workflow_cache_ttl_seconds: i64,
    #[serde(deserialize_with = "deserialize_number")]
    workflow_value: RawBusinessNumber,
    workflow_organization_id: Option<String>,
}
impl WorkspaceEvidence {
    pub fn schema_version(&self) -> &String {
        &self.schema_version
    }
    pub fn workspace_release_row_id(&self) -> &CanonicalUuid {
        &self.workspace_release_row_id
    }
    pub fn workspace_release_artifact_id(&self) -> &CanonicalUuid {
        &self.workspace_release_artifact_id
    }
    pub fn workflow_id(&self) -> &CanonicalUuid {
        &self.workflow_id
    }
    pub fn workspace_release_id(&self) -> &String {
        &self.workspace_release_id
    }
    pub fn workspace_release_effective_manifest_id(&self) -> &String {
        &self.workspace_release_effective_manifest_id
    }
    pub fn workspace_release_governed_manifest_id(&self) -> &String {
        &self.workspace_release_governed_manifest_id
    }
    pub fn workspace_release_registration_manifest_id(&self) -> &String {
        &self.workspace_release_registration_manifest_id
    }
    pub fn workspace_release_runtime_storage_prefix(&self) -> &String {
        &self.workspace_release_runtime_storage_prefix
    }
    pub fn workspace_release_source_commit_sha(&self) -> &String {
        &self.workspace_release_source_commit_sha
    }
    pub fn workspace_release_source_tree_sha(&self) -> &String {
        &self.workspace_release_source_tree_sha
    }
    pub fn workspace_release_registration_state_fingerprint(&self) -> &String {
        &self.workspace_release_registration_state_fingerprint
    }
    pub fn workflow_name(&self) -> &String {
        &self.workflow_name
    }
    pub fn workflow_function_name(&self) -> &String {
        &self.workflow_function_name
    }
    pub fn workflow_path(&self) -> &String {
        &self.workflow_path
    }
    pub fn workflow_source_hash(&self) -> &String {
        &self.workflow_source_hash
    }
    pub fn workflow_execution_mode(&self) -> &String {
        &self.workflow_execution_mode
    }
    pub fn workflow_type(&self) -> &String {
        &self.workflow_type
    }
    pub fn workspace_release_source_hashes(&self) -> &BTreeMap<String, String> {
        &self.workspace_release_source_hashes
    }
    pub fn workflow_runtime_bounds(&self) -> &BTreeMap<String, Positive> {
        &self.workflow_runtime_bounds
    }
    pub fn workflow_timeout_seconds(&self) -> &i64 {
        &self.workflow_timeout_seconds
    }
    pub fn workflow_time_saved(&self) -> &i64 {
        &self.workflow_time_saved
    }
    pub fn workflow_cache_ttl_seconds(&self) -> &i64 {
        &self.workflow_cache_ttl_seconds
    }
    pub fn workflow_value(&self) -> &RawBusinessNumber {
        &self.workflow_value
    }
    pub fn workflow_organization_id(&self) -> &Option<String> {
        &self.workflow_organization_id
    }
}
impl Emit for WorkspaceEvidence {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"schema_version", out)?;
        out.push(b':');
        self.schema_version.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_row_id", out)?;
        out.push(b':');
        self.workspace_release_row_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_artifact_id", out)?;
        out.push(b':');
        self.workspace_release_artifact_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_id", out)?;
        out.push(b':');
        self.workflow_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_id", out)?;
        out.push(b':');
        self.workspace_release_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_effective_manifest_id", out)?;
        out.push(b':');
        self.workspace_release_effective_manifest_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_governed_manifest_id", out)?;
        out.push(b':');
        self.workspace_release_governed_manifest_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_registration_manifest_id", out)?;
        out.push(b':');
        self.workspace_release_registration_manifest_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_runtime_storage_prefix", out)?;
        out.push(b':');
        self.workspace_release_runtime_storage_prefix.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_source_commit_sha", out)?;
        out.push(b':');
        self.workspace_release_source_commit_sha.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_source_tree_sha", out)?;
        out.push(b':');
        self.workspace_release_source_tree_sha.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_registration_state_fingerprint", out)?;
        out.push(b':');
        self.workspace_release_registration_state_fingerprint
            .emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_name", out)?;
        out.push(b':');
        self.workflow_name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_function_name", out)?;
        out.push(b':');
        self.workflow_function_name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_path", out)?;
        out.push(b':');
        self.workflow_path.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_source_hash", out)?;
        out.push(b':');
        self.workflow_source_hash.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_execution_mode", out)?;
        out.push(b':');
        self.workflow_execution_mode.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_type", out)?;
        out.push(b':');
        self.workflow_type.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workspace_release_source_hashes", out)?;
        out.push(b':');
        self.workspace_release_source_hashes.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_runtime_bounds", out)?;
        out.push(b':');
        self.workflow_runtime_bounds.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_timeout_seconds", out)?;
        out.push(b':');
        self.workflow_timeout_seconds.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_time_saved", out)?;
        out.push(b':');
        self.workflow_time_saved.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_cache_ttl_seconds", out)?;
        out.push(b':');
        self.workflow_cache_ttl_seconds.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_value", out)?;
        out.push(b':');
        self.workflow_value.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"workflow_organization_id", out)?;
        out.push(b':');
        self.workflow_organization_id.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ParentAgentEvidence {
    admission_snapshot_id: CanonicalUuid,
    agent_id: CanonicalUuid,
    authored_agent_manifest_sha256: String,
    source_observation_id: CanonicalUuid,
}
impl ParentAgentEvidence {
    pub fn admission_snapshot_id(&self) -> &CanonicalUuid {
        &self.admission_snapshot_id
    }
    pub fn agent_id(&self) -> &CanonicalUuid {
        &self.agent_id
    }
    pub fn authored_agent_manifest_sha256(&self) -> &String {
        &self.authored_agent_manifest_sha256
    }
    pub fn source_observation_id(&self) -> &CanonicalUuid {
        &self.source_observation_id
    }
}
impl Emit for ParentAgentEvidence {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"admission_snapshot_id", out)?;
        out.push(b':');
        self.admission_snapshot_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"agent_id", out)?;
        out.push(b':');
        self.agent_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"authored_agent_manifest_sha256", out)?;
        out.push(b':');
        self.authored_agent_manifest_sha256.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"source_observation_id", out)?;
        out.push(b':');
        self.source_observation_id.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StagedClosure {
    entrypoint: EntrypointFacts,
    execution_evidence: ExecutionEvidence,
    entries: Vec<StagedEntry>,
    namespace: Vec<ExpectedNamespace>,
    runtime_expected: PrepareArtifact,
}
impl StagedClosure {
    pub fn entrypoint(&self) -> &EntrypointFacts {
        &self.entrypoint
    }
    pub fn execution_evidence(&self) -> &ExecutionEvidence {
        &self.execution_evidence
    }
    pub fn entries(&self) -> &Vec<StagedEntry> {
        &self.entries
    }
    pub fn namespace(&self) -> &Vec<ExpectedNamespace> {
        &self.namespace
    }
    pub fn runtime_expected(&self) -> &PrepareArtifact {
        &self.runtime_expected
    }
}
impl Emit for StagedClosure {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"entrypoint", out)?;
        out.push(b':');
        self.entrypoint.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"execution_evidence", out)?;
        out.push(b':');
        self.execution_evidence.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"entries", out)?;
        out.push(b':');
        self.entries.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"namespace", out)?;
        out.push(b':');
        self.namespace.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"runtime_expected", out)?;
        out.push(b':');
        self.runtime_expected.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareFacts {
    #[serde(rename = "type")]
    frame_type: String,
    binding: AgentBinding,
    source_baseline: SourceBaseline,
    staged_closure: StagedClosure,
    admission: AdmissionFacts,
    limits: LimitFacts,
    agent: AgentFacts,
    prompt: PromptFacts,
    tools: Vec<ToolFacts>,
    model_chain: Vec<ModelFacts>,
    provision_slot_id: CanonicalUuid,
    loop_profile: String,
}
impl PrepareFacts {
    pub fn frame_type(&self) -> &String {
        &self.frame_type
    }
    pub fn binding(&self) -> &AgentBinding {
        &self.binding
    }
    pub fn source_baseline(&self) -> &SourceBaseline {
        &self.source_baseline
    }
    pub fn staged_closure(&self) -> &StagedClosure {
        &self.staged_closure
    }
    pub fn admission(&self) -> &AdmissionFacts {
        &self.admission
    }
    pub fn limits(&self) -> &LimitFacts {
        &self.limits
    }
    pub fn agent(&self) -> &AgentFacts {
        &self.agent
    }
    pub fn prompt(&self) -> &PromptFacts {
        &self.prompt
    }
    pub fn tools(&self) -> &Vec<ToolFacts> {
        &self.tools
    }
    pub fn model_chain(&self) -> &Vec<ModelFacts> {
        &self.model_chain
    }
    pub fn provision_slot_id(&self) -> &CanonicalUuid {
        &self.provision_slot_id
    }
    pub fn loop_profile(&self) -> &String {
        &self.loop_profile
    }
}
impl Emit for PrepareFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"type", out)?;
        out.push(b':');
        self.frame_type.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"binding", out)?;
        out.push(b':');
        self.binding.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"source_baseline", out)?;
        out.push(b':');
        self.source_baseline.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"staged_closure", out)?;
        out.push(b':');
        self.staged_closure.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"admission", out)?;
        out.push(b':');
        self.admission.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"limits", out)?;
        out.push(b':');
        self.limits.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"agent", out)?;
        out.push(b':');
        self.agent.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"prompt", out)?;
        out.push(b':');
        self.prompt.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"tools", out)?;
        out.push(b':');
        self.tools.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"model_chain", out)?;
        out.push(b':');
        self.model_chain.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"provision_slot_id", out)?;
        out.push(b':');
        self.provision_slot_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"loop_profile", out)?;
        out.push(b':');
        self.loop_profile.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservedEntry {
    entry_id: String,
    observed_path: String,
    observed_byte_length: UInt,
    observed_sha256: String,
}
impl ObservedEntry {
    pub fn entry_id(&self) -> &String {
        &self.entry_id
    }
    pub fn observed_path(&self) -> &String {
        &self.observed_path
    }
    pub fn observed_byte_length(&self) -> &UInt {
        &self.observed_byte_length
    }
    pub fn observed_sha256(&self) -> &String {
        &self.observed_sha256
    }
}
impl Emit for ObservedEntry {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"entry_id", out)?;
        out.push(b':');
        self.entry_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"observed_path", out)?;
        out.push(b':');
        self.observed_path.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"observed_byte_length", out)?;
        out.push(b':');
        self.observed_byte_length.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"observed_sha256", out)?;
        out.push(b':');
        self.observed_sha256.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservedNamespace {
    module_name: String,
    kind: String,
    observed_paths: Vec<String>,
    origin_entry_ids: Vec<String>,
    shadowing_origins: Vec<String>,
}
impl ObservedNamespace {
    pub fn module_name(&self) -> &String {
        &self.module_name
    }
    pub fn kind(&self) -> &String {
        &self.kind
    }
    pub fn observed_paths(&self) -> &Vec<String> {
        &self.observed_paths
    }
    pub fn origin_entry_ids(&self) -> &Vec<String> {
        &self.origin_entry_ids
    }
    pub fn shadowing_origins(&self) -> &Vec<String> {
        &self.shadowing_origins
    }
}
impl Emit for ObservedNamespace {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"module_name", out)?;
        out.push(b':');
        self.module_name.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"kind", out)?;
        out.push(b':');
        self.kind.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"observed_paths", out)?;
        out.push(b':');
        self.observed_paths.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"origin_entry_ids", out)?;
        out.push(b':');
        self.origin_entry_ids.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"shadowing_origins", out)?;
        out.push(b':');
        self.shadowing_origins.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservedFile {
    path: String,
    byte_length: UInt,
    sha256: String,
}
impl ObservedFile {
    pub fn path(&self) -> &String {
        &self.path
    }
    pub fn byte_length(&self) -> &UInt {
        &self.byte_length
    }
    pub fn sha256(&self) -> &String {
        &self.sha256
    }
}
impl Emit for ObservedFile {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"path", out)?;
        out.push(b':');
        self.path.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"byte_length", out)?;
        out.push(b':');
        self.byte_length.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"sha256", out)?;
        out.push(b':');
        self.sha256.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservedPackage {
    distribution: String,
    version: String,
    installed_path: String,
    files: Vec<ObservedFile>,
}
impl ObservedPackage {
    pub fn distribution(&self) -> &String {
        &self.distribution
    }
    pub fn version(&self) -> &String {
        &self.version
    }
    pub fn installed_path(&self) -> &String {
        &self.installed_path
    }
    pub fn files(&self) -> &Vec<ObservedFile> {
        &self.files
    }
}
impl Emit for ObservedPackage {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"distribution", out)?;
        out.push(b':');
        self.distribution.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"version", out)?;
        out.push(b':');
        self.version.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"installed_path", out)?;
        out.push(b':');
        self.installed_path.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"files", out)?;
        out.push(b':');
        self.files.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservedArtifact {
    artifact_id: String,
    image_digest: Option<String>,
    interpreter: PrepareInterpreter,
    sdk: PrepareSdk,
    requirements_lock_sha256: Option<String>,
    runtime_protocol: String,
    launch_artifact_files: Vec<ObservedFile>,
    interpreter_executable: ObservedFile,
    sdk_files: Vec<ObservedFile>,
    packages: Vec<ObservedPackage>,
}
impl ObservedArtifact {
    pub fn artifact_id(&self) -> &String {
        &self.artifact_id
    }
    pub fn image_digest(&self) -> &Option<String> {
        &self.image_digest
    }
    pub fn interpreter(&self) -> &PrepareInterpreter {
        &self.interpreter
    }
    pub fn sdk(&self) -> &PrepareSdk {
        &self.sdk
    }
    pub fn requirements_lock_sha256(&self) -> &Option<String> {
        &self.requirements_lock_sha256
    }
    pub fn runtime_protocol(&self) -> &String {
        &self.runtime_protocol
    }
    pub fn launch_artifact_files(&self) -> &Vec<ObservedFile> {
        &self.launch_artifact_files
    }
    pub fn interpreter_executable(&self) -> &ObservedFile {
        &self.interpreter_executable
    }
    pub fn sdk_files(&self) -> &Vec<ObservedFile> {
        &self.sdk_files
    }
    pub fn packages(&self) -> &Vec<ObservedPackage> {
        &self.packages
    }
}
impl Emit for ObservedArtifact {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"artifact_id", out)?;
        out.push(b':');
        self.artifact_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"image_digest", out)?;
        out.push(b':');
        self.image_digest.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"interpreter", out)?;
        out.push(b':');
        self.interpreter.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"sdk", out)?;
        out.push(b':');
        self.sdk.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"requirements_lock_sha256", out)?;
        out.push(b':');
        self.requirements_lock_sha256.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"runtime_protocol", out)?;
        out.push(b':');
        self.runtime_protocol.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"launch_artifact_files", out)?;
        out.push(b':');
        self.launch_artifact_files.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"interpreter_executable", out)?;
        out.push(b':');
        self.interpreter_executable.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"sdk_files", out)?;
        out.push(b':');
        self.sdk_files.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"packages", out)?;
        out.push(b':');
        self.packages.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservedStartup {
    observed_sys_path: Vec<String>,
    site_hook_files: Vec<ObservedFile>,
    package_hook_files: Vec<ObservedFile>,
}
impl ObservedStartup {
    pub fn observed_sys_path(&self) -> &Vec<String> {
        &self.observed_sys_path
    }
    pub fn site_hook_files(&self) -> &Vec<ObservedFile> {
        &self.site_hook_files
    }
    pub fn package_hook_files(&self) -> &Vec<ObservedFile> {
        &self.package_hook_files
    }
}
impl Emit for ObservedStartup {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"observed_sys_path", out)?;
        out.push(b':');
        self.observed_sys_path.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"site_hook_files", out)?;
        out.push(b':');
        self.site_hook_files.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"package_hook_files", out)?;
        out.push(b':');
        self.package_hook_files.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservationFacts {
    entries: Vec<ObservedEntry>,
    namespace: Vec<ObservedNamespace>,
    artifact: ObservedArtifact,
    startup: ObservedStartup,
}
impl ObservationFacts {
    pub fn entries(&self) -> &Vec<ObservedEntry> {
        &self.entries
    }
    pub fn namespace(&self) -> &Vec<ObservedNamespace> {
        &self.namespace
    }
    pub fn artifact(&self) -> &ObservedArtifact {
        &self.artifact
    }
    pub fn startup(&self) -> &ObservedStartup {
        &self.startup
    }
}
impl Emit for ObservationFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"entries", out)?;
        out.push(b':');
        self.entries.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"namespace", out)?;
        out.push(b':');
        self.namespace.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"artifact", out)?;
        out.push(b':');
        self.artifact.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"startup", out)?;
        out.push(b':');
        self.startup.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreparedFacts {
    #[serde(rename = "type")]
    frame_type: String,
    session_id: CanonicalUuid,
    prepare_message_id: CanonicalUuid,
    prepare_payload_sha256: String,
    provision_slot_id: CanonicalUuid,
    hello_message_id: CanonicalUuid,
    hello_payload_sha256: String,
    observations: ObservationFacts,
}
impl PreparedFacts {
    pub fn frame_type(&self) -> &String {
        &self.frame_type
    }
    pub fn session_id(&self) -> &CanonicalUuid {
        &self.session_id
    }
    pub fn prepare_message_id(&self) -> &CanonicalUuid {
        &self.prepare_message_id
    }
    pub fn prepare_payload_sha256(&self) -> &String {
        &self.prepare_payload_sha256
    }
    pub fn provision_slot_id(&self) -> &CanonicalUuid {
        &self.provision_slot_id
    }
    pub fn hello_message_id(&self) -> &CanonicalUuid {
        &self.hello_message_id
    }
    pub fn hello_payload_sha256(&self) -> &String {
        &self.hello_payload_sha256
    }
    pub fn observations(&self) -> &ObservationFacts {
        &self.observations
    }
}
impl Emit for PreparedFacts {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.push(b'{');
        let mut first = true;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"type", out)?;
        out.push(b':');
        self.frame_type.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"session_id", out)?;
        out.push(b':');
        self.session_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"prepare_message_id", out)?;
        out.push(b':');
        self.prepare_message_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"prepare_payload_sha256", out)?;
        out.push(b':');
        self.prepare_payload_sha256.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"provision_slot_id", out)?;
        out.push(b':');
        self.provision_slot_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"hello_message_id", out)?;
        out.push(b':');
        self.hello_message_id.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"hello_payload_sha256", out)?;
        out.push(b':');
        self.hello_payload_sha256.emit(out)?;
        if !first {
            out.push(b',');
        }
        first = false;
        atom(&"observations", out)?;
        out.push(b':');
        self.observations.emit(out)?;
        let _ = first;
        out.push(b'}');
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(tag = "kind", content = "data", deny_unknown_fields)]
pub enum ExecutionEvidence {
    #[serde(rename = "solution_deployment")]
    SolutionDeployment(Box<SolutionEvidence>),
    #[serde(rename = "workspace_release")]
    WorkspaceRelease(Box<WorkspaceEvidence>),
    #[serde(rename = "parent_admitted_agent")]
    ParentAdmittedAgent(Box<ParentAgentEvidence>),
}
impl Emit for ExecutionEvidence {
    fn emit(&self, out: &mut Vec<u8>) -> Result<(), Error> {
        out.extend_from_slice(b"{\"kind\":");
        match self {
            Self::SolutionDeployment(v) => {
                atom(&"solution_deployment", out)?;
                out.extend_from_slice(b",\"data\":");
                v.emit(out)?;
            }
            Self::WorkspaceRelease(v) => {
                atom(&"workspace_release", out)?;
                out.extend_from_slice(b",\"data\":");
                v.emit(out)?;
            }
            Self::ParentAdmittedAgent(v) => {
                atom(&"parent_admitted_agent", out)?;
                out.extend_from_slice(b",\"data\":");
                v.emit(out)?;
            }
        }
        out.push(b'}');
        Ok(())
    }
}
fn validate(value: &Value, rule: &str) -> Result<(), Error> {
    if let Some(inner) = rule.strip_prefix("?") {
        return if value.is_null() {
            Ok(())
        } else {
            validate(value, inner)
        };
    }
    if rule.starts_with('[') {
        let array = value.as_array().ok_or(Error::InvalidFrame)?;
        for item in array {
            validate(item, &rule[1..rule.len() - 1])?;
        }
        return Ok(());
    }
    if let Some(variants) = rule.strip_prefix("=") {
        return if value
            .as_str()
            .is_some_and(|v| variants.split('|').any(|x| x == v))
        {
            Ok(())
        } else {
            Err(Error::InvalidFrame)
        };
    }
    let fields: Option<&[(&str, &str)]> = match rule {
        "AgentLogical" => Some(&[("kind", "=agent_run"), ("run_id", "uuid")]),
        "AgentAttempt" => Some(&[
            ("kind", "=agent_execution_attempt"),
            ("attempt_id", "uuid"),
            ("attempt_number", "positive"),
        ]),
        "AgentBinding" => Some(&[
            ("supervisor_incarnation_id", "uuid"),
            ("session_id", "uuid"),
            ("prepare_message_id", "uuid"),
            ("process_identity", "name"),
            ("logical_job", "AgentLogical"),
            ("attempt", "AgentAttempt"),
            ("expected_artifact_id", "name"),
            ("expected_image_digest", "?name"),
        ]),
        "SourceBaseline" => Some(&[
            ("reference_source_main", "git"),
            ("authored_workspace_revision", "git"),
            ("selected_manifest_sha256", "hash"),
            ("adapter_source_sha256", "hash"),
            ("mechanical_contract_sha256", "hash"),
        ]),
        "CallerFacts" => Some(&[
            ("user_id", "uuid"),
            ("email", "text"),
            ("name", "text"),
            ("organization_id", "?uuid"),
            ("is_superuser", "bool"),
            ("is_platform_admin", "bool"),
            ("is_external", "bool"),
            ("is_provider_org", "bool"),
            ("roles", "[text]"),
            ("verified_role_ids", "~[uuid]"),
        ]),
        "EffectiveFacts" => Some(&[
            ("organization_id", "?uuid"),
            ("solution_id", "?uuid"),
            ("solution_install_id", "?uuid"),
            ("agent_id", "uuid"),
            ("agent_run_id", "uuid"),
            ("caller_user_id", "?uuid"),
            ("caller_email", "?text"),
            ("caller_name", "?text"),
            ("accounting_org_id", "?uuid"),
            ("trigger_type", "text"),
            ("trigger_source", "?text"),
            ("event_delivery_id", "?uuid"),
            ("artifact_workspace_id", "?text"),
        ]),
        "AdmissionFacts" => Some(&[
            ("admission_snapshot_id", "uuid"),
            ("original_caller", "?CallerFacts"),
            ("effective_context", "EffectiveFacts"),
            ("original_caller_provenance_sha256", "hash"),
        ]),
        "LimitFacts" => Some(&[
            ("configured_max_iterations", "positive"),
            ("configured_max_token_budget", "positive"),
            ("llm_max_tokens", "?positive"),
            ("parent_duration_seconds", "positive"),
            ("parent_deadline_monotonic_ms", "uint"),
        ]),
        "AgentFacts" => Some(&[
            ("id", "uuid"),
            ("name", "text"),
            ("is_active", "bool"),
            ("organization_id", "?uuid"),
        ]),
        "PromptFacts" => Some(&[
            ("system_prompt", "text"),
            ("input_data", "?business"),
            ("output_schema", "?business"),
        ]),
        "ToolFacts" => Some(&[
            ("name", "name"),
            ("description", "text"),
            ("parameters", "business"),
            ("workflow_id", "uuid"),
        ]),
        "ModelFacts" => Some(&[
            ("slot", "positive"),
            ("profile_id", "uuid"),
            ("provider", "=openai|anthropic|google"),
            ("model", "name"),
            ("provider_connection_id", "?uuid"),
            ("default_max_tokens", "?positive"),
            ("anthropic_prompt_cache_supported", "?bool"),
            ("openai_transport", "?=responses|chat_completions"),
        ]),
        "EntrypointFacts" => Some(&[
            ("kind", "=autonomous_agent"),
            ("adapter_module", "text"),
            ("adapter_function", "text"),
            ("registered_agent_id", "uuid"),
        ]),
        "StagedEntry" => Some(&[
            ("entry_id", "name"),
            ("path", "name"),
            (
                "kind",
                "=adapter_source|dependency_file|resource_file|manifest_file",
            ),
            ("expected_byte_length", "uint"),
            ("expected_digest", "name"),
        ]),
        "ExpectedNamespace" => Some(&[
            ("module_name", "name"),
            ("kind", "=module|package|implicit_namespace"),
            ("staged_paths", "[text]"),
            ("entry_ids", "[text]"),
            ("expected_origins", "[text]"),
        ]),
        "PrepareInterpreter" => Some(&[("implementation", "name"), ("version", "name")]),
        "PrepareSdk" => Some(&[("distribution", "?name"), ("version", "?name")]),
        "PrepareArtifact" => Some(&[
            ("artifact_id", "name"),
            ("image_digest", "?name"),
            ("interpreter", "PrepareInterpreter"),
            ("sdk", "PrepareSdk"),
            ("requirements_lock_sha256", "?hash"),
            ("runtime_protocol", "=bifrost.runtime/v1"),
        ]),
        "SolutionEvidence" => Some(&[
            ("solution_id", "uuid"),
            ("solution_deployment_id", "uuid"),
            ("bundle_hash", "text"),
            ("compiled_manifest_hash", "text"),
            ("workflow_source_hash", "text"),
            ("git_commit_sha", "?text"),
            ("workflow_organization_id", "?text"),
            ("runtime_storage_prefix", "text"),
            ("workflow_portable_ref", "text"),
            ("workflow_name", "text"),
            ("workflow_function_name", "text"),
            ("workflow_path", "text"),
            ("workflow_execution_mode", "text"),
            ("workflow_type", "text"),
            ("workflow_timeout_seconds", "signed"),
            ("workflow_time_saved", "signed"),
            ("workflow_cache_ttl_seconds", "signed"),
            ("workflow_value", "number"),
            ("solution_global_repo_access", "bool"),
            ("deployment_source_hashes", "strings"),
            ("workflow_runtime_bounds", "~bounds"),
            ("workflow_parameters_schema", "~business"),
        ]),
        "WorkspaceEvidence" => Some(&[
            ("schema_version", "=bifrost.workspace-release-runtime/v1"),
            ("workspace_release_row_id", "uuid"),
            ("workspace_release_artifact_id", "uuid"),
            ("workflow_id", "uuid"),
            ("workspace_release_id", "prefixed"),
            ("workspace_release_effective_manifest_id", "prefixed"),
            ("workspace_release_governed_manifest_id", "prefixed"),
            ("workspace_release_registration_manifest_id", "prefixed"),
            ("workspace_release_runtime_storage_prefix", "text"),
            ("workspace_release_source_commit_sha", "text"),
            ("workspace_release_source_tree_sha", "text"),
            ("workspace_release_registration_state_fingerprint", "text"),
            ("workflow_name", "text"),
            ("workflow_function_name", "text"),
            ("workflow_path", "text"),
            ("workflow_source_hash", "text"),
            ("workflow_execution_mode", "text"),
            ("workflow_type", "text"),
            ("workspace_release_source_hashes", "strings"),
            ("workflow_runtime_bounds", "bounds"),
            ("workflow_timeout_seconds", "signed"),
            ("workflow_time_saved", "signed"),
            ("workflow_cache_ttl_seconds", "signed"),
            ("workflow_value", "number"),
            ("workflow_organization_id", "?text"),
        ]),
        "ParentAgentEvidence" => Some(&[
            ("admission_snapshot_id", "uuid"),
            ("agent_id", "uuid"),
            ("authored_agent_manifest_sha256", "hash"),
            ("source_observation_id", "uuid"),
        ]),
        "StagedClosure" => Some(&[
            ("entrypoint", "EntrypointFacts"),
            ("execution_evidence", "evidence"),
            ("entries", "[StagedEntry]"),
            ("namespace", "[ExpectedNamespace]"),
            ("runtime_expected", "PrepareArtifact"),
        ]),
        "PrepareFacts" => Some(&[
            ("type", "=Prepare"),
            ("binding", "AgentBinding"),
            ("source_baseline", "SourceBaseline"),
            ("staged_closure", "StagedClosure"),
            ("admission", "AdmissionFacts"),
            ("limits", "LimitFacts"),
            ("agent", "AgentFacts"),
            ("prompt", "PromptFacts"),
            ("tools", "[ToolFacts]"),
            ("model_chain", "[ModelFacts]"),
            ("provision_slot_id", "uuid"),
            ("loop_profile", "name"),
        ]),
        "ObservedEntry" => Some(&[
            ("entry_id", "text"),
            ("observed_path", "text"),
            ("observed_byte_length", "uint"),
            ("observed_sha256", "hash"),
        ]),
        "ObservedNamespace" => Some(&[
            ("module_name", "text"),
            ("kind", "=module|package|implicit_namespace"),
            ("observed_paths", "[text]"),
            ("origin_entry_ids", "[text]"),
            ("shadowing_origins", "[text]"),
        ]),
        "ObservedFile" => Some(&[
            ("path", "text"),
            ("byte_length", "uint"),
            ("sha256", "hash"),
        ]),
        "ObservedPackage" => Some(&[
            ("distribution", "text"),
            ("version", "text"),
            ("installed_path", "text"),
            ("files", "[ObservedFile]"),
        ]),
        "ObservedArtifact" => Some(&[
            ("artifact_id", "name"),
            ("image_digest", "?name"),
            ("interpreter", "PrepareInterpreter"),
            ("sdk", "PrepareSdk"),
            ("requirements_lock_sha256", "?hash"),
            ("runtime_protocol", "=bifrost.runtime/v1"),
            ("launch_artifact_files", "[ObservedFile]"),
            ("interpreter_executable", "ObservedFile"),
            ("sdk_files", "[ObservedFile]"),
            ("packages", "[ObservedPackage]"),
        ]),
        "ObservedStartup" => Some(&[
            ("observed_sys_path", "[text]"),
            ("site_hook_files", "[ObservedFile]"),
            ("package_hook_files", "[ObservedFile]"),
        ]),
        "ObservationFacts" => Some(&[
            ("entries", "[ObservedEntry]"),
            ("namespace", "[ObservedNamespace]"),
            ("artifact", "ObservedArtifact"),
            ("startup", "ObservedStartup"),
        ]),
        "PreparedFacts" => Some(&[
            ("type", "=Prepared"),
            ("session_id", "uuid"),
            ("prepare_message_id", "uuid"),
            ("prepare_payload_sha256", "hash"),
            ("provision_slot_id", "uuid"),
            ("hello_message_id", "uuid"),
            ("hello_payload_sha256", "hash"),
            ("observations", "ObservationFacts"),
        ]),
        _ => None,
    };
    if let Some(fields) = fields {
        let map = value.as_object().ok_or(Error::InvalidFrame)?;
        if map
            .keys()
            .any(|k| !fields.iter().any(|(name, _)| *name == k))
        {
            return Err(Error::InvalidFrame);
        }
        for (name, kind) in fields {
            match (map.get(*name), kind.strip_prefix("~")) {
                (None, Some(_)) => {}
                (Some(v), Some(inner)) => validate(v, inner)?,
                (Some(v), None) => validate(v, kind)?,
                _ => return Err(Error::InvalidFrame),
            }
        }
        return Ok(());
    }
    let valid = match rule {
        "text" => value.is_string(),
        "name" => value.as_str().is_some_and(|v| !v.is_empty()),
        "uuid" => value
            .as_str()
            .is_some_and(|v| CanonicalUuid::new(v).is_ok()),
        "hash" | "git" => value.as_str().is_some_and(|v| {
            v.len() == if rule == "hash" { 64 } else { 40 }
                && v.bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        }),
        "prefixed" => value.as_str().is_some_and(|v| {
            v.strip_prefix("sha256:").is_some_and(|h| {
                h.len() == 64
                    && h.bytes()
                        .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
            })
        }),
        "bool" => value.is_boolean(),
        "uint" => value.as_u64().is_some_and(|v| v <= MAX_SAFE_INTEGER),
        "positive" => value
            .as_u64()
            .is_some_and(|v| v > 0 && v <= MAX_SAFE_INTEGER),
        "signed" => value
            .as_i64()
            .is_some_and(|v| v.unsigned_abs() <= MAX_SAFE_INTEGER),
        "number" => value.is_number(),
        "business" => value.is_object(),
        "strings" => value
            .as_object()
            .is_some_and(|m| m.values().all(Value::is_string)),
        "bounds" => value.as_object().is_some_and(|m| {
            [
                "max_duration_seconds",
                "max_external_calls",
                "max_records_read",
                "max_output_bytes",
            ]
            .iter()
            .all(|k| m.contains_key(*k))
                && m.values()
                    .all(|v| v.as_u64().is_some_and(|n| n > 0 && n <= MAX_SAFE_INTEGER))
        }),
        "evidence" => {
            let map = value.as_object().ok_or(Error::InvalidFrame)?;
            if map.len() != 2 || !map.contains_key("kind") || !map.contains_key("data") {
                return Err(Error::InvalidFrame);
            }
            let kind = match map["kind"].as_str() {
                Some("solution_deployment") => "SolutionEvidence",
                Some("workspace_release") => "WorkspaceEvidence",
                Some("parent_admitted_agent") => "ParentAgentEvidence",
                _ => return Err(Error::InvalidFrame),
            };
            validate(&map["data"], kind)?;
            true
        }
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err(Error::InvalidFrame)
    }
}

/// Opaque scalar/container view. Floating-point lexemes have no numeric getter.
pub struct BusinessView(Box<RawValue>);
impl BusinessView {
    pub fn kind(&self) -> &'static str {
        match self.0.get().as_bytes().first() {
            Some(b'{') => "object",
            Some(b'[') => "array",
            Some(b'"') => "string",
            Some(b't' | b'f') => "bool",
            Some(b'n') => "null",
            _ => "number",
        }
    }
    pub fn field(&self, key: &str) -> Result<Option<Self>, Error> {
        let mut object: BTreeMap<String, Box<RawValue>> =
            serde_json::from_str(self.0.get()).map_err(|_| Error::InvalidFrame)?;
        Ok(object.remove(key).map(Self))
    }
    pub fn element(&self, index: usize) -> Result<Option<Self>, Error> {
        let values: Vec<Box<RawValue>> =
            serde_json::from_str(self.0.get()).map_err(|_| Error::InvalidFrame)?;
        Ok(values.into_iter().nth(index).map(Self))
    }
    pub fn string(&self) -> Result<String, Error> {
        serde_json::from_str(self.0.get()).map_err(|_| Error::InvalidFrame)
    }
    pub fn boolean(&self) -> Result<bool, Error> {
        serde_json::from_str(self.0.get()).map_err(|_| Error::InvalidFrame)
    }
    pub fn integer(&self) -> Result<i64, Error> {
        let token = self.0.get();
        if token.bytes().any(|c| matches!(c, b'.' | b'e' | b'E')) || token == "-0" {
            return Err(Error::InvalidFrame);
        }
        let number: i64 = serde_json::from_str(token).map_err(|_| Error::InvalidFrame)?;
        if number.unsigned_abs() > MAX_SAFE_INTEGER {
            return Err(Error::InvalidFrame);
        }
        Ok(number)
    }
}
impl RawBusinessJson {
    pub fn view(&self) -> Result<BusinessView, Error> {
        RawValue::from_string(self.0.get().to_owned())
            .map(BusinessView)
            .map_err(|_| Error::InvalidFrame)
    }
}

/// Numeric lexical preflight only. serde_json owns complete JSON grammar.
fn lexical_numbers(bytes: &[u8]) -> Result<(), Error> {
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'"' {
            index += 1;
            while index < bytes.len() {
                match bytes[index] {
                    b'\\' => index += 2,
                    b'"' => {
                        index += 1;
                        break;
                    }
                    _ => index += 1,
                }
            }
        } else if bytes[index] == b'-' || bytes[index].is_ascii_digit() {
            let start = index;
            index += 1;
            while index < bytes.len()
                && (bytes[index].is_ascii_digit()
                    || matches!(bytes[index], b'-' | b'+' | b'.' | b'e' | b'E'))
            {
                index += 1;
            }
            let token =
                std::str::from_utf8(&bytes[start..index]).map_err(|_| Error::InvalidJson)?;
            if !token.bytes().any(|c| matches!(c, b'.' | b'e' | b'E')) {
                let integer = token.parse::<i128>().map_err(|_| Error::InvalidJson)?;
                if integer.unsigned_abs() > u128::from(MAX_SAFE_INTEGER) {
                    return Err(Error::InvalidJson);
                }
            } else {
                let floating = token.parse::<f64>().map_err(|_| Error::InvalidJson)?;
                if !floating.is_finite() {
                    return Err(Error::InvalidJson);
                }
            }
        } else {
            index += 1;
        }
    }
    Ok(())
}

pub enum AgentBody {
    Control(Box<Frame>),
    Prepare(Box<PrepareFacts>),
    Prepared(Box<PreparedFacts>),
}
pub struct AgentFrame {
    session_id: CanonicalUuid,
    message_id: CanonicalUuid,
    sequence: Positive,
    correlation_id: Option<CanonicalUuid>,
    body: AgentBody,
}
impl AgentFrame {
    pub fn new(
        session_id: CanonicalUuid,
        message_id: CanonicalUuid,
        sequence: Positive,
        correlation_id: Option<CanonicalUuid>,
        body: AgentBody,
    ) -> Self {
        Self {
            session_id,
            message_id,
            sequence,
            correlation_id,
            body,
        }
    }
    pub fn session_id(&self) -> &CanonicalUuid {
        &self.session_id
    }
    pub fn message_id(&self) -> &CanonicalUuid {
        &self.message_id
    }
    pub fn sequence(&self) -> Positive {
        self.sequence
    }
    pub fn correlation_id(&self) -> Option<&CanonicalUuid> {
        self.correlation_id.as_ref()
    }
    pub fn body(&self) -> &AgentBody {
        &self.body
    }
}
pub struct RawPayload(Vec<u8>);
pub struct DecodedAgentFrame {
    frame: AgentFrame,
    payload: RawPayload,
    payload_sha256: String,
}
impl DecodedAgentFrame {
    pub fn frame(&self) -> &AgentFrame {
        &self.frame
    }
    pub fn payload_sha256(&self) -> &str {
        &self.payload_sha256
    }
    pub fn into_frame(self) -> AgentFrame {
        self.frame
    }
}
pub struct EncodedAgentFrame {
    wire: Vec<u8>,
    payload_sha256: String,
}
impl EncodedAgentFrame {
    pub fn payload_sha256(&self) -> &str {
        &self.payload_sha256
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AgentWire {
    protocol: String,
    #[serde(rename = "type")]
    frame_type: String,
    session_id: CanonicalUuid,
    message_id: CanonicalUuid,
    sequence: Positive,
    correlation_id: Option<CanonicalUuid>,
    body: Box<RawValue>,
}

pub struct AgentPrepareCodec;
impl Default for AgentPrepareCodec {
    fn default() -> Self {
        Self::new()
    }
}
impl AgentPrepareCodec {
    pub const fn new() -> Self {
        Self
    }
    pub fn parse_business_json(&self, bytes: &[u8]) -> Result<RawBusinessJson, Error> {
        if bytes.len() > MAX_FRAME_BYTES {
            return Err(Error::FrameTooLarge);
        }
        lexical_numbers(bytes)?;
        let tree = super::decode_ordinary_json(bytes)?;
        validate(&tree, "business")?;
        serde_json::from_slice::<Box<RawValue>>(bytes)
            .map(RawBusinessJson)
            .map_err(|_| Error::InvalidFrame)
    }
    pub fn decode_json(&self, bytes: &[u8]) -> Result<DecodedAgentFrame, Error> {
        if bytes.len() > MAX_FRAME_BYTES {
            return Err(Error::FrameTooLarge);
        }
        let digest = hash(bytes);
        lexical_numbers(bytes)?;
        let tree = super::decode_ordinary_json(bytes)?;
        let map = tree.as_object().ok_or(Error::InvalidFrame)?;
        let keys = [
            "protocol",
            "type",
            "session_id",
            "message_id",
            "sequence",
            "correlation_id",
            "body",
        ];
        if map.len() != keys.len() || keys.iter().any(|k| !map.contains_key(*k)) {
            return Err(Error::InvalidFrame);
        }
        let protocol = map["protocol"].as_str().ok_or(Error::InvalidFrame)?;
        if protocol != PROTOCOL {
            return Err(Error::UnsupportedProtocol);
        }
        validate(&map["session_id"], "uuid")?;
        validate(&map["message_id"], "uuid")?;
        validate(&map["sequence"], "positive")?;
        validate(&map["correlation_id"], "?uuid")?;
        let kind = map["type"]
            .as_str()
            .filter(|v| !v.is_empty())
            .ok_or(Error::InvalidFrame)?;
        let wire: AgentWire = serde_json::from_slice(bytes).map_err(|_| Error::InvalidFrame)?;
        if wire.protocol != PROTOCOL || wire.frame_type != kind {
            return Err(Error::InvalidFrame);
        }
        let body = match kind {
            "Prepare" => {
                validate(&map["body"], "PrepareFacts")?;
                AgentBody::Prepare(
                    serde_json::from_str(wire.body.get()).map_err(|_| Error::InvalidFrame)?,
                )
            }
            "Prepared" => {
                validate(&map["body"], "PreparedFacts")?;
                AgentBody::Prepared(
                    serde_json::from_str(wire.body.get()).map_err(|_| Error::InvalidFrame)?,
                )
            }
            _ => AgentBody::Control(Box::new(super::decode_json(bytes)?)),
        };
        let frame = AgentFrame {
            session_id: wire.session_id,
            message_id: wire.message_id,
            sequence: wire.sequence,
            correlation_id: wire.correlation_id,
            body,
        };
        associations(&frame)?;
        Ok(DecodedAgentFrame {
            frame,
            payload: RawPayload(bytes.to_vec()),
            payload_sha256: digest,
        })
    }
    pub fn encode_retained(&self, decoded: DecodedAgentFrame) -> Result<EncodedAgentFrame, Error> {
        encoded(decoded.payload.0, decoded.payload_sha256)
    }
    pub fn encode_typed(&self, frame: AgentFrame) -> Result<EncodedAgentFrame, Error> {
        associations(&frame)?;
        let kind = match &frame.body {
            AgentBody::Prepare(_) => "Prepare",
            AgentBody::Prepared(_) => "Prepared",
            AgentBody::Control(control) => {
                if control.session_id != frame.session_id
                    || control.message_id != frame.message_id
                    || control.sequence != frame.sequence
                    || control.correlation_id != frame.correlation_id
                {
                    return Err(Error::InvalidFrame);
                }
                let wire = super::encode_frame(control)?;
                let payload = wire.get(4..).ok_or(Error::InvalidFrame)?.to_vec();
                self.decode_json(&payload)?;
                return encoded(payload, hash(&wire[4..]));
            }
        };
        let mut payload = Vec::new();
        payload.extend_from_slice(b"{\"protocol\":");
        atom(&PROTOCOL, &mut payload)?;
        payload.extend_from_slice(b",\"type\":");
        atom(&kind, &mut payload)?;
        payload.extend_from_slice(b",\"session_id\":");
        frame.session_id.emit(&mut payload)?;
        payload.extend_from_slice(b",\"message_id\":");
        frame.message_id.emit(&mut payload)?;
        payload.extend_from_slice(b",\"sequence\":");
        frame.sequence.emit(&mut payload)?;
        payload.extend_from_slice(b",\"correlation_id\":");
        frame.correlation_id.emit(&mut payload)?;
        payload.extend_from_slice(b",\"body\":");
        match &frame.body {
            AgentBody::Prepare(v) => v.emit(&mut payload)?,
            AgentBody::Prepared(v) => v.emit(&mut payload)?,
            AgentBody::Control(_) => return Err(Error::InvalidFrame),
        }
        payload.push(b'}');
        self.decode_json(&payload)?;
        let digest = hash(&payload);
        encoded(payload, digest)
    }
    pub fn write_frame(
        &self,
        writer: &mut impl Write,
        frame: &EncodedAgentFrame,
    ) -> Result<(), Error> {
        writer.write_all(&frame.wire).map_err(|_| Error::Io)
    }
    pub fn read_frame(&self, reader: &mut impl Read) -> Result<Option<DecodedAgentFrame>, Error> {
        let mut prefix = [0_u8; 4];
        loop {
            match reader.read(&mut prefix[..1]) {
                Ok(0) => return Ok(None),
                Ok(_) => break,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => return Err(Error::Io),
            }
        }
        reader.read_exact(&mut prefix[1..]).map_err(read_error)?;
        let length = u32::from_be_bytes(prefix) as usize;
        if length == 0 {
            return Err(Error::InvalidFrame);
        }
        if length > MAX_FRAME_BYTES {
            return Err(Error::FrameTooLarge);
        }
        let mut bytes = vec![0; length];
        reader.read_exact(&mut bytes).map_err(read_error)?;
        self.decode_json(&bytes).map(Some)
    }
}
fn read_error(error: std::io::Error) -> Error {
    if error.kind() == std::io::ErrorKind::UnexpectedEof {
        Error::TruncatedFrame
    } else {
        Error::Io
    }
}
fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn encoded(payload: Vec<u8>, payload_sha256: String) -> Result<EncodedAgentFrame, Error> {
    if payload.len() > MAX_FRAME_BYTES {
        return Err(Error::FrameTooLarge);
    }
    let length = u32::try_from(payload.len()).map_err(|_| Error::FrameTooLarge)?;
    let mut wire = length.to_be_bytes().to_vec();
    wire.extend(payload);
    Ok(EncodedAgentFrame {
        wire,
        payload_sha256,
    })
}
fn associations(frame: &AgentFrame) -> Result<(), Error> {
    match &frame.body {
        AgentBody::Prepare(body) => {
            let binding = &body.binding;
            if frame.correlation_id.is_some()
                || binding.session_id != frame.session_id
                || binding.prepare_message_id != frame.message_id
            {
                return Err(Error::InvalidFrame);
            }
            if body.agent.id != body.admission.effective_context.agent_id
                || body.agent.id != body.staged_closure.entrypoint.registered_agent_id
                || binding.logical_job.run_id != body.admission.effective_context.agent_run_id
            {
                return Err(Error::InvalidFrame);
            }
            if binding.expected_artifact_id != body.staged_closure.runtime_expected.artifact_id
                || binding.expected_image_digest
                    != body.staged_closure.runtime_expected.image_digest
            {
                return Err(Error::InvalidFrame);
            }
            if let ExecutionEvidence::ParentAdmittedAgent(data) =
                &body.staged_closure.execution_evidence
            {
                if data.agent_id != body.agent.id
                    || data.admission_snapshot_id != body.admission.admission_snapshot_id
                {
                    return Err(Error::InvalidFrame);
                }
            }
            let models = &body.model_chain;
            let mut profiles = BTreeSet::new();
            if models.is_empty() {
                return Err(Error::InvalidFrame);
            }
            for (index, model) in models.iter().enumerate() {
                if model.slot.get() != u64::try_from(index + 1).map_err(|_| Error::InvalidFrame)?
                    || !profiles.insert(model.profile_id.as_str())
                {
                    return Err(Error::InvalidFrame);
                }
            }
            let mut entries = BTreeSet::new();
            for entry in &body.staged_closure.entries {
                if !entries.insert(entry.entry_id.as_str()) {
                    return Err(Error::InvalidFrame);
                }
            }
            if body.staged_closure.namespace.iter().any(|node| {
                node.entry_ids
                    .iter()
                    .any(|id| !entries.contains(id.as_str()))
            }) {
                return Err(Error::InvalidFrame);
            }
        }
        AgentBody::Prepared(body) => {
            if body.session_id != frame.session_id
                || frame.correlation_id.as_ref() != Some(&body.prepare_message_id)
            {
                return Err(Error::InvalidFrame);
            }
        }
        AgentBody::Control(_) => {}
    }
    Ok(())
}
pub fn validate_prepared_binding(
    prepared: &DecodedAgentFrame,
    prepare: &DecodedAgentFrame,
    hello: &DecodedAgentFrame,
) -> Result<(), Error> {
    let (AgentBody::Prepared(value), AgentBody::Prepare(request), AgentBody::Control(greeting)) =
        (&prepared.frame.body, &prepare.frame.body, &hello.frame.body)
    else {
        return Err(Error::InvalidFrame);
    };
    if !matches!(&greeting.body, super::Body::Hello(_)) {
        return Err(Error::InvalidFrame);
    }
    if prepared.frame.session_id != prepare.frame.session_id
        || prepared.frame.session_id != hello.frame.session_id
        || value.prepare_message_id != prepare.frame.message_id
        || value.hello_message_id != hello.frame.message_id
        || value.provision_slot_id != request.provision_slot_id
        || value.prepare_payload_sha256 != prepare.payload_sha256
        || value.hello_payload_sha256 != hello.payload_sha256
    {
        return Err(Error::InvalidFrame);
    }
    Ok(())
}
