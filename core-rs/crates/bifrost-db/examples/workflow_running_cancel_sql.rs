//! Private bounded SQL caller; neither an authority grant nor a state oracle.
use std::{
    fmt,
    io::{self, Read, Write},
    process::ExitCode,
    time::Duration,
};

use bifrost_contracts::runtime::CanonicalUuid;
use bifrost_db::{
    Database, DatabaseConfig,
    workflow_parity::{
        SqlClaimFence, SqlDecision, SqlFailureClass, SqlInfrastructureError, SqlStage,
        apply_cancel, apply_running,
    },
};
use serde::{Deserialize, Deserializer, Serialize, de::Visitor};

const INPUT_LIMIT: usize = 65_536;
const OUTPUT_LIMIT: usize = 4_096;
const INPUT_SCHEMA: &str = "bifrost.test.workflow-running-cancel/v1";
const OUTPUT_SCHEMA: &str = "bifrost.test.workflow-running-cancel-result/v1";

struct RequiredProcess(Option<String>);
impl<'de> Deserialize<'de> for RequiredProcess {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ProcessVisitor;
        impl Visitor<'_> for ProcessVisitor {
            type Value = RequiredProcess;
            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("required null or string")
            }
            fn visit_unit<E: serde::de::Error>(self) -> Result<Self::Value, E> {
                Ok(RequiredProcess(None))
            }
            fn visit_str<E: serde::de::Error>(self, value: &str) -> Result<Self::Value, E> {
                Ok(RequiredProcess(Some(value.to_owned())))
            }
            fn visit_string<E: serde::de::Error>(self, value: String) -> Result<Self::Value, E> {
                Ok(RequiredProcess(Some(value)))
            }
        }
        deserializer.deserialize_any(ProcessVisitor)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    schema: String,
    case_id: String,
    transaction_disposition: Disposition,
    operation: Operation,
}
#[derive(Deserialize, Clone, Copy)]
#[serde(rename_all = "snake_case")]
enum Disposition {
    Commit,
    Rollback,
}
#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum Operation {
    Running {
        execution_id: CanonicalUuid,
        claim_token: CanonicalUuid,
        process_id: RequiredProcess,
    },
    Cancel {
        execution_id: CanonicalUuid,
    },
}
fn parse_input(bytes: &[u8]) -> Result<Input, ()> {
    if bytes.len() > INPUT_LIMIT {
        return Err(());
    }
    let input: Input = serde_json::from_slice(bytes).map_err(|_| ())?;
    let id = input.case_id.as_bytes();
    if input.schema != INPUT_SCHEMA
        || id.is_empty()
        || id.len() > 48
        || !id[0].is_ascii_alphabetic()
        || !id
            .iter()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
    {
        return Err(());
    }
    Ok(input)
}
fn read_input(reader: impl Read) -> Result<Input, ()> {
    let mut bytes = Vec::new();
    reader
        .take((INPUT_LIMIT + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| ())?;
    parse_input(&bytes)
}

#[derive(Serialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Outcome {
    Accepted,
    Rejected,
    InfrastructureFailure,
}
#[derive(Serialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Settlement {
    Committed,
    RolledBack,
    Unknown,
}
#[derive(Serialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Stage {
    Ready,
    Connect,
    Setup,
    Advisory,
    ReadExecution,
    ReadAttempt,
    Decode,
    Clock,
    WriteAttempt,
    WriteExecution,
    Commit,
    Rollback,
}
#[derive(Serialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Failure {
    DomainRejected,
    Database,
    InvalidRow,
    ClockRange,
    Cardinality,
}
#[derive(Serialize)]
struct Output<'a> {
    schema: &'static str,
    case_id: &'a str,
    outcome: Outcome,
    transaction: Settlement,
    stage: Stage,
    error_class: Option<Failure>,
}
fn map_error(error: SqlInfrastructureError) -> (Stage, Failure) {
    let stage = match error.stage {
        SqlStage::Advisory => Stage::Advisory,
        SqlStage::ReadExecution => Stage::ReadExecution,
        SqlStage::ReadAttempt => Stage::ReadAttempt,
        SqlStage::Decode => Stage::Decode,
        SqlStage::Clock => Stage::Clock,
        SqlStage::WriteAttempt => Stage::WriteAttempt,
        SqlStage::WriteExecution => Stage::WriteExecution,
    };
    let class = match error.class {
        SqlFailureClass::Database => Failure::Database,
        SqlFailureClass::InvalidRow => Failure::InvalidRow,
        SqlFailureClass::ClockRange => Failure::ClockRange,
        SqlFailureClass::Cardinality => Failure::Cardinality,
    };
    (stage, class)
}
fn infrastructure(output: &mut Output<'_>, stage: Stage, class: Failure) {
    output.outcome = Outcome::InfrastructureFailure;
    output.stage = stage;
    output.error_class = Some(class);
}
async fn execute<'a>(database: &Database, input: &'a Input) -> Output<'a> {
    let mut output = Output {
        schema: OUTPUT_SCHEMA,
        case_id: &input.case_id,
        outcome: Outcome::Accepted,
        transaction: Settlement::Unknown,
        stage: Stage::Ready,
        error_class: None,
    };
    let mut tx = match database.pool().begin().await {
        Ok(tx) => tx,
        Err(_) => {
            infrastructure(&mut output, Stage::Connect, Failure::Database);
            return output;
        }
    };
    let setup = async {
        for statement in [
            "SET LOCAL statement_timeout = '5000ms'",
            "SET LOCAL lock_timeout = '5000ms'",
        ] {
            sqlx::query(statement)
                .persistent(false)
                .execute(&mut *tx)
                .await?;
        }
        sqlx::query("SELECT set_config('application_name', $1, true)")
            .bind(format!("bifrost-rc:{}", input.case_id))
            .persistent(false)
            .execute(&mut *tx)
            .await?;
        Ok::<(), sqlx::Error>(())
    }
    .await;
    if setup.is_err() {
        infrastructure(&mut output, Stage::Setup, Failure::Database);
    } else {
        let decision = match &input.operation {
            Operation::Running {
                execution_id,
                claim_token,
                process_id,
            } => apply_running(
                &mut tx,
                execution_id.clone(),
                &SqlClaimFence::new(claim_token.clone()),
                process_id.0.as_deref(),
            )
            .await
            .map(|decision| matches!(decision, SqlDecision::Applied(_))),
            Operation::Cancel { execution_id } => apply_cancel(&mut tx, execution_id.clone())
                .await
                .map(|decision| matches!(decision, SqlDecision::Applied(_))),
        };
        match decision {
            Ok(true) => {}
            Ok(false) => {
                output.outcome = Outcome::Rejected;
                output.error_class = Some(Failure::DomainRejected);
            }
            Err(error) => {
                let (stage, class) = map_error(error);
                infrastructure(&mut output, stage, class);
            }
        }
    }
    if output.outcome == Outcome::Accepted
        && matches!(input.transaction_disposition, Disposition::Commit)
    {
        match tx.commit().await {
            Ok(()) => output.transaction = Settlement::Committed,
            Err(_) => infrastructure(&mut output, Stage::Commit, Failure::Database),
        }
    } else {
        match tx.rollback().await {
            Ok(()) => output.transaction = Settlement::RolledBack,
            Err(_) if output.outcome == Outcome::InfrastructureFailure => {}
            Err(_) => infrastructure(&mut output, Stage::Rollback, Failure::Database),
        }
    }
    output
}
fn write_output(writer: &mut impl Write, output: &Output<'_>) -> Result<(), ()> {
    let mut bytes = serde_json::to_vec(output).map_err(|_| ())?;
    bytes.push(b'\n');
    if bytes.len() > OUTPUT_LIMIT {
        return Err(());
    }
    writer
        .write_all(&bytes)
        .and_then(|()| writer.flush())
        .map_err(|_| ())
}
#[tokio::main]
async fn main() -> ExitCode {
    if std::env::args_os().count() != 1 {
        return ExitCode::from(2);
    }
    let input = match read_input(io::stdin().lock()) {
        Ok(input) => input,
        Err(()) => return ExitCode::from(2),
    };
    let config = match std::env::var("BIFROST_RUST_TEST_DATABASE_URL")
        .ok()
        .and_then(|url| DatabaseConfig::new(&url, 1, Duration::from_secs(5)).ok())
    {
        Some(config) => config,
        None => return ExitCode::from(2),
    };
    let database = match Database::new(&config) {
        Ok(database) => database,
        Err(_) => return ExitCode::from(2),
    };
    let output = execute(&database, &input).await;
    database.close().await;
    if write_output(&mut io::stdout().lock(), &output).is_err() {
        return ExitCode::from(1);
    }
    if output.outcome == Outcome::InfrastructureFailure {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    const RUNNING: &str = r#"{"schema":"bifrost.test.workflow-running-cancel/v1","case_id":"Case_1","transaction_disposition":"rollback","operation":{"kind":"running","execution_id":"00000000-0000-0000-0000-000000000001","claim_token":"00000000-0000-0000-0000-000000000002","process_id":null}}"#;

    #[test]
    fn required_nullable_and_closed_fields() -> TestResult {
        let input = parse_input(RUNNING.as_bytes())
            .map_err(|()| io::Error::other("synthetic input failed"))?;
        assert!(matches!(
            input.operation,
            Operation::Running {
                process_id: RequiredProcess(None),
                ..
            }
        ));
        let empty = RUNNING.replace("null", "\"\"");
        let input = parse_input(empty.as_bytes())
            .map_err(|()| io::Error::other("synthetic empty process failed"))?;
        match input.operation {
            Operation::Running {
                process_id: RequiredProcess(Some(process)),
                ..
            } => assert!(process.is_empty()),
            _ => return Err(io::Error::other("synthetic nullable shape failed").into()),
        }
        for bad in [
            RUNNING.replace(",\"process_id\":null", ""),
            RUNNING.replace("null", "true"),
            RUNNING.replace("null", "1"),
            RUNNING.replace(
                "\"process_id\":null",
                "\"process_id\":null,\"process_id\":null",
            ),
            RUNNING.replace(
                "\"case_id\":\"Case_1\"",
                "\"case_id\":\"Case_1\",\"case_id\":\"Other\"",
            ),
            RUNNING.replace(
                "\"kind\":\"running\"",
                "\"kind\":\"running\",\"kind\":\"running\"",
            ),
            RUNNING.replace("\"operation\":", "\"unexpected\":null,\"operation\":"),
            RUNNING.replace(
                "\"process_id\":null",
                "\"process_id\":null,\"unexpected\":null",
            ),
            format!("{RUNNING}{{}}"),
        ] {
            assert!(parse_input(bad.as_bytes()).is_err());
        }
        Ok(())
    }
    #[test]
    fn schema_ids_disposition_and_case_bounds() {
        for bad in [
            RUNNING.replace(INPUT_SCHEMA, "wrong"),
            RUNNING.replace("rollback", "default"),
            RUNNING.replace("00000000-0000-0000-0000-000000000001", "bad"),
            RUNNING.replace("Case_1", "1Case"),
            RUNNING.replace("Case_1", "has space"),
            RUNNING.replace("Case_1", ""),
            RUNNING.replace("Case_1", &"A".repeat(49)),
            RUNNING.replace("Case_1", "é"),
            RUNNING.replace("\"transaction_disposition\":\"rollback\",", ""),
        ] {
            assert!(parse_input(bad.as_bytes()).is_err());
        }
        assert!(parse_input(RUNNING.replace("Case_1", &"A".repeat(48)).as_bytes()).is_ok());
        let cancel = RUNNING.replace("\"kind\":\"running\"", "\"kind\":\"cancel\"");
        assert!(parse_input(cancel.as_bytes()).is_err());
        let cancel = cancel.replace(
            ",\"claim_token\":\"00000000-0000-0000-0000-000000000002\",\"process_id\":null",
            "",
        );
        assert!(parse_input(cancel.as_bytes()).is_ok());
    }
    #[test]
    fn input_byte_utf8_and_depth_bounds() {
        let mut exact = RUNNING.as_bytes().to_vec();
        exact.resize(INPUT_LIMIT, b' ');
        assert!(read_input(exact.as_slice()).is_ok());
        exact.push(b' ');
        assert!(read_input(exact.as_slice()).is_err());
        assert!(read_input(&b"\xff"[..]).is_err());
        let deep = RUNNING.replace("null", &format!("{}0{}", "[".repeat(64), "]".repeat(64)));
        assert!(parse_input(deep.as_bytes()).is_err());
    }
    #[test]
    fn error_mapping_is_closed() {
        let stages = [
            (SqlStage::Advisory, Stage::Advisory),
            (SqlStage::ReadExecution, Stage::ReadExecution),
            (SqlStage::ReadAttempt, Stage::ReadAttempt),
            (SqlStage::Decode, Stage::Decode),
            (SqlStage::Clock, Stage::Clock),
            (SqlStage::WriteAttempt, Stage::WriteAttempt),
            (SqlStage::WriteExecution, Stage::WriteExecution),
        ];
        for (source, target) in stages {
            for (class, expected) in [
                (SqlFailureClass::Database, Failure::Database),
                (SqlFailureClass::InvalidRow, Failure::InvalidRow),
                (SqlFailureClass::ClockRange, Failure::ClockRange),
                (SqlFailureClass::Cardinality, Failure::Cardinality),
            ] {
                assert!(
                    map_error(SqlInfrastructureError {
                        stage: source,
                        class
                    }) == (target, expected)
                );
            }
        }
    }
    #[test]
    fn output_is_bounded_closed_and_redacted() -> TestResult {
        let output = Output {
            schema: OUTPUT_SCHEMA,
            case_id: "Case_1",
            outcome: Outcome::Accepted,
            transaction: Settlement::RolledBack,
            stage: Stage::Ready,
            error_class: None,
        };
        let mut bytes = Vec::new();
        write_output(&mut bytes, &output)
            .map_err(|()| io::Error::other("synthetic output failed"))?;
        assert_eq!(bytes, b"{\"schema\":\"bifrost.test.workflow-running-cancel-result/v1\",\"case_id\":\"Case_1\",\"outcome\":\"accepted\",\"transaction\":\"rolled_back\",\"stage\":\"ready\",\"error_class\":null}\n");
        assert!(bytes.len() <= OUTPUT_LIMIT);
        struct BrokenWriter;
        impl Write for BrokenWriter {
            fn write(&mut self, _: &[u8]) -> io::Result<usize> {
                Err(io::Error::other("synthetic private error"))
            }
            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }
        assert!(write_output(&mut BrokenWriter, &output).is_err());
        let oversized = "A".repeat(OUTPUT_LIMIT);
        let output = Output {
            case_id: &oversized,
            ..output
        };
        let mut bytes = Vec::new();
        assert!(write_output(&mut bytes, &output).is_err());
        assert!(bytes.is_empty());
        Ok(())
    }
}
