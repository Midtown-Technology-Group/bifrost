use serde::{Deserialize, Deserializer, Serialize, de};

use super::Error;

pub const PROTOCOL: &str = "bifrost.runtime/v1";
pub const CONTROL_CAPABILITY: &str = "control_profile/v1";
pub const MAX_SAFE_INTEGER: u64 = 9_007_199_254_740_991;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct CanonicalUuid(String);

impl CanonicalUuid {
    pub fn new(value: impl Into<String>) -> Result<Self, Error> {
        let value = value.into();
        if value.len() != 36
            || !value.bytes().enumerate().all(|(index, byte)| {
                if [8, 13, 18, 23].contains(&index) {
                    byte == b'-'
                } else {
                    byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
                }
            })
        {
            return Err(Error::InvalidFrame);
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for CanonicalUuid {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Self::new(String::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct UInt(u64);

impl UInt {
    pub fn new(value: u64) -> Result<Self, Error> {
        if value > MAX_SAFE_INTEGER {
            return Err(Error::InvalidFrame);
        }
        Ok(Self(value))
    }

    pub fn get(self) -> u64 {
        self.0
    }
}

impl<'de> Deserialize<'de> for UInt {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Self::new(u64::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct Positive(UInt);

impl Positive {
    pub fn new(value: u64) -> Result<Self, Error> {
        if value == 0 {
            return Err(Error::InvalidFrame);
        }
        Ok(Self(UInt::new(value)?))
    }

    pub fn get(self) -> u64 {
        self.0.get()
    }
}

impl<'de> Deserialize<'de> for Positive {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Self::new(u64::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Interpreter {
    pub implementation: String,
    pub version: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Sdk {
    pub distribution: Option<String>,
    pub version: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeArtifact {
    pub artifact_id: String,
    pub image_digest: Option<String>,
    pub interpreter: Interpreter,
    pub sdk: Sdk,
    pub requirements_lock_sha256: Option<String>,
    pub runtime_protocol: String,
}

impl RuntimeArtifact {
    pub fn validate(&self) -> Result<(), Error> {
        if self.artifact_id.is_empty()
            || self.interpreter.implementation.is_empty()
            || self.interpreter.version.is_empty()
            || self.runtime_protocol != PROTOCOL
            || self.image_digest.as_ref().is_some_and(String::is_empty)
            || self.sdk.distribution.as_ref().is_some_and(String::is_empty)
            || self.sdk.version.as_ref().is_some_and(String::is_empty)
            || self.requirements_lock_sha256.as_ref().is_some_and(|hash| {
                hash.len() != 64
                    || !hash
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            })
        {
            return Err(Error::InvalidFrame);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeError {
    #[serde(rename = "type")]
    pub error_type: String,
    pub message: String,
    pub traceback: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Hello {
    pub runtime_incarnation_id: CanonicalUuid,
    pub supported_protocols: Vec<String>,
    pub capabilities: Vec<String>,
    pub artifact: RuntimeArtifact,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Start {
    pub prepare_message_id: CanonicalUuid,
    pub committed_start_id: CanonicalUuid,
    pub parent_duration_seconds: Positive,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HeartbeatState {
    Prepared,
    Executing,
    Cancelling,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Heartbeat {
    pub start_message_id: Option<CanonicalUuid>,
    pub state: HeartbeatState,
    pub monotonic_elapsed_ms: UInt,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CancelReason {
    Requested,
    Deadline,
    AuthorityRevoked,
    SupervisorShutdown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cancel {
    pub cancel_id: CanonicalUuid,
    pub reason: CancelReason,
    pub grace_ms: UInt,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StopReason {
    Completed,
    Cancelled,
    PrepareRejected,
    ProtocolError,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Stopped {
    pub start_message_id: Option<CanonicalUuid>,
    pub cancel_id: Option<CanonicalUuid>,
    pub reason: StopReason,
    pub result_message_id: Option<CanonicalUuid>,
    pub error: Option<RuntimeError>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Body {
    Hello(Hello),
    Start(Start),
    Heartbeat(Heartbeat),
    Cancel(Cancel),
    Stopped(Stopped),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    pub session_id: CanonicalUuid,
    pub message_id: CanonicalUuid,
    pub sequence: Positive,
    pub correlation_id: Option<CanonicalUuid>,
    pub body: Body,
}

impl Frame {
    pub fn validate(&self) -> Result<(), Error> {
        match &self.body {
            Body::Hello(hello) => {
                hello.artifact.validate()?;
                for names in [&hello.supported_protocols, &hello.capabilities] {
                    if !super::codec::unique_names(names) {
                        return Err(Error::InvalidFrame);
                    }
                }
            }
            Body::Start(start) => {
                if self.correlation_id.as_ref() != Some(&start.prepare_message_id) {
                    return Err(Error::InvalidFrame);
                }
            }
            Body::Stopped(stopped) => {
                if stopped
                    .error
                    .as_ref()
                    .is_some_and(|error| error.error_type.is_empty())
                {
                    return Err(Error::InvalidFrame);
                }
            }
            Body::Heartbeat(_) | Body::Cancel(_) => {}
        }
        if !matches!(self.body, Body::Start(_)) && self.correlation_id.is_some() {
            return Err(Error::InvalidFrame);
        }
        Ok(())
    }
}
