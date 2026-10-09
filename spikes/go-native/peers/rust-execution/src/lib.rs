//! Candidate shared wire codec. No supervisor, sessions, credentials or lifecycle.
mod json;
mod schema;

use serde::de::DeserializeSeed;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::{self, Read, Write};

pub const PROTOCOL: &str = "bifrost.runtime/v1";
pub const PROFILE: &str = "bifrost.runtime/v1/execution_profile/v1";
pub const MAX_FRAME: usize = 16 * 1024 * 1024;
pub const MAX_DEPTH: usize = 64;
const MAX_SAFE: u64 = 9_007_199_254_740_991;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    InvalidJson,
    InvalidFrame,
    UnsupportedProtocol,
    UnsupportedFrame,
    FrameTooLarge,
    TruncatedFrame,
    Io,
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "proposed runtime codec: {self:?}")
    }
}
impl std::error::Error for Error {}

pub struct Decoded {
    pub frame: Value,
    payload: Vec<u8>,
}
impl Decoded {
    pub fn payload_sha256(&self) -> String {
        format!("{:x}", Sha256::digest(&self.payload))
    }
}

pub struct Codec {
    schemas: schema::Schemas,
}
impl Codec {
    pub fn new() -> Result<Self, Error> {
        Ok(Self {
            schemas: schema::Schemas::new()?,
        })
    }

    pub fn matches_schema(&self, document: &Value) -> bool {
        self.schemas.valid(document)
    }

    pub fn decode(&self, payload: &[u8]) -> Result<Decoded, Error> {
        if payload.len() > MAX_FRAME {
            return Err(Error::FrameTooLarge);
        }
        std::str::from_utf8(payload).map_err(|_| Error::InvalidJson)?;
        let mut parser = serde_json::Deserializer::from_slice(payload);
        let frame = json::StrictJson { depth: 0 }
            .deserialize(&mut parser)
            .map_err(|_| Error::InvalidJson)?;
        parser.end().map_err(|_| Error::InvalidJson)?;
        let object = frame.as_object().ok_or(Error::InvalidFrame)?;
        const KEYS: [&str; 7] = [
            "protocol",
            "type",
            "session_id",
            "message_id",
            "sequence",
            "correlation_id",
            "body",
        ];
        if object.len() != KEYS.len() || KEYS.iter().any(|key| !object.contains_key(*key)) {
            return Err(Error::InvalidFrame);
        }
        let protocol = frame["protocol"].as_str().ok_or(Error::InvalidFrame)?;
        if protocol != PROTOCOL {
            return Err(Error::UnsupportedProtocol);
        }
        let kind = frame["type"]
            .as_str()
            .filter(|s| !s.is_empty())
            .ok_or(Error::InvalidFrame)?;
        for key in ["session_id", "message_id", "correlation_id"] {
            if key == "correlation_id" && frame[key].is_null() {
                continue;
            }
            if !frame[key].as_str().is_some_and(schema::uuid) {
                return Err(Error::InvalidFrame);
            }
        }
        if !frame["sequence"]
            .as_u64()
            .is_some_and(|n| n > 0 && n <= MAX_SAFE)
        {
            return Err(Error::InvalidFrame);
        }
        if !self.schemas.message_kind(kind) {
            return Err(Error::UnsupportedFrame);
        }
        if !self.schemas.valid(&frame) {
            return Err(Error::InvalidFrame);
        }
        let correlation_key = match kind {
            "Prepared" | "Start" | "Provision" => Some("prepare_message_id"),
            "LogBatch" | "Usage" | "Result" => Some("start_message_id"),
            "ResultReceipt" => Some("result_message_id"),
            _ => None,
        };
        if correlation_key.is_some_and(|key| frame["correlation_id"] != frame["body"][key]) {
            return Err(Error::InvalidFrame);
        }
        if matches!(kind, "Offer" | "Heartbeat" | "Cancel" | "Stopped")
            && !frame["correlation_id"].is_null()
        {
            return Err(Error::InvalidFrame);
        }
        if matches!(kind, "Select" | "Prepare") && frame["correlation_id"].is_null() {
            return Err(Error::InvalidFrame);
        }
        Ok(Decoded {
            frame,
            payload: payload.to_vec(),
        })
    }

    pub fn read(&self, reader: &mut impl Read) -> Result<Option<Decoded>, Error> {
        let mut prefix = [0; 4];
        if !read_exact(reader, &mut prefix, true)? {
            return Ok(None);
        }
        let size = u32::from_be_bytes(prefix) as usize;
        if size == 0 {
            return Err(Error::InvalidFrame);
        }
        if size > MAX_FRAME {
            return Err(Error::FrameTooLarge);
        }
        let mut payload = vec![0; size];
        read_exact(reader, &mut payload, false)?;
        self.decode(&payload).map(Some)
    }

    pub fn write(&self, writer: &mut impl Write, frame: &Value) -> Result<(), Error> {
        let payload = serde_json::to_vec(frame).map_err(|_| Error::InvalidFrame)?;
        self.decode(&payload)?;
        let mut bytes = Vec::with_capacity(payload.len() + 4);
        bytes.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        bytes.extend_from_slice(&payload);
        let mut pending = bytes.as_slice();
        while !pending.is_empty() {
            match writer.write(pending) {
                Ok(n) if n > 0 && n <= pending.len() => pending = &pending[n..],
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                _ => return Err(Error::Io),
            }
        }
        Ok(())
    }
}

fn read_exact(reader: &mut impl Read, buffer: &mut [u8], allow_eof: bool) -> Result<bool, Error> {
    let mut offset = 0;
    while offset < buffer.len() {
        match reader.read(&mut buffer[offset..]) {
            Ok(0) if offset == 0 && allow_eof => return Ok(false),
            Ok(0) => return Err(Error::TruncatedFrame),
            Ok(n) if n <= buffer.len() - offset => offset += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            _ => return Err(Error::Io),
        }
    }
    Ok(true)
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod session_tests;
