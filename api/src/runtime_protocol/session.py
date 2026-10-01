"""Parent-input control frontier. No process launch, execution or database writes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .control import (
    CONTROL_CAPABILITY,
    PROTOCOL,
    Cancel,
    ErrorCode,
    Frame,
    Heartbeat,
    Hello,
    ProtocolError,
    Start,
    Stopped,
    canonical_uuid,
    encode_frame,
    integer,
)


@dataclass(frozen=True)
class WorkflowJob:
    execution_id: str


@dataclass(frozen=True)
class AgentRunJob:
    run_id: str


@dataclass(frozen=True)
class WorkflowExecutionAttempt:
    attempt_id: str
    attempt_number: int


@dataclass(frozen=True)
class AgentExecutionAttempt:
    attempt_id: str
    attempt_number: int


@dataclass(frozen=True)
class PreparedBinding:
    """Architect-provided accepted preparation, not fake Prepare/Prepared frames.

    process_identity is an opaque parent observation; the caller must obtain it
    from its supervised child handle. A PID string reported by a child is not proof.
    """

    supervisor_incarnation_id: str
    session_id: str
    process_identity: str
    logical_job: WorkflowJob | AgentRunJob
    attempt: WorkflowExecutionAttempt | AgentExecutionAttempt
    prepare_message_id: str
    expected_artifact_id: str
    expected_image_digest: str | None


@dataclass(frozen=True)
class StartAuthorization:
    """Parent assertion of a durable commit; this object does not perform/prove SQL."""

    committed_start_id: str
    parent_duration_seconds: int


class Direction(str, Enum):
    PARENT_TO_RUNTIME = "ParentToRuntime"
    RUNTIME_TO_PARENT = "RuntimeToParent"


class SessionState(str, Enum):
    AWAIT_HELLO = "AwaitHello"
    PREPARED = "Prepared"
    EXECUTING = "Executing"
    CANCELLING = "Cancelling"
    STOPPED_OBSERVED = "StoppedObserved"


class ControlSession:
    def __init__(self, binding: PreparedBinding):
        matches = (
            isinstance(binding.logical_job, WorkflowJob)
            and isinstance(binding.attempt, WorkflowExecutionAttempt)
        ) or (
            isinstance(binding.logical_job, AgentRunJob)
            and isinstance(binding.attempt, AgentExecutionAttempt)
        )
        if (
            not matches
            or not binding.process_identity
            or not binding.expected_artifact_id
            or binding.expected_image_digest == ""
        ):
            raise ProtocolError(ErrorCode.INVALID_BINDING)
        try:
            for value in (
                binding.supervisor_incarnation_id,
                binding.session_id,
                binding.prepare_message_id,
                binding.attempt.attempt_id,
            ):
                canonical_uuid(value)
            if isinstance(binding.logical_job, WorkflowJob):
                canonical_uuid(binding.logical_job.execution_id)
            else:
                canonical_uuid(binding.logical_job.run_id)
            integer(binding.attempt.attempt_number, positive=True)
        except ProtocolError:
            raise ProtocolError(ErrorCode.INVALID_BINDING) from None
        self.binding = binding
        self.state = SessionState.AWAIT_HELLO
        self._parent_sequence = 0
        self._runtime_sequence = 0
        self._authorization: StartAuthorization | None = None
        self._start_message_id: str | None = None
        self._cancel_id: str | None = None
        self._child_start_observed = False
        self._child_cancel_observed = False
        self._last_elapsed_ms = 0

    def authorize_start(self, authorization: StartAuthorization) -> None:
        if self.state != SessionState.PREPARED or self._authorization is not None:
            raise ProtocolError(ErrorCode.INVALID_TRANSITION)
        canonical_uuid(authorization.committed_start_id)
        integer(authorization.parent_duration_seconds, positive=True)
        self._authorization = authorization

    def accept(
        self, direction: Direction, observed_process_identity: str, frame: Frame
    ) -> None:
        # Also validate programmatically constructed dataclass instances.
        encode_frame(frame)
        if (
            observed_process_identity != self.binding.process_identity
            or frame.session_id != self.binding.session_id
        ):
            raise ProtocolError(ErrorCode.INVALID_BINDING)
        current = (
            self._parent_sequence
            if direction == Direction.PARENT_TO_RUNTIME
            else self._runtime_sequence
        )
        if (
            not isinstance(direction, Direction)
            or frame.sequence != current + 1
            or self.state == SessionState.STOPPED_OBSERVED
        ):
            raise ProtocolError(ErrorCode.INVALID_TRANSITION)
        body = frame.body
        next_state = self.state
        if (
            isinstance(body, Hello)
            and direction == Direction.RUNTIME_TO_PARENT
            and self.state == SessionState.AWAIT_HELLO
        ):
            if PROTOCOL not in body.supported_protocols:
                raise ProtocolError(ErrorCode.UNSUPPORTED_PROTOCOL)
            if (
                body.capabilities != (CONTROL_CAPABILITY,)
                or body.artifact.artifact_id != self.binding.expected_artifact_id
                or (
                    self.binding.expected_image_digest is not None
                    and body.artifact.image_digest != self.binding.expected_image_digest
                )
            ):
                raise ProtocolError(ErrorCode.INVALID_BINDING)
            next_state = SessionState.PREPARED
        elif (
            isinstance(body, Start)
            and direction == Direction.PARENT_TO_RUNTIME
            and self.state == SessionState.PREPARED
        ):
            if self._authorization is None:
                raise ProtocolError(ErrorCode.INVALID_TRANSITION)
            if (
                body.prepare_message_id != self.binding.prepare_message_id
                or body.committed_start_id != self._authorization.committed_start_id
                or body.parent_duration_seconds
                != self._authorization.parent_duration_seconds
            ):
                raise ProtocolError(ErrorCode.INVALID_BINDING)
            next_state = SessionState.EXECUTING
        elif isinstance(body, Heartbeat) and direction == Direction.RUNTIME_TO_PARENT:
            # Commands sent by the parent do not order queued child observations.
            matches = (
                (
                    body.state == "prepared"
                    and not self._child_start_observed
                    and not self._child_cancel_observed
                    and body.start_message_id is None
                )
                or (
                    body.state == "executing"
                    and self._start_message_id is not None
                    and not self._child_cancel_observed
                    and body.start_message_id == self._start_message_id
                )
                or (
                    body.state == "cancelling"
                    and self._cancel_id is not None
                    and body.start_message_id == self._start_message_id
                )
            )
            if (
                not matches
                or self.state == SessionState.AWAIT_HELLO
                or body.monotonic_elapsed_ms < self._last_elapsed_ms
            ):
                raise ProtocolError(ErrorCode.INVALID_TRANSITION)
        elif (
            isinstance(body, Cancel)
            and direction == Direction.PARENT_TO_RUNTIME
            and self.state in (SessionState.PREPARED, SessionState.EXECUTING)
        ):
            next_state = SessionState.CANCELLING
        elif (
            isinstance(body, Stopped)
            and direction == Direction.RUNTIME_TO_PARENT
            and self.state != SessionState.AWAIT_HELLO
        ):
            start_matches = body.start_message_id == self._start_message_id or (
                not self._child_start_observed
                and body.start_message_id is None
                and body.reason in ("prepare_rejected", "protocol_error")
            )
            cancel_matches = body.cancel_id == self._cancel_id or (
                not self._child_cancel_observed and body.cancel_id is None
            )
            if not start_matches or not cancel_matches:
                raise ProtocolError(ErrorCode.INVALID_BINDING)
            if (
                (body.result_message_id is not None and body.start_message_id is None)
                or (
                    body.reason == "completed"
                    and (
                        body.start_message_id is None
                        or self._child_cancel_observed
                        or body.result_message_id is None
                    )
                )
                or (
                    body.reason == "cancelled"
                    and (
                        self.state != SessionState.CANCELLING or body.cancel_id is None
                    )
                )
                or (
                    body.reason == "prepare_rejected"
                    and body.start_message_id is not None
                )
            ):
                raise ProtocolError(ErrorCode.INVALID_TRANSITION)
            next_state = SessionState.STOPPED_OBSERVED
        else:
            raise ProtocolError(ErrorCode.INVALID_TRANSITION)
        if isinstance(body, Start):
            self._start_message_id = frame.message_id
        elif isinstance(body, Cancel):
            self._cancel_id = body.cancel_id
        elif isinstance(body, Heartbeat):
            self._last_elapsed_ms = body.monotonic_elapsed_ms
            self._child_start_observed |= body.start_message_id is not None
            self._child_cancel_observed |= body.state == "cancelling"
        if direction == Direction.PARENT_TO_RUNTIME:
            self._parent_sequence = frame.sequence
        else:
            self._runtime_sequence = frame.sequence
        self.state = next_state
