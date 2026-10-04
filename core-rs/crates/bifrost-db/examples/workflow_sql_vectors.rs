//! Private, one-shot Result SQL bridge; no application owner or public protocol.
//! The supported parent owns deadline, process disposal and source/image custody.
use bifrost_contracts::runtime::CanonicalUuid;
use bifrost_db::{
    workflow_numeric::NumericInput,
    workflow_parity::{
        self as sql, AppliedResult, FailureResultFields, PreparedJson, ResultJsonRole,
        ResultMetrics, ResultPresence, ResultRoi, ResultSqlFailure, ResultSqlFailureClass,
        ResultSqlStage, SqlClaimFence, SqlDecision, SqlResultInput, SuccessResultFields,
    },
};
use bifrost_domain::workflow as domain;
use serde::{
    Deserialize,
    de::{self, MapAccess, Visitor},
};
use serde_json::{Value, json, value::RawValue};
use sqlx::{
    Acquire, ConnectOptions,
    postgres::{PgConnectOptions, PgPoolOptions},
};
use std::{
    collections::BTreeMap,
    fmt,
    io::{self, Read, Write},
    process::ExitCode,
    str::FromStr,
    time::Duration,
};

const INPUT_LIMIT: usize = 65_536;
const OUTPUT_LIMIT: usize = 4_096;
type Parse<T> = Result<T, ()>;

// No formatting traits: all diagnostics are closed static labels.
enum Node {
    Null,
    Bool(bool),
    String(String),
    Number(String),
    Array(Vec<Node>),
    Object(BTreeMap<String, Node>),
}
struct RawObject(BTreeMap<String, Box<RawValue>>);
impl<'de> Deserialize<'de> for RawObject {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor;
        impl<'de> Visitor<'de> for ObjectVisitor {
            type Value = RawObject;
            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("closed object")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<RawObject, A::Error> {
                let mut result = BTreeMap::new();
                while let Some(key) = map.next_key::<String>()? {
                    if result.contains_key(&key) {
                        return Err(de::Error::custom("duplicate member"));
                    }
                    result.insert(key, map.next_value::<Box<RawValue>>()?);
                }
                Ok(RawObject(result))
            }
        }
        deserializer.deserialize_map(ObjectVisitor)
    }
}

impl Node {
    fn parse(raw: &str, depth: usize) -> Parse<Self> {
        let raw = raw.trim_matches([' ', '\n', '\r', '\t']);
        match raw.as_bytes().first().copied() {
            Some(b'{') => {
                if depth >= 64 {
                    return Err(());
                }
                let RawObject(members) = serde_json::from_str(raw).map_err(|_| ())?;
                let mut nodes = BTreeMap::new();
                for (key, value) in members {
                    if key.contains('\0') {
                        return Err(());
                    }
                    nodes.insert(key, Self::parse(value.get(), depth + 1)?);
                }
                Ok(Self::Object(nodes))
            }
            Some(b'[') => {
                if depth >= 64 {
                    return Err(());
                }
                let values: Vec<Box<RawValue>> = serde_json::from_str(raw).map_err(|_| ())?;
                let nodes = values
                    .iter()
                    .map(|value| Self::parse(value.get(), depth + 1))
                    .collect::<Parse<Vec<_>>>()?;
                Ok(Self::Array(nodes))
            }
            Some(b'"') => {
                let value: String = serde_json::from_str(raw).map_err(|_| ())?;
                if value.contains('\0') {
                    return Err(());
                }
                Ok(Self::String(value))
            }
            Some(b't' | b'f') => Ok(Self::Bool(serde_json::from_str(raw).map_err(|_| ())?)),
            Some(b'n') if raw == "null" => Ok(Self::Null),
            Some(b'-' | b'0'..=b'9') => {
                // RawValue validates grammar without rounding/normalizing a Number.
                let _: Box<RawValue> = serde_json::from_str(raw).map_err(|_| ())?;
                Ok(Self::Number(raw.to_owned()))
            }
            _ => Err(()),
        }
    }
    fn object(&self) -> Parse<&BTreeMap<String, Node>> {
        if let Self::Object(value) = self {
            Ok(value)
        } else {
            Err(())
        }
    }
    fn string(&self) -> Parse<&str> {
        if let Self::String(value) = self {
            Ok(value)
        } else {
            Err(())
        }
    }
    fn keys(&self, keys: &[&str]) -> Parse<()> {
        let map = self.object()?;
        if map.len() == keys.len() && keys.iter().all(|key| map.contains_key(*key)) {
            Ok(())
        } else {
            Err(())
        }
    }
    fn member(&self, key: &str) -> Parse<&Node> {
        self.object()?.get(key).ok_or(())
    }
    fn numeric(&self) -> Parse<NumericInput> {
        let Self::Number(text) = self else {
            return Err(());
        };
        numeric(text)
    }
    // Only private JSONdata integers are canonicalized. The original lexical
    // tree remains the numeric classifier for ROI/CPU and decode-number.
    fn canonical_data(&self) -> Parse<String> {
        match self {
            Self::Null => Ok("null".to_owned()),
            Self::Bool(value) => Ok(value.to_string()),
            Self::String(value) => serde_json::to_string(value).map_err(|_| ()),
            Self::Number(_) => match self.numeric()? {
                NumericInput::Signed(value) => Ok(value.to_string()),
                NumericInput::Unsigned(value) => Ok(value.to_string()),
                NumericInput::Float(_) => Err(()),
            },
            Self::Array(values) => Ok(format!(
                "[{}]",
                values
                    .iter()
                    .map(Self::canonical_data)
                    .collect::<Parse<Vec<_>>>()?
                    .join(",")
            )),
            Self::Object(values) => {
                let mut members = Vec::with_capacity(values.len());
                for (key, value) in values {
                    members.push(format!(
                        "{}:{}",
                        serde_json::to_string(key).map_err(|_| ())?,
                        value.canonical_data()?
                    ));
                }
                Ok(format!("{{{}}}", members.join(",")))
            }
        }
    }
}

fn numeric(text: &str) -> Parse<NumericInput> {
    if text.contains(['.', 'e', 'E']) {
        let value = text.parse::<f64>().map_err(|_| ())?;
        if value.is_finite() {
            Ok(NumericInput::Float(value))
        } else {
            Err(())
        }
    } else if text.starts_with('-') {
        Ok(NumericInput::Signed(text.parse::<i64>().map_err(|_| ())?))
    } else {
        Ok(NumericInput::Unsigned(text.parse::<u64>().map_err(|_| ())?))
    }
}

fn integer(node: &Node) -> Parse<i64> {
    match node.numeric()? {
        NumericInput::Signed(value) => Ok(value),
        NumericInput::Unsigned(value) => i64::try_from(value).map_err(|_| ()),
        NumericInput::Float(_) => Err(()),
    }
}
fn int32(node: &Node) -> Parse<i32> {
    i32::try_from(integer(node)?).map_err(|_| ())
}
fn cpu(node: &Node) -> Parse<f64> {
    let value = node.numeric()?;
    match value {
        NumericInput::Float(value) => Ok(value),
        NumericInput::Signed(value) => value.to_string().parse::<f64>().map_err(|_| ()),
        NumericInput::Unsigned(value) => value.to_string().parse::<f64>().map_err(|_| ()),
    }
}

fn tag<T>(node: &Node, convert: impl FnOnce(&Node) -> Parse<T>) -> Parse<ResultPresence<T>> {
    let kind = node.member("kind")?.string()?;
    match kind {
        "absent" => {
            node.keys(&["kind"])?;
            Ok(ResultPresence::Absent)
        }
        "null" => {
            node.keys(&["kind"])?;
            Ok(ResultPresence::Null)
        }
        "value" => {
            node.keys(&["kind", "value"])?;
            let value = node.member("value")?;
            if matches!(value, Node::Null) {
                return Err(());
            }
            Ok(ResultPresence::Value(convert(value)?))
        }
        _ => Err(()),
    }
}
fn text(node: &Node) -> Parse<String> {
    Ok(node.string()?.to_owned())
}
fn text_tag(node: &Node, key: &str) -> Parse<ResultPresence<String>> {
    tag(node.member(key)?, text)
}
fn projection(
    raw: &Node,
    prepared: &Node,
    role: ResultJsonRole,
) -> Parse<ResultPresence<PreparedJson>> {
    let raw_kind = raw.member("kind")?.string()?;
    let prepared_kind = prepared.member("kind")?.string()?;
    if raw_kind != prepared_kind {
        return Err(());
    }
    match raw_kind {
        "absent" => {
            raw.keys(&["kind"])?;
            prepared.keys(&["kind"])?;
            Ok(ResultPresence::Absent)
        }
        "null" => {
            raw.keys(&["kind"])?;
            prepared.keys(&["kind"])?;
            Ok(ResultPresence::Null)
        }
        "value" => {
            raw.keys(&["kind", "value"])?;
            prepared.keys(&["kind", "value"])?;
            let original = raw.member("value")?.canonical_data()?;
            let safe = prepared.member("value")?.canonical_data()?;
            PreparedJson::new(&original, safe, role)
                .map(ResultPresence::Value)
                .map_err(|_| ())
        }
        _ => Err(()),
    }
}
fn metrics(node: &Node) -> Parse<ResultMetrics> {
    node.keys(&[
        "peak_memory_bytes",
        "process_rss_bytes",
        "cpu_user_seconds",
        "cpu_system_seconds",
        "cpu_total_seconds",
    ])?;
    Ok(ResultMetrics {
        peak_memory_bytes: tag(node.member("peak_memory_bytes")?, integer)?,
        process_rss_bytes: tag(node.member("process_rss_bytes")?, integer)?,
        cpu_user_seconds: tag(node.member("cpu_user_seconds")?, cpu)?,
        cpu_system_seconds: tag(node.member("cpu_system_seconds")?, cpu)?,
        cpu_total_seconds: tag(node.member("cpu_total_seconds")?, cpu)?,
    })
}
fn roi(node: &Node) -> Parse<ResultRoi> {
    node.keys(&["time_saved", "value"])?;
    Ok(ResultRoi {
        time_saved: tag(node.member("time_saved")?, int32)?,
        value: tag(node.member("value")?, Node::numeric)?,
    })
}
fn case_id(node: &Node) -> Parse<String> {
    let value = node.string()?;
    if value.is_empty()
        || value.len() > 64
        || !value.as_bytes()[0].is_ascii_alphabetic()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(());
    }
    Ok(value.to_owned())
}
fn uuid(node: &Node) -> Parse<CanonicalUuid> {
    CanonicalUuid::new(node.string()?.to_owned()).map_err(|_| ())
}

fn request(node: &Node) -> Parse<(String, SqlResultInput)> {
    node.keys(&["schema", "case_id", "operation", "cohort", "projection"])?;
    if node.member("schema")?.string()? != "bifrost.test.workflow-sql/v1" {
        return Err(());
    }
    let id = case_id(node.member("case_id")?)?;
    let cohort = node.member("cohort")?;
    cohort.keys(&["execution_id", "submitted_token"])?;
    let execution_id = uuid(cohort.member("execution_id")?)?;
    let fence = match cohort.member("submitted_token")? {
        Node::Null => None,
        node => Some(SqlClaimFence::new(uuid(node)?)),
    };
    let operation = node.member("operation")?;
    operation.keys(&["kind", "lane", "raw_fields"])?;
    if operation.member("kind")?.string()? != "result" {
        return Err(());
    }
    let lane = operation.member("lane")?.string()?;
    let fields = operation.member("raw_fields")?;
    let prepared = node.member("projection")?;
    prepared.keys(&[
        "prepared_result",
        "prepared_variables",
        "prepared_context",
        "preparation_source_sha256",
    ])?;
    let digest = prepared.member("preparation_source_sha256")?.string()?;
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        return Err(());
    }
    // Exact source receipt admission is the parent harness's precondition;
    // this syntax check cannot bless arbitrary source-prepared values.
    let context = projection(
        fields.member("execution_context")?,
        prepared.member("prepared_context")?,
        ResultJsonRole::Context,
    )?;
    let input = match lane {
        "success" => {
            fields.keys(&[
                "status",
                "result",
                "error",
                "error_type",
                "duration_ms",
                "variables",
                "execution_context",
                "metrics",
                "roi",
            ])?;
            SqlResultInput::success(
                execution_id,
                fence,
                SuccessResultFields {
                    status: text_tag(fields, "status")?,
                    result: projection(
                        fields.member("result")?,
                        prepared.member("prepared_result")?,
                        ResultJsonRole::Result,
                    )?,
                    error: text_tag(fields, "error")?,
                    error_type: text_tag(fields, "error_type")?,
                    duration_ms: tag(fields.member("duration_ms")?, int32)?,
                    variables: projection(
                        fields.member("variables")?,
                        prepared.member("prepared_variables")?,
                        ResultJsonRole::Variables,
                    )?,
                    execution_context: context,
                    metrics: tag(fields.member("metrics")?, metrics)?,
                    roi: tag(fields.member("roi")?, roi)?,
                },
            )
        }
        "failure" => {
            fields.keys(&[
                "error",
                "error_type",
                "duration_ms",
                "execution_context",
                "metrics",
            ])?;
            for key in ["prepared_result", "prepared_variables"] {
                if !matches!(
                    tag(prepared.member(key)?, |_| -> Parse<()> { Err(()) })?,
                    ResultPresence::Absent
                ) {
                    return Err(());
                }
            }
            SqlResultInput::failure(
                execution_id,
                fence,
                FailureResultFields {
                    error: text_tag(fields, "error")?,
                    error_type: text_tag(fields, "error_type")?,
                    duration_ms: tag(fields.member("duration_ms")?, int32)?,
                    execution_context: context,
                    metrics: tag(fields.member("metrics")?, metrics)?,
                },
            )
        }
        _ => return Err(()),
    }
    .map_err(|_| ())?;
    Ok((id, input))
}

fn logical_status(value: domain::LogicalExecutionStatus) -> &'static str {
    use domain::LogicalExecutionStatus as S;
    match value {
        S::Scheduled => "Scheduled",
        S::Pending => "Pending",
        S::Running => "Running",
        S::Success => "Success",
        S::Failed => "Failed",
        S::Timeout => "Timeout",
        S::Stuck => "Stuck",
        S::CompletedWithErrors => "CompletedWithErrors",
        S::Cancelling => "Cancelling",
        S::Cancelled => "Cancelled",
    }
}
fn attempt_status(value: domain::AttemptStatus) -> &'static str {
    use domain::AttemptStatus as S;
    match value {
        S::Dispatching => "Dispatching",
        S::Published => "Published",
        S::Claimed => "Claimed",
        S::Running => "Running",
        S::Succeeded => "Succeeded",
        S::Failed => "Failed",
        S::TimedOut => "TimedOut",
        S::Cancelled => "Cancelled",
        S::WorkerLost => "WorkerLost",
        S::AdmissionRejected => "AdmissionRejected",
    }
}
fn attempt_phase(value: domain::AttemptPhase) -> &'static str {
    use domain::AttemptPhase as S;
    match value {
        S::Dispatch => "Dispatch",
        S::Queue => "Queue",
        S::Claim => "Claim",
        S::Admission => "Admission",
        S::Execution => "Execution",
        S::Result => "Result",
        S::Terminal => "Terminal",
    }
}
fn failure_phase(value: domain::FailurePhase) -> &'static str {
    use domain::FailurePhase as S;
    match value {
        S::Dispatch => "Dispatch",
        S::Queue => "Queue",
        S::Claim => "Claim",
        S::Admission => "Admission",
        S::Execution => "Execution",
        S::Result => "Result",
        S::Worker => "Worker",
        S::Cancellation => "Cancellation",
    }
}
fn failure_code(value: domain::FailureCode) -> &'static str {
    use domain::FailureCode as S;
    match value {
        S::ExecutionTimeout => "ExecutionTimeout",
        S::Cancelled => "Cancelled",
        S::ResultPersistFailed => "ResultPersistFailed",
        S::TenantCodeError => "TenantCodeError",
        S::CancelledBeforeClaim => "CancelledBeforeClaim",
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
fn permission(value: domain::ProjectionPermission) -> &'static str {
    match value {
        domain::ProjectionPermission::Keep => "Keep",
        domain::ProjectionPermission::AllowSupplied => "AllowSupplied",
    }
}
fn reason(value: domain::DecisionError) -> &'static str {
    use domain::DecisionError as E;
    match value {
        E::MissingExecution => "MissingExecution",
        E::MissingAttempt => "MissingAttempt",
        E::InvalidAttemptFence => "InvalidAttemptFence",
        E::InvalidAttemptState => "InvalidAttemptState",
        E::InvalidLogicalState => "InvalidLogicalState",
        E::MissingFence => "MissingFence",
        E::LegacyUnfencedOutsideTrackedPath => "LegacyUnfencedOutsideTrackedPath",
        E::RequiresCoordinatorPolicy => "RequiresCoordinatorPolicy",
        E::InconsistentRows => "InconsistentRows",
    }
}
fn plan(value: domain::FencedResultPlan) -> Value {
    let execution = value.execution;
    let attempt = value.attempt;
    json!({"execution": {
        "status": logical_status(execution.status), "duration_ms": input_write(execution.duration_ms),
        "completed_at": time_write(execution.completed_at), "result": permission(execution.result),
        "result_type": permission(execution.result_type), "error_message": permission(execution.error_message),
        "time_saved": permission(execution.time_saved), "value": permission(execution.value),
        "variables": permission(execution.variables), "execution_context": permission(execution.execution_context),
        "metrics": permission(execution.metrics), "logs": permission(execution.logs)
    }, "attempt": {
        "status": attempt_status(attempt.status), "phase": attempt_phase(attempt.phase),
        "failure_phase": attempt.failure_phase.map(failure_phase), "failure_code": attempt.failure_code.map(failure_code),
        "started_at": time_write(attempt.started_at), "heartbeat_at": time_write(attempt.heartbeat_at),
        "completed_at": time_write(attempt.completed_at), "duration_ms": input_write(attempt.duration_ms),
        "peak_memory_bytes": input_write(attempt.peak_memory_bytes), "cpu_total_seconds": input_write(attempt.cpu_total_seconds)
    }})
}
fn operation_stage(value: ResultSqlStage) -> &'static str {
    match value {
        ResultSqlStage::Advisory => "Advisory",
        ResultSqlStage::ReadHistory => "ReadHistory",
        ResultSqlStage::ReadExecution => "ReadExecution",
        ResultSqlStage::ReadAttempt => "ReadAttempt",
        ResultSqlStage::ReadContext => "ReadContext",
        ResultSqlStage::Decode => "Decode",
        ResultSqlStage::Clock => "Clock",
        ResultSqlStage::WriteAttempt => "WriteAttempt",
        ResultSqlStage::WriteExecution => "WriteExecution",
    }
}
fn operation_class(value: ResultSqlFailureClass) -> &'static str {
    match value {
        ResultSqlFailureClass::Database => "Database",
        ResultSqlFailureClass::InvalidRow => "InvalidRow",
        ResultSqlFailureClass::ClockRange => "ClockRange",
        ResultSqlFailureClass::Cardinality => "Cardinality",
    }
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum SettlementStage {
    Setup,
    Acquire,
    Begin,
    Commit,
    Rollback,
    Close,
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum SettlementClass {
    Database,
    Resource,
}
struct SettlementFailure {
    stage: SettlementStage,
    class: SettlementClass,
    code: Option<&'static str>,
}
impl SettlementFailure {
    fn database(stage: SettlementStage, error: sqlx::Error) -> Self {
        let class = if error.as_database_error().is_some() {
            SettlementClass::Database
        } else {
            SettlementClass::Resource
        };
        Self {
            stage,
            class,
            code: sql::result_sqlstate(&error),
        }
    }
    fn close_resource() -> Self {
        Self {
            stage: SettlementStage::Close,
            class: SettlementClass::Resource,
            code: None,
        }
    }
    fn stage(&self) -> &'static str {
        match self.stage {
            SettlementStage::Setup => "Setup",
            SettlementStage::Acquire => "Acquire",
            SettlementStage::Begin => "Begin",
            SettlementStage::Commit => "Commit",
            SettlementStage::Rollback => "Rollback",
            SettlementStage::Close => "Close",
        }
    }
    fn class(&self) -> &'static str {
        match self.class {
            SettlementClass::Database => "Database",
            SettlementClass::Resource => "Resource",
        }
    }
}
enum Failure {
    Operation(ResultSqlFailure),
    Settlement(SettlementFailure),
}
impl Failure {
    fn output(&self) -> Value {
        let (stage, class, code) = match self {
            Self::Operation(value) => (
                operation_stage(value.stage()),
                operation_class(value.class()),
                value.code(),
            ),
            Self::Settlement(value) => (value.stage(), value.class(), value.code),
        };
        json!({"kind":"infrastructure_failure","stage":stage,"class":class,"sqlstate":code})
    }
}
fn first_failure(first: &mut Option<Failure>, next: Failure) {
    if first.is_none() {
        *first = Some(next);
    }
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum Settlement {
    Committed,
    RolledBack,
    Unknown,
}
impl Settlement {
    fn as_str(self) -> &'static str {
        match self {
            Self::Committed => "committed",
            Self::RolledBack => "rolled_back",
            Self::Unknown => "unknown",
        }
    }
}
fn acknowledged(
    result: Result<(), sqlx::Error>,
    stage: SettlementStage,
    success: Settlement,
) -> Result<Settlement, SettlementFailure> {
    result
        .map(|()| success)
        .map_err(|error| SettlementFailure::database(stage, error))
}
struct SqlResponse {
    decision: Value,
    settlement: Settlement,
    execution_rows: Option<u64>,
    attempt_rows: Option<u64>,
    failed: bool,
    clock: Option<sql::ResultClockWitness>,
}
impl SqlResponse {
    fn failure(failure: Failure, settlement: Settlement) -> Self {
        Self {
            decision: failure.output(),
            settlement,
            execution_rows: None,
            attempt_rows: None,
            failed: true,
            clock: None,
        }
    }
    fn output(&self, id: &str) -> Value {
        json!({"schema":"bifrost.test.workflow-sql-result/v2","case_id":id,"decision":self.decision,
            "transaction":{"status":self.settlement.as_str(),"affected_execution_rows":self.execution_rows,"affected_attempt_rows":self.attempt_rows},"clock_witness":clock_output(self.clock.as_ref())})
    }
}

fn clock_event(value: Option<sql::ResultClockEvent>) -> Value {
    value.map_or(Value::Null, |event| json!({"ordinal":event.ordinal,"utc_us":event.utc_us,"elapsed_ns":event.elapsed_ns}))
}
fn clock_output(value: Option<&sql::ResultClockWitness>) -> Value {
    let Some(value) = value else {
        return json!({"complete":false,"attempt":null,"logical":null,"commit":null});
    };
    clock_views_output(
        value.complete(),
        value.attempt(),
        value.logical(),
        value.commit(),
    )
}
fn clock_views_output(
    complete: bool,
    attempt: Option<sql::ResultAttemptClock>,
    logical: Option<sql::ResultLogicalClock>,
    commit: Option<sql::ResultCommitClock>,
) -> Value {
    let attempt = attempt.map_or(Value::Null, |value| json!({"read_ack":clock_event(value.read_ack),"sample":clock_event(value.sample),"write_dispatch":clock_event(value.write_dispatch),"write_ack":clock_event(value.write_ack)}));
    let logical = logical.map_or(Value::Null, |value| json!({"status_read_ack":clock_event(value.status_read_ack),"sample":clock_event(value.sample),"upper":clock_event(value.upper),"upper_kind":match value.upper_kind { sql::ResultClockUpper::ContextReadDispatch => "context_read_dispatch", sql::ResultClockUpper::WriteExecutionDispatch => "write_execution_dispatch" },"write_ack":clock_event(value.write_ack)}));
    let commit = commit.map_or(
        Value::Null,
        |value| json!({"dispatch":clock_event(value.dispatch),"ack":clock_event(value.ack)}),
    );
    json!({"complete":complete,"attempt":attempt,"logical":logical,"commit":commit})
}
fn delivery_admitted(response: &SqlResponse) -> bool {
    response.failed
        || response
            .clock
            .as_ref()
            .is_some_and(sql::ResultClockWitness::complete)
}

async fn transaction(
    connection: &mut sqlx::pool::PoolConnection<sqlx::Postgres>,
    input: &SqlResultInput,
) -> SqlResponse {
    let mut tx = match connection.begin().await {
        Ok(tx) => tx,
        Err(error) => {
            return SqlResponse::failure(
                Failure::Settlement(SettlementFailure::database(SettlementStage::Begin, error)),
                Settlement::Unknown,
            );
        }
    };
    let setup = async {
        sqlx::query("SET LOCAL statement_timeout = '5000ms'")
            .persistent(false)
            .execute(&mut *tx)
            .await?;
        sqlx::query("SET LOCAL lock_timeout = '5000ms'")
            .persistent(false)
            .execute(&mut *tx)
            .await?;
        Ok::<(), sqlx::Error>(())
    }
    .await;
    if let Err(error) = setup {
        let mut failure = Some(Failure::Settlement(SettlementFailure::database(
            SettlementStage::Setup,
            error,
        )));
        let settlement = match acknowledged(
            tx.rollback().await,
            SettlementStage::Rollback,
            Settlement::RolledBack,
        ) {
            Ok(settlement) => settlement,
            Err(error) => {
                first_failure(&mut failure, Failure::Settlement(error));
                Settlement::Unknown
            }
        };
        if let Some(failure) = failure {
            return SqlResponse::failure(failure, settlement);
        }
        return SqlResponse::failure(
            Failure::Settlement(SettlementFailure::close_resource()),
            settlement,
        );
    }
    let (operation, mut clock) = sql::apply_result_observed(&mut tx, input).await;
    let mut response = match operation {
        Ok(SqlDecision::Applied(AppliedResult {
            plan: tentative,
            affected_execution_rows,
            affected_attempt_rows,
        })) => {
            clock.commit_dispatch();
            match acknowledged(
                tx.commit().await,
                SettlementStage::Commit,
                Settlement::Committed,
            ) {
                Ok(_) => {
                    clock.commit_ack();
                    SqlResponse {
                        decision: json!({"kind":"applied","plan":plan(tentative)}),
                        settlement: Settlement::Committed,
                        execution_rows: Some(affected_execution_rows),
                        attempt_rows: Some(affected_attempt_rows),
                        failed: false,
                        clock: None,
                    }
                }
                Err(error) => {
                    let mut response =
                        SqlResponse::failure(Failure::Settlement(error), Settlement::Unknown);
                    response.execution_rows = Some(affected_execution_rows);
                    response.attempt_rows = Some(affected_attempt_rows);
                    response
                }
            }
        }
        Ok(SqlDecision::Rejected(value)) => match acknowledged(
            tx.rollback().await,
            SettlementStage::Rollback,
            Settlement::RolledBack,
        ) {
            Ok(_) => SqlResponse {
                decision: json!({"kind":"rejected","reason":reason(value)}),
                settlement: Settlement::RolledBack,
                execution_rows: None,
                attempt_rows: None,
                failed: false,
                clock: None,
            },
            Err(error) => SqlResponse::failure(Failure::Settlement(error), Settlement::Unknown),
        },
        Err(error) => {
            let mut failure = Some(Failure::Operation(error));
            let settlement = match acknowledged(
                tx.rollback().await,
                SettlementStage::Rollback,
                Settlement::RolledBack,
            ) {
                Ok(settlement) => settlement,
                Err(error) => {
                    first_failure(&mut failure, Failure::Settlement(error));
                    Settlement::Unknown
                }
            };
            if let Some(failure) = failure {
                SqlResponse::failure(failure, settlement)
            } else {
                SqlResponse::failure(
                    Failure::Settlement(SettlementFailure::close_resource()),
                    settlement,
                )
            }
        }
    };
    response.clock = Some(clock);
    response
}

async fn apply(input: &SqlResultInput, options: PgConnectOptions) -> SqlResponse {
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(5))
        .connect_lazy_with(options);
    let mut response = match pool.acquire().await {
        Ok(mut connection) => {
            let response = transaction(&mut connection, input).await;
            drop(connection);
            response
        }
        Err(error) => SqlResponse::failure(
            Failure::Settlement(SettlementFailure::database(SettlementStage::Acquire, error)),
            Settlement::Unknown,
        ),
    };
    // close has Output=(), not a database Result. Retain known commit fact even
    // if a later local resource admission fails; never invent a rollback ACK.
    pool.close().await;
    if !pool.is_closed() && !response.failed {
        response.decision = Failure::Settlement(SettlementFailure::close_resource()).output();
        response.failed = true;
    }
    response
}

fn number_request(node: &Node) -> Parse<Value> {
    node.keys(&["schema", "case_id", "role", "lexeme"])?;
    if node.member("schema")?.string()? != "bifrost.test.workflow-sql-number/v1" {
        return Err(());
    }
    let id = case_id(node.member("case_id")?)?;
    let role = node.member("role")?.string()?;
    if !matches!(role, "roi" | "cpu" | "json") {
        return Err(());
    }
    let lexeme = node.member("lexeme")?.string()?;
    let parsed = Node::parse(lexeme, 0).and_then(|node| node.numeric());
    let (kind, integer, float_bits, admitted) = match parsed {
        Ok(NumericInput::Signed(value)) => (
            "signed",
            Some(value.to_string()),
            if role == "cpu" {
                Some(format!(
                    "{:016x}",
                    value.to_string().parse::<f64>().map_err(|_| ())?.to_bits()
                ))
            } else {
                None
            },
            true,
        ),
        Ok(NumericInput::Unsigned(value)) => (
            "unsigned",
            Some(value.to_string()),
            if role == "cpu" {
                Some(format!(
                    "{:016x}",
                    value.to_string().parse::<f64>().map_err(|_| ())?.to_bits()
                ))
            } else {
                None
            },
            true,
        ),
        Ok(NumericInput::Float(value)) => (
            "float",
            None,
            Some(format!("{:016x}", value.to_bits())),
            role != "json",
        ),
        Err(()) => ("invalid", None, None, false),
    };
    Ok(
        json!({"schema":"bifrost.test.workflow-sql-number-result/v1","case_id":id,"kind":kind,"integer":integer,"float_bits":float_bits,"admitted":admitted}),
    )
}

// Fixed six-table software catalog projection; no caller SQL or closure.
const SCHEMA_IDENTITY_SQL: &str = r#"WITH fixed(table_name) AS (VALUES
 ('executions'), ('workflow_execution_attempts'), ('execution_logs'),
 ('devices'), ('device_jobs'), ('device_job_logs')
), selected AS MATERIALIZED (
 SELECT n.nspname::text AS schema_name, c.relname::text AS table_name,
        c.oid AS relation_id, c.*
 FROM fixed f
 JOIN pg_catalog.pg_namespace n ON n.nspname = 'public'
 JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = f.table_name
)
SELECT count(*)::bigint AS count,
       coalesce(bool_and(relkind = 'r'), false) AS ordinary,
       count(DISTINCT table_name)::bigint AS distinct_names
FROM selected;"#;

const SCHEMA_RELATIONS_SQL: &str = r#"WITH fixed(table_name) AS (VALUES
 ('executions'), ('workflow_execution_attempts'), ('execution_logs'),
 ('devices'), ('device_jobs'), ('device_job_logs')
), selected AS MATERIALIZED (
 SELECT n.nspname::text AS schema_name, c.relname::text AS table_name,
        c.oid AS relation_id, c.*
 FROM fixed f
 JOIN pg_catalog.pg_namespace n ON n.nspname = 'public'
 JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = f.table_name
)
, rows AS MATERIALIZED (
SELECT s.table_name, 0 AS member_number, s.schema_name AS member_schema,
       s.table_name AS member_name,
       jsonb_build_array(s.schema_name,s.table_name,s.relkind::text,
         s.relpersistence::text,am.amname::text,ts.spcname::text,
         s.relnatts,s.relchecks,s.relhasindex,s.relhasrules,s.relhastriggers,
         s.relhassubclass,s.relrowsecurity,s.relforcerowsecurity,
         s.relreplident::text,s.relispartition,s.reloptions,
         pg_get_expr(s.relpartbound,s.relation_id,false))::text AS payload
FROM selected s
LEFT JOIN pg_catalog.pg_am am ON am.oid=s.relam
LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid=s.reltablespace
ORDER BY s.table_name COLLATE "C" LIMIT 1025
)
, limits AS MATERIALIZED (
 SELECT count(*)::bigint AS row_count,
        (2 + coalesce(sum(octet_length(convert_to(payload,'UTF8'))),0)
           + greatest(count(*)-1,0))::bigint AS document_bytes
 FROM rows
), admitted_rows AS MATERIALIZED (
 SELECT r.* FROM rows r CROSS JOIN limits l
 WHERE l.row_count<=1024 AND l.document_bytes<=65536
), document AS (
 SELECT '[' || coalesce(string_agg(payload,',' ORDER BY
          table_name COLLATE "C",member_number,member_schema COLLATE "C",member_name COLLATE "C"),'')
        || ']' AS text FROM admitted_rows
)
SELECT l.row_count,l.document_bytes,
       CASE WHEN l.row_count<=1024 AND l.document_bytes<=65536
            THEN encode(sha256(convert_to(d.text,'UTF8')),'hex') ELSE NULL END AS sha256
FROM limits l CROSS JOIN document d;"#;

const SCHEMA_COLUMNS_SQL: &str = r#"WITH fixed(table_name) AS (VALUES
 ('executions'), ('workflow_execution_attempts'), ('execution_logs'),
 ('devices'), ('device_jobs'), ('device_job_logs')
), selected AS MATERIALIZED (
 SELECT n.nspname::text AS schema_name, c.relname::text AS table_name,
        c.oid AS relation_id, c.*
 FROM fixed f
 JOIN pg_catalog.pg_namespace n ON n.nspname = 'public'
 JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = f.table_name
)
, rows AS MATERIALIZED (
SELECT s.table_name,a.attnum::integer AS member_number,
       s.schema_name AS member_schema,a.attname::text AS member_name,
       jsonb_build_array(s.schema_name,s.table_name,a.attnum,a.attname::text,
         tn.nspname::text,t.typname::text,a.atttypmod,
         CASE WHEN a.attisdropped THEN NULL ELSE format_type(a.atttypid,a.atttypmod) END,
         a.attnotnull,a.atthasdef,a.attidentity::text,a.attgenerated::text,
         a.attisdropped,a.attndims,a.attlen,a.attbyval,a.attalign::text,
         a.attstorage::text,a.attcompression::text,a.attislocal,a.attinhcount,
         a.attstattarget,a.attoptions,a.atthasmissing,a.attmissingval::text,
         cn.nspname::text,co.collname::text,
         pg_get_expr(d.adbin,d.adrelid,false))::text AS payload
FROM selected s
JOIN pg_catalog.pg_attribute a ON a.attrelid=s.relation_id AND a.attnum>0
LEFT JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
LEFT JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation
LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=co.collnamespace
ORDER BY s.table_name COLLATE "C",a.attnum LIMIT 1025
)
, limits AS MATERIALIZED (
 SELECT count(*)::bigint AS row_count,
        (2 + coalesce(sum(octet_length(convert_to(payload,'UTF8'))),0)
           + greatest(count(*)-1,0))::bigint AS document_bytes
 FROM rows
), admitted_rows AS MATERIALIZED (
 SELECT r.* FROM rows r CROSS JOIN limits l
 WHERE l.row_count<=1024 AND l.document_bytes<=65536
), document AS (
 SELECT '[' || coalesce(string_agg(payload,',' ORDER BY
          table_name COLLATE "C",member_number,member_schema COLLATE "C",member_name COLLATE "C"),'')
        || ']' AS text FROM admitted_rows
)
SELECT l.row_count,l.document_bytes,
       CASE WHEN l.row_count<=1024 AND l.document_bytes<=65536
            THEN encode(sha256(convert_to(d.text,'UTF8')),'hex') ELSE NULL END AS sha256
FROM limits l CROSS JOIN document d;"#;

const SCHEMA_CONSTRAINTS_SQL: &str = r#"WITH fixed(table_name) AS (VALUES
 ('executions'), ('workflow_execution_attempts'), ('execution_logs'),
 ('devices'), ('device_jobs'), ('device_job_logs')
), selected AS MATERIALIZED (
 SELECT n.nspname::text AS schema_name, c.relname::text AS table_name,
        c.oid AS relation_id, c.*
 FROM fixed f
 JOIN pg_catalog.pg_namespace n ON n.nspname = 'public'
 JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = f.table_name
)
, rows AS MATERIALIZED (
SELECT s.table_name,0 AS member_number,ns.nspname::text AS member_schema,
       c.conname::text AS member_name,
       jsonb_build_array(s.schema_name,s.table_name,ns.nspname::text,c.conname::text,
         c.contype::text,c.convalidated,c.condeferrable,c.condeferred,
         c.conislocal,c.coninhcount,c.connoinherit,c.conkey,c.confkey,
         c.confupdtype::text,c.confdeltype::text,c.confmatchtype::text,c.confdelsetcols,
         rn.nspname::text,r.relname::text,
         ixn.nspname::text,ix.relname::text,
         pn.nspname::text,pr.relname::text,p.conname::text,
         pg_get_constraintdef(c.oid,false))::text AS payload
FROM selected s
JOIN pg_catalog.pg_constraint c ON c.conrelid=s.relation_id
JOIN pg_catalog.pg_namespace ns ON ns.oid=c.connamespace
LEFT JOIN pg_catalog.pg_class r ON r.oid=c.confrelid
LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace
LEFT JOIN pg_catalog.pg_class ix ON ix.oid=c.conindid
LEFT JOIN pg_catalog.pg_namespace ixn ON ixn.oid=ix.relnamespace
LEFT JOIN pg_catalog.pg_constraint p ON p.oid=c.conparentid
LEFT JOIN pg_catalog.pg_class pr ON pr.oid=p.conrelid
LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid=pr.relnamespace
ORDER BY s.table_name COLLATE "C",ns.nspname::text COLLATE "C",c.conname::text COLLATE "C"
LIMIT 1025
)
, limits AS MATERIALIZED (
 SELECT count(*)::bigint AS row_count,
        (2 + coalesce(sum(octet_length(convert_to(payload,'UTF8'))),0)
           + greatest(count(*)-1,0))::bigint AS document_bytes
 FROM rows
), admitted_rows AS MATERIALIZED (
 SELECT r.* FROM rows r CROSS JOIN limits l
 WHERE l.row_count<=1024 AND l.document_bytes<=65536
), document AS (
 SELECT '[' || coalesce(string_agg(payload,',' ORDER BY
          table_name COLLATE "C",member_number,member_schema COLLATE "C",member_name COLLATE "C"),'')
        || ']' AS text FROM admitted_rows
)
SELECT l.row_count,l.document_bytes,
       CASE WHEN l.row_count<=1024 AND l.document_bytes<=65536
            THEN encode(sha256(convert_to(d.text,'UTF8')),'hex') ELSE NULL END AS sha256
FROM limits l CROSS JOIN document d;"#;

const SCHEMA_INDEXES_SQL: &str = r#"WITH fixed(table_name) AS (VALUES
 ('executions'), ('workflow_execution_attempts'), ('execution_logs'),
 ('devices'), ('device_jobs'), ('device_job_logs')
), selected AS MATERIALIZED (
 SELECT n.nspname::text AS schema_name, c.relname::text AS table_name,
        c.oid AS relation_id, c.*
 FROM fixed f
 JOIN pg_catalog.pg_namespace n ON n.nspname = 'public'
 JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = f.table_name
)
, rows AS MATERIALIZED (
SELECT s.table_name,0 AS member_number,n.nspname::text AS member_schema,
       c.relname::text AS member_name,
       jsonb_build_array(s.schema_name,s.table_name,n.nspname::text,c.relname::text,
         c.relkind::text,am.amname::text,ts.spcname::text,c.reloptions,
         i.indnatts,i.indnkeyatts,i.indisunique,i.indnullsnotdistinct,
         i.indisprimary,i.indisexclusion,i.indimmediate,i.indisclustered,
         i.indisvalid,i.indcheckxmin,i.indisready,i.indislive,i.indisreplident,
         i.indkey::text,i.indoption::text,
         pg_get_indexdef(i.indexrelid,0,false),
         pg_get_expr(i.indexprs,i.indrelid,false),
         pg_get_expr(i.indpred,i.indrelid,false))::text AS payload
FROM selected s
JOIN pg_catalog.pg_index i ON i.indrelid=s.relation_id
JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_catalog.pg_am am ON am.oid=c.relam
LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid=c.reltablespace
ORDER BY s.table_name COLLATE "C",n.nspname::text COLLATE "C",c.relname::text COLLATE "C"
LIMIT 1025
)
, limits AS MATERIALIZED (
 SELECT count(*)::bigint AS row_count,
        (2 + coalesce(sum(octet_length(convert_to(payload,'UTF8'))),0)
           + greatest(count(*)-1,0))::bigint AS document_bytes
 FROM rows
), admitted_rows AS MATERIALIZED (
 SELECT r.* FROM rows r CROSS JOIN limits l
 WHERE l.row_count<=1024 AND l.document_bytes<=65536
), document AS (
 SELECT '[' || coalesce(string_agg(payload,',' ORDER BY
          table_name COLLATE "C",member_number,member_schema COLLATE "C",member_name COLLATE "C"),'')
        || ']' AS text FROM admitted_rows
)
SELECT l.row_count,l.document_bytes,
       CASE WHEN l.row_count<=1024 AND l.document_bytes<=65536
            THEN encode(sha256(convert_to(d.text,'UTF8')),'hex') ELSE NULL END AS sha256
FROM limits l CROSS JOIN document d;"#;

const SCHEMA_COMBINED_SQL: &str = r#"WITH fixed(table_name) AS (VALUES
 ('executions'), ('workflow_execution_attempts'), ('execution_logs'),
 ('devices'), ('device_jobs'), ('device_job_logs')
), selected AS MATERIALIZED (
 SELECT n.nspname::text AS schema_name, c.relname::text AS table_name,
        c.oid AS relation_id, c.*
 FROM fixed f
 JOIN pg_catalog.pg_namespace n ON n.nspname = 'public'
 JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = f.table_name
)
, relations_rows AS MATERIALIZED (
SELECT s.table_name, 0 AS member_number, s.schema_name AS member_schema,
       s.table_name AS member_name,
       jsonb_build_array(s.schema_name,s.table_name,s.relkind::text,
         s.relpersistence::text,am.amname::text,ts.spcname::text,
         s.relnatts,s.relchecks,s.relhasindex,s.relhasrules,s.relhastriggers,
         s.relhassubclass,s.relrowsecurity,s.relforcerowsecurity,
         s.relreplident::text,s.relispartition,s.reloptions,
         pg_get_expr(s.relpartbound,s.relation_id,false))::text AS payload
FROM selected s
LEFT JOIN pg_catalog.pg_am am ON am.oid=s.relam
LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid=s.reltablespace
ORDER BY s.table_name COLLATE "C" LIMIT 1025
)
, relations_limits AS MATERIALIZED (
 SELECT count(*)::bigint AS row_count,
        (2 + coalesce(sum(octet_length(convert_to(payload,'UTF8'))),0)
           + greatest(count(*)-1,0))::bigint AS document_bytes
 FROM relations_rows
), relations_admitted AS MATERIALIZED (
 SELECT r.* FROM relations_rows r CROSS JOIN relations_limits l
 WHERE l.row_count<=1024 AND l.document_bytes<=65536
), relations_document AS (
 SELECT '[' || coalesce(string_agg(payload,',' ORDER BY
          table_name COLLATE "C",member_number,member_schema COLLATE "C",member_name COLLATE "C"),'')
        || ']' AS text FROM relations_admitted
)
, columns_rows AS MATERIALIZED (
SELECT s.table_name,a.attnum::integer AS member_number,
       s.schema_name AS member_schema,a.attname::text AS member_name,
       jsonb_build_array(s.schema_name,s.table_name,a.attnum,a.attname::text,
         tn.nspname::text,t.typname::text,a.atttypmod,
         CASE WHEN a.attisdropped THEN NULL ELSE format_type(a.atttypid,a.atttypmod) END,
         a.attnotnull,a.atthasdef,a.attidentity::text,a.attgenerated::text,
         a.attisdropped,a.attndims,a.attlen,a.attbyval,a.attalign::text,
         a.attstorage::text,a.attcompression::text,a.attislocal,a.attinhcount,
         a.attstattarget,a.attoptions,a.atthasmissing,a.attmissingval::text,
         cn.nspname::text,co.collname::text,
         pg_get_expr(d.adbin,d.adrelid,false))::text AS payload
FROM selected s
JOIN pg_catalog.pg_attribute a ON a.attrelid=s.relation_id AND a.attnum>0
LEFT JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
LEFT JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation
LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=co.collnamespace
ORDER BY s.table_name COLLATE "C",a.attnum LIMIT 1025
)
, columns_limits AS MATERIALIZED (
 SELECT count(*)::bigint AS row_count,
        (2 + coalesce(sum(octet_length(convert_to(payload,'UTF8'))),0)
           + greatest(count(*)-1,0))::bigint AS document_bytes
 FROM columns_rows
), columns_admitted AS MATERIALIZED (
 SELECT r.* FROM columns_rows r CROSS JOIN columns_limits l
 WHERE l.row_count<=1024 AND l.document_bytes<=65536
), columns_document AS (
 SELECT '[' || coalesce(string_agg(payload,',' ORDER BY
          table_name COLLATE "C",member_number,member_schema COLLATE "C",member_name COLLATE "C"),'')
        || ']' AS text FROM columns_admitted
)
, constraints_rows AS MATERIALIZED (
SELECT s.table_name,0 AS member_number,ns.nspname::text AS member_schema,
       c.conname::text AS member_name,
       jsonb_build_array(s.schema_name,s.table_name,ns.nspname::text,c.conname::text,
         c.contype::text,c.convalidated,c.condeferrable,c.condeferred,
         c.conislocal,c.coninhcount,c.connoinherit,c.conkey,c.confkey,
         c.confupdtype::text,c.confdeltype::text,c.confmatchtype::text,c.confdelsetcols,
         rn.nspname::text,r.relname::text,
         ixn.nspname::text,ix.relname::text,
         pn.nspname::text,pr.relname::text,p.conname::text,
         pg_get_constraintdef(c.oid,false))::text AS payload
FROM selected s
JOIN pg_catalog.pg_constraint c ON c.conrelid=s.relation_id
JOIN pg_catalog.pg_namespace ns ON ns.oid=c.connamespace
LEFT JOIN pg_catalog.pg_class r ON r.oid=c.confrelid
LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace
LEFT JOIN pg_catalog.pg_class ix ON ix.oid=c.conindid
LEFT JOIN pg_catalog.pg_namespace ixn ON ixn.oid=ix.relnamespace
LEFT JOIN pg_catalog.pg_constraint p ON p.oid=c.conparentid
LEFT JOIN pg_catalog.pg_class pr ON pr.oid=p.conrelid
LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid=pr.relnamespace
ORDER BY s.table_name COLLATE "C",ns.nspname::text COLLATE "C",c.conname::text COLLATE "C"
LIMIT 1025
)
, constraints_limits AS MATERIALIZED (
 SELECT count(*)::bigint AS row_count,
        (2 + coalesce(sum(octet_length(convert_to(payload,'UTF8'))),0)
           + greatest(count(*)-1,0))::bigint AS document_bytes
 FROM constraints_rows
), constraints_admitted AS MATERIALIZED (
 SELECT r.* FROM constraints_rows r CROSS JOIN constraints_limits l
 WHERE l.row_count<=1024 AND l.document_bytes<=65536
), constraints_document AS (
 SELECT '[' || coalesce(string_agg(payload,',' ORDER BY
          table_name COLLATE "C",member_number,member_schema COLLATE "C",member_name COLLATE "C"),'')
        || ']' AS text FROM constraints_admitted
)
, indexes_rows AS MATERIALIZED (
SELECT s.table_name,0 AS member_number,n.nspname::text AS member_schema,
       c.relname::text AS member_name,
       jsonb_build_array(s.schema_name,s.table_name,n.nspname::text,c.relname::text,
         c.relkind::text,am.amname::text,ts.spcname::text,c.reloptions,
         i.indnatts,i.indnkeyatts,i.indisunique,i.indnullsnotdistinct,
         i.indisprimary,i.indisexclusion,i.indimmediate,i.indisclustered,
         i.indisvalid,i.indcheckxmin,i.indisready,i.indislive,i.indisreplident,
         i.indkey::text,i.indoption::text,
         pg_get_indexdef(i.indexrelid,0,false),
         pg_get_expr(i.indexprs,i.indrelid,false),
         pg_get_expr(i.indpred,i.indrelid,false))::text AS payload
FROM selected s
JOIN pg_catalog.pg_index i ON i.indrelid=s.relation_id
JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_catalog.pg_am am ON am.oid=c.relam
LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid=c.reltablespace
ORDER BY s.table_name COLLATE "C",n.nspname::text COLLATE "C",c.relname::text COLLATE "C"
LIMIT 1025
)
, indexes_limits AS MATERIALIZED (
 SELECT count(*)::bigint AS row_count,
        (2 + coalesce(sum(octet_length(convert_to(payload,'UTF8'))),0)
           + greatest(count(*)-1,0))::bigint AS document_bytes
 FROM indexes_rows
), indexes_admitted AS MATERIALIZED (
 SELECT r.* FROM indexes_rows r CROSS JOIN indexes_limits l
 WHERE l.row_count<=1024 AND l.document_bytes<=65536
), indexes_document AS (
 SELECT '[' || coalesce(string_agg(payload,',' ORDER BY
          table_name COLLATE "C",member_number,member_schema COLLATE "C",member_name COLLATE "C"),'')
        || ']' AS text FROM indexes_admitted
)
SELECT encode(sha256(convert_to(
 '[[' || '"relations",' || r.text || '],["columns",' || c.text
       || '],["constraints",' || k.text || '],["indexes",' || i.text || ']]',
 'UTF8')),'hex') AS combined_sha256
FROM relations_document r CROSS JOIN columns_document c
CROSS JOIN constraints_document k CROSS JOIN indexes_document i
CROSS JOIN relations_limits rl CROSS JOIN columns_limits cl
CROSS JOIN constraints_limits kl CROSS JOIN indexes_limits il
WHERE rl.row_count=6 AND cl.row_count<=1024 AND kl.row_count<=1024 AND il.row_count<=1024
 AND rl.document_bytes<=65536 AND cl.document_bytes<=65536
 AND kl.document_bytes<=65536 AND il.document_bytes<=65536;"#;

struct SchemaRequest {
    case_id: &'static str,
    phase: &'static str,
}

fn schema_request(node: &Node) -> Parse<SchemaRequest> {
    node.keys(&["schema", "case_id", "phase"])?;
    if node.member("schema")?.string()? != "bifrost.test.workflow-schema-observation/v1" {
        return Err(());
    }
    match (
        node.member("case_id")?.string()?,
        node.member("phase")?.string()?,
    ) {
        ("schemaBefore", "before_target") => Ok(SchemaRequest {
            case_id: "schemaBefore",
            phase: "before_target",
        }),
        ("schemaAfter", "after_native") => Ok(SchemaRequest {
            case_id: "schemaAfter",
            phase: "after_native",
        }),
        _ => Err(()),
    }
}

// Private error custody only: no Debug, SQL messages, or secondary-error output.
enum SchemaStage {
    Acquire,
    Begin,
    Setup,
    Profile,
    Heads,
    Floor,
    ReadPrivilege,
    Catalog,
    Commit,
    Close,
    Emit,
}
struct SchemaFailure {
    stage: SchemaStage,
    original: Option<sqlx::Error>,
}
impl SchemaFailure {
    fn database(stage: SchemaStage, original: sqlx::Error) -> Self {
        Self {
            stage,
            original: Some(original),
        }
    }
    fn admission(stage: SchemaStage) -> Self {
        Self {
            stage,
            original: None,
        }
    }
    fn discard(self) {
        // Consume the original only after independent resource settlement.
        let Self { stage, original } = self;
        let _stage = stage;
        drop(original);
    }
}

struct SchemaComponent {
    count: i64,
    sha256: String,
}
impl SchemaComponent {
    fn output(&self) -> Value {
        json!({"count":self.count,"sha256":self.sha256})
    }
}
struct SchemaObservation {
    server_major: i32,
    heads: Vec<String>,
    device_floor: bool,
    components: [SchemaComponent; 4],
    combined_sha256: String,
}
impl SchemaObservation {
    fn output(&self, request: &SchemaRequest) -> Value {
        json!({"schema":"bifrost.test.workflow-schema-observed/v1",
            "case_id":request.case_id,"phase":request.phase,"server_major":self.server_major,
            "heads":self.heads,"device_floor":self.device_floor,
            "catalog":{"relations":self.components[0].output(),"columns":self.components[1].output(),
                "constraints":self.components[2].output(),"indexes":self.components[3].output(),
                "combined_sha256":self.combined_sha256},"complete":true})
    }
}

fn schema_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

async fn schema_component(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    query: &'static str,
) -> Result<SchemaComponent, SchemaFailure> {
    let (count, bytes, digest): (i64, i64, Option<String>) = sqlx::query_as(query)
        .persistent(false)
        .fetch_one(&mut **tx)
        .await
        .map_err(|error| SchemaFailure::database(SchemaStage::Catalog, error))?;
    if !(0..=1024).contains(&count) || !(2..=65_536).contains(&bytes) {
        return Err(SchemaFailure::admission(SchemaStage::Catalog));
    }
    let Some(sha256) = digest.filter(|value| schema_digest(value)) else {
        return Err(SchemaFailure::admission(SchemaStage::Catalog));
    };
    Ok(SchemaComponent { count, sha256 })
}

async fn schema_collect(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
) -> Result<SchemaObservation, SchemaFailure> {
    for query in [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SET LOCAL statement_timeout = '2000ms'",
        "SET LOCAL lock_timeout = '1000ms'",
        "SET LOCAL search_path = pg_catalog, public",
    ] {
        sqlx::query(query)
            .persistent(false)
            .execute(&mut **tx)
            .await
            .map_err(|error| SchemaFailure::database(SchemaStage::Setup, error))?;
    }
    let (isolation, read_only, server_major): (String, String, i32) = sqlx::query_as(
        "SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only'), current_setting('server_version_num')::integer / 10000"
    )
    .persistent(false)
    .fetch_one(&mut **tx)
    .await
    .map_err(|error| SchemaFailure::database(SchemaStage::Profile, error))?;
    if isolation != "repeatable read" || read_only != "on" || server_major != 16 {
        return Err(SchemaFailure::admission(SchemaStage::Profile));
    }
    let heads: Vec<String> = sqlx::query_scalar(
        "SELECT version_num::text FROM public.alembic_version ORDER BY version_num::text COLLATE \"C\" LIMIT 9"
    )
    .persistent(false)
    .fetch_all(&mut **tx)
    .await
    .map_err(|error| SchemaFailure::database(SchemaStage::Heads, error))?;
    if heads.is_empty()
        || heads.len() > 8
        || !heads.iter().all(|head| {
            !head.is_empty()
                && head.len() <= 128
                && head
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
        })
        || !heads.windows(2).all(|pair| pair[0] < pair[1])
    {
        return Err(SchemaFailure::admission(SchemaStage::Heads));
    }
    let device_floor: bool = sqlx::query_scalar(include_str!("../src/schema.sql"))
        .persistent(false)
        .fetch_one(&mut **tx)
        .await
        .map_err(|error| SchemaFailure::database(SchemaStage::Floor, error))?;
    if !device_floor {
        return Err(SchemaFailure::admission(SchemaStage::Floor));
    }
    sqlx::query("SELECT d.id, j.claim_token, l.seq FROM public.devices d CROSS JOIN public.device_jobs j CROSS JOIN public.device_job_logs l WHERE FALSE")
        .persistent(false)
        .execute(&mut **tx)
        .await
        .map_err(|error| SchemaFailure::database(SchemaStage::ReadPrivilege, error))?;
    let (count, ordinary, distinct_names): (i64, bool, i64) = sqlx::query_as(SCHEMA_IDENTITY_SQL)
        .persistent(false)
        .fetch_one(&mut **tx)
        .await
        .map_err(|error| SchemaFailure::database(SchemaStage::Catalog, error))?;
    if count != 6 || !ordinary || distinct_names != 6 {
        return Err(SchemaFailure::admission(SchemaStage::Catalog));
    }
    let components = [
        schema_component(tx, SCHEMA_RELATIONS_SQL).await?,
        schema_component(tx, SCHEMA_COLUMNS_SQL).await?,
        schema_component(tx, SCHEMA_CONSTRAINTS_SQL).await?,
        schema_component(tx, SCHEMA_INDEXES_SQL).await?,
    ];
    if components[0].count != 6 {
        return Err(SchemaFailure::admission(SchemaStage::Catalog));
    }
    let combined_sha256: Option<String> = sqlx::query_scalar(SCHEMA_COMBINED_SQL)
        .persistent(false)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|error| SchemaFailure::database(SchemaStage::Catalog, error))?;
    let Some(combined_sha256) = combined_sha256.filter(|value| schema_digest(value)) else {
        return Err(SchemaFailure::admission(SchemaStage::Catalog));
    };
    Ok(SchemaObservation {
        server_major,
        heads,
        device_floor,
        components,
        combined_sha256,
    })
}

async fn schema_transaction(
    connection: &mut sqlx::pool::PoolConnection<sqlx::Postgres>,
) -> Result<SchemaObservation, SchemaFailure> {
    let mut tx = connection
        .begin()
        .await
        .map_err(|error| SchemaFailure::database(SchemaStage::Begin, error))?;
    match schema_collect(&mut tx).await {
        Ok(observed) => {
            tx.commit()
                .await
                .map_err(|error| SchemaFailure::database(SchemaStage::Commit, error))?;
            Ok(observed)
        }
        Err(original) => {
            // Explicit rollback is independently attempted; never replace the first error.
            let _rollback = tx.rollback().await;
            Err(original)
        }
    }
}

async fn observe_schema(options: PgConnectOptions) -> Result<SchemaObservation, SchemaFailure> {
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(5))
        .connect_lazy_with(options);
    let mut observed = match pool.acquire().await {
        Ok(mut connection) => {
            let observed = schema_transaction(&mut connection).await;
            drop(connection);
            observed
        }
        Err(error) => Err(SchemaFailure::database(SchemaStage::Acquire, error)),
    };
    pool.close().await;
    if !pool.is_closed() && observed.is_ok() {
        observed = Err(SchemaFailure::admission(SchemaStage::Close));
    }
    observed
}

fn read_input() -> Parse<Node> {
    let mut bytes = Vec::new();
    io::stdin()
        .lock()
        .take((INPUT_LIMIT + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| ())?;
    if bytes.is_empty() || bytes.len() > INPUT_LIMIT {
        return Err(());
    }
    let raw = std::str::from_utf8(&bytes).map_err(|_| ())?;
    let original: Box<RawValue> = serde_json::from_str(raw).map_err(|_| ())?;
    Node::parse(original.get(), 0)
}
fn emit(value: &Value) -> Parse<()> {
    let mut bytes = serde_json::to_vec(value).map_err(|_| ())?;
    bytes.push(b'\n');
    if bytes.len() > OUTPUT_LIMIT {
        return Err(());
    }
    let mut stdout = io::stdout().lock();
    stdout
        .write_all(&bytes)
        .and_then(|()| stdout.flush())
        .map_err(|_| ())
}

// Private F4 activation only. Ordinary parser/transaction/output modes above
// remain unchanged; the supervisor owns actual before-create deadlines.
mod commit_fault {
    use super::*;
    use sqlx::Row;
    use std::{os::unix::{net::UnixStream, fs::{FileTypeExt,MetadataExt}}, time::Instant};

    struct Channel {
        socket: UnixStream,
        invocation: String,
        sent: u64,
        received: u64,
        end: Instant,
    }
    impl Channel {
        fn remaining(&self) -> Parse<Duration> {
            self.end.checked_duration_since(Instant::now()).ok_or(())
        }
        fn send(&mut self, kind: &str, body: Value, data: bool) -> Parse<()> {
            self.sent = self.sent.checked_add(1).ok_or(())?;
            let raw = serde_json::to_vec(&json!({"schema":"bifrost.test.workflow-commit-ipc/v1",
                "lane":"rust","invocation":self.invocation,"seq":self.sent,"kind":kind,"body":body}))
                .map_err(|_| ())?;
            if raw.is_empty() || raw.len() > if data {INPUT_LIMIT} else {OUTPUT_LIMIT} {
                return Err(());
            }
            self.socket.set_write_timeout(Some(self.remaining()?)).map_err(|_| ())?;
            self.socket.write_all(&(raw.len() as u32).to_be_bytes()).map_err(|_| ())?;
            self.socket.set_write_timeout(Some(self.remaining()?)).map_err(|_| ())?;
            self.socket.write_all(&raw).map_err(|_| ())
        }
        fn receive(&mut self, kind: &str, keys: &[&str]) -> Parse<Node> {
            self.socket.set_read_timeout(Some(self.remaining()?)).map_err(|_| ())?;
            let mut prefix = [0u8;4];
            self.socket.read_exact(&mut prefix).map_err(|_| ())?;
            let length = u32::from_be_bytes(prefix) as usize;
            if length == 0 || length > OUTPUT_LIMIT { return Err(()); }
            let mut raw = vec![0u8;length];
            self.socket.set_read_timeout(Some(self.remaining()?)).map_err(|_| ())?;
            self.socket.read_exact(&mut raw).map_err(|_| ())?;
            let node = Node::parse(std::str::from_utf8(&raw).map_err(|_| ())?,0)?;
            node.keys(&["schema","lane","invocation","seq","kind","body"])?;
            self.received = self.received.checked_add(1).ok_or(())?;
            if node.member("schema")?.string()? != "bifrost.test.workflow-commit-ipc/v1"
                || node.member("lane")?.string()? != "rust"
                || node.member("invocation")?.string()? != self.invocation
                || node.member("kind")?.string()? != kind
                || !matches!(node.member("seq")?,Node::Number(number) if number == &self.received.to_string()) {
                return Err(());
            }
            let Node::Object(mut members) = node else {return Err(());};
            let body = members.remove("body").ok_or(())?;
            body.keys(keys)?;
            Ok(body)
        }
    }
    struct Input {
        invocation: String,
        owned: String,
        foreign: String,
        attempt: String,
        foreign_attempt: String,
        sql: SqlResultInput,
    }
    fn input(node: &Node) -> Parse<Input> {
        node.keys(&["schema","invocation","cycle","actor","scope","request"])?;
        if node.member("schema")?.string()? != "bifrost.test.workflow-result-fault-input/v1"
            || node.member("actor")?.string()? != "rust"
            || !matches!(node.member("cycle")?,Node::Number(number) if number == "2") {return Err(());}
        let invocation = uuid(node.member("invocation")?)?.to_string();
        let scope = node.member("scope")?;
        scope.keys(&["foreign_execution_id","attempt_id","foreign_attempt_id"])?;
        let foreign = uuid(scope.member("foreign_execution_id")?)?.to_string();
        if uuid(scope.member("attempt_id")?)? == uuid(scope.member("foreign_attempt_id")?)? {return Err(());}
        let request_node = node.member("request")?;
        let (id, sql) = request(request_node)?;
        if id != "r-attempt-claimed-unstarted" {return Err(());}
        let owned = uuid(request_node.member("cohort")?.member("execution_id")?)?.to_string();
        if owned == foreign {return Err(());}
        let operation = request_node.member("operation")?;
        if operation.member("lane")?.string()? != "success" {return Err(());}
        let raw = operation.member("raw_fields")?;
        raw.keys(&["status","result","error","error_type","duration_ms","variables","execution_context","metrics","roi"])?;
        for directive in raw.object()?.values() {
            directive.keys(&["kind"])?;
            if directive.member("kind")?.string()? != "absent" {return Err(());}
        }
        Ok(Input {invocation,owned,foreign,
            attempt:uuid(scope.member("attempt_id")?)?.to_string(),
            foreign_attempt:uuid(scope.member("foreign_attempt_id")?)?.to_string(),sql})
    }
    fn binding() -> Value { json!({"cycle":2,"actor":"rust","connection":2}) }
    fn classification(error: &sqlx::Error) -> Value {
        match error {
            sqlx::Error::Io(error) => {
                let code = match error.kind() {
                    io::ErrorKind::UnexpectedEof => "unexpected_eof",
                    io::ErrorKind::ConnectionReset => "connection_reset",
                    io::ErrorKind::BrokenPipe => "broken_pipe",
                    _ => "other",
                };
                json!({"family":"rust_io","code":code})
            }
            _ => json!({"family":"other","code":null}),
        }
    }
    async fn snapshot(tx: &mut sqlx::Transaction<'_,sqlx::Postgres>, input: &Input) -> Parse<Value> {
        let mut result = serde_json::Map::new();
        for (name,query,limit) in [("executions",EXECUTIONS_SQL,2usize),
            ("attempts",ATTEMPTS_SQL,2usize),("logs",LOGS_SQL,1024usize)] {
            let rows = sqlx::query(query).persistent(false).bind(&input.owned).bind(&input.foreign)
                .fetch_all(&mut **tx).await.map_err(|_| ())?;
            if rows.len() > limit || (name != "logs" && rows.len() != 2) {return Err(());}
            let mut values = Vec::new();
            for row in rows {
                let bytes: i32 = row.try_get("row_bytes").map_err(|_| ())?;
                let raw: Option<String> = row.try_get("row_json").map_err(|_| ())?;
                let raw = raw.ok_or(())?;
                if bytes <= 0 || bytes as usize > INPUT_LIMIT || raw.len() != bytes as usize {return Err(());}
                let node = Node::parse(&raw,0)?;
                let actual: String = row.try_get("row_id").map_err(|_| ())?;
                let id = node.member("id")?.member("value")?;
                if !(matches!(id,Node::String(value) if value == &actual)
                    || matches!(id,Node::Number(value) if value == &actual)) {return Err(());}
                validate_row(name,&node)?;
                if name=="executions" && actual!=input.owned && actual!=input.foreign {return Err(());}
                if name=="attempts" && actual!=input.attempt && actual!=input.foreign_attempt {return Err(());}
                values.push(serde_json::from_str::<Value>(&raw).map_err(|_| ())?);
            }
            result.insert(name.to_owned(),Value::Array(values));
        }
        Ok(Value::Object(result))
    }
    async fn selected(tx: &mut sqlx::Transaction<'_,sqlx::Postgres>) -> Parse<Value> {
        let row = sqlx::query(ASSIGNED_SQL).persistent(false).fetch_one(&mut **tx).await.map_err(|_| ())?;
        let xid: Option<String> = row.try_get("xid").map_err(|_| ())?;
        let xid = xid.ok_or(())?;
        if xid.parse::<u64>().map_err(|_| ())? == 0 || xid.starts_with('0') {return Err(());}
        let pid: i32 = row.try_get("backend_pid").map_err(|_| ())?;
        let database: String = row.try_get("database_name").map_err(|_| ())?;
        let address: Option<String> = row.try_get("server_address").map_err(|_| ())?;
        let address = address.ok_or(())?;
        let port: Option<i32> = row.try_get("server_port").map_err(|_| ())?;
        let port = port.ok_or(())?;
        let version: String = row.try_get("server_version_num").map_err(|_| ())?;
        if pid <= 0 || database.is_empty() || database.len()>63 || address.parse::<std::net::Ipv4Addr>().is_err()
            || !(1..=65535).contains(&port) || !(160000..170000).contains(&version.parse::<u32>().map_err(|_| ())?) {
            return Err(());
        }
        Ok(json!({"xid":xid,"backend_pid":pid,"database_name":database,"server_address":address,
            "server_port":port,"server_version_num":version}))
    }
    pub(super) async fn run(node: &Node, options: PgConnectOptions) -> Parse<SqlResponse> {
        let input = input(node)?;
        let end = Instant::now().checked_add(Duration::from_secs(30)).ok_or(())?;
        let before=std::fs::symlink_metadata("/run/f4-control.sock").map_err(|_| ())?;
        if !before.file_type().is_socket() || before.mode() & 0o7777 != 0o600 {return Err(());}
        let socket = tokio::time::timeout_at(tokio::time::Instant::from_std(end),
            tokio::net::UnixStream::connect("/run/f4-control.sock")).await.map_err(|_| ())?
            .map_err(|_| ())?.into_std().map_err(|_| ())?;
        socket.set_nonblocking(false).map_err(|_| ())?;
        let after=std::fs::symlink_metadata("/run/f4-control.sock").map_err(|_| ())?;
        if (before.dev(),before.ino(),before.mode(),before.uid(),before.gid()) !=
            (after.dev(),after.ino(),after.mode(),after.uid(),after.gid()) {return Err(());}
        let mut channel = Channel {socket,invocation:input.invocation.clone(),sent:0,received:0,end};
        channel.send("hello",json!({}),false)?;
        channel.receive("hello_accept",&[])?;
        let pool = PgPoolOptions::new().max_connections(1).acquire_timeout(Duration::from_secs(5))
            .connect_lazy_with(options);
        let mut error_kind = Value::Null;
        let mut commit = "not_dispatched";
        let mut response = None;
        let operation = tokio::time::timeout_at(tokio::time::Instant::from_std(end),async {
            let mut connection = pool.acquire().await.map_err(|_| ())?;
            let mut tx = connection.begin().await.map_err(|_| ())?;
            for query in ["SET LOCAL statement_timeout = '5000ms'","SET LOCAL lock_timeout = '5000ms'"] {
                sqlx::query(query).persistent(false).execute(&mut *tx).await.map_err(|_| ())?;
            }
            let (decision,mut clock) = sql::apply_result_observed(&mut tx,&input.sql).await;
            let Ok(SqlDecision::Applied(AppliedResult {plan:tentative,affected_execution_rows,affected_attempt_rows})) = decision else {
                let _original_rollback = tx.rollback().await;
                return Err(());
            };
            let rows = snapshot(&mut tx,&input).await?;
            let witness = selected(&mut tx).await?;
            channel.send("snapshot",json!({"cycle":2,"actor":"rust","phase":"post_flush","rows":rows}),true)?;
            let mut ready = binding();
            let map = ready.as_object_mut().ok_or(())?;
            map.insert("xid".to_owned(),witness.get("xid").ok_or(())?.clone());
            map.insert("backend_pid".to_owned(),witness.get("backend_pid").ok_or(())?.clone());
            map.insert("query_witness".to_owned(),witness);
            channel.send("selected_ready",ready,false)?;
            let release = channel.receive("release_commit",&["cycle","actor","connection"])?;
            if release.member("actor")?.string()? != "rust"
                || !matches!(release.member("cycle")?,Node::Number(number) if number == "2")
                || !matches!(release.member("connection")?,Node::Number(number) if number == "2") {return Err(());}
            channel.send("listener_returned",binding(),false)?;
            clock.commit_dispatch();
            // Borrow the concrete original cause before the ordinary lossy
            // mapper. Consumed Err retains SQLx's incumbent Drop rollback queue.
            match tx.commit().await {
                Ok(()) => {
                    commit="acknowledged";
                    clock.commit_ack();
                    response=Some(SqlResponse {decision:json!({"kind":"applied","plan":plan(tentative)}),
                        settlement:Settlement::Committed,execution_rows:Some(affected_execution_rows),
                        attempt_rows:Some(affected_attempt_rows),failed:false,clock:Some(clock)});
                    Ok(())
                }
                Err(error) => {
                    commit="error";
                    error_kind=classification(&error);
                    let original=SettlementFailure::database(SettlementStage::Commit,error);
                    let mut failed=SqlResponse::failure(Failure::Settlement(original),Settlement::Unknown);
                    failed.execution_rows=Some(affected_execution_rows);
                    failed.attempt_rows=Some(affected_attempt_rows);
                    failed.clock=Some(clock);
                    response=Some(failed);
                    Ok(())
                }
            }
        }).await;
        let close_completed = tokio::time::timeout_at(tokio::time::Instant::from_std(end),pool.close()).await.is_ok();
        let closed = close_completed && pool.is_closed();
        let publication = channel.send("actor_finished",json!({"cycle":2,"actor":"rust","connection":2,
            "commit":commit,"error":error_kind,"pool_closed":closed}),false);
        // Publication/close cannot turn native failure or normal non-target ACK
        // into the required committed/response-lost positive. Parent qualifies
        // actual error+fate+rows independently, and requires real process exit.
        if !matches!(operation,Ok(Ok(()))) || publication.is_err() || !closed {return Err(());}
        let response=response.ok_or(())?;
        if !delivery_admitted(&response) {return Err(());}
        Ok(response)
    }

    const EXECUTIONS_SQL: &str = "SELECT id::text AS row_id,row_bytes,CASE WHEN row_bytes<=65536 THEN row_data::text ELSE NULL END AS row_json FROM (SELECT id,row_data,pg_catalog.octet_length(row_data::text) AS row_bytes FROM (SELECT id,pg_catalog.jsonb_build_object('id',pg_catalog.jsonb_build_object('sql_null',\"id\" IS NULL,'value',\"id\"::text),'workflow_name',pg_catalog.jsonb_build_object('sql_null',\"workflow_name\" IS NULL,'value',\"workflow_name\"::text),'workflow_version',pg_catalog.jsonb_build_object('sql_null',\"workflow_version\" IS NULL,'value',\"workflow_version\"::text),'status',pg_catalog.jsonb_build_object('sql_null',\"status\" IS NULL,'value',\"status\"::text),'parameters',pg_catalog.jsonb_build_object('sql_null',\"parameters\" IS NULL,'value',\"parameters\"::text),'result',pg_catalog.jsonb_build_object('sql_null',\"result\" IS NULL,'value',\"result\"::text),'result_type',pg_catalog.jsonb_build_object('sql_null',\"result_type\" IS NULL,'value',\"result_type\"::text),'variables',pg_catalog.jsonb_build_object('sql_null',\"variables\" IS NULL,'value',\"variables\"::text),'execution_context',pg_catalog.jsonb_build_object('sql_null',\"execution_context\" IS NULL,'value',\"execution_context\"::text),'error_message',pg_catalog.jsonb_build_object('sql_null',\"error_message\" IS NULL,'value',\"error_message\"::text),'started_at',pg_catalog.jsonb_build_object('sql_null',\"started_at\" IS NULL,'value',(extract(epoch FROM \"started_at\") * 1000000)::bigint),'completed_at',pg_catalog.jsonb_build_object('sql_null',\"completed_at\" IS NULL,'value',(extract(epoch FROM \"completed_at\") * 1000000)::bigint),'duration_ms',pg_catalog.jsonb_build_object('sql_null',\"duration_ms\" IS NULL,'value',\"duration_ms\"),'peak_memory_bytes',pg_catalog.jsonb_build_object('sql_null',\"peak_memory_bytes\" IS NULL,'value',\"peak_memory_bytes\"),'process_rss_bytes',pg_catalog.jsonb_build_object('sql_null',\"process_rss_bytes\" IS NULL,'value',\"process_rss_bytes\"),'cpu_user_seconds',pg_catalog.jsonb_build_object('sql_null',\"cpu_user_seconds\" IS NULL,'value',pg_catalog.encode(pg_catalog.float8send(\"cpu_user_seconds\"),'hex')),'cpu_system_seconds',pg_catalog.jsonb_build_object('sql_null',\"cpu_system_seconds\" IS NULL,'value',pg_catalog.encode(pg_catalog.float8send(\"cpu_system_seconds\"),'hex')),'cpu_total_seconds',pg_catalog.jsonb_build_object('sql_null',\"cpu_total_seconds\" IS NULL,'value',pg_catalog.encode(pg_catalog.float8send(\"cpu_total_seconds\"),'hex')),'time_saved',pg_catalog.jsonb_build_object('sql_null',\"time_saved\" IS NULL,'value',\"time_saved\"),'value',pg_catalog.jsonb_build_object('sql_null',\"value\" IS NULL,'value',\"value\"::text),'executed_by',pg_catalog.jsonb_build_object('sql_null',\"executed_by\" IS NULL,'value',\"executed_by\"::text),'executed_by_name',pg_catalog.jsonb_build_object('sql_null',\"executed_by_name\" IS NULL,'value',\"executed_by_name\"::text),'organization_id',pg_catalog.jsonb_build_object('sql_null',\"organization_id\" IS NULL,'value',\"organization_id\"::text),'form_id',pg_catalog.jsonb_build_object('sql_null',\"form_id\" IS NULL,'value',\"form_id\"::text),'workflow_id',pg_catalog.jsonb_build_object('sql_null',\"workflow_id\" IS NULL,'value',\"workflow_id\"::text),'solution_deployment_id',pg_catalog.jsonb_build_object('sql_null',\"solution_deployment_id\" IS NULL,'value',\"solution_deployment_id\"::text),'runtime_mode',pg_catalog.jsonb_build_object('sql_null',\"runtime_mode\" IS NULL,'value',\"runtime_mode\"::text),'runtime_evidence',pg_catalog.jsonb_build_object('sql_null',\"runtime_evidence\" IS NULL,'value',\"runtime_evidence\"::text),'runtime_evidence_hash',pg_catalog.jsonb_build_object('sql_null',\"runtime_evidence_hash\" IS NULL,'value',\"runtime_evidence_hash\"::text),'dispatch_evidence',pg_catalog.jsonb_build_object('sql_null',\"dispatch_evidence\" IS NULL,'value',\"dispatch_evidence\"::text),'dispatch_evidence_hash',pg_catalog.jsonb_build_object('sql_null',\"dispatch_evidence_hash\" IS NULL,'value',\"dispatch_evidence_hash\"::text),'retry_policy',pg_catalog.jsonb_build_object('sql_null',\"retry_policy\" IS NULL,'value',\"retry_policy\"::text),'attempt_tracking_version',pg_catalog.jsonb_build_object('sql_null',\"attempt_tracking_version\" IS NULL,'value',\"attempt_tracking_version\"::text),'api_key_id',pg_catalog.jsonb_build_object('sql_null',\"api_key_id\" IS NULL,'value',\"api_key_id\"::text),'is_local_execution',pg_catalog.jsonb_build_object('sql_null',\"is_local_execution\" IS NULL,'value',\"is_local_execution\"),'execution_model',pg_catalog.jsonb_build_object('sql_null',\"execution_model\" IS NULL,'value',\"execution_model\"::text),'session_id',pg_catalog.jsonb_build_object('sql_null',\"session_id\" IS NULL,'value',\"session_id\"::text),'created_at',pg_catalog.jsonb_build_object('sql_null',\"created_at\" IS NULL,'value',(extract(epoch FROM \"created_at\") * 1000000)::bigint),'scheduled_at',pg_catalog.jsonb_build_object('sql_null',\"scheduled_at\" IS NULL,'value',(extract(epoch FROM \"scheduled_at\") * 1000000)::bigint)) AS row_data FROM public.executions WHERE id IN ($1::uuid,$2::uuid) ORDER BY id LIMIT 3) AS cells) AS sized ORDER BY id";

    const ATTEMPTS_SQL: &str = "SELECT id::text AS row_id,row_bytes,CASE WHEN row_bytes<=65536 THEN row_data::text ELSE NULL END AS row_json FROM (SELECT id,row_data,pg_catalog.octet_length(row_data::text) AS row_bytes FROM (SELECT id,pg_catalog.jsonb_build_object('id',pg_catalog.jsonb_build_object('sql_null',\"id\" IS NULL,'value',\"id\"::text),'execution_id',pg_catalog.jsonb_build_object('sql_null',\"execution_id\" IS NULL,'value',\"execution_id\"::text),'attempt_number',pg_catalog.jsonb_build_object('sql_null',\"attempt_number\" IS NULL,'value',\"attempt_number\"),'claim_token',pg_catalog.jsonb_build_object('sql_null',\"claim_token\" IS NULL,'value',\"claim_token\"::text),'status',pg_catalog.jsonb_build_object('sql_null',\"status\" IS NULL,'value',\"status\"::text),'phase',pg_catalog.jsonb_build_object('sql_null',\"phase\" IS NULL,'value',\"phase\"::text),'failure_phase',pg_catalog.jsonb_build_object('sql_null',\"failure_phase\" IS NULL,'value',\"failure_phase\"::text),'failure_code',pg_catalog.jsonb_build_object('sql_null',\"failure_code\" IS NULL,'value',\"failure_code\"::text),'worker_id',pg_catalog.jsonb_build_object('sql_null',\"worker_id\" IS NULL,'value',\"worker_id\"::text),'worker_incarnation_id',pg_catalog.jsonb_build_object('sql_null',\"worker_incarnation_id\" IS NULL,'value',\"worker_incarnation_id\"::text),'process_id',pg_catalog.jsonb_build_object('sql_null',\"process_id\" IS NULL,'value',\"process_id\"::text),'runtime_mode',pg_catalog.jsonb_build_object('sql_null',\"runtime_mode\" IS NULL,'value',\"runtime_mode\"::text),'runtime_evidence_hash',pg_catalog.jsonb_build_object('sql_null',\"runtime_evidence_hash\" IS NULL,'value',\"runtime_evidence_hash\"::text),'dispatch_evidence_hash',pg_catalog.jsonb_build_object('sql_null',\"dispatch_evidence_hash\" IS NULL,'value',\"dispatch_evidence_hash\"::text),'policy_digest',pg_catalog.jsonb_build_object('sql_null',\"policy_digest\" IS NULL,'value',\"policy_digest\"::text),'policy_version',pg_catalog.jsonb_build_object('sql_null',\"policy_version\" IS NULL,'value',\"policy_version\"::text),'published_at',pg_catalog.jsonb_build_object('sql_null',\"published_at\" IS NULL,'value',(extract(epoch FROM \"published_at\") * 1000000)::bigint),'claimed_at',pg_catalog.jsonb_build_object('sql_null',\"claimed_at\" IS NULL,'value',(extract(epoch FROM \"claimed_at\") * 1000000)::bigint),'started_at',pg_catalog.jsonb_build_object('sql_null',\"started_at\" IS NULL,'value',(extract(epoch FROM \"started_at\") * 1000000)::bigint),'heartbeat_at',pg_catalog.jsonb_build_object('sql_null',\"heartbeat_at\" IS NULL,'value',(extract(epoch FROM \"heartbeat_at\") * 1000000)::bigint),'completed_at',pg_catalog.jsonb_build_object('sql_null',\"completed_at\" IS NULL,'value',(extract(epoch FROM \"completed_at\") * 1000000)::bigint),'duration_ms',pg_catalog.jsonb_build_object('sql_null',\"duration_ms\" IS NULL,'value',\"duration_ms\"),'peak_memory_bytes',pg_catalog.jsonb_build_object('sql_null',\"peak_memory_bytes\" IS NULL,'value',\"peak_memory_bytes\"),'cpu_total_seconds',pg_catalog.jsonb_build_object('sql_null',\"cpu_total_seconds\" IS NULL,'value',pg_catalog.encode(pg_catalog.float8send(\"cpu_total_seconds\"),'hex')),'created_at',pg_catalog.jsonb_build_object('sql_null',\"created_at\" IS NULL,'value',(extract(epoch FROM \"created_at\") * 1000000)::bigint)) AS row_data FROM public.workflow_execution_attempts WHERE execution_id IN ($1::uuid,$2::uuid) ORDER BY id LIMIT 3) AS cells) AS sized ORDER BY id";

    const LOGS_SQL: &str = "SELECT id::text AS row_id,row_bytes,CASE WHEN row_bytes<=65536 THEN row_data::text ELSE NULL END AS row_json FROM (SELECT id,row_data,pg_catalog.octet_length(row_data::text) AS row_bytes FROM (SELECT id,pg_catalog.jsonb_build_object('id',pg_catalog.jsonb_build_object('sql_null',\"id\" IS NULL,'value',\"id\"),'execution_id',pg_catalog.jsonb_build_object('sql_null',\"execution_id\" IS NULL,'value',\"execution_id\"::text),'level',pg_catalog.jsonb_build_object('sql_null',\"level\" IS NULL,'value',\"level\"::text),'message',pg_catalog.jsonb_build_object('sql_null',\"message\" IS NULL,'value',\"message\"::text),'log_metadata',pg_catalog.jsonb_build_object('sql_null',\"log_metadata\" IS NULL,'value',\"log_metadata\"::text),'timestamp',pg_catalog.jsonb_build_object('sql_null',\"timestamp\" IS NULL,'value',(extract(epoch FROM \"timestamp\") * 1000000)::bigint),'sequence',pg_catalog.jsonb_build_object('sql_null',\"sequence\" IS NULL,'value',\"sequence\")) AS row_data FROM public.execution_logs WHERE execution_id IN ($1::uuid,$2::uuid) ORDER BY id LIMIT 1025) AS cells) AS sized ORDER BY id";

    const ASSIGNED_SQL: &str = "SELECT pg_backend_pid() AS backend_pid,pg_current_xact_id_if_assigned()::text AS xid,current_database() AS database_name,inet_server_addr()::text AS server_address,inet_server_port() AS server_port,current_setting('server_version_num') AS server_version_num";

    fn validate_row(table: &str,node: &Node) -> Parse<()> {
        let columns: &[(&str,&str,bool)] = match table {
            "executions" => &[
                ("id","uuid",false),
                ("workflow_name","text",false),
                ("workflow_version","text",true),
                ("status","text",false),
                ("parameters","jsonb",false),
                ("result","jsonb",true),
                ("result_type","text",true),
                ("variables","jsonb",true),
                ("execution_context","jsonb",true),
                ("error_message","text",true),
                ("started_at","utc_us",true),
                ("completed_at","utc_us",true),
                ("duration_ms","integer",true),
                ("peak_memory_bytes","integer",true),
                ("process_rss_bytes","integer",true),
                ("cpu_user_seconds","float64_bits",true),
                ("cpu_system_seconds","float64_bits",true),
                ("cpu_total_seconds","float64_bits",true),
                ("time_saved","integer",false),
                ("value","decimal",false),
                ("executed_by","uuid",true),
                ("executed_by_name","text",false),
                ("organization_id","uuid",true),
                ("form_id","uuid",true),
                ("workflow_id","uuid",true),
                ("solution_deployment_id","uuid",true),
                ("runtime_mode","text",false),
                ("runtime_evidence","jsonb",true),
                ("runtime_evidence_hash","text",true),
                ("dispatch_evidence","jsonb",true),
                ("dispatch_evidence_hash","text",true),
                ("retry_policy","jsonb",false),
                ("attempt_tracking_version","text",true),
                ("api_key_id","uuid",true),
                ("is_local_execution","bool",false),
                ("execution_model","text",true),
                ("session_id","uuid",true),
                ("created_at","utc_us",false),
                ("scheduled_at","utc_us",true),
            ],
            "attempts" => &[
                ("id","uuid",false),
                ("execution_id","uuid",false),
                ("attempt_number","integer",false),
                ("claim_token","uuid",true),
                ("status","text",false),
                ("phase","text",false),
                ("failure_phase","text",true),
                ("failure_code","text",true),
                ("worker_id","text",true),
                ("worker_incarnation_id","uuid",true),
                ("process_id","text",true),
                ("runtime_mode","text",true),
                ("runtime_evidence_hash","text",true),
                ("dispatch_evidence_hash","text",true),
                ("policy_digest","text",true),
                ("policy_version","text",false),
                ("published_at","utc_us",true),
                ("claimed_at","utc_us",true),
                ("started_at","utc_us",true),
                ("heartbeat_at","utc_us",true),
                ("completed_at","utc_us",true),
                ("duration_ms","integer",true),
                ("peak_memory_bytes","integer",true),
                ("cpu_total_seconds","float64_bits",true),
                ("created_at","utc_us",false),
            ],
            "logs" => &[
                ("id","integer",false),
                ("execution_id","uuid",false),
                ("level","text",false),
                ("message","text",false),
                ("log_metadata","jsonb",true),
                ("timestamp","utc_us",false),
                ("sequence","integer",false),
            ],
            _ => return Err(()),
        };
        let keys:Vec<&str>=columns.iter().map(|(name,_,_)|*name).collect();
        node.keys(&keys)?;
        for (name,kind,nullable) in columns {
            let cell=node.member(name)?;
            cell.keys(&["sql_null","value"])?;
            let Node::Bool(is_null)=cell.member("sql_null")? else {return Err(());};
            let value=cell.member("value")?;
            if *is_null {
                if !*nullable || !matches!(value,Node::Null) {return Err(());}
                continue;
            }
            match *kind {
                "uuid" => {uuid(value)?;}
                "text"|"jsonb"|"decimal" => {value.string()?;}
                "bool" if matches!(value,Node::Bool(_)) => {}
                "integer"|"utc_us" => {
                    let Node::Number(raw)=value else {return Err(());};
                    let number=raw.parse::<i64>().map_err(|_| ())?;
                    if *kind=="integer" && !matches!(*name,"peak_memory_bytes"|"process_rss_bytes") {
                        i32::try_from(number).map_err(|_| ())?;
                    }
                    if number.to_string()!=*raw {return Err(());}
                }
                "float64_bits" => {
                    let raw=value.string()?;
                    if raw.len()!=16 || !raw.bytes().all(|byte|byte.is_ascii_digit()||(b'a'..=b'f').contains(&byte)) {return Err(());}
                    let bits=u64::from_str_radix(raw,16).map_err(|_| ())?;
                    if !f64::from_bits(bits).is_finite() {return Err(());}
                }
                _ => return Err(()),
            }
        }
        Ok(())
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    let mut args = std::env::args_os().skip(1);
    let Some(command) = args.next() else {
        return ExitCode::from(2);
    };
    if args.next().is_some() {
        return ExitCode::from(2);
    }
    let Ok(node) = read_input() else {
        return ExitCode::from(2);
    };
    match command.to_str() {
        Some("apply-result-fault") => {
            let Ok(url)=std::env::var("BIFROST_RUST_TEST_DATABASE_URL") else {return ExitCode::from(2);};
            if !url.starts_with("postgres://") && !url.starts_with("postgresql://") {return ExitCode::from(2);}
            let Ok(options)=PgConnectOptions::from_str(&url) else {return ExitCode::from(2);};
            let options=options.statement_cache_capacity(0).disable_statement_logging();
            drop(url);
            match commit_fault::run(&node,options).await {
                Ok(response)=>{
                    if emit(&response.output("r-attempt-claimed-unstarted")).is_err() || response.failed {
                        ExitCode::from(1)
                    } else {ExitCode::SUCCESS}
                },
                Err(())=>ExitCode::from(1),
            }
        }
        Some("observe-schema") => {
            let Ok(input) = schema_request(&node) else {
                return ExitCode::from(2);
            };
            let Ok(url) = std::env::var("BIFROST_RUST_TEST_DATABASE_URL") else {
                return ExitCode::from(2);
            };
            if !url.starts_with("postgres://") && !url.starts_with("postgresql://") {
                return ExitCode::from(2);
            }
            let Ok(options) = PgConnectOptions::from_str(&url) else {
                return ExitCode::from(2);
            };
            let options = options
                .statement_cache_capacity(0)
                .disable_statement_logging();
            drop(url);
            match observe_schema(options).await {
                Ok(observed) => {
                    if emit(&observed.output(&input)).is_ok() {
                        ExitCode::SUCCESS
                    } else {
                        SchemaFailure::admission(SchemaStage::Emit).discard();
                        ExitCode::from(1)
                    }
                }
                Err(original) => {
                    original.discard();
                    ExitCode::from(1)
                }
            }
        }
        Some("decode-number") => match number_request(&node) {
            Ok(value) => {
                if emit(&value).is_ok() {
                    ExitCode::SUCCESS
                } else {
                    ExitCode::from(1)
                }
            }
            Err(()) => ExitCode::from(2),
        },
        Some("observe-feature-marker") => {
            let Ok((id, input)) = request(&node) else {
                return ExitCode::from(2);
            };
            if id != "syntheticFeatureMarker"
                || node
                    .member("operation")
                    .and_then(|value| value.member("lane"))
                    .and_then(Node::string)
                    != Ok("success")
            {
                return ExitCode::from(2);
            }
            let observed = sql::observe_feature_marker(&input);
            let output = json!({"schema":"bifrost.test.workflow-result-feature-marker/v1", "case_id":id,
                "result":observed.result, "variables":observed.variables, "context":observed.context});
            if emit(&output).is_ok() {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(1)
            }
        }
        Some("apply-result") => {
            let Ok((id, input)) = request(&node) else {
                return ExitCode::from(2);
            };
            let Ok(url) = std::env::var("BIFROST_RUST_TEST_DATABASE_URL") else {
                return ExitCode::from(2);
            };
            if !url.starts_with("postgres://") && !url.starts_with("postgresql://") {
                return ExitCode::from(2);
            }
            let Ok(options) = PgConnectOptions::from_str(&url) else {
                return ExitCode::from(2);
            };
            let options = options
                .statement_cache_capacity(0)
                .disable_statement_logging();
            drop(url);
            let response = apply(&input, options).await;
            // A known commit is retained internally, but incomplete observation is
            // unavailable delivery: no fabricated infrastructure class or receipt.
            if !delivery_admitted(&response) {
                return ExitCode::from(1);
            }
            let output = response.output(&id);
            if emit(&output).is_err() {
                return ExitCode::from(1);
            }
            if response.failed {
                ExitCode::from(1)
            } else {
                ExitCode::SUCCESS
            }
        }
        _ => ExitCode::from(2),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn resource(stage: SettlementStage) -> Failure {
        Failure::Settlement(SettlementFailure {
            stage,
            class: SettlementClass::Resource,
            code: None,
        })
    }
    #[test]
    fn result_error_first_primary_wins() {
        let original = match PreparedJson::new("[]", "{}".to_owned(), ResultJsonRole::Context) {
            Err(error) => Failure::Operation(error),
            Ok(_) => panic!("synthetic invalid context admitted"),
        };
        let mut first = Some(original);
        let mut attempts = Vec::new();
        for stage in [SettlementStage::Rollback, SettlementStage::Close] {
            attempts.push(stage);
            first_failure(&mut first, resource(stage));
        }
        assert!(attempts == [SettlementStage::Rollback, SettlementStage::Close]);
        match first {
            Some(Failure::Operation(value)) => assert!(
                value.stage() == ResultSqlStage::Decode
                    && value.class() == ResultSqlFailureClass::InvalidRow
                    && value.code().is_none()
            ),
            _ => panic!("synthetic first failure was replaced"),
        }
    }
    fn clock_admission_controls() {
        use sql::{
            ResultAttemptClock as A, ResultClockEvent as E, ResultClockUpper as U,
            ResultCommitClock as C, ResultLogicalClock as L,
        };
        fn event(ordinal: u8) -> Option<E> {
            Some(E {
                ordinal,
                utc_us: 100,
                elapsed_ns: u64::from(ordinal),
            })
        }
        let attempt = A {
            read_ack: event(1),
            sample: event(2),
            write_dispatch: event(3),
            write_ack: event(4),
        };
        let logical = L {
            status_read_ack: event(5),
            sample: event(6),
            upper: event(7),
            upper_kind: U::ContextReadDispatch,
            write_ack: event(8),
        };
        let commit = C {
            dispatch: event(9),
            ack: event(10),
        };
        let keep_commit = C {
            dispatch: event(5),
            ack: event(6),
        };
        // 1: full and Keep ledgers use actual collector role admission.
        assert!(sql::result_clock_roles_complete(
            Some(attempt),
            Some(logical),
            Some(commit)
        ));
        assert!(sql::result_clock_roles_complete(
            Some(attempt),
            None,
            Some(keep_commit)
        ));
        // 2: equal UTC cannot hide swapped causal roles.
        let swapped = A {
            sample: attempt.write_dispatch,
            write_dispatch: attempt.sample,
            ..attempt
        };
        assert!(!sql::result_clock_roles_complete(
            Some(swapped),
            Some(logical),
            Some(commit)
        ));
        // 3: a logical sample predating its acknowledged read is rejected.
        let early = L {
            sample: Some(E {
                ordinal: 6,
                utc_us: 99,
                elapsed_ns: 6,
            }),
            ..logical
        };
        assert!(!sql::result_clock_roles_complete(
            Some(attempt),
            Some(early),
            Some(commit)
        ));
        // 4: an attempt sample confused with the logical sample role is rejected.
        let wrong_row = A {
            sample: logical.sample,
            ..attempt
        };
        assert!(!sql::result_clock_roles_complete(
            Some(wrong_row),
            Some(logical),
            Some(commit)
        ));
        // 5: sample/write-ACK role confusion and foreign full-ledger commit in Keep.
        let heartbeat = A {
            sample: attempt.write_ack,
            ..attempt
        };
        assert!(!sql::result_clock_roles_complete(
            Some(heartbeat),
            Some(logical),
            Some(commit)
        ));
        assert!(!sql::result_clock_roles_complete(
            Some(attempt),
            None,
            Some(commit)
        ));
        // 6: partial/unsafe event shapes cannot admit delivery.
        let partial = C {
            ack: None,
            ..commit
        };
        assert!(!sql::result_clock_roles_complete(
            Some(attempt),
            Some(logical),
            Some(partial)
        ));
        let unsafe_event = A {
            sample: Some(E {
                ordinal: 2,
                utc_us: 9_007_199_254_740_992,
                elapsed_ns: 2,
            }),
            ..attempt
        };
        assert!(!sql::result_clock_roles_complete(
            Some(unsafe_event),
            Some(logical),
            Some(commit)
        ));
        let no_clock = SqlResponse {
            decision: json!({"kind":"applied"}),
            settlement: Settlement::Committed,
            execution_rows: Some(1),
            attempt_rows: Some(1),
            failed: false,
            clock: None,
        };
        assert!(!delivery_admitted(&no_clock) && no_clock.settlement == Settlement::Committed);
        let output = no_clock.output("syntheticClock");
        assert!(
            output["schema"] == "bifrost.test.workflow-sql-result/v2"
                && output.get("clock_witness").is_some()
        );
        assert!(output.as_object().is_some_and(|object| object.len() == 5));
        assert!(
            output["clock_witness"]
                .as_object()
                .is_some_and(|object| object.len() == 4)
        );
        assert!(output["clock_witness"]["complete"] == false);
        // Same fixed serializer with maximally wide safe events and overestimated
        // closed plan strings; ASCII case IDs need no JSON escaping.
        fn wide(ordinal: u8) -> Option<E> {
            Some(E {
                ordinal,
                utc_us: 9_007_199_254_740_991,
                elapsed_ns: 9_007_199_254_740_991,
            })
        }
        let attempt = A {
            read_ack: wide(1),
            sample: wide(2),
            write_dispatch: wide(3),
            write_ack: wide(4),
        };
        let logical = L {
            status_read_ack: wide(5),
            sample: wide(6),
            upper: wide(7),
            upper_kind: U::WriteExecutionDispatch,
            write_ack: wide(8),
        };
        let commit = C {
            dispatch: wide(9),
            ack: wide(10),
        };
        let plan = json!({"execution":{
            "status":"X".repeat(32),"duration_ms":"X".repeat(32),"completed_at":"X".repeat(32),"result":"X".repeat(32),
            "result_type":"X".repeat(32),"error_message":"X".repeat(32),"time_saved":"X".repeat(32),"value":"X".repeat(32),
            "variables":"X".repeat(32),"execution_context":"X".repeat(32),"metrics":"X".repeat(32),"logs":"X".repeat(32)},
            "attempt":{"status":"X".repeat(32),"phase":"X".repeat(32),"failure_phase":"X".repeat(32),"failure_code":"X".repeat(32),
            "started_at":"X".repeat(32),"heartbeat_at":"X".repeat(32),"completed_at":"X".repeat(32),"duration_ms":"X".repeat(32),
            "peak_memory_bytes":"X".repeat(32),"cpu_total_seconds":"X".repeat(32)}});
        let maximal = json!({"schema":"bifrost.test.workflow-sql-result/v2","case_id":"X".repeat(64),"decision":{"kind":"applied","plan":plan},
            "transaction":{"status":"rolled_back","affected_execution_rows":u64::MAX,"affected_attempt_rows":u64::MAX},
            "clock_witness":clock_views_output(false,Some(attempt),Some(logical),Some(commit))});
        match serde_json::to_vec(&maximal) {
            Ok(bytes) => assert!(bytes.len() + 1 <= OUTPUT_LIMIT),
            Err(_) => panic!("synthetic closed output failed serialization"),
        }
    }
    #[test]
    fn result_settlement_commit_unknown() {
        let error = match acknowledged(
            Err(sqlx::Error::PoolTimedOut),
            SettlementStage::Commit,
            Settlement::Committed,
        ) {
            Err(error) => error,
            Ok(_) => panic!("synthetic failed commit acknowledged"),
        };
        let response = SqlResponse::failure(Failure::Settlement(error), Settlement::Unknown);
        assert!(response.failed && response.settlement == Settlement::Unknown);
        let actual = response.output("syntheticCommit");
        assert!(
            actual["decision"]["kind"] == "infrastructure_failure"
                && actual["decision"]["stage"] == "Commit"
        );
        assert!(
            actual["transaction"]["status"] == "unknown"
                && actual["decision"].get("plan").is_none()
        );
        clock_admission_controls();
    }
    #[test]
    fn result_settlement_rollback_unknown() {
        let error = match acknowledged(
            Err(sqlx::Error::PoolTimedOut),
            SettlementStage::Rollback,
            Settlement::RolledBack,
        ) {
            Err(error) => error,
            Ok(_) => panic!("synthetic failed rollback acknowledged"),
        };
        let response = SqlResponse::failure(Failure::Settlement(error), Settlement::Unknown);
        let actual = response.output("syntheticRollback");
        assert!(
            actual["decision"]["stage"] == "Rollback"
                && actual["transaction"]["status"] == "unknown"
        );
        assert!(actual["decision"].get("reason").is_none());
    }
    #[test]
    fn result_settlement_close_no_database_code() {
        let failure = SettlementFailure::close_resource();
        assert!(
            failure.stage == SettlementStage::Close
                && failure.class == SettlementClass::Resource
                && failure.code.is_none()
        );
        let response = SqlResponse::failure(Failure::Settlement(failure), Settlement::Committed);
        assert!(response.failed && response.settlement == Settlement::Committed);
        assert!(response.output("syntheticClose")["decision"]["kind"] == "infrastructure_failure");
    }
    #[test]
    fn original_lexemes_preserve_integer_width_and_float_sign() {
        assert!(matches!(
            numeric("9007199254740993"),
            Ok(NumericInput::Unsigned(9_007_199_254_740_993))
        ));
        assert!(matches!(
            numeric("18446744073709551615"),
            Ok(NumericInput::Unsigned(u64::MAX))
        ));
        assert!(matches!(numeric("-0"), Ok(NumericInput::Signed(0))));
        assert!(
            matches!(numeric("-0.0"), Ok(NumericInput::Float(value)) if value.to_bits() == (-0.0_f64).to_bits())
        );
        assert!(matches!(numeric("1e0"), Ok(NumericInput::Float(value)) if value == 1.0));
        assert!(numeric("18446744073709551616").is_err() && numeric("1e9999").is_err());
    }
    #[test]
    fn ordinary_object_keys_and_duplicate_depth_rejections() {
        let raw = r#"{"schema":"bifrost.test.workflow-sql/v1","case_id":"syntheticFeatureMarker","operation":{"kind":"result","lane":"success","raw_fields":{"status":{"kind":"absent"},"result":{"kind":"value","value":{"$serde_json::private::RawValue":"{\"hidden\":1}","visible":2}},"error":{"kind":"absent"},"error_type":{"kind":"absent"},"duration_ms":{"kind":"absent"},"variables":{"kind":"value","value":{"$serde_json::private::RawValue":"{\"hidden\":1}","visible":2}},"execution_context":{"kind":"value","value":{"$serde_json::private::RawValue":"{\"hidden\":1}","visible":2}},"metrics":{"kind":"absent"},"roi":{"kind":"absent"}}},"cohort":{"execution_id":"00000000-0000-0000-0000-000000000001","submitted_token":null},"projection":{"prepared_result":{"kind":"value","value":{"$serde_json::private::RawValue":"{\"hidden\":1}","visible":2}},"prepared_variables":{"kind":"value","value":{"$serde_json::private::RawValue":"{\"hidden\":1}","visible":2}},"prepared_context":{"kind":"value","value":{"$serde_json::private::RawValue":"{\"hidden\":1}","visible":2}},"preparation_source_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}}"#;
        let node = match Node::parse(raw, 0) {
            Ok(value) => value,
            Err(()) => panic!("synthetic marker parse rejected"),
        };
        let (_, input) = match request(&node) {
            Ok(value) => value,
            Err(()) => panic!("synthetic marker request rejected"),
        };
        let observed = sql::observe_feature_marker(&input);
        assert!(observed.result && observed.variables && observed.context);

        let raw = "{\"$serde_json::private::RawValue\":\"{\\\"hidden\\\":1}\",\"visible\":2}";
        match Node::parse(raw, 0) {
            Ok(node) => assert!(
                node.object()
                    .is_ok_and(|map| map.len() == 2 && map.contains_key("visible"))
            ),
            Err(()) => panic!("synthetic literal map was rejected"),
        }
        assert!(Node::parse("{\"a\":1,\"a\":2}", 0).is_err());
        assert!(Node::parse("{\"nested\":{\"a\":1,\"a\":2}}", 0).is_err());
        assert!(Node::parse(&format!("{}0{}", "[".repeat(65), "]".repeat(65)), 0).is_err());
        assert!(Node::parse(&format!("{}0{}", "[".repeat(64), "]".repeat(64)), 0).is_ok());
    }
    #[test]
    fn source_jsondata_negative_zero_is_integer_and_floats_are_held() {
        match Node::parse("{\"value\":-0}", 0).and_then(|node| node.canonical_data()) {
            Ok(text) => assert!(text == "{\"value\":0}"),
            Err(()) => panic!("synthetic integer zero rejected"),
        }
        assert!(
            Node::parse("{\"value\":-0.0}", 0)
                .and_then(|node| node.canonical_data())
                .is_err()
        );
        assert!(Node::parse("{\"key\\u0000\":1}", 0).is_err());
    }
}
