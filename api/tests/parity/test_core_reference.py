"""C1-R0 Scenario A characterization; supported PostgreSQL Docker lane only."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import update
from src.models.orm.executions import Execution

from tests.parity.core.adapter import ReferenceAdapter, ResponseDriftTransport
from tests.parity.core.capture import (
    CapturedStep,
    ScopedReferenceCapture,
    TransportEvidence,
)
from tests.parity.core.environment import SOURCE_SHA256, ReferenceEnvironment
from tests.parity.core.profile import PROFILE, assert_reference_parity
from tests.parity.core.scenarios import (
    assert_readiness,
    execute_readiness,
    expected_result,
)
from tests.parity.core.sdk_observer import PREFIX, assert_synthetic_request, target_url
from tests.parity.harness import Observation

pytestmark = pytest.mark.e2e


@pytest.fixture
def core_environment(async_engine):
    @asynccontextmanager
    async def create(**options):
        environment = ReferenceEnvironment(async_engine)
        try:
            await environment.seed(**options)
            async with ScopedReferenceCapture(
                environment, os.environ["BIFROST_REDIS_URL"]
            ) as capture:
                await environment.install()
                adapter = ReferenceAdapter(
                    os.environ["TEST_API_URL"], capture, environment.token
                )
                try:
                    yield environment, adapter
                finally:
                    await adapter.close()
        finally:
            await environment.cleanup()

    return create


async def test_core_python_authored_readiness(core_environment):
    async with core_environment() as (environment, adapter):
        captured = await execute_readiness(
            adapter, environment, before=environment.before
        )
        assert_readiness(captured, environment, expected_result(environment))
        assert PROFILE.canonicalize([captured.observation], environment.bindings)
        assert PROFILE.transport(captured, environment.bindings)["sdk_requests"]
        execution = captured.observation.database["executions"][0]
        report = {
            "schema": "bifrost.core-reference/v1",
            "proof": "Python Scenario A only; public repo registration; no Rust/model/vendor acceptance",
            "source_sha256": SOURCE_SHA256,
            "observation": PROFILE.canonicalize(
                [captured.observation], environment.bindings
            )[0],
            "transport": PROFILE.transport(captured, environment.bindings),
            "measurements": {
                "before": captured.observation.before.isoformat(),
                "after": captured.observation.after.isoformat(),
                **{
                    field: execution[field]
                    for field in (
                        "duration_ms",
                        "peak_memory_bytes",
                        "process_rss_bytes",
                        "cpu_user_seconds",
                        "cpu_system_seconds",
                        "cpu_total_seconds",
                    )
                },
            },
        }
        destination = Path("/tmp/bifrost/core-reference-readiness.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n")


async def test_core_independent_python_comparison(core_environment):
    async with core_environment() as (left_env, left):
        reference = await execute_readiness(left, left_env, before=left_env.before)
        assert_readiness(reference, left_env, expected_result(left_env))
        async with core_environment() as (right_env, right):
            assert left_env.ids["org"] != right_env.ids["org"]
            assert left_env.ids["user"] != right_env.ids["user"]
            assert set(left_env.executions).isdisjoint(right_env.executions)
            candidate = await execute_readiness(
                right, right_env, before=right_env.before
            )
            assert_readiness(candidate, right_env, expected_result(right_env))
            assert_reference_parity(
                [reference], [candidate], left_env.bindings, right_env.bindings
            )


@pytest.mark.parametrize(
    "case", ["missing_config", "missing_required_key", "missing_mapping"]
)
async def test_core_readiness_is_not_nominal_success(core_environment, case):
    async with core_environment(
        configured=case != "missing_config", mapping=case != "missing_mapping"
    ) as (env, adapter):
        required = (
            ["absent_key", "fixture_value"]
            if case == "missing_required_key"
            else ["fixture_value"]
        )
        captured = await execute_readiness(
            adapter,
            env,
            parameters=env.parameters(required_config_keys=required),
            before=env.before,
        )
        assert_readiness(
            captured,
            env,
            expected_result(
                env,
                required=required,
                configured=case not in {"missing_config", "missing_mapping"},
                mapping=case != "missing_mapping",
            ),
        )
        assert captured.observation.body["result"]["ready"] is False


async def test_core_missing_integration_is_not_readiness(core_environment):
    async with core_environment() as (env, adapter):
        missing_name = f"{env.name}-absent"
        env.bindings[missing_name] = "absent-integration-name"
        captured = await execute_readiness(
            adapter,
            env,
            parameters=env.parameters(integration_name=missing_name),
            before=env.before,
        )
        assert_readiness(
            captured,
            env,
            expected_result(
                env, name=missing_name, found=False, configured=False, mapping=False
            ),
        )
        assert captured.observation.body["result"]["ready"] is False


@pytest.mark.parametrize(
    "parameters,error",
    [
        ({"integration_name": ""}, "integration_name is required"),
        (
            {"organization_id": None},
            "organization_id is required when require_mapping is true",
        ),
    ],
)
async def test_core_authored_validation_errors(core_environment, parameters, error):
    async with core_environment() as (env, adapter):
        captured = await execute_readiness(
            adapter, env, parameters=env.parameters(**parameters), before=env.before
        )
        assert captured.observation.body["status"] == "Failed"
        assert error in captured.observation.body["error"]
        assert not captured.transport.sdk_requests
        assert captured.observation.database["executions"][0]["status"] == "Failed"


async def test_core_real_sdk_response_error_is_not_readiness(core_environment):
    async with core_environment() as (env, adapter):
        execution = await env.allocate_execution("sdk-error")
        owner = {
            "user_id": str(env.ids["user"]),
            "org_id": str(env.ids["org"]),
            "sdk_response_mutation": "forbidden",
            "request_name": env.name,
        }
        await env.redis.set(f"{PREFIX}{execution}:owner", json.dumps(owner), ex=600)
        captured = await adapter.request(
            "sdk-error",
            "POST",
            "/api/workflows/execute",
            json={
                "workflow_id": str(env.ids["workflow"]),
                "input_data": env.parameters(),
                "sync": True,
            },
            headers={"X-Bifrost-Execution-ID": str(execution)},
            settled=True,
            before=env.before,
        )
        assert_readiness(
            captured,
            env,
            expected_result(
                env,
                found=False,
                configured=False,
                lookup_error="BifrostAuthorizationError",
            ),
        )
        request = captured.transport.sdk_requests[0]
        assert request["upstream_status"] == 200 and request["status"] == 403
        assert request["synthetic_mutation"] == "forbidden"


async def test_core_normal_caller_cannot_override_foreign_tenant(core_environment):
    async with core_environment() as (env, adapter):
        captured = await adapter.request(
            "foreign-tenant-denial",
            "POST",
            "/api/workflows/execute",
            json={
                "workflow_id": str(env.ids["workflow"]),
                "input_data": env.parameters(),
                "sync": True,
                "org_id": str(env.ids["foreign_org"]),
            },
            before=env.before,
        )
        assert captured.observation.status == 403
        assert captured.observation.body == {
            "detail": "org_id and run_as overrides require platform admin"
        }
        assert not captured.observation.database["executions"]
        assert not captured.observation.database["workflow_execution_attempts"]
        assert not captured.observation.database["execution_attempts"]
        assert not captured.observation.database["work_deliveries"]
        assert not captured.observation.events and not captured.transport.sdk_requests


@pytest.mark.parametrize(
    "path",
    [
        "//evil.invalid/api/sdk/integrations/get",
        "/evil.invalid/api/sdk/integrations/get",
        "http://evil.invalid/path",
        "\\evil.invalid\\path",
    ],
)
def test_core_observer_rejects_authority_changing_paths(path):
    with pytest.raises(AssertionError, match="Unsupported observer request path"):
        target_url(httpx.URL("http://api:8000"), path, b"")


def test_core_observer_query_cannot_change_upstream_authority():
    url = target_url(
        httpx.URL("http://api:8000"),
        "api/sdk/integrations/get",
        b"next=//evil.invalid&url=http://evil.invalid",
    )
    assert url.host == "api" and url.port == 8000 and url.scheme == "http"
    assert url.path == "/api/sdk/integrations/get"
    assert url.query == b"next=//evil.invalid&url=http://evil.invalid"


async def result_observation(adapter, env):
    return await adapter.request(
        "capture-self-test",
        "GET",
        f"/api/executions/{env.executions[0]}/result",
        before=env.before,
    )


@pytest.mark.parametrize("plane", ["status", "body"])
async def test_core_actual_http_wire_drift(core_environment, plane):
    async with core_environment() as (env, adapter):
        ready = await execute_readiness(adapter, env, before=env.before)
        assert_readiness(ready, env, expected_result(env))
        reference = await result_observation(adapter, env)
        transport = ResponseDriftTransport(plane)
        wrapped = ReferenceAdapter(
            os.environ["TEST_API_URL"], adapter.capture, env.token, transport
        )
        try:
            candidate = await result_observation(wrapped, env)
        finally:
            await wrapped.close()
        assert transport.original == (
            reference.observation.status,
            reference.observation.body,
        )
        assert reference.observation.database == candidate.observation.database
        assert reference.observation.events == candidate.observation.events == []
        assert reference.transport == candidate.transport
        with pytest.raises(AssertionError, match=f"{plane} differs"):
            assert_reference_parity(
                [reference], [candidate], env.bindings, env.bindings
            )


@pytest.mark.parametrize("field", ["time_saved", "peak_memory_bytes"])
async def test_core_actual_committed_sql_drift(core_environment, field):
    async with core_environment() as (env, adapter):
        ready = await execute_readiness(adapter, env, before=env.before)
        assert_readiness(ready, env, expected_result(env))
        reference = await result_observation(adapter, env)
        value = reference.observation.database["executions"][0][field]
        assert type(value) is int
        async with env.sessions() as db:
            await db.execute(
                update(Execution)
                .where(Execution.id == env.executions[0])
                .values({field: value + 1})
            )
            await db.commit()
        candidate = await result_observation(adapter, env)
        assert candidate.observation.database["executions"][0][field] == value + 1
        assert reference.observation.body == candidate.observation.body
        assert reference.observation.events == candidate.observation.events == []
        assert reference.transport == candidate.transport
        reason = (
            "database differs"
            if field == "time_saved"
            else "measurement projection differs"
        )
        with pytest.raises(AssertionError, match=reason):
            assert_reference_parity(
                [reference], [candidate], env.bindings, env.bindings
            )


async def test_core_actual_scoped_redis_drift(core_environment):
    async with core_environment() as (env, adapter):
        ready = await execute_readiness(adapter, env, before=env.before)
        assert_readiness(ready, env, expected_result(env))
        reference = await result_observation(adapter, env)
        channel = f"unexpected:execution:{env.executions[0]}"
        payload = {
            "type": "synthetic-capture-probe",
            "executionId": str(env.executions[0]),
            "status": "Failed",
        }
        await adapter.capture.redis.publish(channel, json.dumps(payload))
        candidate = await result_observation(adapter, env)
        assert candidate.observation.events == [
            {"channel": channel, "payload": payload}
        ]
        assert reference.observation.body == candidate.observation.body
        assert reference.observation.database == candidate.observation.database
        assert reference.transport == candidate.transport
        with pytest.raises(AssertionError, match="events differs"):
            assert_reference_parity(
                [reference], [candidate], env.bindings, env.bindings
            )


@pytest.mark.parametrize(
    "method,path,query,body",
    [
        ("GET", "api/sdk/integrations/get", b"", {"name": "approved", "scope": "org"}),
        ("POST", "api/sdk/secrets/get", b"", {"name": "approved", "scope": "org"}),
        (
            "POST",
            "api/sdk/integrations/get",
            b"secret=unexpected",
            {"name": "approved", "scope": "org"},
        ),
        (
            "POST",
            "api/sdk/integrations/get",
            b"",
            {"name": "unexpected-secret", "scope": "org"},
        ),
        (
            "POST",
            "api/sdk/integrations/get",
            b"",
            {"name": "approved", "scope": "foreign"},
        ),
        (
            "POST",
            "api/sdk/integrations/get",
            b"",
            {"name": "approved", "scope": "org", "secret": "unexpected-secret"},
        ),
        (
            "POST",
            "api/sdk/integrations/get_mapping",
            b"",
            {"name": "approved", "scope": "org", "entity_id": "unexpected-secret"},
        ),
    ],
)
def test_core_observer_rejects_unapproved_request_vectors(method, path, query, body):
    # Supplemental seam vectors; actual capture/wire mutants are separate tests.
    with pytest.raises(AssertionError, match="Unapproved owned SDK") as error:
        assert_synthetic_request(
            method,
            path,
            query,
            json.dumps(body).encode(),
            {"request_name": "approved", "org_id": "org"},
        )
    assert "unexpected-secret" not in str(error.value)
    assert "secret=unexpected" not in str(error.value)


@pytest.mark.parametrize("mapping", [False, True])
def test_core_observer_accepts_exact_synthetic_request_vector(mapping):
    body = {"name": "approved", "scope": "org"}
    if mapping:
        body["entity_id"] = None
    path = "api/sdk/integrations/get_mapping" if mapping else "api/sdk/integrations/get"
    assert (
        assert_synthetic_request(
            "POST",
            path,
            b"",
            json.dumps(body).encode(),
            {"request_name": "approved", "org_id": "org"},
        )
        == body
    )


@pytest.mark.parametrize("token_owner", ["execution-a", "execution-b"])
def test_core_profile_binds_fence_to_signed_execution_vector(token_owner):
    # Distinct owned executions can both have attempt 1; ownership must agree.
    before = datetime.now(UTC)
    fence = "synthetic-high-entropy-fence-vector"
    captured = CapturedStep(
        Observation(
            "fence-vector",
            200,
            None,
            {
                "workflow_execution_attempts": [
                    {
                        "execution_id": token_owner,
                        "claim_token": fence,
                        "attempt_number": 1,
                    }
                ]
            },
            [],
            before,
            before + timedelta(seconds=1),
        ),
        TransportEvidence(
            sdk_requests=[
                {
                    "before": before.isoformat(),
                    "after": before.isoformat(),
                    "authorization": {
                        "claims": {
                            "engine_execution_id": "execution-a",
                            "org_id": "org",
                            "delegated_user_id": "user",
                            "delegated_email": "email",
                            "engine_attempt_token_present": True,
                            "engine_attempt_token_digest": hashlib.sha256(
                                fence.encode()
                            ).hexdigest(),
                        }
                    },
                    "request": {"name": "approved", "scope": "org"},
                    "response": None,
                }
            ]
        ),
    )
    bindings = {"execution-a": "first", "execution-b": "second"}
    if token_owner != "execution-a":
        with pytest.raises(
            AssertionError, match="SDK attempt fence has no committed owner"
        ):
            PROFILE.transport(captured, bindings)
    else:
        normalized = PROFILE.transport(captured, bindings)
        assert (
            normalized["sdk_requests"][0]["authorization"]["claims"][
                "engine_attempt_token_digest"
            ]
            == "execution:first:attempt:1"
        )
        # Comparison never rewrites the retained signed identity or digest.
        assert (
            captured.transport.sdk_requests[0]["authorization"]["claims"][
                "engine_execution_id"
            ]
            == "execution-a"
        )


@pytest.mark.parametrize(
    "raw", [b"not JSON unexpected-secret", b"\xffunexpected-secret"]
)
def test_core_observer_rejects_malformed_json_without_request_bytes(raw):
    with pytest.raises(AssertionError, match="Unapproved owned SDK JSON") as error:
        assert_synthetic_request(
            "POST",
            "api/sdk/integrations/get",
            b"",
            raw,
            {"request_name": "approved", "org_id": "org"},
        )
    assert "unexpected-secret" not in str(error.value)
