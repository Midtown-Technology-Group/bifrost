use std::{
    error::Error as StdError,
    fs,
    io::{self, Cursor, Read, Write},
    path::PathBuf,
};

use serde::Deserialize;
use serde_json::{Value, json};

use super::*;

type TestResult<T = ()> = Result<T, Box<dyn StdError>>;

#[derive(Deserialize)]
struct Vectors {
    max_frame_bytes: usize,
    max_depth: usize,
    wire: Vec<WireVector>,
    binary: Vec<BinaryVector>,
    sessions: Vec<SessionVector>,
}
#[derive(Deserialize)]
struct WireVector {
    name: String,
    json: Option<String>,
    hex: Option<String>,
    expected: String,
}
#[derive(Deserialize)]
struct BinaryVector {
    name: String,
    hex: String,
    expected: String,
}
#[derive(Deserialize)]
struct SessionVector {
    name: String,
    binding: BindingVector,
    expected_binding: String,
    events: Vec<EventVector>,
}
#[derive(Deserialize)]
struct BindingVector {
    supervisor_incarnation_id: CanonicalUuid,
    session_id: CanonicalUuid,
    process_identity: String,
    logical_kind: String,
    logical_id: CanonicalUuid,
    attempt_kind: String,
    attempt_id: CanonicalUuid,
    attempt_number: Positive,
    prepare_message_id: CanonicalUuid,
    expected_artifact_id: String,
    expected_image_digest: Option<String>,
}
#[derive(Deserialize)]
struct AuthorizationVector {
    committed_start_id: CanonicalUuid,
    parent_duration_seconds: Positive,
}
#[derive(Deserialize)]
struct EventVector {
    action: String,
    direction: Option<String>,
    process_identity: Option<String>,
    frame: Option<serde_json::Value>,
    authorization: Option<AuthorizationVector>,
    expected: String,
}

fn fixture() -> TestResult<Vectors> {
    let path = std::env::var_os("BIFROST_RUNTIME_VECTORS")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/runtime/v1/control-vectors.json")
        });
    let data = fs::read_to_string(path)?;
    Ok(serde_json::from_str(&data)?)
}

fn missing() -> io::Error {
    io::Error::other("invalid shared control vector")
}

fn hex_bytes(hex: &str) -> TestResult<Vec<u8>> {
    if !hex.len().is_multiple_of(2) {
        return Err(missing().into());
    }
    Ok((0..hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16))
        .collect::<Result<Vec<_>, _>>()?)
}

#[test]
fn shared_wire_vectors() -> TestResult {
    let vectors = fixture()?;
    assert_eq!(vectors.max_frame_bytes, MAX_FRAME_BYTES);
    assert_eq!(vectors.max_depth, MAX_DEPTH);
    assert!(!vectors.wire.is_empty());
    for vector in vectors.wire {
        let bytes = match (vector.json, vector.hex) {
            (Some(json), None) => json.into_bytes(),
            (None, Some(hex)) => hex_bytes(&hex)?,
            _ => return Err(missing().into()),
        };
        let result = decode_json(&bytes);
        if vector.expected == "ok" {
            let frame = result?;
            let encoded = encode_frame(&frame)?;
            assert_eq!(
                read_frame(&mut Cursor::new(encoded))?,
                Some(frame),
                "{}",
                vector.name
            );
        } else {
            match result {
                Err(error) => assert_eq!(format!("{error:?}"), vector.expected, "{}", vector.name),
                Ok(_) => panic!("accepted invalid vector: {}", vector.name),
            }
        }
    }
    Ok(())
}

struct PartialReader {
    inner: Cursor<Vec<u8>>,
    interrupted: bool,
}
impl Read for PartialReader {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if self.interrupted {
            self.interrupted = false;
            return Err(io::ErrorKind::Interrupted.into());
        }
        let length = buffer.len().min(1);
        self.inner.read(&mut buffer[..length])
    }
}

struct PartialWriter {
    bytes: Vec<u8>,
    interrupted: bool,
}
impl Write for PartialWriter {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        if self.interrupted {
            self.interrupted = false;
            return Err(io::ErrorKind::Interrupted.into());
        }
        let length = buffer.len().min(1);
        self.bytes.extend_from_slice(&buffer[..length]);
        Ok(length)
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn shared_binary_vectors_with_partial_io() -> TestResult {
    let vectors = fixture()?;
    assert!(!vectors.binary.is_empty());
    for vector in vectors.binary {
        let mut reader = PartialReader {
            inner: Cursor::new(hex_bytes(&vector.hex)?),
            interrupted: true,
        };
        let result = read_frame(&mut reader);
        match vector.expected.as_str() {
            "eof" => assert_eq!(result?, None, "{}", vector.name),
            "ok" => {
                let frame = result?.ok_or_else(missing)?;
                let mut writer = PartialWriter {
                    bytes: Vec::new(),
                    interrupted: true,
                };
                write_frame(&mut writer, &frame)?;
                assert_eq!(
                    read_frame(&mut Cursor::new(writer.bytes))?,
                    Some(frame),
                    "{}",
                    vector.name
                );
            }
            _ => match result {
                Err(error) => assert_eq!(format!("{error:?}"), vector.expected, "{}", vector.name),
                Ok(_) => panic!("accepted invalid binary vector: {}", vector.name),
            },
        }
    }
    Ok(())
}

fn binding(vector: BindingVector) -> TestResult<PreparedBinding> {
    let logical_job = match vector.logical_kind.as_str() {
        "workflow" => LogicalJob::Workflow {
            execution_id: vector.logical_id,
        },
        "agent_run" => LogicalJob::AgentRun {
            run_id: vector.logical_id,
        },
        _ => return Err(missing().into()),
    };
    let attempt = match vector.attempt_kind.as_str() {
        "workflow_execution_attempt" => DomainAttempt::WorkflowExecutionAttempt {
            attempt_id: vector.attempt_id,
            attempt_number: vector.attempt_number,
        },
        "execution_attempt" => DomainAttempt::AgentExecutionAttempt {
            attempt_id: vector.attempt_id,
            attempt_number: vector.attempt_number,
        },
        _ => return Err(missing().into()),
    };
    Ok(PreparedBinding {
        supervisor_incarnation_id: vector.supervisor_incarnation_id,
        session_id: vector.session_id,
        process_identity: vector.process_identity,
        logical_job,
        attempt,
        prepare_message_id: vector.prepare_message_id,
        expected_artifact_id: vector.expected_artifact_id,
        expected_image_digest: vector.expected_image_digest,
    })
}

#[test]
fn shared_parent_session_vectors() -> TestResult {
    let vectors = fixture()?;
    assert!(!vectors.sessions.is_empty());
    for vector in vectors.sessions {
        let result = ControlSession::new(binding(vector.binding)?);
        let mut session = if vector.expected_binding == "ok" {
            result?
        } else {
            match result {
                Err(error) => assert_eq!(
                    format!("{error:?}"),
                    vector.expected_binding,
                    "{}",
                    vector.name
                ),
                Ok(_) => panic!("accepted invalid parent binding: {}", vector.name),
            }
            assert!(vector.events.is_empty());
            continue;
        };
        for event in vector.events {
            let before = session.state();
            let result = match event.action.as_str() {
                "authorize" => {
                    let authorization = event.authorization.ok_or_else(missing)?;
                    session.authorize_start(StartAuthorization {
                        committed_start_id: authorization.committed_start_id,
                        parent_duration_seconds: authorization.parent_duration_seconds,
                    })
                }
                "frame" => {
                    let json = serde_json::to_vec(&event.frame.ok_or_else(missing)?)?;
                    let frame = decode_json(&json)?;
                    let direction = match event.direction.as_deref() {
                        Some("ParentToRuntime") => Direction::ParentToRuntime,
                        Some("RuntimeToParent") => Direction::RuntimeToParent,
                        _ => return Err(missing().into()),
                    };
                    session.accept(
                        direction,
                        &event.process_identity.ok_or_else(missing)?,
                        &frame,
                    )
                }
                _ => return Err(missing().into()),
            };
            match result {
                Ok(()) if event.action == "authorize" => {
                    assert_eq!(event.expected, "ok", "{}", vector.name)
                }
                Ok(()) => assert_eq!(
                    format!("{:?}", session.state()),
                    event.expected,
                    "{}",
                    vector.name
                ),
                Err(error) => {
                    assert_eq!(format!("{error:?}"), event.expected, "{}", vector.name);
                    assert_eq!(
                        session.state(),
                        before,
                        "invalid event mutated frontier: {}",
                        vector.name
                    );
                }
            }
        }
    }
    Ok(())
}

#[test]
fn over_cap_payload_and_concatenated_frames() -> TestResult {
    assert_eq!(
        decode_json(&vec![b' '; MAX_FRAME_BYTES + 1]),
        Err(Error::FrameTooLarge)
    );
    let vectors = fixture()?;
    let json = vectors
        .wire
        .first()
        .and_then(|vector| vector.json.as_ref())
        .ok_or_else(missing)?;
    let frame = decode_json(json.as_bytes())?;
    let bytes = encode_frame(&frame)?;
    let mut stream = Cursor::new([bytes.clone(), bytes].concat());
    assert_eq!(read_frame(&mut stream)?, Some(frame.clone()));
    assert_eq!(read_frame(&mut stream)?, Some(frame));
    assert_eq!(read_frame(&mut stream)?, None);
    Ok(())
}

#[test]
fn ordinary_json_retains_literal_private_looking_keys() -> TestResult {
    // Synthetic ordinary data, never normalized through Value::Deserialize.
    let cases = [
        (
            r#"{"$serde_json::private::RawValue":"{\"hidden\":1}"}"#,
            json!({"$serde_json::private::RawValue": "{\"hidden\":1}"}),
        ),
        (
            r#"{"$serde_json::private::RawValue":"{\"hidden\":1}","neighbor":2}"#,
            json!({"$serde_json::private::RawValue": "{\"hidden\":1}", "neighbor": 2}),
        ),
        (
            r#"{"neighbor":2,"$serde_json::private::RawValue":"{\"hidden\":1}"}"#,
            json!({"neighbor": 2, "$serde_json::private::RawValue": "{\"hidden\":1}"}),
        ),
        (
            r#"{"outer":[{"$serde_json::private::RawValue":"{\"hidden\":1}"}]}"#,
            json!({"outer": [{"$serde_json::private::RawValue": "{\"hidden\":1}"}]}),
        ),
        (
            r#"{"$serde_json::private::RawValue":7}"#,
            json!({"$serde_json::private::RawValue": 7}),
        ),
        (
            r#"{"$serde_json::private::RawValue":true}"#,
            json!({"$serde_json::private::RawValue": true}),
        ),
        (
            r#"{"$serde_json::private::RawValue":null}"#,
            json!({"$serde_json::private::RawValue": null}),
        ),
        (
            r#"{"$serde_json::private::RawValue":"{not-json"}"#,
            json!({"$serde_json::private::RawValue": "{not-json"}),
        ),
    ];
    for (index, (input, expected)) in cases.into_iter().enumerate() {
        let actual = decode_ordinary_json(input.as_bytes())?;
        assert!(actual == expected, "synthetic ordinary map case {index}");
    }
    Ok(())
}

#[test]
fn ordinary_json_keeps_incumbent_structural_limits() {
    for input in [
        Vec::new(),
        vec![0xff],
        br#"{"x":1,"\u0078":2}"#.to_vec(),
        b"null true".to_vec(),
        b"1e309".to_vec(),
    ] {
        assert!(matches!(
            decode_ordinary_json(&input),
            Err(Error::InvalidJson)
        ));
    }
    let at_depth = format!("{}0{}", "[".repeat(MAX_DEPTH), "]".repeat(MAX_DEPTH));
    let value = decode_ordinary_json(at_depth.as_bytes());
    assert!(matches!(value, Ok(Value::Array(_))));
    let over_depth = format!("[{at_depth}]");
    assert!(matches!(
        decode_ordinary_json(over_depth.as_bytes()),
        Err(Error::InvalidJson)
    ));
}

#[test]
fn protocol_precedence_preserves_size_and_depth_boundaries() -> TestResult {
    let vectors = fixture()?;
    let vector = vectors
        .wire
        .iter()
        .find(|vector| vector.name == "e0-unsupported-sequence-zero")
        .ok_or_else(missing)?;
    let mut bytes = vector
        .json
        .as_ref()
        .ok_or_else(missing)?
        .as_bytes()
        .to_vec();
    bytes.resize(MAX_FRAME_BYTES, b' ');
    assert_eq!(decode_json(&bytes), Err(Error::UnsupportedProtocol));
    bytes.push(b' ');
    assert_eq!(decode_json(&bytes), Err(Error::FrameTooLarge));
    for (name, expected) in [
        ("e0-depth64-protocol-first", Error::UnsupportedProtocol),
        ("e0-depth65-before-protocol", Error::InvalidJson),
        ("e0-shape-missing-sequence", Error::InvalidFrame),
    ] {
        let vector = vectors
            .wire
            .iter()
            .find(|vector| vector.name == name)
            .ok_or_else(missing)?;
        let bytes = vector.json.as_ref().ok_or_else(missing)?.as_bytes();
        assert_eq!(decode_json(bytes), Err(expected), "{name}");
    }
    Ok(())
}
