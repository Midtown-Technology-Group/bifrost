//! Synthetic source characterization only. This module is never linked by the library.

pub mod fixtures;
mod number;

use serde::Serialize;
use serde_json::ser::{CompactFormatter, Formatter};
use std::{collections::BTreeSet, fmt, io, io::Write};

#[derive(Clone, Debug)]
pub enum ProjectedValue {
    Null,
    Bool(bool),
    I64(i64),
    U64(u64),
    F64(f64),
    String(String),
    Array(Vec<Self>),
    Object(Vec<(String, Self)>),
}

// Fixture identity compares float bits, including signed zero and NaN payloads.
impl PartialEq for ProjectedValue {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Null, Self::Null) => true,
            (Self::Bool(a), Self::Bool(b)) => a == b,
            (Self::I64(a), Self::I64(b)) => a == b,
            (Self::U64(a), Self::U64(b)) => a == b,
            (Self::F64(a), Self::F64(b)) => a.to_bits() == b.to_bits(),
            (Self::String(a), Self::String(b)) => a == b,
            (Self::Array(a), Self::Array(b)) => a == b,
            (Self::Object(a), Self::Object(b)) => a == b,
            _ => false,
        }
    }
}
impl Eq for ProjectedValue {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProjectedRoot(pub Vec<(String, ProjectedValue)>);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Limit {
    Depth,
    Nodes,
    ObjectMembers,
    InputText,
    OutputBytes,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EvidenceEncodeError {
    NonFinite,
    DuplicateKey,
    LimitExceeded(Limit),
    FormatterInvariant,
    InvalidFixture,
}

impl EvidenceEncodeError {
    pub fn category(self) -> &'static str {
        match self {
            Self::NonFinite => "NonFinite",
            Self::DuplicateKey => "DuplicateKey",
            Self::LimitExceeded(Limit::Depth) => "LimitExceeded:Depth",
            Self::LimitExceeded(Limit::Nodes) => "LimitExceeded:Nodes",
            Self::LimitExceeded(Limit::ObjectMembers) => "LimitExceeded:ObjectMembers",
            Self::LimitExceeded(Limit::InputText) => "LimitExceeded:InputText",
            Self::LimitExceeded(Limit::OutputBytes) => "LimitExceeded:OutputBytes",
            Self::FormatterInvariant => "FormatterInvariant",
            Self::InvalidFixture => "InvalidFixture",
        }
    }
}
impl fmt::Display for EvidenceEncodeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.category())
    }
}
impl std::error::Error for EvidenceEncodeError {}

#[derive(Clone, Copy, Debug)]
pub struct EncodingLimits {
    pub depth: usize,
    pub nodes: usize,
    pub object_members: usize,
    pub input_text: usize,
    pub output_bytes: usize,
}
impl Default for EncodingLimits {
    fn default() -> Self {
        Self {
            depth: 64,
            nodes: 65_536,
            object_members: 4_096,
            input_text: 1024 * 1024,
            output_bytes: 1024 * 1024,
        }
    }
}

type Result<T> = std::result::Result<T, EvidenceEncodeError>;

#[derive(Clone, Copy, Eq, PartialEq)]
enum Mode {
    FiniteUtf8,
    SortedAscii,
    DeliveryAscii,
    WorkspaceSource,
}

pub fn encode_finite_sorted_utf8(root: &ProjectedRoot, limits: EncodingLimits) -> Result<Vec<u8>> {
    encode(root, limits, Mode::FiniteUtf8)
}
pub fn encode_sorted_ascii_fixture(
    root: &ProjectedRoot,
    limits: EncodingLimits,
) -> Result<Vec<u8>> {
    encode(root, limits, Mode::SortedAscii)
}
pub fn encode_delivery_ascii_fixture(
    root: &ProjectedRoot,
    limits: EncodingLimits,
) -> Result<Vec<u8>> {
    encode(root, limits, Mode::DeliveryAscii)
}
pub fn encode_workspace_source_fixture(
    root: &ProjectedRoot,
    limits: EncodingLimits,
) -> Result<Vec<u8>> {
    encode(root, limits, Mode::WorkspaceSource)
}

struct Validation {
    limits: EncodingLimits,
    nodes: usize,
    text: usize,
    allow_nonfinite: bool,
}
impl Validation {
    fn node(&mut self) -> Result<()> {
        self.nodes = self
            .nodes
            .checked_add(1)
            .ok_or(EvidenceEncodeError::LimitExceeded(Limit::Nodes))?;
        if self.nodes > self.limits.nodes {
            return Err(EvidenceEncodeError::LimitExceeded(Limit::Nodes));
        }
        Ok(())
    }
    fn text(&mut self, bytes: usize) -> Result<()> {
        self.text = self
            .text
            .checked_add(bytes)
            .ok_or(EvidenceEncodeError::LimitExceeded(Limit::InputText))?;
        if self.text > self.limits.input_text {
            return Err(EvidenceEncodeError::LimitExceeded(Limit::InputText));
        }
        Ok(())
    }
    fn container(&self, depth: usize) -> Result<()> {
        if depth > self.limits.depth {
            return Err(EvidenceEncodeError::LimitExceeded(Limit::Depth));
        }
        Ok(())
    }
    fn object(&mut self, entries: &[(String, ProjectedValue)], depth: usize) -> Result<()> {
        self.node()?;
        self.container(depth)?;
        if entries.len() > self.limits.object_members {
            return Err(EvidenceEncodeError::LimitExceeded(Limit::ObjectMembers));
        }
        let mut seen = BTreeSet::new();
        for (key, value) in entries {
            self.text(key.len())?;
            if !seen.insert(key.as_str()) {
                return Err(EvidenceEncodeError::DuplicateKey);
            }
            self.value(value, depth + 1)?;
        }
        Ok(())
    }
    fn value(&mut self, value: &ProjectedValue, depth: usize) -> Result<()> {
        if let ProjectedValue::Object(entries) = value {
            return self.object(entries, depth);
        }
        self.node()?;
        match value {
            ProjectedValue::Array(items) => {
                self.container(depth)?;
                for item in items {
                    self.value(item, depth + 1)?;
                }
            }
            ProjectedValue::String(text) => self.text(text.len())?,
            ProjectedValue::F64(value) if !self.allow_nonfinite && !value.is_finite() => {
                return Err(EvidenceEncodeError::NonFinite);
            }
            _ => {}
        }
        Ok(())
    }
}

struct BoundedWriter {
    bytes: Vec<u8>,
    max: usize,
    exhausted: bool,
}
impl Write for BoundedWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if bytes.len() > self.max.saturating_sub(self.bytes.len()) {
            self.exhausted = true;
            return Err(io::Error::other("synthetic encoder output bound"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

struct AsciiFormatter;
impl Formatter for AsciiFormatter {
    fn write_string_fragment<W: ?Sized + Write>(
        &mut self,
        writer: &mut W,
        fragment: &str,
    ) -> io::Result<()> {
        for scalar in fragment.chars() {
            if scalar <= '\u{7e}' {
                let mut bytes = [0; 4];
                writer.write_all(scalar.encode_utf8(&mut bytes).as_bytes())?;
            } else {
                let mut units = [0; 2];
                for unit in scalar.encode_utf16(&mut units) {
                    const HEX: &[u8; 16] = b"0123456789abcdef";
                    writer.write_all(&[
                        b'\\',
                        b'u',
                        HEX[usize::from((*unit >> 12) & 15)],
                        HEX[usize::from((*unit >> 8) & 15)],
                        HEX[usize::from((*unit >> 4) & 15)],
                        HEX[usize::from(*unit & 15)],
                    ])?;
                }
            }
        }
        Ok(())
    }
}

fn string(writer: &mut BoundedWriter, value: &str, mode: Mode) -> io::Result<()> {
    if matches!(mode, Mode::SortedAscii | Mode::DeliveryAscii) {
        value
            .serialize(&mut serde_json::Serializer::with_formatter(
                writer,
                AsciiFormatter,
            ))
            .map_err(io::Error::other)
    } else {
        serde_json::to_writer(writer, value).map_err(io::Error::other)
    }
}

fn object(
    writer: &mut BoundedWriter,
    entries: &[(String, ProjectedValue)],
    mode: Mode,
) -> Result<()> {
    writer.write_all(b"{").map_err(|_| writer_error(writer))?;
    let mut ordered: Vec<_> = entries.iter().collect();
    if mode != Mode::DeliveryAscii {
        ordered.sort_unstable_by(|left, right| left.0.cmp(&right.0));
    }
    for (index, (key, value)) in ordered.into_iter().enumerate() {
        if index != 0 {
            separator(writer, mode)?;
        }
        string(writer, key, mode).map_err(|_| writer_error(writer))?;
        writer
            .write_all(if mode == Mode::DeliveryAscii {
                b": "
            } else {
                b":"
            })
            .map_err(|_| writer_error(writer))?;
        scalar_or_container(writer, value, mode)?;
    }
    writer.write_all(b"}").map_err(|_| writer_error(writer))
}
fn separator(writer: &mut BoundedWriter, mode: Mode) -> Result<()> {
    writer
        .write_all(if mode == Mode::DeliveryAscii {
            b", "
        } else {
            b","
        })
        .map_err(|_| writer_error(writer))
}
fn scalar_or_container(
    writer: &mut BoundedWriter,
    value: &ProjectedValue,
    mode: Mode,
) -> Result<()> {
    let mut formatter = CompactFormatter;
    match value {
        ProjectedValue::Object(entries) => object(writer, entries, mode)?,
        ProjectedValue::Array(items) => {
            writer.write_all(b"[").map_err(|_| writer_error(writer))?;
            for (index, item) in items.iter().enumerate() {
                if index != 0 {
                    separator(writer, mode)?;
                }
                scalar_or_container(writer, item, mode)?;
            }
            writer.write_all(b"]").map_err(|_| writer_error(writer))?;
        }
        ProjectedValue::String(value) => {
            string(writer, value, mode).map_err(|_| writer_error(writer))?
        }
        ProjectedValue::Null => formatter
            .write_null(writer)
            .map_err(|_| writer_error(writer))?,
        ProjectedValue::Bool(value) => formatter
            .write_bool(writer, *value)
            .map_err(|_| writer_error(writer))?,
        ProjectedValue::I64(value) => formatter
            .write_i64(writer, *value)
            .map_err(|_| writer_error(writer))?,
        ProjectedValue::U64(value) => formatter
            .write_u64(writer, *value)
            .map_err(|_| writer_error(writer))?,
        ProjectedValue::F64(value) => {
            let rendered = if value.is_finite() {
                number::finite(*value)?
            } else if mode == Mode::WorkspaceSource {
                if value.is_nan() {
                    "NaN".to_owned()
                } else if value.is_sign_negative() {
                    "-Infinity".to_owned()
                } else {
                    "Infinity".to_owned()
                }
            } else {
                return Err(EvidenceEncodeError::NonFinite);
            };
            writer
                .write_all(rendered.as_bytes())
                .map_err(|_| writer_error(writer))?;
        }
    }
    Ok(())
}
fn writer_error(writer: &BoundedWriter) -> EvidenceEncodeError {
    if writer.exhausted {
        EvidenceEncodeError::LimitExceeded(Limit::OutputBytes)
    } else {
        EvidenceEncodeError::FormatterInvariant
    }
}
fn encode(root: &ProjectedRoot, limits: EncodingLimits, mode: Mode) -> Result<Vec<u8>> {
    let maxima = EncodingLimits::default();
    for (chosen, maximum, category) in [
        (limits.depth, maxima.depth, Limit::Depth),
        (limits.nodes, maxima.nodes, Limit::Nodes),
        (
            limits.object_members,
            maxima.object_members,
            Limit::ObjectMembers,
        ),
        (limits.input_text, maxima.input_text, Limit::InputText),
        (limits.output_bytes, maxima.output_bytes, Limit::OutputBytes),
    ] {
        if chosen > maximum {
            return Err(EvidenceEncodeError::LimitExceeded(category));
        }
    }
    Validation {
        limits,
        nodes: 0,
        text: 0,
        allow_nonfinite: mode == Mode::WorkspaceSource,
    }
    .object(&root.0, 1)?;
    let mut writer = BoundedWriter {
        bytes: Vec::with_capacity(limits.output_bytes.min(4096)),
        max: limits.output_bytes,
        exhausted: false,
    };
    object(&mut writer, &root.0, mode)?;
    Ok(writer.bytes)
}
