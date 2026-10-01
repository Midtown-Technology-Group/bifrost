"""Synchronous raw-owner and opacity vectors for the bounded R0 profile."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from src.core import redis_client as redis_module
from src.jobs.rabbitmq import _message_headers, infer_idempotency_key
from src.sdk.context import ExecutionContext, Organization
from src.services.execution import async_executor
from src.services.execution.async_executor import (
    _dispatch_request_identity,
    _pending_dispatch_envelope,
)

from tests.parity.core.capture import CapturedStep, TransportEvidence
from tests.parity.core.profile import (
    CLOCKS,
    GENERATED_IDENTITIES,
    PROFILE,
    CoreReferenceProfile,
    digest,
    validate_pending_context,
)
from tests.parity.harness import Observation


def pending_vector(label="left"):
    before = datetime(2026, 10, 1, tzinfo=UTC)
    created = before + timedelta(seconds=1)
    bindings = {
        f"{label}-{role}": role
        for role in (
            "readiness",
            "workflow",
            "user",
            "org",
            "caller-email",
            "source-path",
            "integration-name",
            "installed-source-generation",
            "foreign_org",
            "setup_user",
        )
    }
    owner = {role: value for value, role in bindings.items()}
    parameters = {
        "integration_name": owner["integration-name"],
        "organization_id": owner["org"],
        "required_config_keys": ["fixture_value"],
        "require_mapping": True,
        "require_oauth": False,
    }
    # The public execute route supplies metadata, not the optional file_path.
    metadata = {
        "name": "Utilities: Check Integration Readiness",
        "function_name": "check_integration_readiness",
        "path": owner["source-path"],
        "organization_id": owner["org"],
        "type": "workflow",
    }
    caller_context = ExecutionContext(
        user_id=owner["user"],
        email=owner["caller-email"],
        name="Caller",
        scope=owner["org"],
        organization=Organization(id=owner["org"], name="Tenant"),
        is_platform_admin=False,
        is_function_key=False,
        execution_id=owner["readiness"],
    )
    request = _dispatch_request_identity(
        caller_context,
        owner["readiness"],
        owner["workflow"],
        deepcopy(parameters),
        form_id=None,
        sync=True,
        api_key_id=None,
        file_path=None,
        org_id_override=None,
        dispatch_metadata=metadata,
    )
    dispatch = _pending_dispatch_envelope(
        request,
        solution_deployment_id=None,
        runtime_evidence=None,
        runtime_mode="repo-v1",
    )
    publish = dispatch["publish"]
    # Concrete wire vector of the Redis producer, including absent/default fields.
    pending = {
        key: deepcopy(value)
        for key, value in publish.items()
        if key not in {"file_path", "execution_record_exists", "dispatch_metadata"}
    }
    pending.update(
        script_name=None,
        org_id_overridden=False,
        artifact_workspace_id=None,
        created_at=created.isoformat(),
        cancelled=False,
    )
    organization = {
        "id": owner["org"],
        "name": "Tenant",
        "is_active": True,
        "is_provider": False,
    }
    context = {
        "user_id": owner["user"],
        "email": owner["caller-email"],
        "name": "Caller",
        "scope": owner["org"],
        "organization": deepcopy(organization),
        "execution_id": owner["readiness"],
        "parameters": deepcopy(parameters),
        "startup": None,
        "form_inputs": {},
        "embed": {},
        "is_platform_admin": False,
        "is_provider_org": False,
        "is_external": False,
        "is_function_key": False,
        "workspace_generation": owner["installed-source-generation"],
    }
    row = {
        "id": owner["readiness"],
        "workflow_id": owner["workflow"],
        "parameters": deepcopy(parameters),
        "organization_id": owner["org"],
        "executed_by": owner["user"],
        "executed_by_name": "Caller",
        "status": "Success",
        "form_id": None,
        "api_key_id": None,
        "solution_deployment_id": None,
        "runtime_mode": "repo-v1",
        "runtime_evidence": None,
        "runtime_evidence_hash": None,
        "dispatch_evidence": dispatch,
        "dispatch_evidence_hash": digest(dispatch),
        "execution_context": context,
        "variables": None,
        "duration_ms": None,
        "peak_memory_bytes": None,
        "process_rss_bytes": None,
        "cpu_user_seconds": None,
        "cpu_system_seconds": None,
        "cpu_total_seconds": None,
    }
    delivery = {
        "id": f"{label}-delivery",
        "message_id": owner["readiness"],
        "queue_name": "workflow-executions",
        "encrypted_envelope_present": True,
        "envelope": {
            "body": {
                "execution_id": owner["readiness"],
                "workflow_id": owner["workflow"],
                "sync": True,
                "execution_record_exists": True,
                "dispatch_metadata": deepcopy(metadata),
                "pending_context": pending,
            },
            "headers": _message_headers(
                {"execution_id": owner["readiness"]},
                "workflow-executions",
                message_id=owner["readiness"],
                headers={
                    "x-enqueued-at": (created + timedelta(milliseconds=1)).isoformat()
                },
            ),
        },
    }
    database = {table: [] for table in CLOCKS.keys() | GENERATED_IDENTITIES.keys()}
    database.update(
        executions=[row],
        work_deliveries=[delivery],
        organizations=[organization],
        users=[
            {
                "id": owner["user"],
                "email": owner["caller-email"],
                "name": "Caller",
                "organization_id": owner["org"],
                "is_superuser": False,
            }
        ],
    )
    return Observation(
        "readiness", 200, None, database, [], before, before + timedelta(seconds=2)
    ), bindings


def pending_of(observation):
    return observation.database["work_deliveries"][0]["envelope"]["body"][
        "pending_context"
    ]


def rehash(row):
    dispatch = row["dispatch_evidence"]
    dispatch["request_hash"] = digest(dispatch["request"])
    dispatch["publish_hash"] = digest(dispatch["publish"])
    row["dispatch_evidence_hash"] = digest(dispatch)


def test_pending_independent_projection_preserves_raw_evidence():
    left, left_bindings = pending_vector()
    right, right_bindings = pending_vector("right")
    original = deepcopy(left)
    assert PROFILE.canonicalize([left], left_bindings) == PROFILE.canonicalize(
        [right], right_bindings
    )
    assert left == original


def test_real_pending_producer_keeps_public_metadata_path_separate(monkeypatch):
    observation, bindings = pending_vector()
    row = observation.database["executions"][0]
    delivery = observation.database["work_deliveries"][0]
    stored = []
    published = []
    created = datetime.fromisoformat(pending_of(observation)["created_at"])

    class ImmediateRedis:
        async def setex(self, key, ttl, value):
            stored.append(value)

    client = redis_module.RedisClient()

    async def connection():
        return ImmediateRedis()

    async def publish(queue, message, **kwargs):
        published.append(
            {
                "body": deepcopy(message),
                "headers": _message_headers(
                    message,
                    queue,
                    message_id=infer_idempotency_key(queue, message),
                    headers={
                        "x-enqueued-at": (
                            created + timedelta(milliseconds=1)
                        ).isoformat()
                    },
                ),
            }
        )

    monkeypatch.setattr(client, "_get_redis", connection)
    monkeypatch.setattr(
        redis_module, "datetime", SimpleNamespace(now=lambda _: created)
    )
    monkeypatch.setattr(async_executor, "get_redis_client", lambda: client)
    monkeypatch.setattr(
        async_executor,
        "get_settings",
        lambda: SimpleNamespace(work_delivery_backend="postgres"),
    )
    monkeypatch.setattr(async_executor, "publish_message", publish)
    # Every await is an immediate in-memory boundary; no event loop/socket/DB.
    coroutine = async_executor._publish_pending(**row["dispatch_evidence"]["publish"])
    with pytest.raises(StopIteration):
        coroutine.send(None)
    assert len(stored) == len(published) == 1
    body = published[0]["body"]
    assert "file_path" not in body
    assert row["dispatch_evidence"]["publish"]["file_path"] is None
    assert body["dispatch_metadata"]["path"] == next(
        raw for raw, role in bindings.items() if role == "source-path"
    )
    assert published[0] == delivery["envelope"]
    delivery["envelope"] = published[0]
    validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    "field,value",
    [
        ("org_id_overridden", 0),
        ("cancelled", 0),
        ("sync", 1),
        ("parameters", []),
        ("form_inputs", None),
        ("user_id", 7),
        ("created_at", None),
    ],
)
def test_pending_rejects_wire_type_drift(field, value):
    observation, bindings = pending_vector()
    pending_of(observation)[field] = value
    with pytest.raises(AssertionError, match="wire type differs"):
        validate_pending_context(observation, bindings)


def test_pending_rejects_parameter_bool_integer_wire_drift():
    observation, bindings = pending_vector()
    pending_of(observation)["parameters"]["require_mapping"] = 1
    with pytest.raises(AssertionError, match="Pending producer projection differs"):
        validate_pending_context(observation, bindings)


@pytest.mark.parametrize("mutation", ["missing", "extra", "attempt"])
def test_pending_rejects_shape_drift(mutation):
    observation, bindings = pending_vector()
    pending = pending_of(observation)
    if mutation == "missing":
        del pending["startup"]
    else:
        pending["execution_attempt_id" if mutation == "attempt" else "extra"] = "known"
    with pytest.raises(AssertionError, match="schema differs"):
        validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    "created",
    [
        "invalid",
        "2026-10-01T00:00:01",
        "2020-01-01T00:00:00+00:00",
        "2026-10-01T00:00:03+00:00",
        "2026-10-01T00:00:01+01:00",
    ],
)
def test_pending_rejects_unowned_clock(created):
    observation, bindings = pending_vector()
    pending_of(observation)["created_at"] = created
    with pytest.raises(AssertionError):
        PROFILE.canonicalize([observation], bindings)


def test_pending_clock_retains_validated_admission_in_later_trace_snapshot():
    admission, bindings = pending_vector()
    later = deepcopy(admission)
    later.before = admission.after + timedelta(seconds=1)
    later.after = later.before + timedelta(seconds=1)
    projected = PROFILE.canonicalize([admission, later], bindings)
    assert projected[0] == projected[1]
    with pytest.raises(AssertionError, match="clock outside measured operation"):
        PROFILE.canonicalize([later], bindings)


@pytest.mark.parametrize(
    "sidecar", ["startup", "form_inputs", "embed", "event", "parameters"]
)
def test_pending_private_payload_identity_and_clock_strings_remain_exact(sidecar):
    observation, bindings = pending_vector()
    row = observation.database["executions"][0]
    private = {
        "execution_id": row["id"],
        "user_id": row["executed_by"],
        "created_at": "2020-01-01T00:00:00+00:00",
        "workspace_generation": row["execution_context"]["workspace_generation"],
    }
    value = (
        {**row["parameters"], "private": private}
        if sidecar == "parameters"
        else {"data": private}
    )
    pending_of(observation)[sidecar] = deepcopy(value)
    for section in ("request", "publish"):
        row["dispatch_evidence"][section][sidecar] = deepcopy(value)
    if sidecar != "event":  # Public serializer deliberately omits event.
        row["execution_context"][sidecar] = deepcopy(value)
    if sidecar == "parameters":
        row["parameters"] = deepcopy(value)
    rehash(row)
    raw = deepcopy(observation)
    projected = PROFILE.canonicalize([observation], bindings)[0]
    canonical_pending = projected["database"]["work_deliveries"][0]["envelope"]["body"][
        "pending_context"
    ]
    assert (
        canonical_pending[sidecar]["private" if sidecar == "parameters" else "data"]
        == private
    )
    assert observation == raw


@pytest.mark.parametrize(
    "field", ["is_platform_admin", "is_provider_org", "is_external"]
)
def test_pending_rejects_self_consistent_authority_drift(field):
    observation, bindings = pending_vector()
    row = observation.database["executions"][0]
    pending_of(observation)[field] = True
    row["execution_context"][field] = True
    row["dispatch_evidence"]["request"][field] = True
    row["dispatch_evidence"]["publish"][field] = True
    rehash(row)
    with pytest.raises(AssertionError, match="caller authority differs"):
        validate_pending_context(observation, bindings)


@pytest.mark.parametrize("plane", ["generation", "encryption", "hash", "header"])
def test_pending_rejects_provenance_drift(plane):
    observation, bindings = pending_vector()
    row = observation.database["executions"][0]
    delivery = observation.database["work_deliveries"][0]
    if plane == "generation":
        del row["execution_context"]["workspace_generation"]
    elif plane == "encryption":
        delivery["encrypted_envelope_present"] = False
    elif plane == "hash":
        row["dispatch_evidence"]["publish_hash"] = "sha256:invalid"
    else:
        delivery["envelope"]["headers"]["x-original-message-id"] = "right-readiness"
    with pytest.raises(AssertionError):
        validate_pending_context(observation, bindings)


def test_pending_generation_gate_does_not_redefine_failed_authored_execution():
    observation, bindings = pending_vector()
    row = observation.database["executions"][0]
    row["status"] = "Failed"
    del row["execution_context"]["workspace_generation"]
    validate_pending_context(observation, bindings)


def test_signed_sdk_caller_is_bound_to_raw_pending_owner_before_projection():
    observation, bindings = pending_vector()
    pending = pending_of(observation)
    claims = {
        "engine_execution_id": pending["execution_id"],
        "org_id": pending["org_id"],
        "delegated_user_id": pending["user_id"],
        "delegated_email": pending["user_email"],
        "delegated_name": pending["user_name"],
        "delegated_is_superuser": False,
        "delegated_is_provider_org": False,
        "delegated_is_external": False,
    }
    claims["delegated_user_id"] = next(
        raw for raw, role in bindings.items() if role == "setup_user"
    )
    captured = CapturedStep(
        observation,
        TransportEvidence(
            sdk_requests=[
                {
                    "before": observation.before.isoformat(),
                    "after": observation.after.isoformat(),
                    "authorization": {"claims": claims},
                }
            ]
        ),
    )
    with pytest.raises(AssertionError, match="SDK pending caller projection differs"):
        PROFILE.transport(captured, bindings)


@pytest.mark.parametrize("plane", ["observation", "transport"])
def test_profile_requires_validation_hook_in_both_comparison_planes(plane):
    class StrictReplacementProfile(CoreReferenceProfile):
        def validate_pending_context(
            self, observation: Observation, bindings: dict[str, str]
        ) -> None:
            raise AssertionError("Strict replacement admission gate")

    observation, bindings = pending_vector()
    profile = StrictReplacementProfile()
    with pytest.raises(AssertionError, match="Strict replacement admission gate"):
        if plane == "observation":
            profile.canonicalize([observation], bindings)
        else:
            profile.transport(CapturedStep(observation, TransportEvidence()), bindings)
