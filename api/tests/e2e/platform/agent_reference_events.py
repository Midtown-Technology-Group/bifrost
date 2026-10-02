"""Bounded passive event evidence; no Redis connection or domain acceptance."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, NoReturn, TypeGuard

from scripts.agent_reference_contract import (
    MAX_EVENT_BYTES,
    MAX_EVENT_RECEIPTS_PER_CASE,
    MAX_PREBIND_EVENTS,
)

MAX_TOTAL_EVENT_BYTES = 4194304
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 8192
PATTERN = b"bifrost:agent-run:*"
ALL_CHANNEL = b"bifrost:agent-runs:all"
DETAIL_PREFIX = b"bifrost:agent-run:"
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
FailureCode = Literal[
    "invalid_state",
    "identity_mismatch",
    "invalid_message",
    "invalid_payload",
    "capacity_exhausted",
    "acknowledgement_mismatch",
]


class EventEvidenceError(Exception):
    """Only static classifications cross the error boundary."""

    def __init__(self, code: FailureCode):
        self.code = code
        super().__init__(code)


def _uuid(value: object) -> TypeGuard[str]:
    return type(value) is str and _UUID.fullmatch(value) is not None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_payload")
        result[key] = value
    return result


def _nonfinite(_value: str) -> None:
    raise ValueError("invalid_payload")


def _decode(raw: bytes) -> dict[str, Any]:
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_nonfinite,
    )
    if type(value) is not dict:
        raise ValueError("invalid_payload")
    # Root depth is one. Nodes are JSON values/containers, not member names.
    pending = [(value, 1)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise ValueError("invalid_payload")
        if type(item) is dict:
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("invalid_payload")
    return value


@dataclass(frozen=True, slots=True)
class EventRecord:
    """No payload appears in repr; each payload read is an independent copy."""

    index: int
    channel: bytes
    pattern: bytes | None
    observed_ns: str
    _raw: bytes = field(repr=False)

    @property
    def payload(self) -> dict[str, Any]:
        return _decode(self._raw)


class BoundedEventCollector:
    """Single-owner synchronous collector for actual Redis message mappings.

    The future adapter owns I/O, the three-second deadline and producer drain.
    Local readiness/bind/close cannot prove those operations or domain lineage.
    """

    def __init__(self, agent_id: str, organization_id: str):
        if not _uuid(agent_id) or not _uuid(organization_id):
            raise EventEvidenceError("identity_mismatch") from None
        self._agent_id = agent_id
        self._organization_id = organization_id
        self._org_channel = f"bifrost:agent-runs:org:{organization_id}".encode("ascii")
        self._acks: set[tuple[str, bytes]] = set()
        self._records: list[EventRecord] = []
        self._total_bytes = 0
        self._pending_run: str | None = None
        self._run_id: str | None = None
        self._closed = False
        self._first_failure: FailureCode | None = None

    @property
    def ready(self) -> bool:
        return len(self._acks) == 3 and self._first_failure is None

    @property
    def first_failure(self) -> FailureCode | None:
        return self._first_failure

    @property
    def publication_count(self) -> int:
        return len(self._records)

    @property
    def retained_bytes(self) -> int:
        return self._total_bytes

    def _fail(self, code: FailureCode) -> NoReturn:
        if self._first_failure is None:
            self._first_failure = code
        raise EventEvidenceError(self._first_failure) from None

    def _mutable(self) -> None:
        if self._first_failure is not None:
            self._fail(self._first_failure)
        if self._closed:
            self._fail("invalid_state")

    def accept(self, message: Mapping[str, object]) -> None:
        """Admit one ACK or one selected-channel publication, without retries."""
        self._mutable()
        if not isinstance(message, Mapping) or set(message) != {
            "type",
            "pattern",
            "channel",
            "data",
        }:
            self._fail("invalid_message")
        kind = message["type"]
        if type(kind) is not str:
            self._fail("invalid_message")
        if kind in ("subscribe", "psubscribe"):
            self._acknowledge(message)
        elif kind in ("message", "pmessage"):
            self._publication(message)
        else:
            self._fail("invalid_message")

    def _acknowledge(self, message: Mapping[str, object]) -> None:
        kind, channel, count = message["type"], message["channel"], message["data"]
        if (
            type(channel) is not bytes
            or len(channel) > 128
            or message["pattern"] is not None
            or type(count) is not int
            or count != len(self._acks) + 1
            or (kind, channel)
            not in {
                ("psubscribe", PATTERN),
                ("subscribe", ALL_CHANNEL),
                ("subscribe", self._org_channel),
            }
            or (kind, channel) in self._acks
        ):
            self._fail("acknowledgement_mismatch")
        self._acks.add((str(kind), channel))

    def _publication(self, message: Mapping[str, object]) -> None:
        kind, channel, pattern, raw = (
            message["type"],
            message["channel"],
            message["pattern"],
            message["data"],
        )
        if type(channel) is not bytes:
            self._fail("invalid_message")
        detail_run = None
        if kind == "pmessage":
            if pattern != PATTERN or type(pattern) is not bytes:
                self._fail("invalid_message")
            if len(channel) != len(DETAIL_PREFIX) + 36 or not channel.startswith(
                DETAIL_PREFIX
            ):
                self._fail("invalid_message")
            try:
                detail_run = channel[len(DETAIL_PREFIX) :].decode("ascii")
            except UnicodeDecodeError:
                detail_run = None
            # Reject outside the catch: the original exception owns raw bytes.
            if not _uuid(detail_run):
                self._fail("invalid_message")
        elif pattern is not None or channel not in (ALL_CHANNEL, self._org_channel):
            self._fail("invalid_message")
        if type(raw) is not bytes:
            self._fail("invalid_payload")
        if (
            len(raw) > MAX_EVENT_BYTES
            or len(self._records) >= MAX_EVENT_RECEIPTS_PER_CASE
            or (self._run_id is None and len(self._records) >= MAX_PREBIND_EVENTS)
            or self._total_bytes + len(raw) > MAX_TOTAL_EVENT_BYTES
        ):
            self._fail("capacity_exhausted")
        try:
            payload = _decode(raw)
        except (ValueError, RecursionError, OverflowError):
            payload = None
        # Neither JSONDecodeError.doc nor UnicodeDecodeError.object may escape.
        if payload is None:
            self._fail("invalid_payload")
        run_id = payload.get("run_id")
        if not _uuid(run_id):
            self._fail("invalid_payload")
        if detail_run is not None and detail_run != run_id:
            self._fail("identity_mismatch")
        event_type = payload.get("type")
        if event_type == "agent_run_update":
            if (
                payload.get("agent_id") != self._agent_id
                or payload.get("org_id") != self._organization_id
            ):
                self._fail("identity_mismatch")
        elif event_type == "agent_run_step":
            if detail_run is None or type(payload.get("step")) is not dict:
                self._fail("invalid_payload")
        else:
            self._fail("invalid_payload")
        known_run = self._run_id or self._pending_run
        if known_run is not None and run_id != known_run:
            self._fail("identity_mismatch")
        self._pending_run = run_id
        self._records.append(
            EventRecord(
                index=len(self._records) + 1,
                channel=channel,
                pattern=pattern,
                observed_ns=str(time.monotonic_ns()),
                _raw=raw,
            )
        )
        self._total_bytes += len(raw)

    def bind(self, run_id: str) -> None:
        """Bind once to an independently obtained enqueue202 run identity."""
        self._mutable()
        if not self.ready or self._run_id is not None:
            self._fail("invalid_state")
        if not _uuid(run_id) or (
            self._pending_run is not None and self._pending_run != run_id
        ):
            self._fail("identity_mismatch")
        self._run_id = run_id

    def close(self) -> None:
        """Prevent local admission; this is not producer quiescence."""
        self._mutable()
        self._closed = True

    def snapshot(self) -> tuple[EventRecord, ...]:
        if not self.ready or self._run_id is None:
            self._fail("invalid_state")
        return tuple(self._records)
