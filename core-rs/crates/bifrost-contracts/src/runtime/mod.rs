//! Partial v1 control profile: codecs and parent-supplied session validation only.
//! No work preparation, execution, database authority, or credential transport.

mod codec;
mod control;
mod session;

#[cfg(test)]
mod tests;

pub use codec::{MAX_DEPTH, MAX_FRAME_BYTES, decode_json, encode_frame, read_frame, write_frame};
pub use control::*;
pub use session::*;

use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    InvalidJson,
    InvalidFrame,
    UnsupportedProtocol,
    UnsupportedFrame,
    FrameTooLarge,
    TruncatedFrame,
    Io,
    InvalidBinding,
    InvalidTransition,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "runtime control protocol: {self:?}")
    }
}

impl std::error::Error for Error {}
