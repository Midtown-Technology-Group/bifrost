//! Bounded tests-only NDJSON encoder, not a runtime or evidence authority.

#[path = "../tests/support/evidence_encoding/mod.rs"]
mod evidence_encoding;

use evidence_encoding::{
    EncodingLimits, EvidenceEncodeError, encode_delivery_ascii_fixture, encode_finite_sorted_utf8,
    encode_sorted_ascii_fixture, encode_workspace_source_fixture,
    fixtures::{self, Description},
};
use serde::{Deserialize, Serialize};
use std::{
    io::{self, BufRead, Write},
    process::ExitCode,
};

const MAX_REQUEST_LINE: usize = 8 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    id: String,
    profile: String,
    input: Description,
}
#[derive(Serialize)]
#[serde(tag = "outcome")]
enum Response {
    #[serde(rename = "encoded")]
    Encoded {
        id: String,
        utf8: String,
        hex: String,
    },
    #[serde(rename = "error")]
    Error {
        id: Option<String>,
        error: &'static str,
    },
}

fn response(record: &[u8]) -> Response {
    let request = match serde_json::from_slice::<Request>(record) {
        Ok(request) => request,
        Err(_) => {
            return Response::Error {
                id: None,
                error: "InvalidFixture",
            };
        }
    };
    if request.id.is_empty()
        || request.id.len() > 128
        || !request
            .id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"-_.:".contains(&c))
    {
        return Response::Error {
            id: None,
            error: "InvalidFixture",
        };
    }
    let result = fixtures::decode_root(&request.input).and_then(|root| {
        let limits = EncodingLimits::default();
        match request.profile.as_str() {
            "finite_utf8" => encode_finite_sorted_utf8(&root, limits),
            "sorted_ascii_fixture" => encode_sorted_ascii_fixture(&root, limits),
            "delivery_ascii_fixture" => encode_delivery_ascii_fixture(&root, limits),
            "workspace_source_fixture" => encode_workspace_source_fixture(&root, limits),
            _ => Err(EvidenceEncodeError::InvalidFixture),
        }
    });
    match result {
        Ok(bytes) => {
            let hex = fixtures::hex(&bytes);
            match String::from_utf8(bytes) {
                Ok(utf8) => Response::Encoded {
                    id: request.id,
                    utf8,
                    hex,
                },
                Err(_) => Response::Error {
                    id: Some(request.id),
                    error: "FormatterInvariant",
                },
            }
        }
        Err(error) => Response::Error {
            id: Some(request.id),
            error: error.category(),
        },
    }
}

enum Record {
    Bytes(Vec<u8>),
    TooLarge,
}
fn read_record<R: BufRead>(input: &mut R) -> Result<Option<Record>, &'static str> {
    let mut record = Vec::new();
    let mut oversized = false;
    let mut observed = false;
    loop {
        let available = input.fill_buf().map_err(|_| "InputIo")?;
        if available.is_empty() {
            return Ok(if !observed {
                None
            } else if oversized {
                Some(Record::TooLarge)
            } else {
                Some(Record::Bytes(record))
            });
        }
        observed = true;
        let newline = available.iter().position(|byte| *byte == b'\n');
        let count = newline.map_or(available.len(), |index| index + 1);
        let content = newline.map_or(count, |index| index);
        if !oversized {
            if content > MAX_REQUEST_LINE.saturating_sub(record.len()) {
                oversized = true;
                record.clear();
            } else {
                record.extend_from_slice(&available[..content]);
            }
        }
        input.consume(count);
        if newline.is_some() {
            return Ok(Some(if oversized {
                Record::TooLarge
            } else {
                Record::Bytes(record)
            }));
        }
    }
}
fn encode() -> Result<(), &'static str> {
    let mut input = io::stdin().lock();
    let mut output = io::BufWriter::new(io::stdout().lock());
    while let Some(record) = read_record(&mut input)? {
        let result = match record {
            Record::Bytes(record) => response(&record),
            Record::TooLarge => Response::Error {
                id: None,
                error: "InputTooLarge",
            },
        };
        serde_json::to_writer(&mut output, &result).map_err(|_| "OutputIo")?;
        output.write_all(b"\n").map_err(|_| "OutputIo")?;
    }
    output.flush().map_err(|_| "OutputIo")
}

fn emit() -> Result<(), &'static str> {
    let mut output = Vec::new();
    for vector in fixtures::load().map_err(|_| "InvalidFixture")? {
        let entry = match vector.render() {
            Ok(bytes) => {
                if vector.outcome != "encoded"
                    || vector.utf8.as_deref().map(str::as_bytes) != Some(bytes.as_slice())
                    || vector.hex.as_deref() != Some(fixtures::hex(&bytes).as_str())
                {
                    return Err("FrozenVectorMismatch");
                }
                serde_json::json!({
                    "profile": vector.profile, "name": vector.name, "outcome": "encoded",
                    "utf8": String::from_utf8(bytes.clone()).map_err(|_| "FormatterInvariant")?,
                    "hex": fixtures::hex(&bytes),
                })
            }
            Err(EvidenceEncodeError::NonFinite)
                if vector.outcome == "exception"
                    && vector.exception_type.as_deref() == Some("ValueError")
                    && vector.expected_failure_observed == Some(true) =>
            {
                serde_json::json!({ "profile": vector.profile, "name": vector.name, "outcome": "exception", "error": "NonFinite" })
            }
            Err(_) => return Err("FrozenVectorMismatch"),
        };
        output.push(entry);
    }
    serde_json::to_writer(
        io::stdout().lock(),
        &serde_json::json!({ "schema": "bifrost.test.evidence-encodings/v1", "results": output }),
    )
    .map_err(|_| "OutputIo")
}
fn run() -> Result<(), &'static str> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    match args.as_slice() {
        [operation] if operation == "encode" => encode(),
        [operation] if operation == "emit" => emit(),
        _ => Err("usage: runtime_evidence_vectors encode < REQUESTS.ndjson | emit"),
    }
}
fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(category) => {
            eprintln!("{category}");
            ExitCode::FAILURE
        }
    }
}
