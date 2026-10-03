//! Private, one-shot Result SQL bridge; no application owner or public protocol.
//! The supported parent owns deadline, process disposal and source/image custody.
use std::{collections::BTreeMap, fmt, io::{self, Read, Write}, process::ExitCode, str::FromStr, time::Duration};
use bifrost_contracts::runtime::CanonicalUuid;
use bifrost_db::{workflow_numeric::NumericInput, workflow_parity::{self as sql, AppliedResult, FailureResultFields, PreparedJson, ResultJsonRole, ResultMetrics, ResultPresence, ResultRoi, ResultSqlFailure, ResultSqlFailureClass, ResultSqlStage, SqlClaimFence, SqlDecision, SqlResultInput, SuccessResultFields}};
use bifrost_domain::workflow as domain;
use serde::{Deserialize, de::{self, MapAccess, Visitor}};
use serde_json::{Value, json, value::RawValue};
use sqlx::{Acquire, ConnectOptions, postgres::{PgConnectOptions, PgPoolOptions}};

const INPUT_LIMIT: usize = 65_536;
const OUTPUT_LIMIT: usize = 4_096;
type Parse<T> = Result<T, ()>;

// No formatting traits: all diagnostics are closed static labels.
enum Node { Null, Bool(bool), String(String), Number(String), Array(Vec<Node>), Object(BTreeMap<String, Node>) }
struct RawObject(BTreeMap<String, Box<RawValue>>);
impl<'de> Deserialize<'de> for RawObject {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor;
        impl<'de> Visitor<'de> for ObjectVisitor {
            type Value = RawObject;
            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result { formatter.write_str("closed object") }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<RawObject, A::Error> {
                let mut result = BTreeMap::new();
                while let Some(key) = map.next_key::<String>()? {
                    if result.contains_key(&key) { return Err(de::Error::custom("duplicate member")); }
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
                if depth >= 64 { return Err(()); }
                let RawObject(members) = serde_json::from_str(raw).map_err(|_| ())?;
                let mut nodes = BTreeMap::new();
                for (key, value) in members {
                    if key.contains('\0') { return Err(()); }
                    nodes.insert(key, Self::parse(value.get(), depth + 1)?);
                }
                Ok(Self::Object(nodes))
            }
            Some(b'[') => {
                if depth >= 64 { return Err(()); }
                let values: Vec<Box<RawValue>> = serde_json::from_str(raw).map_err(|_| ())?;
                let nodes = values.iter().map(|value| Self::parse(value.get(), depth + 1)).collect::<Parse<Vec<_>>>()?;
                Ok(Self::Array(nodes))
            }
            Some(b'"') => {
                let value: String = serde_json::from_str(raw).map_err(|_| ())?;
                if value.contains('\0') { return Err(()); }
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
    fn object(&self) -> Parse<&BTreeMap<String, Node>> { if let Self::Object(value) = self { Ok(value) } else { Err(()) } }
    fn string(&self) -> Parse<&str> { if let Self::String(value) = self { Ok(value) } else { Err(()) } }
    fn keys(&self, keys: &[&str]) -> Parse<()> {
        let map = self.object()?;
        if map.len() == keys.len() && keys.iter().all(|key| map.contains_key(*key)) { Ok(()) } else { Err(()) }
    }
    fn member(&self, key: &str) -> Parse<&Node> { self.object()?.get(key).ok_or(()) }
    fn numeric(&self) -> Parse<NumericInput> {
        let Self::Number(text) = self else { return Err(()); };
        numeric(text)
    }
    // Only private JSONdata integers are canonicalized. The original lexical
    // tree remains the numeric classifier for ROI/CPU and decode-number.
    fn canonical_data(&self) -> Parse<String> {
        match self {
            Self::Null => Ok("null".to_owned()), Self::Bool(value) => Ok(value.to_string()),
            Self::String(value) => serde_json::to_string(value).map_err(|_| ()),
            Self::Number(_) => match self.numeric()? { NumericInput::Signed(value) => Ok(value.to_string()), NumericInput::Unsigned(value) => Ok(value.to_string()), NumericInput::Float(_) => Err(()) },
            Self::Array(values) => Ok(format!("[{}]", values.iter().map(Self::canonical_data).collect::<Parse<Vec<_>>>()?.join(","))),
            Self::Object(values) => {
                let mut members = Vec::with_capacity(values.len());
                for (key, value) in values { members.push(format!("{}:{}", serde_json::to_string(key).map_err(|_| ())?, value.canonical_data()?)); }
                Ok(format!("{{{}}}", members.join(",")))
            }
        }
    }
}

fn numeric(text: &str) -> Parse<NumericInput> {
    if text.contains(['.', 'e', 'E']) {
        let value = text.parse::<f64>().map_err(|_| ())?;
        if value.is_finite() { Ok(NumericInput::Float(value)) } else { Err(()) }
    } else if text.starts_with('-') {
        Ok(NumericInput::Signed(text.parse::<i64>().map_err(|_| ())?))
    } else { Ok(NumericInput::Unsigned(text.parse::<u64>().map_err(|_| ())?)) }
}

fn integer(node: &Node) -> Parse<i64> {
    match node.numeric()? { NumericInput::Signed(value) => Ok(value), NumericInput::Unsigned(value) => i64::try_from(value).map_err(|_| ()), NumericInput::Float(_) => Err(()) }
}
fn int32(node: &Node) -> Parse<i32> { i32::try_from(integer(node)?).map_err(|_| ()) }
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
        "absent" => { node.keys(&["kind"])?; Ok(ResultPresence::Absent) }
        "null" => { node.keys(&["kind"])?; Ok(ResultPresence::Null) }
        "value" => {
            node.keys(&["kind", "value"])?;
            let value = node.member("value")?;
            if matches!(value, Node::Null) { return Err(()); }
            Ok(ResultPresence::Value(convert(value)?))
        }
        _ => Err(()),
    }
}
fn text(node: &Node) -> Parse<String> { Ok(node.string()?.to_owned()) }
fn text_tag(node: &Node, key: &str) -> Parse<ResultPresence<String>> { tag(node.member(key)?, text) }
fn projection(raw: &Node, prepared: &Node, role: ResultJsonRole) -> Parse<ResultPresence<PreparedJson>> {
    let raw_kind = raw.member("kind")?.string()?;
    let prepared_kind = prepared.member("kind")?.string()?;
    if raw_kind != prepared_kind { return Err(()); }
    match raw_kind {
        "absent" => { raw.keys(&["kind"])?; prepared.keys(&["kind"])?; Ok(ResultPresence::Absent) }
        "null" => { raw.keys(&["kind"])?; prepared.keys(&["kind"])?; Ok(ResultPresence::Null) }
        "value" => {
            raw.keys(&["kind", "value"])?; prepared.keys(&["kind", "value"])?;
            let original = raw.member("value")?.canonical_data()?;
            let safe = prepared.member("value")?.canonical_data()?;
            PreparedJson::new(&original, safe, role).map(ResultPresence::Value).map_err(|_| ())
        }
        _ => Err(()),
    }
}
fn metrics(node: &Node) -> Parse<ResultMetrics> {
    node.keys(&["peak_memory_bytes", "process_rss_bytes", "cpu_user_seconds", "cpu_system_seconds", "cpu_total_seconds"])?;
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
    Ok(ResultRoi { time_saved: tag(node.member("time_saved")?, int32)?, value: tag(node.member("value")?, Node::numeric)? })
}
fn case_id(node: &Node) -> Parse<String> {
    let value = node.string()?;
    if value.is_empty() || value.len() > 64 || !value.as_bytes()[0].is_ascii_alphabetic()
        || !value.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-')) { return Err(()); }
    Ok(value.to_owned())
}
fn uuid(node: &Node) -> Parse<CanonicalUuid> { CanonicalUuid::new(node.string()?.to_owned()).map_err(|_| ()) }

fn request(node: &Node) -> Parse<(String, SqlResultInput)> {
    node.keys(&["schema", "case_id", "operation", "cohort", "projection"])?;
    if node.member("schema")?.string()? != "bifrost.test.workflow-sql/v1" { return Err(()); }
    let id = case_id(node.member("case_id")?)?;
    let cohort = node.member("cohort")?; cohort.keys(&["execution_id", "submitted_token"])?;
    let execution_id = uuid(cohort.member("execution_id")?)?;
    let fence = match cohort.member("submitted_token")? { Node::Null => None, node => Some(SqlClaimFence::new(uuid(node)?)) };
    let operation = node.member("operation")?; operation.keys(&["kind", "lane", "raw_fields"])?;
    if operation.member("kind")?.string()? != "result" { return Err(()); }
    let lane = operation.member("lane")?.string()?;
    let fields = operation.member("raw_fields")?;
    let prepared = node.member("projection")?;
    prepared.keys(&["prepared_result", "prepared_variables", "prepared_context", "preparation_source_sha256"])?;
    let digest = prepared.member("preparation_source_sha256")?.string()?;
    if digest.len() != 64 || !digest.bytes().all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f')) { return Err(()); }
    // Exact source receipt admission is the parent harness's precondition;
    // this syntax check cannot bless arbitrary source-prepared values.
    let context = projection(fields.member("execution_context")?, prepared.member("prepared_context")?, ResultJsonRole::Context)?;
    let input = match lane {
        "success" => {
            fields.keys(&["status", "result", "error", "error_type", "duration_ms", "variables", "execution_context", "metrics", "roi"])?;
            SqlResultInput::success(execution_id, fence, SuccessResultFields {
                status: text_tag(fields, "status")?,
                result: projection(fields.member("result")?, prepared.member("prepared_result")?, ResultJsonRole::Result)?,
                error: text_tag(fields, "error")?, error_type: text_tag(fields, "error_type")?,
                duration_ms: tag(fields.member("duration_ms")?, int32)?,
                variables: projection(fields.member("variables")?, prepared.member("prepared_variables")?, ResultJsonRole::Variables)?,
                execution_context: context, metrics: tag(fields.member("metrics")?, metrics)?, roi: tag(fields.member("roi")?, roi)?,
            })
        }
        "failure" => {
            fields.keys(&["error", "error_type", "duration_ms", "execution_context", "metrics"])?;
            for key in ["prepared_result", "prepared_variables"] {
                if !matches!(tag(prepared.member(key)?, |_| -> Parse<()> { Err(()) })?, ResultPresence::Absent) { return Err(()); }
            }
            SqlResultInput::failure(execution_id, fence, FailureResultFields {
                error: text_tag(fields, "error")?, error_type: text_tag(fields, "error_type")?,
                duration_ms: tag(fields.member("duration_ms")?, int32)?, execution_context: context,
                metrics: tag(fields.member("metrics")?, metrics)?,
            })
        }
        _ => return Err(()),
    }.map_err(|_| ())?;
    Ok((id, input))
}


fn logical_status(value: domain::LogicalExecutionStatus) -> &'static str {
    use domain::LogicalExecutionStatus as S;
    match value { S::Scheduled => "Scheduled", S::Pending => "Pending", S::Running => "Running", S::Success => "Success", S::Failed => "Failed", S::Timeout => "Timeout", S::Stuck => "Stuck", S::CompletedWithErrors => "CompletedWithErrors", S::Cancelling => "Cancelling", S::Cancelled => "Cancelled" }
}
fn attempt_status(value: domain::AttemptStatus) -> &'static str {
    use domain::AttemptStatus as S;
    match value { S::Dispatching => "Dispatching", S::Published => "Published", S::Claimed => "Claimed", S::Running => "Running", S::Succeeded => "Succeeded", S::Failed => "Failed", S::TimedOut => "TimedOut", S::Cancelled => "Cancelled", S::WorkerLost => "WorkerLost", S::AdmissionRejected => "AdmissionRejected" }
}
fn attempt_phase(value: domain::AttemptPhase) -> &'static str {
    use domain::AttemptPhase as S;
    match value { S::Dispatch => "Dispatch", S::Queue => "Queue", S::Claim => "Claim", S::Admission => "Admission", S::Execution => "Execution", S::Result => "Result", S::Terminal => "Terminal" }
}
fn failure_phase(value: domain::FailurePhase) -> &'static str {
    use domain::FailurePhase as S;
    match value { S::Dispatch => "Dispatch", S::Queue => "Queue", S::Claim => "Claim", S::Admission => "Admission", S::Execution => "Execution", S::Result => "Result", S::Worker => "Worker", S::Cancellation => "Cancellation" }
}
fn failure_code(value: domain::FailureCode) -> &'static str {
    use domain::FailureCode as S;
    match value { S::ExecutionTimeout => "ExecutionTimeout", S::Cancelled => "Cancelled", S::ResultPersistFailed => "ResultPersistFailed", S::TenantCodeError => "TenantCodeError", S::CancelledBeforeClaim => "CancelledBeforeClaim" }
}
fn time_write(value: domain::TimeWrite) -> &'static str { match value { domain::TimeWrite::Keep => "Keep", domain::TimeWrite::Now => "Now" } }
fn input_write(value: domain::InputWrite) -> &'static str { match value { domain::InputWrite::Keep => "Keep", domain::InputWrite::SetSupplied => "SetSupplied" } }
fn permission(value: domain::ProjectionPermission) -> &'static str { match value { domain::ProjectionPermission::Keep => "Keep", domain::ProjectionPermission::AllowSupplied => "AllowSupplied" } }
fn reason(value: domain::DecisionError) -> &'static str {
    use domain::DecisionError as E;
    match value { E::MissingExecution => "MissingExecution", E::MissingAttempt => "MissingAttempt", E::InvalidAttemptFence => "InvalidAttemptFence", E::InvalidAttemptState => "InvalidAttemptState", E::InvalidLogicalState => "InvalidLogicalState", E::MissingFence => "MissingFence", E::LegacyUnfencedOutsideTrackedPath => "LegacyUnfencedOutsideTrackedPath", E::RequiresCoordinatorPolicy => "RequiresCoordinatorPolicy", E::InconsistentRows => "InconsistentRows" }
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
    match value { ResultSqlStage::Advisory => "Advisory", ResultSqlStage::ReadHistory => "ReadHistory", ResultSqlStage::ReadExecution => "ReadExecution", ResultSqlStage::ReadAttempt => "ReadAttempt", ResultSqlStage::ReadContext => "ReadContext", ResultSqlStage::Decode => "Decode", ResultSqlStage::Clock => "Clock", ResultSqlStage::WriteAttempt => "WriteAttempt", ResultSqlStage::WriteExecution => "WriteExecution" }
}
fn operation_class(value: ResultSqlFailureClass) -> &'static str {
    match value { ResultSqlFailureClass::Database => "Database", ResultSqlFailureClass::InvalidRow => "InvalidRow", ResultSqlFailureClass::ClockRange => "ClockRange", ResultSqlFailureClass::Cardinality => "Cardinality" }
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum SettlementStage { Setup, Acquire, Begin, Commit, Rollback, Close }
#[derive(Clone, Copy, PartialEq, Eq)]
enum SettlementClass { Database, Resource }
struct SettlementFailure { stage: SettlementStage, class: SettlementClass, code: Option<&'static str> }
impl SettlementFailure {
    fn database(stage: SettlementStage, error: sqlx::Error) -> Self {
        let class = if error.as_database_error().is_some() { SettlementClass::Database } else { SettlementClass::Resource };
        Self { stage, class, code: sql::result_sqlstate(&error) }
    }
    fn close_resource() -> Self { Self { stage: SettlementStage::Close, class: SettlementClass::Resource, code: None } }
    fn stage(&self) -> &'static str {
        match self.stage { SettlementStage::Setup => "Setup", SettlementStage::Acquire => "Acquire", SettlementStage::Begin => "Begin", SettlementStage::Commit => "Commit", SettlementStage::Rollback => "Rollback", SettlementStage::Close => "Close" }
    }
    fn class(&self) -> &'static str { match self.class { SettlementClass::Database => "Database", SettlementClass::Resource => "Resource" } }
}
enum Failure { Operation(ResultSqlFailure), Settlement(SettlementFailure) }
impl Failure {
    fn output(&self) -> Value {
        let (stage, class, code) = match self {
            Self::Operation(value) => (operation_stage(value.stage()), operation_class(value.class()), value.code()),
            Self::Settlement(value) => (value.stage(), value.class(), value.code),
        };
        json!({"kind":"infrastructure_failure","stage":stage,"class":class,"sqlstate":code})
    }
}
fn first_failure(first: &mut Option<Failure>, next: Failure) { if first.is_none() { *first = Some(next); } }
#[derive(Clone, Copy, PartialEq, Eq)]
enum Settlement { Committed, RolledBack, Unknown }
impl Settlement { fn as_str(self) -> &'static str { match self { Self::Committed => "committed", Self::RolledBack => "rolled_back", Self::Unknown => "unknown" } } }
fn acknowledged(result: Result<(), sqlx::Error>, stage: SettlementStage, success: Settlement) -> Result<Settlement, SettlementFailure> {
    result.map(|()| success).map_err(|error| SettlementFailure::database(stage, error))
}
struct SqlResponse {
    decision: Value, settlement: Settlement, execution_rows: Option<u64>, attempt_rows: Option<u64>, failed: bool, clock: Option<sql::ResultClockWitness>,
}
impl SqlResponse {
    fn failure(failure: Failure, settlement: Settlement) -> Self {
        Self { decision: failure.output(), settlement, execution_rows: None, attempt_rows: None, failed: true, clock: None }
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
    let Some(value) = value else { return json!({"complete":false,"attempt":null,"logical":null,"commit":null}); };
    clock_views_output(value.complete(), value.attempt(), value.logical(), value.commit())
}
fn clock_views_output(complete: bool, attempt: Option<sql::ResultAttemptClock>, logical: Option<sql::ResultLogicalClock>, commit: Option<sql::ResultCommitClock>) -> Value {
    let attempt = attempt.map_or(Value::Null, |value| json!({"read_ack":clock_event(value.read_ack),"sample":clock_event(value.sample),"write_dispatch":clock_event(value.write_dispatch),"write_ack":clock_event(value.write_ack)}));
    let logical = logical.map_or(Value::Null, |value| json!({"status_read_ack":clock_event(value.status_read_ack),"sample":clock_event(value.sample),"upper":clock_event(value.upper),"upper_kind":match value.upper_kind { sql::ResultClockUpper::ContextReadDispatch => "context_read_dispatch", sql::ResultClockUpper::WriteExecutionDispatch => "write_execution_dispatch" },"write_ack":clock_event(value.write_ack)}));
    let commit = commit.map_or(Value::Null, |value| json!({"dispatch":clock_event(value.dispatch),"ack":clock_event(value.ack)}));
    json!({"complete":complete,"attempt":attempt,"logical":logical,"commit":commit})
}
fn delivery_admitted(response: &SqlResponse) -> bool {
    response.failed || response.clock.as_ref().is_some_and(sql::ResultClockWitness::complete)
}

async fn transaction(connection: &mut sqlx::pool::PoolConnection<sqlx::Postgres>, input: &SqlResultInput) -> SqlResponse {
    let mut tx = match connection.begin().await {
        Ok(tx) => tx,
        Err(error) => return SqlResponse::failure(Failure::Settlement(SettlementFailure::database(SettlementStage::Begin, error)), Settlement::Unknown),
    };
    let setup = async {
        sqlx::query("SET LOCAL statement_timeout = '5000ms'").persistent(false).execute(&mut *tx).await?;
        sqlx::query("SET LOCAL lock_timeout = '5000ms'").persistent(false).execute(&mut *tx).await?;
        Ok::<(), sqlx::Error>(())
    }.await;
    if let Err(error) = setup {
        let mut failure = Some(Failure::Settlement(SettlementFailure::database(SettlementStage::Setup, error)));
        let settlement = match acknowledged(tx.rollback().await, SettlementStage::Rollback, Settlement::RolledBack) {
            Ok(settlement) => settlement,
            Err(error) => { first_failure(&mut failure, Failure::Settlement(error)); Settlement::Unknown }
        };
        if let Some(failure) = failure { return SqlResponse::failure(failure, settlement); }
        return SqlResponse::failure(Failure::Settlement(SettlementFailure::close_resource()), settlement);
    }
    let (operation, mut clock) = sql::apply_result_observed(&mut tx, input).await;
    let mut response = match operation {
        Ok(SqlDecision::Applied(AppliedResult { plan: tentative, affected_execution_rows, affected_attempt_rows })) => {
            clock.commit_dispatch();
            match acknowledged(tx.commit().await, SettlementStage::Commit, Settlement::Committed) {
                Ok(_) => {
                    clock.commit_ack();
                    SqlResponse { decision: json!({"kind":"applied","plan":plan(tentative)}), settlement: Settlement::Committed,
                        execution_rows: Some(affected_execution_rows), attempt_rows: Some(affected_attempt_rows), failed: false, clock: None }
                },
                Err(error) => {
                    let mut response = SqlResponse::failure(Failure::Settlement(error), Settlement::Unknown);
                    response.execution_rows = Some(affected_execution_rows); response.attempt_rows = Some(affected_attempt_rows);
                    response
                }
            }
        }
        Ok(SqlDecision::Rejected(value)) => match acknowledged(tx.rollback().await, SettlementStage::Rollback, Settlement::RolledBack) {
            Ok(_) => SqlResponse { decision: json!({"kind":"rejected","reason":reason(value)}), settlement: Settlement::RolledBack,
                execution_rows: None, attempt_rows: None, failed: false, clock: None },
            Err(error) => SqlResponse::failure(Failure::Settlement(error), Settlement::Unknown),
        },
        Err(error) => {
            let mut failure = Some(Failure::Operation(error));
            let settlement = match acknowledged(tx.rollback().await, SettlementStage::Rollback, Settlement::RolledBack) {
                Ok(settlement) => settlement,
                Err(error) => { first_failure(&mut failure, Failure::Settlement(error)); Settlement::Unknown }
            };
            if let Some(failure) = failure { SqlResponse::failure(failure, settlement) }
            else { SqlResponse::failure(Failure::Settlement(SettlementFailure::close_resource()), settlement) }
        }
    };
    response.clock = Some(clock);
    response
}

async fn apply(input: &SqlResultInput, options: PgConnectOptions) -> SqlResponse {
    let pool = PgPoolOptions::new().max_connections(1).acquire_timeout(Duration::from_secs(5)).connect_lazy_with(options);
    let mut response = match pool.acquire().await {
        Ok(mut connection) => {
            let response = transaction(&mut connection, input).await;
            drop(connection);
            response
        }
        Err(error) => SqlResponse::failure(Failure::Settlement(SettlementFailure::database(SettlementStage::Acquire, error)), Settlement::Unknown),
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
    if node.member("schema")?.string()? != "bifrost.test.workflow-sql-number/v1" { return Err(()); }
    let id = case_id(node.member("case_id")?)?;
    let role = node.member("role")?.string()?;
    if !matches!(role, "roi" | "cpu" | "json") { return Err(()); }
    let lexeme = node.member("lexeme")?.string()?;
    let parsed = Node::parse(lexeme, 0).and_then(|node| node.numeric());
    let (kind, integer, float_bits, admitted) = match parsed {
        Ok(NumericInput::Signed(value)) => ("signed", Some(value.to_string()), if role == "cpu" { Some(format!("{:016x}", value.to_string().parse::<f64>().map_err(|_| ())?.to_bits())) } else { None }, true),
        Ok(NumericInput::Unsigned(value)) => ("unsigned", Some(value.to_string()), if role == "cpu" { Some(format!("{:016x}", value.to_string().parse::<f64>().map_err(|_| ())?.to_bits())) } else { None }, true),
        Ok(NumericInput::Float(value)) => ("float", None, Some(format!("{:016x}", value.to_bits())), role != "json"),
        Err(()) => ("invalid", None, None, false),
    };
    Ok(json!({"schema":"bifrost.test.workflow-sql-number-result/v1","case_id":id,"kind":kind,"integer":integer,"float_bits":float_bits,"admitted":admitted}))
}

fn read_input() -> Parse<Node> {
    let mut bytes = Vec::new();
    io::stdin().lock().take((INPUT_LIMIT + 1) as u64).read_to_end(&mut bytes).map_err(|_| ())?;
    if bytes.is_empty() || bytes.len() > INPUT_LIMIT { return Err(()); }
    let raw = std::str::from_utf8(&bytes).map_err(|_| ())?;
    let original: Box<RawValue> = serde_json::from_str(raw).map_err(|_| ())?;
    Node::parse(original.get(), 0)
}
fn emit(value: &Value) -> Parse<()> {
    let mut bytes = serde_json::to_vec(value).map_err(|_| ())?;
    bytes.push(b'\n');
    if bytes.len() > OUTPUT_LIMIT { return Err(()); }
    let mut stdout = io::stdout().lock();
    stdout.write_all(&bytes).and_then(|()| stdout.flush()).map_err(|_| ())
}

#[tokio::main]
async fn main() -> ExitCode {
    let mut args = std::env::args_os().skip(1);
    let Some(command) = args.next() else { return ExitCode::from(2); };
    if args.next().is_some() { return ExitCode::from(2); }
    let Ok(node) = read_input() else { return ExitCode::from(2); };
    match command.to_str() {
        Some("decode-number") => match number_request(&node) {
            Ok(value) => if emit(&value).is_ok() { ExitCode::SUCCESS } else { ExitCode::from(1) },
            Err(()) => ExitCode::from(2),
        },
        Some("apply-result") => {
            let Ok((id, input)) = request(&node) else { return ExitCode::from(2); };
            let Ok(url) = std::env::var("BIFROST_RUST_TEST_DATABASE_URL") else { return ExitCode::from(2); };
            if !url.starts_with("postgres://") && !url.starts_with("postgresql://") { return ExitCode::from(2); }
            let Ok(options) = PgConnectOptions::from_str(&url) else { return ExitCode::from(2); };
            let options = options.statement_cache_capacity(0).disable_statement_logging();
            drop(url);
            let response = apply(&input, options).await;
            // A known commit is retained internally, but incomplete observation is
            // unavailable delivery: no fabricated infrastructure class or receipt.
            if !delivery_admitted(&response) { return ExitCode::from(1); }
            let output = response.output(&id);
            if emit(&output).is_err() { return ExitCode::from(1); }
            if response.failed { ExitCode::from(1) } else { ExitCode::SUCCESS }
        }
        _ => ExitCode::from(2),
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    fn resource(stage: SettlementStage) -> Failure {
        Failure::Settlement(SettlementFailure { stage, class: SettlementClass::Resource, code: None })
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
            Some(Failure::Operation(value)) => assert!(value.stage() == ResultSqlStage::Decode && value.class() == ResultSqlFailureClass::InvalidRow && value.code().is_none()),
            _ => panic!("synthetic first failure was replaced"),
        }
    }
    fn clock_admission_controls() {
        use sql::{ResultAttemptClock as A, ResultClockEvent as E, ResultClockUpper as U, ResultCommitClock as C, ResultLogicalClock as L};
        fn event(ordinal: u8) -> Option<E> { Some(E { ordinal, utc_us: 100, elapsed_ns: u64::from(ordinal) }) }
        let attempt = A { read_ack: event(1), sample: event(2), write_dispatch: event(3), write_ack: event(4) };
        let logical = L { status_read_ack: event(5), sample: event(6), upper: event(7), upper_kind: U::ContextReadDispatch, write_ack: event(8) };
        let commit = C { dispatch: event(9), ack: event(10) };
        let keep_commit = C { dispatch: event(5), ack: event(6) };
        // 1: full and Keep ledgers use actual collector role admission.
        assert!(sql::result_clock_roles_complete(Some(attempt), Some(logical), Some(commit)));
        assert!(sql::result_clock_roles_complete(Some(attempt), None, Some(keep_commit)));
        // 2: equal UTC cannot hide swapped causal roles.
        let swapped = A { sample: attempt.write_dispatch, write_dispatch: attempt.sample, ..attempt };
        assert!(!sql::result_clock_roles_complete(Some(swapped), Some(logical), Some(commit)));
        // 3: a logical sample predating its acknowledged read is rejected.
        let early = L { sample: Some(E { ordinal: 6, utc_us: 99, elapsed_ns: 6 }), ..logical };
        assert!(!sql::result_clock_roles_complete(Some(attempt), Some(early), Some(commit)));
        // 4: an attempt sample confused with the logical sample role is rejected.
        let wrong_row = A { sample: logical.sample, ..attempt };
        assert!(!sql::result_clock_roles_complete(Some(wrong_row), Some(logical), Some(commit)));
        // 5: sample/write-ACK role confusion and foreign full-ledger commit in Keep.
        let heartbeat = A { sample: attempt.write_ack, ..attempt };
        assert!(!sql::result_clock_roles_complete(Some(heartbeat), Some(logical), Some(commit)));
        assert!(!sql::result_clock_roles_complete(Some(attempt), None, Some(commit)));
        // 6: partial/unsafe event shapes cannot admit delivery.
        let partial = C { ack: None, ..commit };
        assert!(!sql::result_clock_roles_complete(Some(attempt), Some(logical), Some(partial)));
        let unsafe_event = A { sample: Some(E { ordinal: 2, utc_us: 9_007_199_254_740_992, elapsed_ns: 2 }), ..attempt };
        assert!(!sql::result_clock_roles_complete(Some(unsafe_event), Some(logical), Some(commit)));
        let no_clock = SqlResponse { decision: json!({"kind":"applied"}), settlement: Settlement::Committed,
            execution_rows: Some(1), attempt_rows: Some(1), failed: false, clock: None };
        assert!(!delivery_admitted(&no_clock) && no_clock.settlement == Settlement::Committed);
        let output = no_clock.output("syntheticClock");
        assert!(output["schema"] == "bifrost.test.workflow-sql-result/v2" && output.get("clock_witness").is_some());
        assert!(output.as_object().is_some_and(|object| object.len() == 5));
        assert!(output["clock_witness"].as_object().is_some_and(|object| object.len() == 4));
        assert!(output["clock_witness"]["complete"] == false);
        // Same fixed serializer with maximally wide safe events and overestimated
        // closed plan strings; ASCII case IDs need no JSON escaping.
        fn wide(ordinal: u8) -> Option<E> { Some(E { ordinal, utc_us: 9_007_199_254_740_991, elapsed_ns: 9_007_199_254_740_991 }) }
        let attempt = A { read_ack: wide(1), sample: wide(2), write_dispatch: wide(3), write_ack: wide(4) };
        let logical = L { status_read_ack: wide(5), sample: wide(6), upper: wide(7), upper_kind: U::WriteExecutionDispatch, write_ack: wide(8) };
        let commit = C { dispatch: wide(9), ack: wide(10) };
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
        let error = match acknowledged(Err(sqlx::Error::PoolTimedOut), SettlementStage::Commit, Settlement::Committed) {
            Err(error) => error, Ok(_) => panic!("synthetic failed commit acknowledged"),
        };
        let response = SqlResponse::failure(Failure::Settlement(error), Settlement::Unknown);
        assert!(response.failed && response.settlement == Settlement::Unknown);
        let actual = response.output("syntheticCommit");
        assert!(actual["decision"]["kind"] == "infrastructure_failure" && actual["decision"]["stage"] == "Commit");
        assert!(actual["transaction"]["status"] == "unknown" && actual["decision"].get("plan").is_none());
        clock_admission_controls();
    }
    #[test]
    fn result_settlement_rollback_unknown() {
        let error = match acknowledged(Err(sqlx::Error::PoolTimedOut), SettlementStage::Rollback, Settlement::RolledBack) {
            Err(error) => error, Ok(_) => panic!("synthetic failed rollback acknowledged"),
        };
        let response = SqlResponse::failure(Failure::Settlement(error), Settlement::Unknown);
        let actual = response.output("syntheticRollback");
        assert!(actual["decision"]["stage"] == "Rollback" && actual["transaction"]["status"] == "unknown");
        assert!(actual["decision"].get("reason").is_none());
    }
    #[test]
    fn result_settlement_close_no_database_code() {
        let failure = SettlementFailure::close_resource();
        assert!(failure.stage == SettlementStage::Close && failure.class == SettlementClass::Resource && failure.code.is_none());
        let response = SqlResponse::failure(Failure::Settlement(failure), Settlement::Committed);
        assert!(response.failed && response.settlement == Settlement::Committed);
        assert!(response.output("syntheticClose")["decision"]["kind"] == "infrastructure_failure");
    }
    #[test]
    fn original_lexemes_preserve_integer_width_and_float_sign() {
        assert!(matches!(numeric("9007199254740993"), Ok(NumericInput::Unsigned(9_007_199_254_740_993))));
        assert!(matches!(numeric("18446744073709551615"), Ok(NumericInput::Unsigned(u64::MAX))));
        assert!(matches!(numeric("-0"), Ok(NumericInput::Signed(0))));
        assert!(matches!(numeric("-0.0"), Ok(NumericInput::Float(value)) if value.to_bits() == (-0.0_f64).to_bits()));
        assert!(matches!(numeric("1e0"), Ok(NumericInput::Float(value)) if value == 1.0));
        assert!(numeric("18446744073709551616").is_err() && numeric("1e9999").is_err());
    }
    #[test]
    fn ordinary_object_keys_and_duplicate_depth_rejections() {
        let raw = "{\"$serde_json::private::RawValue\":\"{\\\"hidden\\\":1}\",\"visible\":2}";
        match Node::parse(raw, 0) {
            Ok(node) => assert!(node.object().is_ok_and(|map| map.len() == 2 && map.contains_key("visible"))),
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
            Ok(text) => assert!(text == "{\"value\":0}"), Err(()) => panic!("synthetic integer zero rejected"),
        }
        assert!(Node::parse("{\"value\":-0.0}", 0).and_then(|node| node.canonical_data()).is_err());
        assert!(Node::parse("{\"key\\u0000\":1}", 0).is_err());
    }
}
