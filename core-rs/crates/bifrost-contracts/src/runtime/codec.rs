use std::{
    collections::BTreeSet,
    io::{Read, Write},
};

use serde::{
    Deserialize, Serialize,
    de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor},
};
use serde_json::{Map, Value};

use super::{Body, Error, Frame, PROTOCOL};

pub const MAX_FRAME_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_DEPTH: usize = 64;

struct StrictJson {
    depth: usize,
}

impl<'de> DeserializeSeed<'de> for StrictJson {
    type Value = Value;

    fn deserialize<D: serde::Deserializer<'de>>(self, deserializer: D) -> Result<Value, D::Error> {
        deserializer.deserialize_any(self)
    }
}

impl<'de> Visitor<'de> for StrictJson {
    type Value = Value;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("bounded JSON without duplicate keys")
    }

    fn visit_bool<E: de::Error>(self, value: bool) -> Result<Value, E> {
        Ok(Value::Bool(value))
    }
    fn visit_i64<E: de::Error>(self, value: i64) -> Result<Value, E> {
        Ok(value.into())
    }
    fn visit_u64<E: de::Error>(self, value: u64) -> Result<Value, E> {
        Ok(value.into())
    }
    fn visit_f64<E: de::Error>(self, value: f64) -> Result<Value, E> {
        serde_json::Number::from_f64(value)
            .map(Value::Number)
            .ok_or_else(|| E::custom("non-finite number"))
    }
    fn visit_str<E: de::Error>(self, value: &str) -> Result<Value, E> {
        Ok(value.into())
    }
    fn visit_string<E: de::Error>(self, value: String) -> Result<Value, E> {
        Ok(value.into())
    }
    fn visit_unit<E: de::Error>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut sequence: A) -> Result<Value, A::Error> {
        if self.depth >= MAX_DEPTH {
            return Err(de::Error::custom("JSON depth"));
        }
        let mut values = Vec::new();
        while let Some(value) = sequence.next_element_seed(StrictJson {
            depth: self.depth + 1,
        })? {
            values.push(value);
        }
        Ok(Value::Array(values))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        if self.depth >= MAX_DEPTH {
            return Err(de::Error::custom("JSON depth"));
        }
        let mut values = Map::new();
        while let Some(key) = map.next_key::<String>()? {
            if values.contains_key(&key) {
                return Err(de::Error::custom("duplicate JSON key"));
            }
            let value = map.next_value_seed(StrictJson {
                depth: self.depth + 1,
            })?;
            values.insert(key, value);
        }
        Ok(Value::Object(values))
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct WireFrame {
    protocol: String,
    #[serde(rename = "type")]
    frame_type: String,
    session_id: super::CanonicalUuid,
    message_id: super::CanonicalUuid,
    sequence: super::Positive,
    correlation_id: Option<super::CanonicalUuid>,
    #[serde(deserialize_with = "deserialize_ordinary_value")]
    body: Value,
}

fn deserialize_ordinary_value<'de, D>(deserializer: D) -> Result<Value, D::Error>
where
    D: serde::Deserializer<'de>,
{
    // Walk the already validated tree without Value::Deserialize's feature-
    // dependent interpretation of private-looking map keys.
    StrictJson { depth: 0 }.deserialize(deserializer)
}

fn fields(value: &Value, expected: &[&str]) -> Result<(), Error> {
    let object = value.as_object().ok_or(Error::InvalidFrame)?;
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        return Err(Error::InvalidFrame);
    }
    Ok(())
}

fn body_fields(kind: &str, value: &Value) -> Result<(), Error> {
    match kind {
        "Hello" => {
            fields(
                value,
                &[
                    "runtime_incarnation_id",
                    "supported_protocols",
                    "capabilities",
                    "artifact",
                ],
            )?;
            let artifact = &value["artifact"];
            fields(
                artifact,
                &[
                    "artifact_id",
                    "image_digest",
                    "interpreter",
                    "sdk",
                    "requirements_lock_sha256",
                    "runtime_protocol",
                ],
            )?;
            fields(&artifact["interpreter"], &["implementation", "version"])?;
            fields(&artifact["sdk"], &["distribution", "version"])?;
        }
        "Start" => fields(
            value,
            &[
                "prepare_message_id",
                "committed_start_id",
                "parent_duration_seconds",
            ],
        )?,
        "Heartbeat" => fields(
            value,
            &["start_message_id", "state", "monotonic_elapsed_ms"],
        )?,
        "Cancel" => fields(value, &["cancel_id", "reason", "grace_ms"])?,
        "Stopped" => {
            fields(
                value,
                &[
                    "start_message_id",
                    "cancel_id",
                    "reason",
                    "result_message_id",
                    "error",
                ],
            )?;
            if !value["error"].is_null() {
                fields(&value["error"], &["type", "message", "traceback"])?;
            }
        }
        _ => return Err(Error::UnsupportedFrame),
    }
    Ok(())
}

/// Parse bounded ordinary JSON without interpreting map keys as serde markers.
/// This is mechanical parsing only; it does not validate a frame or authority.
pub fn decode_ordinary_json(bytes: &[u8]) -> Result<Value, Error> {
    if bytes.is_empty() {
        return Err(Error::InvalidJson);
    }
    if bytes.len() > MAX_FRAME_BYTES {
        return Err(Error::FrameTooLarge);
    }
    std::str::from_utf8(bytes).map_err(|_| Error::InvalidJson)?;
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let value = StrictJson { depth: 0 }
        .deserialize(&mut deserializer)
        .map_err(|_| Error::InvalidJson)?;
    deserializer.end().map_err(|_| Error::InvalidJson)?;
    Ok(value)
}

pub fn decode_json(bytes: &[u8]) -> Result<Frame, Error> {
    let value = decode_ordinary_json(bytes)?;
    fields(
        &value,
        &[
            "protocol",
            "type",
            "session_id",
            "message_id",
            "sequence",
            "correlation_id",
            "body",
        ],
    )?;
    let protocol = value["protocol"].as_str().ok_or(Error::InvalidFrame)?;
    if protocol != PROTOCOL {
        return Err(Error::UnsupportedProtocol);
    }
    let wire: WireFrame = serde_json::from_value(value).map_err(|_| Error::InvalidFrame)?;
    if wire.frame_type.is_empty() {
        return Err(Error::InvalidFrame);
    }
    body_fields(&wire.frame_type, &wire.body)?;
    let body = match wire.frame_type.as_str() {
        "Hello" => Body::Hello(serde_json::from_value(wire.body).map_err(|_| Error::InvalidFrame)?),
        "Start" => Body::Start(serde_json::from_value(wire.body).map_err(|_| Error::InvalidFrame)?),
        "Heartbeat" => {
            Body::Heartbeat(serde_json::from_value(wire.body).map_err(|_| Error::InvalidFrame)?)
        }
        "Cancel" => {
            Body::Cancel(serde_json::from_value(wire.body).map_err(|_| Error::InvalidFrame)?)
        }
        "Stopped" => {
            Body::Stopped(serde_json::from_value(wire.body).map_err(|_| Error::InvalidFrame)?)
        }
        _ => return Err(Error::UnsupportedFrame),
    };
    let frame = Frame {
        session_id: wire.session_id,
        message_id: wire.message_id,
        sequence: wire.sequence,
        correlation_id: wire.correlation_id,
        body,
    };
    frame.validate()?;
    Ok(frame)
}

pub fn encode_frame(frame: &Frame) -> Result<Vec<u8>, Error> {
    frame.validate()?;
    let (kind, body) = match &frame.body {
        Body::Hello(body) => ("Hello", serde_json::to_value(body)),
        Body::Start(body) => ("Start", serde_json::to_value(body)),
        Body::Heartbeat(body) => ("Heartbeat", serde_json::to_value(body)),
        Body::Cancel(body) => ("Cancel", serde_json::to_value(body)),
        Body::Stopped(body) => ("Stopped", serde_json::to_value(body)),
    };
    let wire = WireFrame {
        protocol: PROTOCOL.to_owned(),
        frame_type: kind.to_owned(),
        session_id: frame.session_id.clone(),
        message_id: frame.message_id.clone(),
        sequence: frame.sequence,
        correlation_id: frame.correlation_id.clone(),
        body: body.map_err(|_| Error::InvalidFrame)?,
    };
    let bytes = serde_json::to_vec(&wire).map_err(|_| Error::InvalidFrame)?;
    if bytes.len() > MAX_FRAME_BYTES {
        return Err(Error::FrameTooLarge);
    }
    // Encoding must obey exactly the same validation as decoding.
    decode_json(&bytes)?;
    let length = u32::try_from(bytes.len()).map_err(|_| Error::FrameTooLarge)?;
    let mut output = Vec::with_capacity(4 + bytes.len());
    output.extend_from_slice(&length.to_be_bytes());
    output.extend_from_slice(&bytes);
    Ok(output)
}

pub fn read_frame(reader: &mut impl Read) -> Result<Option<Frame>, Error> {
    let mut prefix = [0_u8; 4];
    loop {
        match reader.read(&mut prefix[..1]) {
            Ok(0) => return Ok(None),
            Ok(_) => break,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(Error::Io),
        }
    }
    reader.read_exact(&mut prefix[1..]).map_err(read_error)?;
    let length = u32::from_be_bytes(prefix) as usize;
    if length == 0 {
        return Err(Error::InvalidFrame);
    }
    if length > MAX_FRAME_BYTES {
        return Err(Error::FrameTooLarge);
    }
    let mut bytes = vec![0; length];
    reader.read_exact(&mut bytes).map_err(read_error)?;
    decode_json(&bytes).map(Some)
}

fn read_error(error: std::io::Error) -> Error {
    if error.kind() == std::io::ErrorKind::UnexpectedEof {
        Error::TruncatedFrame
    } else {
        Error::Io
    }
}

pub fn write_frame(writer: &mut impl Write, frame: &Frame) -> Result<(), Error> {
    writer
        .write_all(&encode_frame(frame)?)
        .map_err(|_| Error::Io)
}

pub(crate) fn unique_names(names: &[String]) -> bool {
    names.iter().all(|name| !name.is_empty())
        && names.iter().collect::<BTreeSet<_>>().len() == names.len()
}
