"""Explicit A-only comparison policy. Raw observations are never rewritten."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime
from typing import Any

from tests.parity.compare import assert_parity
from tests.parity.core.capture import CapturedStep
from tests.parity.harness import SEED_TIME, Observation

CLOCKS = {
    "organizations": {"created_at", "updated_at"},
    "users": {"created_at", "updated_at", "last_login", "mfa_enforced_at"},
    "roles": {"created_at", "updated_at"},
    "user_roles": {"assigned_at"},
    "workflow_roles": {"assigned_at"},
    "executions": {"created_at", "started_at", "completed_at", "scheduled_at"},
    "workflow_execution_attempts": {
        "created_at",
        "published_at",
        "claimed_at",
        "started_at",
        "heartbeat_at",
        "completed_at",
    },
    "execution_logs": {"timestamp"},
    "execution_attempts": {"created_at", "updated_at", "started_at", "completed_at"},
    "execution_lifecycle_events": {"occurred_at"},
    "work_deliveries": {
        "created_at",
        "available_at",
        "started_at",
        "settled_at",
        "lease_expires_at",
    },
    "audit_logs": {"created_at"},
}
IDENTITIES = {
    "organizations": {"id"},
    "users": {"id", "email", "organization_id"},
    "roles": {"id"},
    "user_roles": {"user_id", "role_id", "assigned_by"},
    "workflow_roles": {"workflow_id", "role_id", "assigned_by"},
    "executions": {
        "id",
        "executed_by",
        "organization_id",
        "workflow_id",
        "form_id",
        "api_key_id",
        "session_id",
        "solution_deployment_id",
    },
    "workflow_execution_attempts": {
        "id",
        "execution_id",
        "claim_token",
        "worker_id",
        "worker_incarnation_id",
        "process_id",
    },
    "execution_logs": {"id", "execution_id"},
    "execution_attempts": {
        "id",
        "logical_job_id",
        "organization_id",
        "message_id",
        "worker_id",
        "process_id",
        "lease_token",
    },
    "execution_lifecycle_events": {
        "id",
        "attempt_id",
        "logical_job_id",
        "organization_id",
        "worker_id",
    },
    "work_deliveries": {"id", "message_id", "lease_owner", "lease_token"},
    "audit_logs": {"id", "user_id", "organization_id", "resource_id"},
}
GENERATED_IDENTITIES = {
    "workflow_execution_attempts": {
        "id",
        "claim_token",
        "worker_id",
        "worker_incarnation_id",
        "process_id",
    },
    "execution_logs": {"id"},
    "execution_attempts": {"id", "worker_id", "process_id", "lease_token"},
    "execution_lifecycle_events": {"id", "worker_id"},
    "work_deliveries": {"id", "lease_owner", "lease_token"},
    "audit_logs": {"id"},
}
CONTEXT_IDENTITIES = {
    ("user_id",),
    ("email",),
    ("scope",),
    ("execution_id",),
    ("organization", "id"),
    ("parameters", "integration_name"),
    ("parameters", "organization_id"),
    ("workspace_generation",),
}
DISPATCH_IDENTITIES = {
    ("execution_id",),
    ("workflow_id",),
    ("org_id",),
    ("user_id",),
    ("user_email",),
    ("file_path",),
    ("parameters", "integration_name"),
    ("parameters", "organization_id"),
    ("dispatch_metadata", "workflow_id"),
    ("dispatch_metadata", "organization_id"),
    ("dispatch_metadata", "path"),
    ("dispatch_metadata", "file_path"),
}
LOCAL_IDENTITIES = {
    ("name",),
    ("scope",),
    ("integration", "integration_id"),
    # get_mapping is consumed as bool, not persisted as a mapping local.
}
ELAPSED = {"duration_ms"}
RESOURCES = {
    "peak_memory_bytes",
    "process_rss_bytes",
    "cpu_user_seconds",
    "cpu_system_seconds",
    "cpu_total_seconds",
}


def digest(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
    )


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, "Core server clock lost timezone"
    return parsed


def validate_measurements(observation: Observation) -> None:
    """Check source units, measured elapsed bounds and same-run projections."""
    window_ms = (observation.after - observation.before).total_seconds() * 1000
    executions = observation.database["executions"]
    for row in executions:
        for field in RESOURCES:
            value = row[field]
            if value is None:
                continue
            if field.endswith("_bytes"):
                assert type(value) is int and 0 <= value < 2**63, (
                    f"{field}: integer bytes outside SQL range"
                )
            else:
                assert type(value) is float and math.isfinite(value) and value >= 0, (
                    f"{field}: invalid CPU seconds"
                )
        cpu = [
            row[field]
            for field in ("cpu_user_seconds", "cpu_system_seconds", "cpu_total_seconds")
        ]
        if all(value is not None for value in cpu):
            assert abs(cpu[0] + cpu[1] - cpu[2]) <= 0.0002, (
                "CPU total projection differs"
            )
        duration = row["duration_ms"]
        if duration is not None:
            assert type(duration) is int and 0 <= duration <= window_ms, (
                "duration_ms outside measured operation"
            )
        for attempt in observation.database["workflow_execution_attempts"]:
            if (
                attempt["execution_id"] == row["id"]
                and attempt["status"] == "succeeded"
            ):
                for field in ("duration_ms", "peak_memory_bytes", "cpu_total_seconds"):
                    assert attempt[field] == row[field], (
                        f"attempt {field} measurement projection differs"
                    )
            if attempt["execution_id"] == row["id"]:
                assert (
                    attempt["dispatch_evidence_hash"] == row["dispatch_evidence_hash"]
                ), "attempt dispatch hash projection differs"
                assert (
                    attempt["runtime_evidence_hash"] == row["runtime_evidence_hash"]
                ), "attempt runtime hash projection differs"
        if (
            isinstance(observation.body, dict)
            and observation.body.get("execution_id") == row["id"]
            and observation.body.get("duration_ms") is not None
        ):
            assert observation.body["duration_ms"] == duration, (
                "HTTP duration projection differs"
            )
        dispatch = row["dispatch_evidence"]
        if row["runtime_evidence"] is not None:
            assert row["runtime_evidence_hash"] == digest(row["runtime_evidence"]), (
                "runtime evidence hash differs"
            )
        if dispatch is not None:
            assert dispatch["request_hash"] == digest(dispatch["request"]), (
                "dispatch request hash differs"
            )
            assert dispatch["publish_hash"] == digest(dispatch["publish"]), (
                "dispatch publish hash differs"
            )
            assert row["dispatch_evidence_hash"] == digest(dispatch), (
                "dispatch evidence hash differs"
            )
            assert dispatch["publish"]["execution_id"] == row["id"]
            assert dispatch["publish"]["parameters"] == row["parameters"]
            assert dispatch["publish"]["org_id"] == row["organization_id"]
            assert dispatch["publish"]["user_id"] == row["executed_by"]
        for event in observation.events:
            payload = event["payload"]
            if (
                isinstance(payload, dict)
                and payload.get("executionId", payload.get("execution_id")) == row["id"]
                and payload.get("duration_ms") is not None
            ):
                assert payload["duration_ms"] == duration, (
                    "event duration projection differs"
                )


class CoreReferenceProfile:
    """Map only enumerated protocol/schema/source-local identity paths."""

    def canonicalize(
        self, trace: list[Observation], bindings: dict[str, str]
    ) -> list[dict[str, Any]]:
        identities = dict(bindings)
        generated_counts: dict[str, int] = {}
        clocks: set[datetime] = set()
        for observation in trace:
            validate_measurements(observation)
            for table, fields in GENERATED_IDENTITIES.items():
                for index, row in enumerate(observation.database[table]):
                    for field in sorted(fields):
                        value = row.get(field)
                        if value is not None and str(value) not in identities:
                            role = f"{table}:{index}:{field}"
                            count = generated_counts.get(role, 0)
                            generated_counts[role] = count + 1
                            identities[str(value)] = f"{role}:{count}"
            values = []
            if isinstance(observation.body, dict):
                values.extend(
                    observation.body.get(field)
                    for field in ("started_at", "completed_at", "scheduled_at")
                )
            for table, fields in CLOCKS.items():
                values.extend(
                    row.get(field)
                    for row in observation.database[table]
                    for field in fields
                )
            values.extend(
                event["payload"].get(field)
                for event in observation.events
                if isinstance(event["payload"], dict)
                for field in ("timestamp", "started_at", "completed_at")
            )
            values.extend(
                row["envelope"]["headers"].get("x-enqueued-at")
                for row in observation.database["work_deliveries"]
            )
            for value in values:
                if value is None:
                    continue
                clock = instant(value)
                if clock != SEED_TIME and clock not in clocks:
                    assert observation.before <= clock <= observation.after, (
                        "Core clock outside measured operation"
                    )
                clocks.add(clock)
        clock_names = {
            clock: f"clock:{index}" for index, clock in enumerate(sorted(clocks))
        }

        def walk(value: Any, path: tuple[str, ...]) -> Any:
            leaf = next(reversed(path), None)
            if isinstance(value, dict):
                return {key: walk(item, (*path, key)) for key, item in value.items()}
            if isinstance(value, list):
                return [walk(item, path) for item in value]
            table = path[1] if len(path) >= 3 and path[0] == "database" else None
            clock = (
                table is not None
                and len(path) == 3
                and path[2] in CLOCKS.get(table, set())
            ) or path in {
                ("body", "started_at"),
                ("body", "completed_at"),
                ("body", "scheduled_at"),
                ("events", "payload", "timestamp"),
                ("events", "payload", "started_at"),
                ("events", "payload", "completed_at"),
                ("database", "work_deliveries", "envelope", "headers", "x-enqueued-at"),
            }
            if clock and value is not None:
                parsed = instant(value)
                return "seed-time" if parsed == SEED_TIME else clock_names[parsed]
            measurement = (
                table in {"executions", "workflow_execution_attempts"}
                and len(path) == 3
                and path[2] in RESOURCES | ELAPSED
            ) or path in {
                ("body", "duration_ms"),
                ("events", "payload", "duration_ms"),
            }
            if measurement and value is not None:
                assert leaf is not None
                return {
                    "measured_unit": "milliseconds"
                    if leaf == "duration_ms"
                    else "bytes"
                    if leaf.endswith("_bytes")
                    else "seconds",
                    "type": type(value).__name__,
                }
            identity = (
                table is not None
                and len(path) == 3
                and path[2] in IDENTITIES.get(table, set())
            ) or path in {
                ("body", "execution_id"),
                ("body", "workflow_id"),
                ("body", "result", "integration_name"),
                ("events", "payload", "executionId"),
                ("events", "payload", "execution_id"),
                ("events", "payload", "org_id"),
                ("events", "payload", "executed_by"),
                ("database", "executions", "parameters", "integration_name"),
                ("database", "executions", "parameters", "organization_id"),
                ("database", "executions", "result", "integration_name"),
            }
            if path[:3] == ("database", "executions", "execution_context"):
                identity |= path[3:] in CONTEXT_IDENTITIES
            if path[:3] == ("database", "executions", "variables"):
                identity |= path[3:] in LOCAL_IDENTITIES
            if (
                path[:3] == ("database", "executions", "dispatch_evidence")
                and len(path) >= 5
            ):
                identity |= (
                    path[3] in {"request", "publish"}
                    and path[4:] in DISPATCH_IDENTITIES
                )
            if path[:4] == ("database", "work_deliveries", "envelope", "body"):
                identity |= path[4:] in DISPATCH_IDENTITIES
            if path[:4] == ("database", "work_deliveries", "envelope", "headers"):
                identity |= leaf in {"x-idempotency-key", "x-original-message-id"}
            if identity and value is not None and str(value) in identities:
                return {
                    "identity": identities[str(value)],
                    "wire_type": type(value).__name__,
                }
            if path == ("events", "channel") and isinstance(value, str):
                for raw, role in identities.items():
                    if value in {
                        f"bifrost:execution:{raw}",
                        f"bifrost:history:user:{raw}",
                    }:
                        return value.replace(raw, f"identity:{role}")
            if (
                path
                in {
                    ("database", "executions", "dispatch_evidence_hash"),
                    ("database", "executions", "dispatch_evidence", "request_hash"),
                    ("database", "executions", "dispatch_evidence", "publish_hash"),
                    (
                        "database",
                        "workflow_execution_attempts",
                        "dispatch_evidence_hash",
                    ),
                }
                and value is not None
            ):
                return "validated:sha256"
            return value

        return [
            walk(
                {
                    key: value
                    for key, value in asdict(observation).items()
                    if key not in {"before", "after"}
                },
                (),
            )
            for observation in trace
        ]

    def transport(
        self, captured: CapturedStep, bindings: dict[str, str]
    ) -> dict[str, Any]:
        result = asdict(captured.transport)
        for request in result["sdk_requests"]:
            assert (
                captured.observation.before
                <= instant(request["before"])
                <= instant(request["after"])
                <= captured.observation.after
            )
            request.pop("before")
            request.pop("after")
            claims = request["authorization"]["claims"]
            signed_execution = claims["engine_execution_id"]
            assert signed_execution in bindings, (
                "SDK signed execution has no fixture owner"
            )
            for field in (
                "engine_execution_id",
                "org_id",
                "delegated_user_id",
                "delegated_email",
            ):
                if claims[field] in bindings:
                    claims[field] = f"identity:{bindings[claims[field]]}"
            # The attempt fence must match a committed attempt, never just any UUID.
            token_digest = claims["engine_attempt_token_digest"]
            attempts = captured.observation.database["workflow_execution_attempts"]
            matches = [
                row
                for row in attempts
                if row["claim_token"] is not None
                and row["execution_id"] == signed_execution
                and hashlib.sha256(row["claim_token"].encode()).hexdigest()
                == token_digest
            ]
            assert len(matches) == 1, "SDK attempt fence has no committed owner"
            assert claims["engine_attempt_token_present"] is True
            claims["engine_attempt_token_digest"] = (
                f"execution:{bindings[signed_execution]}:attempt:{matches[0]['attempt_number']}"
            )
            for field in ("name", "scope"):
                value = request["request"].get(field)
                if value in bindings:
                    request["request"][field] = f"identity:{bindings[value]}"
            response = request["response"]
            if isinstance(response, dict):
                for field in ("id", "integration_id", "organization_id"):
                    if response.get(field) in bindings:
                        response[field] = f"identity:{bindings[response[field]]}"
                for field in ("created_at", "updated_at"):
                    if response.get(field) is not None:
                        assert instant(response[field]) == SEED_TIME
                        response[field] = "seed-time"
        return result


PROFILE = CoreReferenceProfile()


def assert_reference_parity(
    left: list[CapturedStep],
    right: list[CapturedStep],
    left_bindings: dict[str, str],
    right_bindings: dict[str, str],
) -> None:
    assert_parity(
        [step.observation for step in left],
        [step.observation for step in right],
        left_bindings,
        right_bindings,
        profile=PROFILE,
    )
    for expected, actual in zip(left, right, strict=True):
        left_transport = PROFILE.transport(expected, left_bindings)
        right_transport = PROFILE.transport(actual, right_bindings)
        for plane in ("sdk_requests", "model_requests", "vendor_requests"):
            assert left_transport[plane] == right_transport[plane], (
                f"{expected.observation.step}: {plane} differs"
            )
