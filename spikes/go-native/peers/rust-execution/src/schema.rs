//! Private restricted vocabulary interpreter for three pinned shared schemas.
use crate::Error;
use serde_json::Value;
use std::collections::BTreeMap;

const PROFILE_ID: &str =
    "https://contracts.bifrost.invalid/runtime/v1/execution-profile/profile.schema.json";
pub(crate) struct Schemas {
    documents: BTreeMap<String, Value>,
}
impl Schemas {
    pub(crate) fn new() -> Result<Self, Error> {
        let mut documents = BTreeMap::new();
        for raw in [
            include_str!("../../../executionprofile/schemas/profile.schema.json"),
            include_str!("../../../executionprofile/schemas/binding.schema.json"),
            include_str!("../../../executionprofile/schemas/artifact.schema.json"),
        ] {
            let value: Value = serde_json::from_str(raw).map_err(|_| Error::InvalidFrame)?;
            let id = value["$id"].as_str().ok_or(Error::InvalidFrame)?.to_owned();
            if documents.insert(id, value).is_some() {
                return Err(Error::InvalidFrame);
            }
        }
        Ok(Self { documents })
    }
    pub(crate) fn message_kind(&self, kind: &str) -> bool {
        self.documents
            .get(PROFILE_ID)
            .is_some_and(|s| s["$defs"].get(kind).is_some())
    }
    pub(crate) fn valid(&self, value: &Value) -> bool {
        self.documents
            .get(PROFILE_ID)
            .is_some_and(|s| self.check(s, value, s))
    }
    fn check(&self, schema: &Value, value: &Value, root: &Value) -> bool {
        let Some(object) = schema.as_object() else {
            return false;
        };
        if object.keys().any(|k| {
            !matches!(
                k.as_str(),
                "$schema"
                    | "$id"
                    | "$defs"
                    | "$ref"
                    | "title"
                    | "description"
                    | "type"
                    | "const"
                    | "enum"
                    | "oneOf"
                    | "anyOf"
                    | "properties"
                    | "required"
                    | "additionalProperties"
                    | "items"
                    | "uniqueItems"
                    | "minItems"
                    | "maxItems"
                    | "minLength"
                    | "maxLength"
                    | "minimum"
                    | "maximum"
                    | "pattern"
                    | "format"
            )
        }) {
            return false;
        }
        if let Some(reference) = schema["$ref"].as_str() {
            let (id, pointer) = reference.split_once('#').unwrap_or((reference, ""));
            let target_root = if id.is_empty() {
                Some(root)
            } else {
                self.documents.get(id)
            };
            let Some(target_root) = target_root else {
                return false;
            };
            let target = if pointer.is_empty() {
                Some(target_root)
            } else {
                target_root.pointer(pointer)
            };
            if !target.is_some_and(|s| self.check(s, value, target_root)) {
                return false;
            }
        }
        for key in ["oneOf", "anyOf"] {
            if let Some(variants) = schema[key].as_array() {
                let count = variants
                    .iter()
                    .filter(|s| self.check(s, value, root))
                    .count();
                if count == 0 || (key == "oneOf" && count != 1) {
                    return false;
                }
            }
        }
        if schema.get("const").is_some_and(|v| v != value) {
            return false;
        }
        if schema["enum"]
            .as_array()
            .is_some_and(|v| !v.contains(value))
        {
            return false;
        }
        if let Some(kind) = schema["type"].as_str() {
            let accepted = match kind {
                "null" => value.is_null(),
                "object" => value.is_object(),
                "array" => value.is_array(),
                "string" => value.is_string(),
                "integer" => value.is_i64() || value.is_u64(),
                _ => false,
            };
            if !accepted {
                return false;
            }
        }
        if let Some(fields) = value.as_object() {
            if let Some(required) = schema["required"].as_array()
                && required
                    .iter()
                    .any(|v| !v.as_str().is_some_and(|k| fields.contains_key(k)))
            {
                return false;
            }
            for (name, child) in fields {
                if let Some(property) = schema["properties"].get(name) {
                    if !self.check(property, child, root) {
                        return false;
                    }
                } else if schema["additionalProperties"] == false {
                    return false;
                }
            }
        }
        if let Some(items) = value.as_array() {
            if !bounds(items.len() as f64, schema, "minItems", "maxItems") {
                return false;
            }
            for (index, child) in items.iter().enumerate() {
                if schema
                    .get("items")
                    .is_some_and(|s| !self.check(s, child, root))
                {
                    return false;
                }
                if schema["uniqueItems"] == true && items[..index].contains(child) {
                    return false;
                }
            }
        }
        if let Some(text) = value.as_str() {
            if !bounds(
                text.chars().count() as f64,
                schema,
                "minLength",
                "maxLength",
            ) {
                return false;
            }
            if let Some(expression) = schema["pattern"].as_str() {
                let accepted = match expression {
                    "^[0-9a-f]{64}$" => hex64(text),
                    "^sha256:[0-9a-f]{64}$" => text.strip_prefix("sha256:").is_some_and(hex64),
                    "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$" => uuid(text),
                    "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?Z$" => {
                        timestamp(text)
                    }
                    _ => false,
                };
                if !accepted {
                    return false;
                }
            }
            if let Some(format) = schema["format"].as_str()
                && (format != "date-time" || !timestamp(text))
            {
                return false;
            }
        }
        if (value.is_i64() || value.is_u64())
            && !value
                .as_f64()
                .is_some_and(|n| bounds(n, schema, "minimum", "maximum"))
        {
            return false;
        }
        true
    }
}
fn bounds(value: f64, schema: &Value, min: &str, max: &str) -> bool {
    !schema[min].as_f64().is_some_and(|n| value < n)
        && !schema[max].as_f64().is_some_and(|n| value > n)
}
fn hex_byte(byte: u8) -> bool {
    byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
}
fn hex64(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(hex_byte)
}
pub(crate) fn uuid(text: &str) -> bool {
    text.len() == 36
        && text.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                hex_byte(b)
            }
        })
}
fn timestamp(text: &str) -> bool {
    let bytes = text.as_bytes();
    if bytes.len() < 20 || bytes.len() > 27 || bytes.last() != Some(&b'Z') {
        return false;
    }
    for (i, &byte) in bytes[..19].iter().enumerate() {
        let delimiter = match i {
            4 | 7 => Some(b'-'),
            10 => Some(b'T'),
            13 | 16 => Some(b':'),
            _ => None,
        };
        if delimiter.map_or(!byte.is_ascii_digit(), |expected| byte != expected) {
            return false;
        }
    }
    if bytes.len() > 20
        && (bytes[19] != b'.'
            || bytes.len() < 22
            || !bytes[20..bytes.len() - 1].iter().all(u8::is_ascii_digit))
    {
        return false;
    }
    let number = |start, end| {
        std::str::from_utf8(&bytes[start..end])
            .ok()
            .and_then(|s| s.parse::<u32>().ok())
    };
    let (Some(year), Some(month), Some(day), Some(hour), Some(minute), Some(second)) = (
        number(0, 4),
        number(5, 7),
        number(8, 10),
        number(11, 13),
        number(14, 16),
        number(17, 19),
    ) else {
        return false;
    };
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => 0,
    };
    year > 0 && day > 0 && day <= days && hour < 24 && minute < 60 && second < 60
}
