"""Strict three-plane comparison with explicit nondeterministic value bindings.

Clock aliases retain equality, order, nullability and changed/unchanged evidence.
Only declared server fields may alias; client timestamps and durations stay exact.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from tests.parity.harness import SEED_TIME, Observation

BODY_CLOCKS = {"server_time", "last_seen_at", "claimed_at"}
DB_CLOCKS = {
    "devices": {"created_at", "updated_at", "last_seen_at"},
    "jobs": {"created_at", "updated_at", "claimed_at", "started_at", "last_agent_activity_at", "cancel_requested_at"},
    "logs": {"received_at"},
}
DB_IDENTITIES = {
    "devices": {"id", "organization_id"},
    "jobs": {"id", "organization_id", "device_id", "requested_by_user_id", "requested_by_api_key_id", "requested_by_workflow_id", "requested_by_execution_id", "agent_session_id", "claim_token"},
    "logs": {"job_id"},
}


class ComparisonProfile(Protocol):
    """An explicitly selected comparison policy; the device default stays fixed."""

    def canonicalize(self, trace: list[Observation], bindings: dict[str, str]) -> list[dict[str, Any]]:
        raise NotImplementedError


def _clock(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise AssertionError("server clock lost its timezone")
        return parsed
    except ValueError as exc:
        raise AssertionError("invalid server clock") from exc


def canonicalize(
    trace: list[Observation], bindings: dict[str, str], profile: ComparisonProfile | None = None,
) -> list[dict[str, Any]]:
    if profile is not None:
        return profile.canonicalize(trace, bindings)
    clocks: set[datetime] = set()
    for observation in trace:
        candidates = [(observation.body or {}).get(field) for field in BODY_CLOCKS] if isinstance(observation.body, dict) else []
        for table, fields in DB_CLOCKS.items():
            candidates.extend(row.get(field) for row in observation.database[table] for field in fields)
        for value in candidates:
            instant = _clock(value)
            if instant and instant != SEED_TIME:
                # A new clock must originate in THIS operation's wall-clock
                # window. Existing clocks may persist; age is not erased.
                if instant not in clocks:
                    assert observation.before <= instant <= observation.after, "server clock outside observed execution window"
                clocks.add(instant)
    clock_names = {instant: f"clock:{index}" for index, instant in enumerate(sorted(clocks))}
    identities = dict(bindings)
    tokens: dict[str, str] = {}

    def walk(value: Any, path: tuple[str, ...]) -> Any:
        if isinstance(value, dict):
            return {key: walk(item, (*path, key)) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item, path) for item in value]
        is_clock = (len(path) == 2 and path[0] == "body" and path[1] in BODY_CLOCKS) or (
            len(path) == 3 and path[0] == "database" and path[2] in DB_CLOCKS.get(path[1], set())
        )
        if is_clock and value is not None:
            instant = _clock(value)
            return "seed-time" if instant == SEED_TIME else clock_names[instant]
        if isinstance(value, str):
            is_identity = path == ("body", "job_id") or (
                len(path) == 3 and path[0] == "database" and path[2] in DB_IDENTITIES.get(path[1], set())
            ) or path in {("events", "payload", "job_id"), ("events", "payload", "device_id")}
            if is_identity and value in identities:
                return f"identity:{identities[value]}"
            if path in {("body", "claim_token"), ("database", "jobs", "claim_token")} and value not in identities:
                UUID(value)  # Fail closed on a malformed fence token.
                if value not in tokens:
                    tokens[value] = f"claim:{len(tokens)}"
                return tokens[value]
            if path == ("events", "channel"):
                for raw, name in identities.items():
                    if value == f"bifrost:device:{raw}" or value == f"bifrost:device_job:{raw}":
                        return value.replace(raw, f"identity:{name}")
        return value

    return [walk({key: value for key, value in asdict(observation).items() if key not in {"before", "after"}}, ()) for observation in trace]


def difference_path(expected: Any, actual: Any, path: str = "") -> str | None:
    """Locate a mismatch without exposing either observed value."""
    if expected == actual:
        return None
    if isinstance(expected, dict) and isinstance(actual, dict):
        if expected.keys() != actual.keys():
            return f"{path}.<keys>"
        for key in expected:
            mismatch = difference_path(expected[key], actual[key], f"{path}.{key}")
            if mismatch is not None:
                return mismatch
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{path}.<length>"
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            mismatch = difference_path(left, right, f"{path}[{index}]")
            if mismatch is not None:
                return mismatch
    return path


def assert_parity(
    reference: list[Observation], candidate: list[Observation],
    reference_bindings: dict[str, str], candidate_bindings: dict[str, str],
    profile: ComparisonProfile | None = None,
) -> None:
    expected = canonicalize(reference, reference_bindings, profile)
    actual = canonicalize(candidate, candidate_bindings, profile)
    assert len(expected) == len(actual), "observation count differs"
    for left, right in zip(expected, actual, strict=True):
        assert left["step"] == right["step"], "scenario step/order differs"
        for plane in ("status", "body", "database", "events"):
            assert left[plane] == right[plane], (
                f"{left['step']}: {plane} differs at "
                f"{difference_path(left[plane], right[plane], plane)}"
            )
