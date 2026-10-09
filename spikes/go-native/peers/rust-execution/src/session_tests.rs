//! Independent specification transcripts only: injected owner/custody events
//! never perform admission, credential issuance, process launch or durable DML.
use crate::{Codec, Decoded, PROTOCOL};
use serde::Deserialize;
use serde_json::{Value, json, value::RawValue};
use std::collections::BTreeMap;
use std::io::Cursor;

type Check = Result<(), &'static str>;
fn require(condition: bool, error: &'static str) -> Check {
    if condition { Ok(()) } else { Err(error) }
}
fn number(value: &Value) -> u64 {
    value.as_u64().unwrap_or_default()
}
fn has(value: &Value, item: &Value) -> bool {
    value.as_array().is_some_and(|items| items.contains(item))
}

// Fixed UTC tuple orders the schema's calendar fields including microseconds.
// This is fixture clock arithmetic, not a trusted execution clock conversion.
fn timestamp(value: &Value) -> Result<[u32; 7], &'static str> {
    let value = value.as_str().ok_or("InvalidTransition")?;
    let pieces: Vec<_> = value
        .split(['-', ':', 'T', '.', 'Z'])
        .filter(|p| !p.is_empty())
        .collect();
    require(
        value.ends_with('Z') && (pieces.len() == 6 || pieces.len() == 7),
        "InvalidTransition",
    )?;
    let mut result = [0; 7];
    for (index, piece) in pieces.iter().enumerate() {
        result[index] = piece.parse().map_err(|_| "InvalidTransition")?;
    }
    if pieces.len() == 7 {
        require(pieces[6].len() <= 6, "InvalidTransition")?;
        result[6] *= 10_u32.pow(6 - pieces[6].len() as u32);
    }
    Ok(result)
}

#[derive(Clone, Debug, PartialEq)]
struct Transcript {
    binding: Value,
    artifact: Value,
    messages: BTreeMap<String, Value>,
    sequences: BTreeMap<String, u64>,
    payloads: BTreeMap<String, Vec<u8>>,
    committed_start: Value,
    budget: Option<u64>,
    grant: Value,
    receipt: Value,
    result_digest: String,
    winner: Option<String>,
    now: [u32; 7],
    elapsed: u64,
    commit_elapsed: u64,
    heartbeat: u64,
    log_sequence: u64,
    observed_start: bool,
    observed_cancel: bool,
    reported_start: bool,
    reported_cancel: bool,
    material: bool,
    released: bool,
    effects: bool,
    closed: bool,
    stopped: bool,
    revoked: bool,
    cleanup: bool,
}
impl Transcript {
    fn new(environment: &Value) -> Self {
        Self {
            binding: environment["binding"].clone(),
            artifact: environment["artifact"].clone(),
            messages: BTreeMap::new(),
            payloads: BTreeMap::new(),
            sequences: BTreeMap::from([("parent".into(), 0), ("adapter".into(), 0)]),
            committed_start: Value::Null,
            budget: None,
            grant: Value::Null,
            receipt: Value::Null,
            result_digest: String::new(),
            winner: None,
            now: [2030, 1, 1, 0, 0, 0, 0],
            elapsed: 0,
            commit_elapsed: 0,
            heartbeat: 0,
            log_sequence: 0,
            observed_start: false,
            observed_cancel: false,
            reported_start: false,
            reported_cancel: false,
            material: false,
            released: false,
            effects: false,
            closed: false,
            stopped: false,
            revoked: false,
            cleanup: false,
        }
    }
    fn snapshot(&self) -> Value {
        json!({"effects_permitted":self.effects, "winner":self.winner,
            "closed":self.closed, "cleanup_verified":self.cleanup,
            "result_observed":self.messages.contains_key("Result"),
            "receipt":self.receipt["disposition"], "sequences":self.sequences})
    }
    fn message(&self, kind: &str) -> Value {
        self.messages.get(kind).cloned().unwrap_or(Value::Null)
    }
    fn id(&self, kind: &str) -> Value {
        self.message(kind)["message_id"].clone()
    }
    fn context(&self) -> Value {
        let mut context = json!({"kind":"tenant-context/v1", "caller_id":self.binding["original_caller"]["caller_id"]});
        for field in [
            "execution_kind",
            "execution_id",
            "attempt_id",
            "attempt_number",
            "solution_id",
            "deployment_id",
            "artifact_id",
            "effective_scope",
        ] {
            context[field] = self.binding[field].clone();
        }
        context
    }
    fn receive(&mut self, direction: &str, decoded: &Decoded, payload: &[u8]) -> Check {
        let frame = &decoded.frame;
        require(
            !(self.closed || direction == "adapter" && self.stopped),
            "SessionClosed",
        )?;
        require(
            frame["session_id"] == self.binding["session_id"],
            "InvalidBinding",
        )?;
        require(
            self.sequences
                .get(direction)
                .is_some_and(|s| number(&frame["sequence"]) == s + 1),
            "InvalidTransition",
        )?;
        let identity = frame["message_id"].as_str().ok_or("InvalidFrame")?;
        if let Some(previous) = self.payloads.get(identity) {
            return Err(if previous == payload {
                "DuplicateMessage"
            } else {
                "ConflictingMessage"
            });
        }
        let kind = frame["type"].as_str().ok_or("InvalidFrame")?;
        let body = &frame["body"];
        let parent = matches!(
            kind,
            "Select" | "Prepare" | "Start" | "Provision" | "Cancel" | "ResultReceipt"
        );
        require(parent == (direction == "parent"), "InvalidTransition")?;
        let correlation = match kind {
            "Select" => self.id("Offer"),
            "Prepare" => self.id("Select"),
            "Prepared" | "Start" | "Provision" => self.id("Prepare"),
            "LogBatch" | "Usage" | "Result" => self.id("Start"),
            "ResultReceipt" => self.id("Result"),
            _ => Value::Null,
        };
        match kind {
            "Offer" => {
                require(!self.messages.contains_key(kind), "InvalidTransition")?;
                require(
                    has(&body["supported_protocols"], &json!(PROTOCOL)),
                    "UnsupportedProtocol",
                )?;
                require(
                    has(&body["capabilities"], &json!("execution_profile/v1")),
                    "UnsupportedProfile",
                )?;
                require(
                    body["runtime_incarnation_id"] == self.binding["runtime_incarnation_id"],
                    "InvalidBinding",
                )?;
            }
            "Select" => {
                require(
                    self.messages.contains_key("Offer") && !self.messages.contains_key(kind),
                    "InvalidTransition",
                )?;
                require(
                    has(
                        &self.message("Offer")["body"]["artifact_classes"],
                        &body["artifact_class"],
                    ),
                    "UnsupportedProfile",
                )?;
                require(
                    body["artifact_class"] == self.artifact["kind"],
                    "InvalidBinding",
                )?;
            }
            "Prepare" | "Prepared" | "Start" => {
                let previous = match kind {
                    "Prepare" => "Select",
                    "Prepared" => "Prepare",
                    _ => "Prepared",
                };
                require(
                    self.messages.contains_key(previous)
                        && !self.messages.contains_key(kind)
                        && self.winner.is_none(),
                    "InvalidTransition",
                )?;
                if kind == "Prepare" {
                    require(
                        body["binding"] == self.binding
                            && body["artifact"] == self.artifact
                            && body["context"] == self.context(),
                        "InvalidBinding",
                    )?;
                } else {
                    require(body["prepare_message_id"] == correlation, "InvalidBinding")?;
                    if kind == "Prepared" {
                        require(body["artifact"] == self.artifact, "InvalidBinding")?;
                    } else {
                        require(
                            body["committed_start_id"] == self.committed_start
                                && body["remaining_run_ms"] == json!(self.budget),
                            "InvalidBinding",
                        )?;
                    }
                }
            }
            "Provision" => {
                require(
                    self.messages.contains_key("Prepared")
                        && !self.messages.contains_key(kind)
                        && self.winner.is_none()
                        && !self.committed_start.is_null(),
                    "InvalidTransition",
                )?;
                require(
                    *body == self.grant
                        && body["binding"] == self.binding
                        && body["committed_start_id"] == self.committed_start,
                    "InvalidGrant",
                )?;
            }
            "Cancel" => {
                require(
                    self.messages.contains_key("Select")
                        && !self.messages.contains_key(kind)
                        && matches!(
                            self.winner.as_deref(),
                            Some("cancel" | "failure" | "result")
                        ),
                    "InvalidTransition",
                )?;
            }
            "ResultReceipt" => {
                require(
                    self.messages.contains_key("Result") && *body == self.receipt,
                    "InvalidTransition",
                )?;
            }
            "LogBatch" | "Usage" | "Result" => {
                require(self.messages.contains_key("Start"), "InvalidTransition")?;
                require(body["start_message_id"] == correlation, "InvalidBinding")?;
                require(
                    self.released && !self.messages.contains_key("Result"),
                    "InvalidTransition",
                )?;
                if kind == "LogBatch" {
                    require(
                        number(&body["batch_sequence"]) == self.log_sequence + 1,
                        "InvalidTransition",
                    )?;
                    self.log_sequence += 1;
                }
                if kind == "Result" {
                    self.result_digest = decoded.payload_sha256();
                }
                self.reported_start = true;
            }
            "Heartbeat" => {
                require(
                    self.messages.contains_key("Prepared")
                        && number(&body["monotonic_elapsed_ms"]) >= self.heartbeat,
                    "InvalidTransition",
                )?;
                let start = &body["start_message_id"];
                let state = body["state"].as_str().unwrap_or_default();
                let valid = match state {
                    "prepared" => !self.reported_start && !self.reported_cancel && start.is_null(),
                    "executing" => {
                        self.messages.contains_key("Start")
                            && *start == self.id("Start")
                            && self.released
                            && !self.reported_cancel
                    }
                    "cancelling" => {
                        self.messages.contains_key("Cancel") && *start == self.id("Start")
                    }
                    _ => false,
                };
                require(valid, "InvalidTransition")?;
                self.heartbeat = number(&body["monotonic_elapsed_ms"]);
                self.reported_start |= !start.is_null();
                self.reported_cancel |= state == "cancelling";
            }
            "Stopped" => {
                require(self.messages.contains_key("Select"), "InvalidTransition")?;
                let start = &body["start_message_id"];
                let cancel = &body["cancel_id"];
                let reason = body["reason"].as_str().unwrap_or_default();
                let start_matches = *start == self.id("Start")
                    || (!self.reported_start
                        && start.is_null()
                        && matches!(reason, "prepare_rejected" | "protocol_error"));
                let cancel_matches = *cancel == self.message("Cancel")["body"]["cancel_id"]
                    || (!self.reported_cancel && cancel.is_null());
                require(
                    start_matches
                        && cancel_matches
                        && body["result_message_id"] == self.id("Result"),
                    "InvalidBinding",
                )?;
                require(
                    reason != "completed"
                        || (self.messages.contains_key("Result") && !self.reported_cancel),
                    "InvalidTransition",
                )?;
                require(
                    reason != "cancelled"
                        || (self.messages.contains_key("Cancel") && !cancel.is_null()),
                    "InvalidTransition",
                )?;
                require(
                    reason != "prepare_rejected" || start.is_null(),
                    "InvalidTransition",
                )?;
                self.stopped = true;
            }
            _ => return Err("UnsupportedFrame"),
        }
        require(frame["correlation_id"] == correlation, "InvalidBinding")?;
        self.sequences
            .insert(direction.into(), number(&frame["sequence"]));
        self.payloads.insert(identity.into(), payload.to_vec());
        if matches!(
            kind,
            "Offer"
                | "Select"
                | "Prepare"
                | "Prepared"
                | "Start"
                | "Provision"
                | "Cancel"
                | "Result"
        ) {
            self.messages.insert(kind.into(), frame.clone());
        }
        Ok(())
    }
    fn grant_current(&self) -> Result<bool, &'static str> {
        Ok(!self.revoked && self.now < timestamp(&self.grant["expires_at"])?)
    }
    fn deadline_expired(&self) -> Result<bool, &'static str> {
        let deadline = self.message("Prepare")["body"]["workload"]["deadline_utc"].clone();
        Ok((!deadline.is_null() && self.now >= timestamp(&deadline)?)
            || self
                .budget
                .is_some_and(|budget| self.elapsed - self.commit_elapsed >= budget))
    }
    fn assume(&mut self, event: &Value) -> Check {
        let kind = event["event"].as_str().ok_or("UnknownEvent")?;
        match kind {
            "commit_start" => {
                require(
                    self.messages.contains_key("Prepared")
                        && self.committed_start.is_null()
                        && self.winner.is_none()
                        && !self.closed,
                    "InvalidTransition",
                )?;
                self.committed_start = event["committed_start_id"].clone();
                self.budget = event["remaining_run_ms"].as_u64();
                self.commit_elapsed = self.elapsed;
            }
            "admit_grant" => {
                require(
                    !self.committed_start.is_null()
                        && !self.closed
                        && self.winner.is_none()
                        && self.grant.is_null(),
                    "InvalidGrant",
                )?;
                let grant = &event["provision"];
                require(
                    grant["binding"] == self.binding
                        && grant["committed_start_id"] == self.committed_start
                        && grant["prepare_message_id"] == self.id("Prepare"),
                    "InvalidGrant",
                )?;
                self.grant = grant.clone();
            }
            "observe_start" => {
                require(
                    self.messages.contains_key("Start") && !self.observed_start,
                    "InvalidTransition",
                )?;
                self.observed_start = true;
            }
            "deliver" => {
                require(
                    self.messages.contains_key("Provision")
                        && event["delivery_id"] == self.grant["delivery_id"],
                    "InvalidGrant",
                )?;
                self.material = true;
            }
            "release" => {
                require(
                    self.observed_start
                        && self.material
                        && !self.closed
                        && self.winner.is_none()
                        && !self.observed_cancel,
                    "EffectsForbidden",
                )?;
                require(!self.released, "InvalidTransition")?;
                require(self.grant_current()?, "InvalidGrant")?;
                require(!self.deadline_expired()?, "DeadlineExceeded")?;
                self.effects = true;
                self.released = true;
            }
            "tick" => {
                let now = timestamp(&event["now"])?;
                let elapsed = number(&event["elapsed_ms"]);
                require(
                    now >= self.now && elapsed >= self.elapsed,
                    "InvalidTransition",
                )?;
                self.now = now;
                self.elapsed = elapsed;
                if self.released && (!self.grant_current()? || self.deadline_expired()?) {
                    self.effects = false;
                }
            }
            "cancel_commit" | "failure_commit" => {
                if self.winner.is_none() {
                    self.winner = Some(
                        if kind == "cancel_commit" {
                            "cancel"
                        } else {
                            "failure"
                        }
                        .into(),
                    );
                }
                self.effects = false;
            }
            "observe_cancel" => {
                require(self.messages.contains_key("Cancel"), "InvalidTransition")?;
                self.observed_cancel = true;
                self.effects = false;
            }
            "revoke" => {
                self.revoked = true;
                self.effects = false;
            }
            "accept_result" => {
                if !self.receipt.is_null() {
                    require(
                        event["result_sha256"] == self.receipt["result_sha256"],
                        "ConflictingReceipt",
                    )?;
                    return Ok(());
                }
                require(!self.closed, "SessionClosed")?;
                require(self.messages.contains_key("Result"), "InvalidTransition")?;
                require(
                    event["result_sha256"] == json!(self.result_digest),
                    "ConflictingReceipt",
                )?;
                if self.winner.is_none() {
                    require(self.grant_current()?, "InvalidGrant")?;
                    require(!self.deadline_expired()?, "DeadlineExceeded")?;
                    self.winner = Some("result".into());
                }
                self.receipt = json!({"result_message_id":self.id("Result"), "result_sha256":self.result_digest,
                    "decision_id":event["decision_id"], "winner":self.winner,
                    "disposition":if self.winner.as_deref() == Some("result") { "accepted" } else { "retained" }});
                self.effects = false;
            }
            "transport_loss" | "child_crash" | "adapter_crash" | "close" => {
                self.closed = true;
                self.effects = false;
            }
            "cleanup_verified" => self.cleanup = true,
            _ => return Err("UnknownEvent"),
        }
        Ok(())
    }
    fn apply(&mut self, step: &Step, codec: &Codec) -> Result<(), String> {
        let mut candidate = self.clone();
        let event: Value = serde_json::from_str(step.input.get()).map_err(|_| "InvalidFrame")?;
        if event["event"] == "receive" {
            let raw = if let Some(hex) = event["hex"].as_str() {
                unhex(hex)?
            } else {
                let literal: LiteralFrame =
                    serde_json::from_str(step.input.get()).map_err(|_| "InvalidFrame")?;
                let payload = compact(literal.frame.get().as_bytes());
                [
                    (payload.len() as u32).to_be_bytes().as_slice(),
                    payload.as_slice(),
                ]
                .concat()
            };
            let mut cursor = Cursor::new(&raw);
            let decoded = codec
                .read(&mut cursor)
                .map_err(|error| format!("{error:?}"))?
                .ok_or("InvalidFrame")?;
            require(cursor.position() as usize == raw.len(), "InvalidFrame")?;
            candidate.receive(
                event["direction"].as_str().ok_or("InvalidTransition")?,
                &decoded,
                &raw[4..],
            )?;
        } else {
            candidate.assume(&event)?;
        }
        *self = candidate;
        Ok(())
    }
}

// Compact trusted JSON while preserving field order and exact string tokens.
// Received payloads are never reserialized for an owner receipt.
fn compact(raw: &[u8]) -> Vec<u8> {
    let mut output = Vec::new();
    let (mut quoted, mut escaped) = (false, false);
    for &byte in raw {
        if quoted {
            output.push(byte);
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                quoted = false;
            }
        } else if byte == b'"' {
            quoted = true;
            output.push(byte);
        } else if !byte.is_ascii_whitespace() {
            output.push(byte);
        }
    }
    output
}
fn unhex(hex: &str) -> Result<Vec<u8>, String> {
    if !hex.len().is_multiple_of(2) {
        return Err("InvalidFrame".into());
    }
    hex.as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|p| {
            let value = std::str::from_utf8(p).map_err(|_| "InvalidFrame")?;
            u8::from_str_radix(value, 16).map_err(|_| "InvalidFrame".into())
        })
        .collect()
}

#[derive(Deserialize)]
struct LiteralFrame {
    frame: Box<RawValue>,
}
#[derive(Deserialize)]
struct Step {
    input: Box<RawValue>,
    error: Option<String>,
    after: Value,
}
#[derive(Deserialize)]
struct Case {
    name: String,
    setup: Option<String>,
    steps: Vec<Step>,
}
#[derive(Deserialize)]
struct Corpus {
    environment: Value,
    fixtures: BTreeMap<String, Vec<Step>>,
    cases: Vec<Case>,
}
fn corpus() -> Corpus {
    serde_json::from_str(include_str!(
        "../../../executionprofile/testdata/session-vectors.json"
    ))
    .unwrap_or_else(|_| panic!("invalid trusted session corpus"))
}
fn session_case(index: usize) {
    let corpus = corpus();
    let case = &corpus.cases[index];
    let codec = Codec::new().unwrap_or_else(|_| panic!("invalid trusted schema copies"));
    let mut model = Transcript::new(&corpus.environment);
    let setup = case.setup.as_ref().map(|name| &corpus.fixtures[name]);
    for (index, step) in setup.into_iter().flatten().chain(&case.steps).enumerate() {
        let before = model.clone();
        let error = model.apply(step, &codec).err();
        if error.is_some() {
            assert_eq!(model, before, "rejection changed private test state");
        }
        assert_eq!(error, step.error, "{} step {index} rejection", case.name);
        assert_eq!(
            model.snapshot(),
            step.after,
            "{} step {index} snapshot",
            case.name
        );
    }
}
#[test]
fn published_session_inventory() {
    let corpus = corpus();
    assert_eq!(corpus.cases.len(), 68);
    let steps: usize = corpus
        .cases
        .iter()
        .map(|case| case.steps.len() + case.setup.as_ref().map_or(0, |s| corpus.fixtures[s].len()))
        .sum();
    assert_eq!(steps, 695);
}
#[test]
fn fixture_compaction_preserves_string_bytes_and_field_order() {
    let literal = br#" { "z": "space and \\\"quote", "a": [ 1, 2 ] } "#;
    assert_eq!(
        compact(literal),
        br#"{"z":"space and \\\"quote","a":[1,2]}"#
    );
    assert_eq!(
        timestamp(&json!("2030-01-01T00:00:00.1Z")),
        timestamp(&json!("2030-01-01T00:00:00.100000Z"))
    );
}
macro_rules! session_test {
    ($name:ident, $index:expr) => {
        #[test]
        fn $name() {
            session_case($index);
        }
    };
}
session_test!(session_000_accepted_success, 0);
session_test!(session_001_reported_success_not_durable, 1);
session_test!(session_002_no_start_no_effects, 2);
session_test!(session_003_start_without_provision, 3);
session_test!(session_004_start_without_commit, 4);
session_test!(session_005_provision_without_commit, 5);
session_test!(session_006_provision_before_start_frame, 6);
session_test!(session_007_notice_without_actual_delivery, 7);
session_test!(session_008_expired_at_exact_boundary, 8);
session_test!(session_009_revoked_before_release, 9);
session_test!(session_010_wrong_caller_org_grant, 10);
session_test!(session_011_wrong_effective_scope_grant, 11);
session_test!(session_012_wrong_session_id_grant, 12);
session_test!(session_013_wrong_attempt_id_grant, 13);
session_test!(session_014_wrong_artifact_id_grant, 14);
session_test!(session_015_wrong_deployment_id_grant, 15);
session_test!(session_016_wrong_runtime_incarnation_id_grant, 16);
session_test!(session_017_wrong_start_grant, 17);
session_test!(session_018_changed_capability_notice, 18);
session_test!(session_019_wrong_delivery, 19);
session_test!(session_020_cancel_before_start, 20);
session_test!(session_021_cancel_during_execution, 21);
session_test!(session_022_result_observed_then_cancel_wins, 22);
session_test!(session_023_cancel_wins_queued_result, 23);
session_test!(session_024_result_accepted_before_cancel, 24);
session_test!(session_025_duplicate_owner_result_identical_bytes, 25);
session_test!(session_026_duplicate_owner_result_conflicting_bytes, 26);
session_test!(session_027_duplicate_wire_result_identical, 27);
session_test!(session_028_duplicate_id_changed_payload, 28);
session_test!(session_029_result_after_session_close, 29);
session_test!(session_030_lost_result_receipt, 30);
session_test!(session_031_child_crash_before_start, 31);
session_test!(session_032_child_crash_after_start, 32);
session_test!(session_033_child_crash_after_release, 33);
session_test!(session_034_adapter_crash_before_start, 34);
session_test!(session_035_adapter_crash_after_start, 35);
session_test!(session_036_adapter_crash_after_release, 36);
session_test!(session_037_transport_loss_before_start, 37);
session_test!(session_038_transport_loss_after_start, 38);
session_test!(session_039_transport_loss_after_release, 39);
session_test!(session_040_adapter_crash_descendants_not_proven, 40);
session_test!(session_041_stopped_does_not_finalize, 41);
session_test!(session_042_result_before_start, 42);
session_test!(session_043_result_before_release, 43);
session_test!(session_044_sequence_gap, 44);
session_test!(session_045_heartbeat_queued_before_parent_start, 45);
session_test!(session_046_logs_and_usage, 46);
session_test!(session_047_log_gap, 47);
session_test!(session_048_no_fallback_to_p0, 48);
session_test!(session_049_unsupported_peer, 49);
session_test!(session_050_context_derived_from_binding_only, 50);
session_test!(session_051_null_deadline_finite_grant, 51);
session_test!(session_052_monotonic_budget_boundary, 52);
session_test!(session_053_uncommitted_result_expired_grant, 53);
session_test!(session_054_uncommitted_result_revoked_grant, 54);
session_test!(session_055_second_result_new_id, 55);
session_test!(session_056_grant_without_issued_evidence, 56);
session_test!(session_057_cancel_after_select_before_prepare, 57);
session_test!(session_058_wrong_prepared_adapter, 58);
session_test!(session_059_crash_is_not_durable_failure, 59);
session_test!(session_060_failure_wins_before_queued_result, 60);
session_test!(session_061_start_budget_not_committed, 61);
session_test!(session_062_prepared_heartbeat_queued_before_release, 62);
session_test!(
    session_063_executing_heartbeat_queued_before_cancel_frontier,
    63
);
session_test!(session_064_cancelling_heartbeat_before_start, 64);
session_test!(session_065_cannot_release_twice, 65);
session_test!(session_066_expired_while_running, 66);
session_test!(session_067_raw_result_digest_includes_whitespace, 67);
