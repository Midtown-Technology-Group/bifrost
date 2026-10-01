"""Explicit A-only comparison policy. Raw observations are never rewritten."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timedelta
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
PENDING_PATH = ("database", "work_deliveries", "envelope", "body", "pending_context")
PENDING_IDENTITIES = {
    ("execution_id",),
    ("workflow_id",),
    ("org_id",),
    ("user_id",),
    ("user_email",),
    ("parameters", "integration_name"),
    ("parameters", "organization_id"),
}
# RedisClient.set_pending_execution's literal output, not arbitrary context fields.
PENDING_TYPES = {
    "execution_id": (str,),
    "workflow_id": (str, type(None)),
    "solution_deployment_id": (str, type(None)),
    "runtime_evidence": (dict, type(None)),
    "runtime_mode": (str,),
    "script_name": (str, type(None)),
    "parameters": (dict,),
    "org_id": (str, type(None)),
    "org_id_overridden": (bool,),
    "user_id": (str,),
    "user_name": (str,),
    "user_email": (str,),
    "form_id": (str, type(None)),
    "api_key_id": (str, type(None)),
    "startup": (),  # Any JSON value: opaque launch output.
    "form_inputs": (dict,),
    "embed": (dict,),
    "sync": (bool,),
    "is_platform_admin": (bool,),
    "is_provider_org": (bool,),
    "is_external": (bool,),
    "event": (dict, type(None)),
    "artifact_workspace_id": (str, type(None)),
    "created_at": (str,),
    "cancelled": (bool,),
}
LOCAL_IDENTITIES = {
    ("integration_name",),
    ("organization_id",),
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


def fixture_binding(bindings: dict[str, str], role: str) -> str:
    values = [value for value, owner in bindings.items() if owner == role]
    assert len(values) == 1, "Pending fixture owner unavailable"
    return values[0]


def validate_pending_context(
    observation: Observation, bindings: dict[str, str]
) -> None:
    """Bind the complete raw producer output before projecting any leaf."""
    for delivery in observation.database.get("work_deliveries", []):
        assert delivery["encrypted_envelope_present"] is True, (
            "Pending delivery encryption absent"
        )
        body = delivery["envelope"]["body"]
        headers = delivery["envelope"]["headers"]
        pending = body.get("pending_context")
        assert type(pending) is dict and pending.keys() == PENDING_TYPES.keys(), (
            "Pending context schema differs"
        )
        for field, types in PENDING_TYPES.items():
            assert not types or type(pending[field]) in types, (
                f"Pending context {field} wire type differs"
            )
        owners = [
            row
            for row in observation.database["executions"]
            if row["id"] == delivery["message_id"]
        ]
        assert len(owners) == 1, "Pending delivery has no unique execution owner"
        row = owners[0]
        assert row["id"] in bindings, "Pending execution has no fixture owner"
        dispatch = row["dispatch_evidence"]
        request, publish = dispatch["request"], dispatch["publish"]
        assert (
            dispatch["request_hash"] == digest(request)
            and dispatch["publish_hash"] == digest(publish)
            and row["dispatch_evidence_hash"] == digest(dispatch)
        ), "Pending dispatch hash differs"
        assert (
            dispatch["schema_version"]
            == request["schema_version"]
            == ("bifrost.workflow-pending-dispatch/v1")
        ), "Pending dispatch schema differs"
        expected_publish = {
            key: value
            for key, value in request.items()
            if key not in {"schema_version", "caller_solution_deployment_id"}
        }
        expected_publish.update(
            solution_deployment_id=row["solution_deployment_id"],
            runtime_evidence=row["runtime_evidence"],
            runtime_mode=row["runtime_mode"],
            execution_record_exists=True,
        )
        assert digest(publish) == digest(expected_publish), (
            "Pending publish request projection differs"
        )
        for field in ("form_id", "api_key_id"):
            assert pending[field] == row[field], "Pending row projection differs"
        assert row["runtime_mode"] == "repo-v1" and (
            row["runtime_evidence"] is None
            and row["runtime_evidence_hash"] is None
            and row["solution_deployment_id"] is None
        ), "Pending profile requires R0 repo runtime"
        expected = {
            field: publish[field]
            for field in PENDING_TYPES
            if field
            not in {
                "script_name",
                "created_at",
                "cancelled",
                "org_id_overridden",
                "artifact_workspace_id",
                "form_inputs",
                "embed",
            }
        }
        expected.update(
            script_name=None,
            cancelled=False,
            org_id_overridden=publish.get("org_id_overridden", False),
            artifact_workspace_id=publish.get("artifact_workspace_id"),
            form_inputs=publish["form_inputs"] or {},
            embed=publish["embed"] or {},
        )
        assert digest(
            {key: value for key, value in pending.items() if key != "created_at"}
        ) == digest(expected), "Pending producer projection differs"
        assert (
            delivery["queue_name"] == headers["x-origin-queue"] == "workflow-executions"
            and body["execution_id"] == pending["execution_id"] == row["id"]
            and headers["x-idempotency-key"]
            == headers["x-original-message-id"]
            == row["id"]
            and body["workflow_id"] == pending["workflow_id"] == row["workflow_id"]
            and body["sync"] is pending["sync"]
            and body["execution_record_exists"] is True
            and body.get("file_path")
            == publish["file_path"]
            == fixture_binding(bindings, "source-path")
        ), "Pending delivery owner projection differs"
        assert pending["workflow_id"] == fixture_binding(bindings, "workflow"), (
            "Pending workflow fixture owner differs"
        )
        assert (
            pending["user_id"]
            == row["executed_by"]
            == fixture_binding(bindings, "user")
            and pending["org_id"]
            == row["organization_id"]
            == fixture_binding(bindings, "org")
            and pending["user_email"] == fixture_binding(bindings, "caller-email")
            and digest(pending["parameters"]) == digest(row["parameters"])
            and pending["org_id_overridden"] is False
        ), "Pending caller scope projection differs"
        caller = next(
            user
            for user in observation.database["users"]
            if user["id"] == row["executed_by"]
        )
        organization = next(
            org
            for org in observation.database["organizations"]
            if org["id"] == row["organization_id"]
        )
        assert (
            pending["user_name"] == row["executed_by_name"] == caller["name"]
            and pending["user_email"] == caller["email"]
            and pending["org_id"] == caller["organization_id"]
            and pending["is_platform_admin"] is caller["is_superuser"] is False
            and pending["is_provider_org"] is organization["is_provider"] is False
            and pending["is_external"] is False
        ), "Pending caller authority differs"
        context = row["execution_context"]
        if isinstance(context, dict) and "user_id" in context:
            assert (
                pending["user_name"]
                == row["executed_by_name"]
                == caller["name"]
                == context["name"]
                and pending["user_email"] == caller["email"] == context["email"]
                and pending["user_id"] == context["user_id"]
                and pending["org_id"] == caller["organization_id"] == context["scope"]
                and context["execution_id"] == row["id"]
                and context["organization"]
                == {
                    key: organization[key]
                    for key in ("id", "name", "is_active", "is_provider")
                }
            ), "Pending public caller projection differs"
            for field in ("parameters", "startup", "form_inputs", "embed"):
                assert digest(pending[field]) == digest(context[field]), (
                    f"Pending public {field} projection differs"
                )
            assert context["is_function_key"] is False and all(
                context[field] is pending[field]
                for field in ("is_platform_admin", "is_provider_org", "is_external")
            ), "Pending caller authority differs"
        if row["status"] == "Success":
            assert isinstance(context, dict) and "user_id" in context, (
                "Pending successful public context absent"
            )
            assert (
                type(pending["parameters"].get("integration_name")) is str
                and bindings.get(pending["parameters"].get("integration_name"))
                in {"integration-name", "absent-integration-name"}
                and pending["parameters"].get("organization_id")
                == fixture_binding(bindings, "org")
            ), "Pending successful input fixture owner differs"
            assert context.get("workspace_generation") == fixture_binding(
                bindings, "installed-source-generation"
            ), "Pending successful source generation differs"
        try:
            created = instant(pending["created_at"])
        except (ValueError, TypeError) as exc:
            raise AssertionError("Pending producer clock invalid") from exc
        assert created != SEED_TIME and created.utcoffset() == timedelta(0), (
            "Pending producer clock provenance differs"
        )


def validate_measurements(observation: Observation) -> None:
    """Check source units, measured elapsed bounds and same-run projections."""
    window_ms = (observation.after - observation.before).total_seconds() * 1000
    executions = observation.database["executions"]
    for row in executions:
        variables = row["variables"]
        if isinstance(variables, dict):
            for field in ("integration_name", "organization_id"):
                if field in variables:
                    assert variables[field] == row["parameters"][field], (
                        "workflow input local projection differs"
                    )
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

    def validate_pending_context(
        self, observation: Observation, bindings: dict[str, str]
    ) -> None:
        """R0 admission gate; pinned profiles must supply their own strict gate.

        A deployment profile must review its producer and ownership chain before
        overriding this hook. Inheriting it remains fail-closed on non-R0 work.
        """
        validate_pending_context(observation, bindings)

    def canonicalize(
        self, trace: list[Observation], bindings: dict[str, str]
    ) -> list[dict[str, Any]]:
        identities = dict(bindings)
        generated_counts: dict[str, int] = {}
        clocks: set[datetime] = set()
        for observation in trace:
            self.validate_pending_context(observation, bindings)
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
            values.extend(
                row["envelope"]["body"]["pending_context"]["created_at"]
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
                (*PENDING_PATH, "created_at"),
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
            if path[:5] == PENDING_PATH:
                identity |= path[5:] in PENDING_IDENTITIES
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
        self.validate_pending_context(captured.observation, bindings)
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
            owners = [
                delivery["envelope"]["body"]["pending_context"]
                for delivery in captured.observation.database.get("work_deliveries", [])
                if delivery["message_id"] == signed_execution
            ]
            if captured.observation.database.get("work_deliveries"):
                assert len(owners) == 1, "SDK signed execution has no pending owner"
                pending = owners[0]
                assert all(
                    type(claims[claim]) is type(pending[field])
                    and claims[claim] == pending[field]
                    for claim, field in {
                        "org_id": "org_id",
                        "delegated_user_id": "user_id",
                        "delegated_email": "user_email",
                        "delegated_name": "user_name",
                        "delegated_is_superuser": "is_platform_admin",
                        "delegated_is_provider_org": "is_provider_org",
                        "delegated_is_external": "is_external",
                    }.items()
                ), "SDK pending caller projection differs"
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
