//! Test-only JSON bridge to actual pure domain calls; no owner or wire contract.
//! The Python conductor owns the finite deadline, kill/reap and binary custody.

use std::io::{self, Read, Write};
use std::process::ExitCode;

use bifrost_contracts::runtime::CanonicalUuid;
use bifrost_domain::workflow as domain;
use serde::{Deserialize, Deserializer, Serialize};

const INPUT_LIMIT: usize = 65_536;
const OUTPUT_LIMIT: usize = 4_096;
const SCHEMA: &str = "bifrost.test.workflow-domain/v1";

#[derive(Deserialize)]
#[serde(try_from = "String")]
enum Schema {
    WorkflowDomainV1,
}

impl TryFrom<String> for Schema {
    type Error = &'static str;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        match value.as_str() {
            "bifrost.test.workflow-domain/v1" => Ok(Self::WorkflowDomainV1),
            _ => Err("invalid test enum"),
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    schema: Schema,
    case_id: String,
    operation: Operation,
    rows: Rows,
}

fn required_nullable<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Rows {
    #[serde(deserialize_with = "required_nullable")]
    execution: Option<ExecutionInput>,
    #[serde(deserialize_with = "required_nullable")]
    attempt: Option<AttemptInput>,
    history: HistoryInput,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExecutionInput {
    id: CanonicalUuid,
    status: LogicalStatusInput,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AttemptInput {
    id: CanonicalUuid,
    execution_id: CanonicalUuid,
    #[serde(deserialize_with = "required_nullable")]
    claim_token: Option<CanonicalUuid>,
    status: AttemptStatusInput,
    phase: AttemptPhaseInput,
    started_at_present: bool,
    completed_at_present: bool,
}

#[derive(Deserialize)]
#[serde(try_from = "String")]
enum HistoryInput {
    Recorded,
    Unrecorded,
}

impl TryFrom<String> for HistoryInput {
    type Error = &'static str;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        match value.as_str() {
            "recorded" => Ok(Self::Recorded),
            "unrecorded" => Ok(Self::Unrecorded),
            _ => Err("invalid test enum"),
        }
    }
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum Operation {
    Running {
        execution_id: CanonicalUuid,
        claim_token: CanonicalUuid,
        process_id: Option<String>,
    },
    Result {
        execution_id: CanonicalUuid,
        #[serde(deserialize_with = "required_nullable")]
        claim_token: Option<CanonicalUuid>,
        outcome: OutcomeInput,
        duration_ms: Option<i64>,
    },
    Cancel {
        execution_id: CanonicalUuid,
    },
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum OutcomeInput {
    Success { status: LogicalStatusInput },
    Failure { error_type: FailureInput },
    CoordinatorLoss {},
}

#[derive(Deserialize)]
#[serde(try_from = "String")]
enum FailureInput {
    Timeout,
    Cancelled,
    ResultPersistence,
    Execution,
}

impl TryFrom<String> for FailureInput {
    type Error = &'static str;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        match value.as_str() {
            "TimeoutError" => Ok(Self::Timeout),
            "CancelledError" => Ok(Self::Cancelled),
            "ResultPersistenceError" => Ok(Self::ResultPersistence),
            "ExecutionError" => Ok(Self::Execution),
            _ => Err("invalid test enum"),
        }
    }
}

#[derive(Deserialize)]
#[serde(try_from = "String")]
enum LogicalStatusInput {
    Scheduled,
    Pending,
    Running,
    Success,
    Failed,
    Timeout,
    Stuck,
    CompletedWithErrors,
    Cancelling,
    Cancelled,
}

impl TryFrom<String> for LogicalStatusInput {
    type Error = &'static str;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        match value.as_str() {
            "Scheduled" => Ok(Self::Scheduled),
            "Pending" => Ok(Self::Pending),
            "Running" => Ok(Self::Running),
            "Success" => Ok(Self::Success),
            "Failed" => Ok(Self::Failed),
            "Timeout" => Ok(Self::Timeout),
            "Stuck" => Ok(Self::Stuck),
            "CompletedWithErrors" => Ok(Self::CompletedWithErrors),
            "Cancelling" => Ok(Self::Cancelling),
            "Cancelled" => Ok(Self::Cancelled),
            _ => Err("invalid test enum"),
        }
    }
}

impl LogicalStatusInput {
    fn domain(self) -> domain::LogicalExecutionStatus {
        match self {
            Self::Scheduled => domain::LogicalExecutionStatus::Scheduled,
            Self::Pending => domain::LogicalExecutionStatus::Pending,
            Self::Running => domain::LogicalExecutionStatus::Running,
            Self::Success => domain::LogicalExecutionStatus::Success,
            Self::Failed => domain::LogicalExecutionStatus::Failed,
            Self::Timeout => domain::LogicalExecutionStatus::Timeout,
            Self::Stuck => domain::LogicalExecutionStatus::Stuck,
            Self::CompletedWithErrors => domain::LogicalExecutionStatus::CompletedWithErrors,
            Self::Cancelling => domain::LogicalExecutionStatus::Cancelling,
            Self::Cancelled => domain::LogicalExecutionStatus::Cancelled,
        }
    }
}

#[derive(Deserialize)]
#[serde(try_from = "String")]
enum AttemptStatusInput {
    Dispatching,
    Published,
    Claimed,
    Running,
    Succeeded,
    Failed,
    TimedOut,
    Cancelled,
    WorkerLost,
    AdmissionRejected,
}

impl TryFrom<String> for AttemptStatusInput {
    type Error = &'static str;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        match value.as_str() {
            "dispatching" => Ok(Self::Dispatching),
            "published" => Ok(Self::Published),
            "claimed" => Ok(Self::Claimed),
            "running" => Ok(Self::Running),
            "succeeded" => Ok(Self::Succeeded),
            "failed" => Ok(Self::Failed),
            "timed_out" => Ok(Self::TimedOut),
            "cancelled" => Ok(Self::Cancelled),
            "worker_lost" => Ok(Self::WorkerLost),
            "admission_rejected" => Ok(Self::AdmissionRejected),
            _ => Err("invalid test enum"),
        }
    }
}

impl AttemptStatusInput {
    fn domain(self) -> domain::AttemptStatus {
        match self {
            Self::Dispatching => domain::AttemptStatus::Dispatching,
            Self::Published => domain::AttemptStatus::Published,
            Self::Claimed => domain::AttemptStatus::Claimed,
            Self::Running => domain::AttemptStatus::Running,
            Self::Succeeded => domain::AttemptStatus::Succeeded,
            Self::Failed => domain::AttemptStatus::Failed,
            Self::TimedOut => domain::AttemptStatus::TimedOut,
            Self::Cancelled => domain::AttemptStatus::Cancelled,
            Self::WorkerLost => domain::AttemptStatus::WorkerLost,
            Self::AdmissionRejected => domain::AttemptStatus::AdmissionRejected,
        }
    }
}

#[derive(Deserialize)]
#[serde(try_from = "String")]
enum AttemptPhaseInput {
    Dispatch,
    Queue,
    Claim,
    Admission,
    Execution,
    Result,
    Terminal,
}

impl TryFrom<String> for AttemptPhaseInput {
    type Error = &'static str;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        match value.as_str() {
            "dispatch" => Ok(Self::Dispatch),
            "queue" => Ok(Self::Queue),
            "claim" => Ok(Self::Claim),
            "admission" => Ok(Self::Admission),
            "execution" => Ok(Self::Execution),
            "result" => Ok(Self::Result),
            "terminal" => Ok(Self::Terminal),
            _ => Err("invalid test enum"),
        }
    }
}

impl AttemptPhaseInput {
    fn domain(self) -> domain::AttemptPhase {
        match self {
            Self::Dispatch => domain::AttemptPhase::Dispatch,
            Self::Queue => domain::AttemptPhase::Queue,
            Self::Claim => domain::AttemptPhase::Claim,
            Self::Admission => domain::AttemptPhase::Admission,
            Self::Execution => domain::AttemptPhase::Execution,
            Self::Result => domain::AttemptPhase::Result,
            Self::Terminal => domain::AttemptPhase::Terminal,
        }
    }
}

#[derive(Serialize)]
struct Response {
    schema: &'static str,
    case_id: String,
    outcome: ResponseOutcome,
}

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum ResponseOutcome {
    Accepted { plan: PlanOutput },
    Rejected { reason: &'static str },
}

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum PlanOutput {
    Running {
        status: &'static str,
        phase: &'static str,
        started_at: &'static str,
        heartbeat_at: &'static str,
        process_id: &'static str,
    },
    Result {
        execution: ExecutionPlanOutput,
        attempt: AttemptPlanOutput,
    },
    Cancel {
        status: &'static str,
        completed_at: &'static str,
        attempt: Option<CancelAttemptOutput>,
    },
}

#[derive(Serialize)]
struct ExecutionPlanOutput {
    status: &'static str,
    duration_ms: &'static str,
    completed_at: &'static str,
    result: &'static str,
    result_type: &'static str,
    error_message: &'static str,
    time_saved: &'static str,
    value: &'static str,
    variables: &'static str,
    execution_context: &'static str,
    metrics: &'static str,
    logs: &'static str,
}

#[derive(Serialize)]
struct AttemptPlanOutput {
    status: &'static str,
    phase: &'static str,
    failure_phase: Option<&'static str>,
    failure_code: Option<&'static str>,
    started_at: &'static str,
    heartbeat_at: &'static str,
    completed_at: &'static str,
    duration_ms: &'static str,
    peak_memory_bytes: &'static str,
    cpu_total_seconds: &'static str,
}

#[derive(Serialize)]
struct CancelAttemptOutput {
    status: &'static str,
    phase: &'static str,
    failure_phase: &'static str,
    failure_code: &'static str,
    completed_at: &'static str,
    heartbeat_at: &'static str,
}

fn logical_status(value: domain::LogicalExecutionStatus) -> &'static str {
    match value {
        domain::LogicalExecutionStatus::Scheduled => "Scheduled",
        domain::LogicalExecutionStatus::Pending => "Pending",
        domain::LogicalExecutionStatus::Running => "Running",
        domain::LogicalExecutionStatus::Success => "Success",
        domain::LogicalExecutionStatus::Failed => "Failed",
        domain::LogicalExecutionStatus::Timeout => "Timeout",
        domain::LogicalExecutionStatus::Stuck => "Stuck",
        domain::LogicalExecutionStatus::CompletedWithErrors => "CompletedWithErrors",
        domain::LogicalExecutionStatus::Cancelling => "Cancelling",
        domain::LogicalExecutionStatus::Cancelled => "Cancelled",
    }
}

fn attempt_status(value: domain::AttemptStatus) -> &'static str {
    match value {
        domain::AttemptStatus::Dispatching => "Dispatching",
        domain::AttemptStatus::Published => "Published",
        domain::AttemptStatus::Claimed => "Claimed",
        domain::AttemptStatus::Running => "Running",
        domain::AttemptStatus::Succeeded => "Succeeded",
        domain::AttemptStatus::Failed => "Failed",
        domain::AttemptStatus::TimedOut => "TimedOut",
        domain::AttemptStatus::Cancelled => "Cancelled",
        domain::AttemptStatus::WorkerLost => "WorkerLost",
        domain::AttemptStatus::AdmissionRejected => "AdmissionRejected",
    }
}

fn attempt_phase(value: domain::AttemptPhase) -> &'static str {
    match value {
        domain::AttemptPhase::Dispatch => "Dispatch",
        domain::AttemptPhase::Queue => "Queue",
        domain::AttemptPhase::Claim => "Claim",
        domain::AttemptPhase::Admission => "Admission",
        domain::AttemptPhase::Execution => "Execution",
        domain::AttemptPhase::Result => "Result",
        domain::AttemptPhase::Terminal => "Terminal",
    }
}

fn failure_phase(value: domain::FailurePhase) -> &'static str {
    match value {
        domain::FailurePhase::Dispatch => "Dispatch",
        domain::FailurePhase::Queue => "Queue",
        domain::FailurePhase::Claim => "Claim",
        domain::FailurePhase::Admission => "Admission",
        domain::FailurePhase::Execution => "Execution",
        domain::FailurePhase::Result => "Result",
        domain::FailurePhase::Worker => "Worker",
        domain::FailurePhase::Cancellation => "Cancellation",
    }
}

fn failure_code(value: domain::FailureCode) -> &'static str {
    match value {
        domain::FailureCode::ExecutionTimeout => "ExecutionTimeout",
        domain::FailureCode::Cancelled => "Cancelled",
        domain::FailureCode::ResultPersistFailed => "ResultPersistFailed",
        domain::FailureCode::TenantCodeError => "TenantCodeError",
        domain::FailureCode::CancelledBeforeClaim => "CancelledBeforeClaim",
    }
}

fn time_write(value: domain::TimeWrite) -> &'static str {
    match value {
        domain::TimeWrite::Keep => "Keep",
        domain::TimeWrite::Now => "Now",
    }
}

fn input_write(value: domain::InputWrite) -> &'static str {
    match value {
        domain::InputWrite::Keep => "Keep",
        domain::InputWrite::SetSupplied => "SetSupplied",
    }
}

fn projection(value: domain::ProjectionPermission) -> &'static str {
    match value {
        domain::ProjectionPermission::Keep => "Keep",
        domain::ProjectionPermission::AllowSupplied => "AllowSupplied",
    }
}

fn decision(value: domain::DecisionError) -> &'static str {
    match value {
        domain::DecisionError::MissingExecution => "MissingExecution",
        domain::DecisionError::MissingAttempt => "MissingAttempt",
        domain::DecisionError::InvalidAttemptFence => "InvalidAttemptFence",
        domain::DecisionError::InvalidAttemptState => "InvalidAttemptState",
        domain::DecisionError::InvalidLogicalState => "InvalidLogicalState",
        domain::DecisionError::MissingFence => "MissingFence",
        domain::DecisionError::LegacyUnfencedOutsideTrackedPath => {
            "LegacyUnfencedOutsideTrackedPath"
        }
        domain::DecisionError::RequiresCoordinatorPolicy => "RequiresCoordinatorPolicy",
        domain::DecisionError::InconsistentRows => "InconsistentRows",
    }
}

fn running_plan(plan: domain::AttemptRunningPlan) -> PlanOutput {
    let domain::AttemptRunningPlan {
        status,
        phase,
        started_at,
        heartbeat_at,
        process_id,
    } = plan;
    PlanOutput::Running {
        status: attempt_status(status),
        phase: attempt_phase(phase),
        started_at: time_write(started_at),
        heartbeat_at: time_write(heartbeat_at),
        process_id: input_write(process_id),
    }
}

fn result_plan(plan: domain::FencedResultPlan) -> PlanOutput {
    let domain::FencedResultPlan { execution, attempt } = plan;
    let domain::ResultExecutionPlan {
        status,
        duration_ms,
        completed_at,
        result,
        result_type,
        error_message,
        time_saved,
        value,
        variables,
        execution_context,
        metrics,
        logs,
    } = execution;
    let execution = ExecutionPlanOutput {
        status: logical_status(status),
        duration_ms: input_write(duration_ms),
        completed_at: time_write(completed_at),
        result: projection(result),
        result_type: projection(result_type),
        error_message: projection(error_message),
        time_saved: projection(time_saved),
        value: projection(value),
        variables: projection(variables),
        execution_context: projection(execution_context),
        metrics: projection(metrics),
        logs: projection(logs),
    };
    let domain::ResultAttemptPlan {
        status,
        phase,
        failure_phase: phase_failure,
        failure_code: code_failure,
        started_at,
        heartbeat_at,
        completed_at,
        duration_ms,
        peak_memory_bytes,
        cpu_total_seconds,
    } = attempt;
    let attempt = AttemptPlanOutput {
        status: attempt_status(status),
        phase: attempt_phase(phase),
        failure_phase: phase_failure.map(failure_phase),
        failure_code: code_failure.map(failure_code),
        started_at: time_write(started_at),
        heartbeat_at: time_write(heartbeat_at),
        completed_at: time_write(completed_at),
        duration_ms: input_write(duration_ms),
        peak_memory_bytes: input_write(peak_memory_bytes),
        cpu_total_seconds: input_write(cpu_total_seconds),
    };
    PlanOutput::Result { execution, attempt }
}

fn cancel_plan(plan: domain::CancelPlan) -> PlanOutput {
    let domain::CancelPlan {
        status,
        completed_at,
        attempt,
    } = plan;
    PlanOutput::Cancel {
        status: logical_status(status),
        completed_at: time_write(completed_at),
        attempt: attempt.map(|plan| {
            let domain::CancelAttemptPlan {
                status,
                phase,
                failure_phase: phase_failure,
                failure_code: code_failure,
                completed_at,
                heartbeat_at,
            } = plan;
            CancelAttemptOutput {
                status: attempt_status(status),
                phase: attempt_phase(phase),
                failure_phase: failure_phase(phase_failure),
                failure_code: failure_code(code_failure),
                completed_at: time_write(completed_at),
                heartbeat_at: time_write(heartbeat_at),
            }
        }),
    }
}

#[derive(Debug, PartialEq, Eq)]
enum DriverError {
    InvalidRequest,
    Io,
}

fn validate_json_shapes(bytes: &[u8]) -> Result<(), DriverError> {
    // This finite type check never normalizes bytes or constructs authority.
    // Deserialize the ORIGINAL bytes afterward so duplicates remain errors.
    let raw: serde_json::Value =
        serde_json::from_slice(bytes).map_err(|_| DriverError::InvalidRequest)?;
    if !raw.is_object()
        || !raw["schema"].is_string()
        || !raw["rows"].is_object()
        || !raw["rows"]["history"].is_string()
        || !raw["operation"].is_object()
        || !raw["operation"]["kind"].is_string()
    {
        return Err(DriverError::InvalidRequest);
    }
    let execution = &raw["rows"]["execution"];
    if !execution.is_null() && (!execution.is_object() || !execution["status"].is_string()) {
        return Err(DriverError::InvalidRequest);
    }
    let attempt = &raw["rows"]["attempt"];
    if !attempt.is_null()
        && (!attempt.is_object() || !attempt["status"].is_string() || !attempt["phase"].is_string())
    {
        return Err(DriverError::InvalidRequest);
    }
    if raw["operation"]["kind"] == "result" {
        let outcome = &raw["operation"]["outcome"];
        if !outcome.is_object() || !outcome["kind"].is_string() {
            return Err(DriverError::InvalidRequest);
        }
        if outcome["kind"] == "success" && !outcome["status"].is_string() {
            return Err(DriverError::InvalidRequest);
        }
        if outcome["kind"] == "failure" && !outcome["error_type"].is_string() {
            return Err(DriverError::InvalidRequest);
        }
    }
    Ok(())
}

fn execute(bytes: &[u8]) -> Result<Response, DriverError> {
    validate_json_shapes(bytes)?;
    let request: Request =
        serde_json::from_slice(bytes).map_err(|_| DriverError::InvalidRequest)?;
    let Request {
        schema,
        case_id,
        operation,
        rows,
    } = request;
    let Schema::WorkflowDomainV1 = schema;
    if case_id.is_empty()
        || case_id.len() > 64
        || !case_id.as_bytes()[0].is_ascii_alphabetic()
        || !case_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(DriverError::InvalidRequest);
    }
    let execution = rows.execution.map(|row| domain::LogicalExecutionView {
        id: row.id,
        status: row.status.domain(),
    });
    let attempt = rows.attempt.map(|row| domain::WorkflowAttemptView {
        id: row.id,
        execution_id: row.execution_id,
        claim_token: row.claim_token.map(domain::ClaimToken::new),
        status: row.status.domain(),
        phase: row.phase.domain(),
        started_at_present: row.started_at_present,
        completed_at_present: row.completed_at_present,
    });
    let history = match rows.history {
        HistoryInput::Recorded => domain::AttemptHistory::Recorded,
        HistoryInput::Unrecorded => domain::AttemptHistory::Unrecorded,
    };
    let result = match operation {
        Operation::Running {
            execution_id,
            claim_token,
            process_id,
        } => {
            let token = domain::ClaimToken::new(claim_token);
            domain::plan_attempt_running(
                attempt.as_ref(),
                &execution_id,
                &token,
                process_id.is_some(),
            )
            .map(running_plan)
        }
        Operation::Result {
            execution_id,
            claim_token,
            outcome,
            duration_ms,
        } => {
            let token = claim_token.map(domain::ClaimToken::new);
            let outcome = match outcome {
                OutcomeInput::Success { status } => {
                    domain::WorkflowOutcome::Success(status.domain())
                }
                OutcomeInput::Failure { error_type } => {
                    domain::WorkflowOutcome::Failure(match error_type {
                        FailureInput::Timeout => domain::RuntimeFailureKind::Timeout,
                        FailureInput::Cancelled => domain::RuntimeFailureKind::Cancelled,
                        FailureInput::ResultPersistence => {
                            domain::RuntimeFailureKind::ResultPersistence
                        }
                        FailureInput::Execution => domain::RuntimeFailureKind::TenantCode,
                    })
                }
                OutcomeInput::CoordinatorLoss {} => domain::WorkflowOutcome::CoordinatorLoss,
            };
            domain::plan_fenced_result(
                execution.as_ref(),
                attempt.as_ref(),
                &execution_id,
                token.as_ref(),
                history,
                outcome,
                duration_ms.is_some(),
            )
            .map(result_plan)
        }
        Operation::Cancel { execution_id } => {
            domain::plan_cancel_state(execution.as_ref(), attempt.as_ref(), &execution_id)
                .map(cancel_plan)
        }
    };
    Ok(Response {
        schema: SCHEMA,
        case_id,
        outcome: match result {
            Ok(plan) => ResponseOutcome::Accepted { plan },
            Err(error) => ResponseOutcome::Rejected {
                reason: decision(error),
            },
        },
    })
}

fn run(input: &mut impl Read, output: &mut impl Write) -> Result<(), DriverError> {
    // Allocation is fixed DURING reads; the extra byte is the overlimit sentinel.
    let mut buffer = [0_u8; INPUT_LIMIT + 1];
    let mut used = 0;
    while used < buffer.len() {
        let read = input
            .read(&mut buffer[used..])
            .map_err(|_| DriverError::Io)?;
        if read == 0 {
            break;
        }
        used += read;
    }
    if used > INPUT_LIMIT {
        return Err(DriverError::InvalidRequest);
    }
    let response = execute(&buffer[..used])?;
    // Only static enum/field labels and the bounded safe case label are encoded.
    // This finite DTO cannot echo rows, tokens, process strings or payloads.
    let mut encoded = serde_json::to_vec(&response).map_err(|_| DriverError::Io)?;
    encoded.push(b'\n');
    if encoded.len() > OUTPUT_LIMIT {
        return Err(DriverError::Io);
    }
    output.write_all(&encoded).map_err(|_| DriverError::Io)?;
    output.flush().map_err(|_| DriverError::Io)
}

fn main() -> ExitCode {
    match run(&mut io::stdin().lock(), &mut io::stdout().lock()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(DriverError::InvalidRequest) => {
            eprintln!("invalid workflow-domain request");
            ExitCode::from(2)
        }
        Err(DriverError::Io) => {
            eprintln!("workflow-domain driver failed");
            ExitCode::from(3)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    const EXECUTION_ID: &str = "00000000-0000-4000-8000-000000000001";
    const ATTEMPT_ID: &str = "00000000-0000-4000-8000-000000000002";
    const TOKEN: &str = "00000000-0000-4000-8000-000000000003";

    fn fixture(operation: Value) -> Value {
        json!({
            "schema": SCHEMA,
            "case_id": "synthetic_case",
            "operation": operation,
            "rows": {
                "execution": {"id": EXECUTION_ID, "status": "Running"},
                "attempt": {
                    "id": ATTEMPT_ID,
                    "execution_id": EXECUTION_ID,
                    "claim_token": TOKEN,
                    "status": "claimed",
                    "phase": "claim",
                    "started_at_present": false,
                    "completed_at_present": false
                },
                "history": "recorded"
            }
        })
    }

    fn running() -> Value {
        fixture(json!({
            "kind": "running", "execution_id": EXECUTION_ID,
            "claim_token": TOKEN, "process_id": null
        }))
    }

    fn result(outcome: Value) -> Value {
        fixture(json!({
            "kind": "result", "execution_id": EXECUTION_ID,
            "claim_token": TOKEN, "outcome": outcome, "duration_ms": null
        }))
    }

    fn bytes(value: &Value) -> Vec<u8> {
        match serde_json::to_vec(value) {
            Ok(bytes) => bytes,
            Err(_) => panic!("Synthetic fixture encoding failed"),
        }
    }

    fn response(value: &Value) -> Value {
        let input = bytes(value);
        let mut output = Vec::new();
        assert!(run(&mut input.as_slice(), &mut output).is_ok());
        assert!(output.len() <= OUTPUT_LIMIT);
        assert!(output.ends_with(b"\n"));
        // No stored/submitted UUID, capability, process string or row is echoed.
        for forbidden in [EXECUTION_ID, ATTEMPT_ID, TOKEN, "synthetic-process-secret"] {
            assert!(
                !output
                    .windows(forbidden.len())
                    .any(|part| part == forbidden.as_bytes())
            );
        }
        match serde_json::from_slice(&output) {
            Ok(value) => value,
            Err(_) => panic!("Driver emitted invalid synthetic response"),
        }
    }

    fn invalid(value: &Value) {
        let input = bytes(value);
        let mut output = Vec::new();
        assert_eq!(
            run(&mut input.as_slice(), &mut output),
            Err(DriverError::InvalidRequest)
        );
        assert!(output.is_empty());
    }

    #[test]
    fn actual_running_call_outputs_all_columns_and_non_none_empty_process() {
        let mut request = running();
        assert_eq!(
            response(&request),
            json!({
                "schema": SCHEMA, "case_id": "synthetic_case",
                "outcome": {"kind": "accepted", "plan": {
                    "kind": "running", "status": "Running", "phase": "Execution",
                    "started_at": "Now", "heartbeat_at": "Now", "process_id": "Keep"
                }}
            })
        );
        request["rows"]["attempt"]["started_at_present"] = json!(true);
        for process in ["", "synthetic-process-secret"] {
            request["operation"]["process_id"] = json!(process);
            let actual = response(&request);
            assert_eq!(actual["outcome"]["plan"]["started_at"], "Keep");
            assert_eq!(actual["outcome"]["plan"]["process_id"], "SetSupplied");
        }
        if let Some(operation) = request["operation"].as_object_mut() {
            assert!(operation.remove("process_id").is_some());
        } else {
            panic!("Synthetic operation object is missing");
        }
        assert_eq!(response(&request)["outcome"]["plan"]["process_id"], "Keep");
    }

    #[test]
    fn result_response_maps_all_actual_fields_and_null_failure_values() {
        let mut request = result(json!({"kind": "success", "status": "Success"}));
        assert_eq!(
            response(&request),
            json!({
                "schema": SCHEMA, "case_id": "synthetic_case",
                "outcome": {"kind": "accepted", "plan": {
                    "kind": "result",
                    "execution": {
                        "status": "Success", "duration_ms": "Keep", "completed_at": "Keep",
                        "result": "AllowSupplied", "result_type": "AllowSupplied",
                        "error_message": "AllowSupplied", "time_saved": "AllowSupplied",
                        "value": "AllowSupplied", "variables": "AllowSupplied",
                        "execution_context": "AllowSupplied", "metrics": "AllowSupplied",
                        "logs": "AllowSupplied"
                    },
                    "attempt": {
                        "status": "Succeeded", "phase": "Terminal",
                        "failure_phase": null, "failure_code": null, "started_at": "Keep",
                        "heartbeat_at": "Now", "completed_at": "Now", "duration_ms": "SetSupplied",
                        "peak_memory_bytes": "SetSupplied", "cpu_total_seconds": "SetSupplied"
                    }
                }}
            })
        );
        for duration in [0, 7] {
            request["operation"]["duration_ms"] = json!(duration);
            let actual = response(&request);
            assert_eq!(
                actual["outcome"]["plan"]["execution"]["duration_ms"],
                "SetSupplied"
            );
            assert_eq!(
                actual["outcome"]["plan"]["execution"]["completed_at"],
                "Now"
            );
        }
        if let Some(operation) = request["operation"].as_object_mut() {
            assert!(operation.remove("duration_ms").is_some());
        } else {
            panic!("Synthetic operation object is missing");
        }
        assert_eq!(
            response(&request)["outcome"]["plan"]["execution"]["duration_ms"],
            "Keep"
        );
    }

    #[test]
    fn all_ten_normalized_success_strings_pass_through_actual_kernel() {
        for status in [
            "Scheduled",
            "Pending",
            "Running",
            "Success",
            "Failed",
            "Timeout",
            "Stuck",
            "CompletedWithErrors",
            "Cancelling",
            "Cancelled",
        ] {
            let request = result(json!({"kind": "success", "status": status}));
            let actual = response(&request);
            assert_eq!(actual["outcome"]["plan"]["execution"]["status"], status);
            assert_eq!(actual["outcome"]["plan"]["attempt"]["status"], "Succeeded");
            assert_eq!(
                actual["outcome"]["plan"]["execution"]["result"],
                "AllowSupplied"
            );
        }
    }

    #[test]
    fn four_failure_inputs_and_coordinator_cancellation_are_real_calls() {
        for (error, status, attempt, code, phase) in [
            (
                "TimeoutError",
                "Timeout",
                "TimedOut",
                "ExecutionTimeout",
                "Execution",
            ),
            (
                "CancelledError",
                "Cancelled",
                "Cancelled",
                "Cancelled",
                "Cancellation",
            ),
            (
                "ResultPersistenceError",
                "Failed",
                "Failed",
                "ResultPersistFailed",
                "Result",
            ),
            (
                "ExecutionError",
                "Failed",
                "Failed",
                "TenantCodeError",
                "Execution",
            ),
        ] {
            let request = result(json!({"kind": "failure", "error_type": error}));
            let actual = response(&request);
            assert_eq!(actual["outcome"]["plan"]["execution"]["status"], status);
            assert_eq!(actual["outcome"]["plan"]["attempt"]["status"], attempt);
            assert_eq!(actual["outcome"]["plan"]["attempt"]["failure_code"], code);
            assert_eq!(actual["outcome"]["plan"]["attempt"]["failure_phase"], phase);
        }
        let mut request = result(json!({"kind": "coordinator_loss"}));
        assert_eq!(
            response(&request)["outcome"],
            json!({"kind": "rejected", "reason": "RequiresCoordinatorPolicy"})
        );
        request["rows"]["execution"]["status"] = json!("Cancelling");
        let actual = response(&request);
        assert_eq!(
            actual["outcome"]["plan"]["execution"]["status"],
            "Cancelled"
        );
        for column in [
            "result",
            "result_type",
            "error_message",
            "time_saved",
            "value",
        ] {
            assert_eq!(actual["outcome"]["plan"]["execution"][column], "Keep");
        }
        for column in ["variables", "execution_context", "metrics", "logs"] {
            assert_eq!(
                actual["outcome"]["plan"]["execution"][column],
                "AllowSupplied"
            );
        }
        assert_eq!(
            actual["outcome"]["plan"]["attempt"]["failure_phase"],
            "Cancellation"
        );
    }

    #[test]
    fn cancel_response_maps_all_actual_columns_and_nullable_attempt() {
        let mut request = fixture(json!({"kind": "cancel", "execution_id": EXECUTION_ID}));
        request["rows"]["execution"]["status"] = json!("Pending");
        assert_eq!(
            response(&request)["outcome"]["plan"],
            json!({
                "kind": "cancel", "status": "Cancelled", "completed_at": "Now",
                "attempt": {
                    "status": "Cancelled", "phase": "Terminal", "failure_phase": "Cancellation",
                    "failure_code": "CancelledBeforeClaim", "completed_at": "Now", "heartbeat_at": "Now"
                }
            })
        );
        request["rows"]["attempt"] = Value::Null;
        assert_eq!(
            response(&request)["outcome"]["plan"]["attempt"],
            Value::Null
        );
        request["rows"]["execution"]["status"] = json!("Running");
        assert_eq!(
            response(&request)["outcome"]["plan"],
            json!({"kind": "cancel", "status": "Cancelling", "completed_at": "Keep", "attempt": null})
        );
    }

    #[test]
    fn actual_rejection_and_required_null_history_precedence_are_closed() {
        let mut request = running();
        request["operation"]["claim_token"] = json!(EXECUTION_ID);
        assert_eq!(
            response(&request)["outcome"],
            json!({"kind": "rejected", "reason": "InvalidAttemptFence"})
        );
        request["rows"]["attempt"] = Value::Null;
        assert_eq!(response(&request)["outcome"]["reason"], "MissingAttempt");
        let mut request = result(json!({"kind": "success", "status": "Success"}));
        request["operation"]["claim_token"] = Value::Null;
        request["rows"]["execution"] = Value::Null;
        assert_eq!(response(&request)["outcome"]["reason"], "MissingFence");
        request["rows"]["history"] = json!("unrecorded");
        assert_eq!(
            response(&request)["outcome"]["reason"],
            "LegacyUnfencedOutsideTrackedPath"
        );
    }

    #[test]
    fn unknown_fields_are_rejected_at_every_request_object_level() {
        let originals = [
            running(),
            result(json!({"kind": "success", "status": "Success"})),
            result(json!({"kind": "failure", "error_type": "ExecutionError"})),
            result(json!({"kind": "coordinator_loss"})),
        ];
        for original in originals {
            for pointer in [
                "",
                "/rows",
                "/rows/execution",
                "/rows/attempt",
                "/operation",
            ] {
                let mut request = original.clone();
                if let Some(Value::Object(object)) = request.pointer_mut(pointer) {
                    assert!(
                        object
                            .insert("unknown_secret".to_owned(), json!("synthetic-secret"))
                            .is_none()
                    );
                } else {
                    panic!("Synthetic object pointer is missing");
                }
                invalid(&request);
            }
            if original["operation"]["kind"] == "result" {
                let mut request = original;
                if let Some(Value::Object(object)) = request.pointer_mut("/operation/outcome") {
                    assert!(
                        object
                            .insert("unknown_secret".to_owned(), json!("synthetic-secret"))
                            .is_none()
                    );
                } else {
                    panic!("Synthetic outcome pointer is missing");
                }
                invalid(&request);
            }
        }
    }

    #[test]
    fn mandatory_nullable_fields_cannot_be_inferred_from_omission() {
        for (parent, field) in [
            ("/rows", "execution"),
            ("/rows", "attempt"),
            ("/rows/attempt", "claim_token"),
        ] {
            let mut request = running();
            if let Some(Value::Object(object)) = request.pointer_mut(parent) {
                assert!(object.remove(field).is_some());
            } else {
                panic!("Synthetic nullable parent is missing");
            }
            invalid(&request);
        }
        let mut request = result(json!({"kind": "success", "status": "Success"}));
        if let Some(operation) = request["operation"].as_object_mut() {
            assert!(operation.remove("claim_token").is_some());
        } else {
            panic!("Synthetic operation object is missing");
        }
        invalid(&request);
    }

    #[test]
    fn enum_uuid_schema_label_and_scalar_errors_fail_without_echo() {
        let original = running();
        for (pointer, value) in [
            ("/schema", json!("wrong-schema")),
            ("/case_id", json!("")),
            ("/case_id", json!("9_bad")),
            ("/case_id", json!("not safe")),
            ("/case_id", json!("é_secret")),
            ("/case_id", json!("x".repeat(65))),
            ("/operation/kind", json!("unknown")),
            ("/operation/execution_id", json!("NOT-UUID")),
            (
                "/operation/claim_token",
                json!("00000000-0000-4000-8000-00000000000A"),
            ),
            ("/rows/history", json!("assumed")),
            ("/rows/execution/status", json!("running")),
            ("/rows/attempt/status", json!("Claimed")),
            ("/rows/attempt/phase", json!("owner")),
            ("/rows/attempt/completed_at_present", json!(1)),
        ] {
            let mut request = original.clone();
            if let Some(target) = request.pointer_mut(pointer) {
                *target = value;
            } else {
                panic!("Synthetic invalid field is missing");
            }
            invalid(&request);
        }
        let mut request = result(json!({"kind": "failure", "error_type": "WorkerShutdownError"}));
        invalid(&request);
        request["operation"]["outcome"] = json!({"kind": "success", "status": "unknown"});
        invalid(&request);
        request["operation"]["outcome"] = json!({"kind": "success", "status": "Success"});
        request["operation"]["duration_ms"] = json!(1.5);
        invalid(&request);
    }

    #[test]
    fn malformed_trailing_duplicate_and_overlimit_inputs_never_emit_response() {
        let valid = bytes(&running());
        let mut double = valid.clone();
        double.extend_from_slice(&valid);
        let mut duplicate = valid.clone();
        assert_eq!(duplicate.pop(), Some(b'}'));
        duplicate.extend_from_slice(br#","schema":"bifrost.test.workflow-domain/v1"}"#);
        for input in [b"{".to_vec(), vec![0xff], double, duplicate] {
            let mut output = Vec::new();
            assert_eq!(
                run(&mut input.as_slice(), &mut output),
                Err(DriverError::InvalidRequest)
            );
            assert!(output.is_empty());
        }
        let mut at_limit = valid;
        at_limit.resize(INPUT_LIMIT, b' ');
        let mut output = Vec::new();
        assert!(run(&mut at_limit.as_slice(), &mut output).is_ok());
        let mut too_large = at_limit;
        too_large.extend_from_slice(&[b' '; 8]);
        let mut input = io::Cursor::new(too_large);
        let mut output = Vec::new();
        assert_eq!(
            run(&mut input, &mut output),
            Err(DriverError::InvalidRequest)
        );
        assert_eq!(input.position(), (INPUT_LIMIT + 1) as u64);
        assert!(output.is_empty());
    }

    #[test]
    fn largest_safe_label_and_static_plan_remain_below_output_ceiling() {
        let mut request = result(json!({"kind": "success", "status": "CompletedWithErrors"}));
        request["case_id"] = json!("x".repeat(64));
        request["operation"]["duration_ms"] = json!(0);
        let actual = response(&request);
        assert_eq!(actual["case_id"], "x".repeat(64));
        assert!(bytes(&actual).len() + 1 <= OUTPUT_LIMIT);
    }

    #[test]
    fn reader_and_writer_failures_are_static_driver_errors() {
        struct FailedRead;
        impl Read for FailedRead {
            fn read(&mut self, _buffer: &mut [u8]) -> io::Result<usize> {
                Err(io::Error::other("synthetic-sensitive-read"))
            }
        }
        struct FailedWrite;
        impl Write for FailedWrite {
            fn write(&mut self, _buffer: &[u8]) -> io::Result<usize> {
                Err(io::Error::other("synthetic-sensitive-write"))
            }
            fn flush(&mut self) -> io::Result<()> {
                Err(io::Error::other("synthetic-sensitive-flush"))
            }
        }
        let mut output = Vec::new();
        assert_eq!(run(&mut FailedRead, &mut output), Err(DriverError::Io));
        assert!(output.is_empty());
        let input = bytes(&running());
        assert_eq!(
            run(&mut input.as_slice(), &mut FailedWrite),
            Err(DriverError::Io)
        );
    }
}

#[cfg(test)]
mod strict_shape_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn request_and_unit_enum_alternative_serde_representations_are_invalid() {
        let id = "00000000-0000-4000-8000-000000000001";
        let original = json!({
            "schema": SCHEMA, "case_id": "synthetic_shape",
            "operation": {"kind": "result", "execution_id": id, "claim_token": null,
                "outcome": {"kind": "failure", "error_type": "ExecutionError"}, "duration_ms": null},
            "rows": {"execution": {"id": id, "status": "Running"},
                "attempt": {"id": id, "execution_id": id, "claim_token": null,
                    "status": "running", "phase": "execution",
                    "started_at_present": true, "completed_at_present": false}, "history": "recorded"}
        });
        let alternatives = [
            ("/schema", json!({SCHEMA: null})),
            (
                "/rows",
                json!([original["rows"]["execution"], null, "recorded"]),
            ),
            ("/rows/execution", json!([id, "Running"])),
            ("/rows/execution/status", json!({"Running": null})),
            ("/rows/history", json!({"recorded": null})),
            (
                "/rows/attempt",
                json!([id, id, null, "running", "execution", true, false]),
            ),
            ("/rows/attempt/status", json!({"running": null})),
            ("/rows/attempt/phase", json!({"execution": null})),
            ("/rows/attempt/status", json!(3)),
            (
                "/operation",
                json!(["result", id, null, {"kind": "failure", "error_type": "ExecutionError"}, null]),
            ),
            ("/operation/kind", json!(1)),
            ("/operation/outcome", json!(["failure", "ExecutionError"])),
            ("/operation/outcome/kind", json!(1)),
            (
                "/operation/outcome/error_type",
                json!({"ExecutionError": null}),
            ),
        ];
        for (pointer, alternative) in alternatives {
            let mut request = original.clone();
            if let Some(value) = request.pointer_mut(pointer) {
                *value = alternative;
            } else {
                panic!("Synthetic structural node is missing");
            }
            let encoded = match serde_json::to_vec(&request) {
                Ok(bytes) => bytes,
                Err(_) => panic!("Synthetic structural encoding failed"),
            };
            assert!(matches!(
                execute(&encoded),
                Err(DriverError::InvalidRequest)
            ));
        }
        let mut success = original.clone();
        success["operation"]["outcome"] = json!({"kind": "success", "status": {"Success": null}});
        let encoded = match serde_json::to_vec(&success) {
            Ok(bytes) => bytes,
            Err(_) => panic!("Synthetic success encoding failed"),
        };
        assert!(matches!(
            execute(&encoded),
            Err(DriverError::InvalidRequest)
        ));
        let root_array = json!([
            SCHEMA,
            "synthetic_shape",
            original["operation"],
            original["rows"]
        ]);
        let encoded = match serde_json::to_vec(&root_array) {
            Ok(bytes) => bytes,
            Err(_) => panic!("Synthetic root encoding failed"),
        };
        assert!(matches!(
            execute(&encoded),
            Err(DriverError::InvalidRequest)
        ));
    }
}
