//! Tests-only interchange of synthetic golden control fixtures, not a service/runtime.

use bifrost_contracts::runtime::{Frame, MAX_FRAME_BYTES, decode_json, encode_frame, read_frame};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    error::Error,
    fs,
    io::{self, Cursor},
    path::PathBuf,
};

const PROFILE: &str = "bifrost.runtime/v1/control_profile/v1";
const MAX_EXCHANGE_BYTES: u64 = 1024 * 1024;

#[derive(Deserialize)]
struct Fixture {
    wire: Vec<Vector>,
}
#[derive(Deserialize)]
struct Vector {
    name: String,
    json: Option<String>,
    expected: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Exchange {
    profile: String,
    frames: Vec<Encoded>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Encoded {
    name: String,
    frame_hex: String,
}

fn invalid() -> io::Error {
    io::Error::other("invalid synthetic control interchange")
}

fn golden() -> Result<BTreeMap<String, Frame>, Box<dyn Error>> {
    let path = std::env::var_os("BIFROST_RUNTIME_VECTORS")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/runtime/v1/control-vectors.json")
        });
    let fixture: Fixture = serde_json::from_str(&fs::read_to_string(path)?)?;
    let mut frames = BTreeMap::new();
    for vector in fixture
        .wire
        .into_iter()
        .filter(|vector| vector.expected == "ok")
    {
        let frame = decode_json(vector.json.ok_or_else(invalid)?.as_bytes())?;
        if frames.insert(vector.name, frame).is_some() {
            return Err(invalid().into());
        }
    }
    if frames.is_empty() {
        return Err(invalid().into());
    }
    Ok(frames)
}

fn hex(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(char::from(ALPHABET[(byte >> 4) as usize]));
        output.push(char::from(ALPHABET[(byte & 15) as usize]));
    }
    output
}

fn unhex(text: &str) -> Result<Vec<u8>, Box<dyn Error>> {
    if !text.len().is_multiple_of(2)
        || text.len() > (MAX_FRAME_BYTES + 4) * 2
        || !text
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(invalid().into());
    }
    Ok((0..text.len())
        .step_by(2)
        .map(|index| u8::from_str_radix(&text[index..index + 2], 16))
        .collect::<Result<Vec<_>, _>>()?)
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let golden = golden()?;
    match args.as_slice() {
        [operation] if operation == "emit" => {
            let frames = golden
                .iter()
                .map(|(name, frame)| {
                    Ok(Encoded {
                        name: name.clone(),
                        frame_hex: hex(&encode_frame(frame)?),
                    })
                })
                .collect::<Result<Vec<_>, bifrost_contracts::runtime::Error>>()?;
            serde_json::to_writer(
                io::stdout().lock(),
                &Exchange {
                    profile: PROFILE.to_owned(),
                    frames,
                },
            )?;
        }
        [operation, path] if operation == "validate" => {
            // Bounded tests-only artifact: only known owned synthetic golden names.
            if fs::metadata(path)?.len() > MAX_EXCHANGE_BYTES {
                return Err(invalid().into());
            }
            let exchange: Exchange = serde_json::from_str(&fs::read_to_string(path)?)?;
            if exchange.profile != PROFILE || exchange.frames.len() != golden.len() {
                return Err(invalid().into());
            }
            let mut seen = BTreeMap::new();
            for encoded in exchange.frames {
                let expected = golden.get(&encoded.name).ok_or_else(invalid)?;
                if seen.insert(encoded.name, ()).is_some() {
                    return Err(invalid().into());
                }
                let mut stream = Cursor::new(unhex(&encoded.frame_hex)?);
                let observed = read_frame(&mut stream)?.ok_or_else(invalid)?;
                if &observed != expected || read_frame(&mut stream)?.is_some() {
                    return Err(invalid().into());
                }
            }
            println!(
                "validated {} synthetic peer control encodings",
                golden.len()
            );
        }
        _ => {
            return Err(
                io::Error::other("usage: runtime_control_vectors emit | validate FILE").into(),
            );
        }
    }
    Ok(())
}
