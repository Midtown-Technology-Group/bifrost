use super::{
    Body, CONTROL_CAPABILITY, CanonicalUuid, Error, Frame, HeartbeatState, PROTOCOL, Positive,
    StopReason,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LogicalJob {
    Workflow { execution_id: CanonicalUuid },
    AgentRun { run_id: CanonicalUuid },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DomainAttempt {
    WorkflowExecutionAttempt {
        attempt_id: CanonicalUuid,
        attempt_number: Positive,
    },
    AgentExecutionAttempt {
        attempt_id: CanonicalUuid,
        attempt_number: Positive,
    },
}

/// Explicit input from the parent, not wire Prepare/Prepared or runtime readiness.
/// It describes an already accepted preparation supplied by future orchestration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedBinding {
    pub supervisor_incarnation_id: CanonicalUuid,
    pub session_id: CanonicalUuid,
    pub process_identity: String,
    pub logical_job: LogicalJob,
    pub attempt: DomainAttempt,
    pub prepare_message_id: CanonicalUuid,
    pub expected_artifact_id: String,
    pub expected_image_digest: Option<String>,
}

/// Parent-supplied assertion of a committed start, never accepted from a child.
/// This type cannot commit a database transition or verify an external commit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartAuthorization {
    pub committed_start_id: CanonicalUuid,
    pub parent_duration_seconds: Positive,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    ParentToRuntime,
    RuntimeToParent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionState {
    AwaitHello,
    Prepared,
    Executing,
    Cancelling,
    StoppedObserved,
}

/// Validates the control frontier only. It launches, imports, executes and commits nothing.
pub struct ControlSession {
    binding: PreparedBinding,
    state: SessionState,
    parent_sequence: u64,
    runtime_sequence: u64,
    authorization: Option<StartAuthorization>,
    start_message_id: Option<CanonicalUuid>,
    cancel_id: Option<CanonicalUuid>,
    child_start_observed: bool,
    child_cancel_observed: bool,
    last_elapsed_ms: u64,
}

impl ControlSession {
    pub fn new(binding: PreparedBinding) -> Result<Self, Error> {
        let kind_matches = matches!(
            (&binding.logical_job, &binding.attempt),
            (
                LogicalJob::Workflow { .. },
                DomainAttempt::WorkflowExecutionAttempt { .. }
            ) | (
                LogicalJob::AgentRun { .. },
                DomainAttempt::AgentExecutionAttempt { .. }
            )
        );
        if !kind_matches
            || binding.process_identity.is_empty()
            || binding.expected_artifact_id.is_empty()
            || binding
                .expected_image_digest
                .as_ref()
                .is_some_and(String::is_empty)
        {
            return Err(Error::InvalidBinding);
        }
        Ok(Self {
            binding,
            state: SessionState::AwaitHello,
            parent_sequence: 0,
            runtime_sequence: 0,
            authorization: None,
            start_message_id: None,
            cancel_id: None,
            child_start_observed: false,
            child_cancel_observed: false,
            last_elapsed_ms: 0,
        })
    }

    pub fn state(&self) -> SessionState {
        self.state
    }

    pub fn authorize_start(&mut self, authorization: StartAuthorization) -> Result<(), Error> {
        if self.state != SessionState::Prepared || self.authorization.is_some() {
            return Err(Error::InvalidTransition);
        }
        self.authorization = Some(authorization);
        Ok(())
    }

    pub fn accept(
        &mut self,
        direction: Direction,
        observed_process_identity: &str,
        frame: &Frame,
    ) -> Result<(), Error> {
        frame.validate()?;
        if observed_process_identity != self.binding.process_identity
            || frame.session_id != self.binding.session_id
        {
            return Err(Error::InvalidBinding);
        }
        let current_sequence = match direction {
            Direction::ParentToRuntime => self.parent_sequence,
            Direction::RuntimeToParent => self.runtime_sequence,
        };
        if frame.sequence.get() != current_sequence + 1
            || self.state == SessionState::StoppedObserved
        {
            return Err(Error::InvalidTransition);
        }
        let mut next_state = self.state;
        match (&frame.body, direction) {
            (Body::Hello(hello), Direction::RuntimeToParent)
                if self.state == SessionState::AwaitHello =>
            {
                if !hello
                    .supported_protocols
                    .iter()
                    .any(|version| version == PROTOCOL)
                {
                    return Err(Error::UnsupportedProtocol);
                }
                if !super::codec::unique_names(&hello.capabilities)
                    || hello.capabilities.as_slice() != [CONTROL_CAPABILITY]
                    || hello.artifact.artifact_id != self.binding.expected_artifact_id
                    || self
                        .binding
                        .expected_image_digest
                        .as_ref()
                        .is_some_and(|expected| {
                            hello.artifact.image_digest.as_ref() != Some(expected)
                        })
                {
                    return Err(Error::InvalidBinding);
                }
                next_state = SessionState::Prepared;
            }
            (Body::Start(start), Direction::ParentToRuntime)
                if self.state == SessionState::Prepared =>
            {
                let expected = self
                    .authorization
                    .as_ref()
                    .ok_or(Error::InvalidTransition)?;
                if start.prepare_message_id != self.binding.prepare_message_id
                    || start.committed_start_id != expected.committed_start_id
                    || start.parent_duration_seconds != expected.parent_duration_seconds
                {
                    return Err(Error::InvalidBinding);
                }
                next_state = SessionState::Executing;
            }
            (Body::Heartbeat(heartbeat), Direction::RuntimeToParent) => {
                // The two directions are independently ordered. A sent parent command
                // cannot erase queued child observations before its acknowledgment.
                let state_matches = match heartbeat.state {
                    HeartbeatState::Prepared => {
                        !self.child_start_observed
                            && !self.child_cancel_observed
                            && heartbeat.start_message_id.is_none()
                    }
                    HeartbeatState::Executing => {
                        self.start_message_id.is_some()
                            && !self.child_cancel_observed
                            && heartbeat.start_message_id == self.start_message_id
                    }
                    HeartbeatState::Cancelling => {
                        self.cancel_id.is_some()
                            && heartbeat.start_message_id == self.start_message_id
                    }
                };
                if !state_matches
                    || self.state == SessionState::AwaitHello
                    || heartbeat.monotonic_elapsed_ms.get() < self.last_elapsed_ms
                {
                    return Err(Error::InvalidTransition);
                }
            }
            (Body::Cancel(_), Direction::ParentToRuntime)
                if matches!(self.state, SessionState::Prepared | SessionState::Executing) =>
            {
                next_state = SessionState::Cancelling;
            }
            (Body::Stopped(stopped), Direction::RuntimeToParent)
                if self.state != SessionState::AwaitHello =>
            {
                let start_matches = stopped.start_message_id == self.start_message_id
                    || (!self.child_start_observed
                        && stopped.start_message_id.is_none()
                        && matches!(
                            stopped.reason,
                            StopReason::PrepareRejected | StopReason::ProtocolError
                        ));
                let cancel_matches = stopped.cancel_id == self.cancel_id
                    || (!self.child_cancel_observed && stopped.cancel_id.is_none());
                if !start_matches || !cancel_matches {
                    return Err(Error::InvalidBinding);
                }
                if (stopped.result_message_id.is_some() && stopped.start_message_id.is_none())
                    || (stopped.reason == StopReason::Completed
                        && (stopped.start_message_id.is_none()
                            || self.child_cancel_observed
                            || stopped.result_message_id.is_none()))
                    || (stopped.reason == StopReason::Cancelled
                        && (self.state != SessionState::Cancelling || stopped.cancel_id.is_none()))
                    || (stopped.reason == StopReason::PrepareRejected
                        && stopped.start_message_id.is_some())
                {
                    return Err(Error::InvalidTransition);
                }
                // A result_message_id is only an observation. Result codec/receipt is out of scope.
                next_state = SessionState::StoppedObserved;
            }
            _ => return Err(Error::InvalidTransition),
        }
        // Invalid input never consumes sequence or mutates the accepted frontier.
        match &frame.body {
            Body::Start(_) => self.start_message_id = Some(frame.message_id.clone()),
            Body::Cancel(cancel) => self.cancel_id = Some(cancel.cancel_id.clone()),
            Body::Heartbeat(heartbeat) => {
                self.last_elapsed_ms = heartbeat.monotonic_elapsed_ms.get();
                self.child_start_observed |= heartbeat.start_message_id.is_some();
                self.child_cancel_observed |= heartbeat.state == HeartbeatState::Cancelling;
            }
            Body::Hello(_) | Body::Stopped(_) => {}
        }
        match direction {
            Direction::ParentToRuntime => self.parent_sequence = frame.sequence.get(),
            Direction::RuntimeToParent => self.runtime_sequence = frame.sequence.get(),
        }
        self.state = next_state;
        Ok(())
    }
}
