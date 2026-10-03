//! Control codecs/session validation and nondefault mechanical agent facts.
//! No launch, execution, source/provider admission, or database authority.

mod codec;
mod control;
mod session;

#[cfg(test)]
mod tests;

pub use codec::{
    MAX_DEPTH, MAX_FRAME_BYTES, decode_json, decode_ordinary_json, encode_frame, read_frame,
    write_frame,
};
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

#[cfg(feature = "agent-prepare-codec")]
mod agent_prepare;
#[cfg(feature = "agent-prepare-codec")]
pub use agent_prepare::*;
#[cfg(all(test, feature = "agent-prepare-codec"))]
mod agent_prepare_tests;
