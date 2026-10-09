//! Trusted synthetic exchange checker, never a coordinator or tenant host.
use bifrost_execution_wire_spike::{Codec, PROFILE};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{self, Cursor, Write},
};

fn unhex(text: &str) -> Result<Vec<u8>, ()> {
    if !text.len().is_multiple_of(2) {
        return Err(());
    }
    text.as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let s = std::str::from_utf8(pair).map_err(|_| ())?;
            u8::from_str_radix(s, 16).map_err(|_| ())
        })
        .collect()
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn bounded(path: &str) -> Result<Vec<u8>, ()> {
    if fs::metadata(path).map_err(|_| ())?.len() > 2 * 1024 * 1024 {
        return Err(());
    }
    fs::read(path).map_err(|_| ())
}
fn frame(codec: &Codec, bytes: &[u8]) -> Result<Value, ()> {
    let mut reader = Cursor::new(bytes);
    let decoded = codec.read(&mut reader).map_err(|_| ())?.ok_or(())?;
    if reader.position() as usize != bytes.len() {
        return Err(());
    }
    Ok(decoded.frame)
}
fn run() -> Result<(), ()> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        return Err(());
    }
    let codec = Codec::new().map_err(|_| ())?;
    let cases: Value = serde_json::from_slice(&bounded(&args[1])?).map_err(|_| ())?;
    let cases = cases.as_array().filter(|v| v.len() == 94).ok_or(())?;
    let mut golden = BTreeMap::new();
    for case in cases {
        if !case["error"].is_null() {
            continue;
        }
        let Some(raw) = case["hex"].as_str().filter(|s| !s.is_empty()) else {
            continue;
        };
        let name = case["name"].as_str().ok_or(())?.to_owned();
        if golden.insert(name, frame(&codec, &unhex(raw)?)?).is_some() {
            return Err(());
        }
    }
    if golden.len() != 18 {
        return Err(());
    }
    let output = match args[2].as_str() {
        "emit" if args.len() == 3 => {
            let mut frames = Vec::new();
            for (name, value) in &golden {
                let mut buffer = Vec::new();
                codec.write(&mut buffer, value).map_err(|_| ())?;
                frames.push(json!({"name": name, "frame_hex": hex(&buffer)}));
            }
            json!({"profile": PROFILE, "frames": frames})
        }
        "validate" if args.len() == 4 => {
            let exchange: Value = serde_json::from_slice(&bounded(&args[3])?).map_err(|_| ())?;
            let object = exchange.as_object().ok_or(())?;
            if object.len() != 2 || exchange["profile"] != PROFILE {
                return Err(());
            }
            let entries = exchange["frames"]
                .as_array()
                .filter(|v| v.len() == 18)
                .ok_or(())?;
            let mut seen = BTreeSet::new();
            let mut digests = BTreeMap::new();
            for entry in entries {
                if entry.as_object().ok_or(())?.len() != 2 {
                    return Err(());
                }
                let name = entry["name"].as_str().ok_or(())?;
                if !seen.insert(name) {
                    return Err(());
                }
                let raw = unhex(entry["frame_hex"].as_str().ok_or(())?)?;
                if golden.get(name) != Some(&frame(&codec, &raw)?) {
                    return Err(());
                }
                let decoded = codec
                    .read(&mut Cursor::new(&raw))
                    .map_err(|_| ())?
                    .ok_or(())?;
                if decoded.frame["type"] == "Result" {
                    digests.insert(name, decoded.payload_sha256());
                }
            }
            json!({"frames_validated": seen.len(), "result_payload_sha256": digests})
        }
        _ => return Err(()),
    };
    let bytes = serde_json::to_vec(&output).map_err(|_| ())?;
    io::stdout().write_all(&bytes).map_err(|_| ())?;
    Ok(())
}
fn main() {
    if run().is_err() {
        eprintln!("proposed Rust profile exchange failed");
        std::process::exit(1);
    }
}
