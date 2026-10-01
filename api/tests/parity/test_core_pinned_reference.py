"""R1 Python pinned A; real Docker/PostgreSQL installation and source/SDK HTTP."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
import redis.asyncio as redis
from sqlalchemy import delete
from sqlalchemy.exc import DBAPIError
from src.models.orm.config import Config
from src.models.orm.integrations import (
    Integration,
    IntegrationConfigSchema,
    IntegrationMapping,
)
from src.models.orm.solution_deployments import SolutionDeployment

from tests.parity.core.adapter import ReferenceAdapter
from tests.parity.core.environment import SOURCE, SOURCE_SHA256
from tests.parity.core.pinned import (
    SOURCE_COMMIT,
    SOURCE_PATH,
    PinnedCapture,
    PinnedEnvironment,
    assert_source_request,
    assert_source_response,
)
from tests.parity.core.pinned_profile import PinnedProfile, assert_pinned_parity
from tests.parity.core.scenarios import (
    assert_readiness,
    execute_readiness,
    expected_result,
)
from tests.parity.core.sdk_observer import PREFIX, assert_synthetic_request

pytestmark = pytest.mark.e2e


@pytest.fixture
def pinned_environment(async_engine):
    @asynccontextmanager
    async def create(*, activate=True):
        environment = PinnedEnvironment(async_engine)
        try:
            await environment.seed()
            async with PinnedCapture(
                environment, os.environ["BIFROST_REDIS_URL"]
            ) as capture:
                environment.capture = capture
                if activate:
                    await environment.install()
                else:
                    await environment.stage()
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


def profile_for(env):
    return PinnedProfile({str(env.ids["solution"]): env.recipe})


def assert_pinned_ready(captured, env):
    assert_readiness(captured, env, expected_result(env))
    execution = captured.observation.database["executions"][0]
    assert execution["runtime_mode"] == "deployment-v1"
    assert execution["solution_deployment_id"] == str(env.ids["deployment"])
    assert execution["runtime_evidence"]["git_commit_sha"] == SOURCE_COMMIT
    sources = captured.transport.source_requests
    assert sources, (
        "No actual owned cold-source fetch observed; do not fabricate source transport"
    )
    for request in sources:
        assert request["upstream_status"] == request["status"] == 200
        assert request["response"]["content"].encode() == SOURCE.read_bytes()
        assert request["response"]["hash"] == SOURCE_SHA256
    requests = captured.transport.sdk_requests
    assert requests[0]["request"]["solution"] == str(env.ids["solution"])
    assert "solution" not in requests[1]["request"]
    for request in [*requests, *sources]:
        claims = request["authorization"]["claims"]
        assert claims["engine_solution_id"] == str(env.ids["solution"])
        assert claims["engine_global_repo_access"] is False
        assert claims["delegated_user_id"] == str(env.ids["user"])
        assert claims["org_id"] == str(env.ids["org"])
        assert (
            claims["delegated_is_superuser"] is False
            and claims["delegated_is_provider_org"] is False
        )
    profile = profile_for(env)
    profile.canonicalize(
        [*[step.observation for step in env.receipts], captured.observation],
        env.bindings,
    )
    profile.transport(captured, env.bindings)


async def test_core_pinned_python_readiness_and_real_source(pinned_environment):
    async with pinned_environment() as (env, adapter):
        captured = await execute_readiness(adapter, env, before=env.before)
        assert_pinned_ready(captured, env)
        profile = profile_for(env)
        report = {
            "schema": "bifrost.core-pinned-reference/v1",
            "proof": "Python pinned A with real source/SDK; no Rust/model/vendor acceptance",
            "source_commit": SOURCE_COMMIT,
            "source_sha256": SOURCE_SHA256,
            "observations": profile.canonicalize(
                [*[step.observation for step in env.receipts], captured.observation],
                env.bindings,
            ),
            "transport": profile.transport(captured, env.bindings),
            "measurements": {
                "before": captured.observation.before.isoformat(),
                "after": captured.observation.after.isoformat(),
                **{
                    key: captured.observation.database["executions"][0][key]
                    for key in (
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
        destination = Path("/tmp/bifrost/core-pinned-reference-readiness.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n")


async def test_core_pinned_cleanup_retains_fixture_and_history_guard(
    pinned_environment,
):
    async with pinned_environment() as (env, adapter):
        captured = await execute_readiness(adapter, env, before=env.before)
        assert_pinned_ready(captured, env)
        assert env.capture is not None
        capture = env.capture
        before = await capture.snapshot()

    # Snapshot opens fresh DB sessions; it does not use the closed capture Redis.
    assert env.client.is_closed and adapter.client.is_closed
    assert await capture.snapshot() == before
    await env.verify_storage()
    async with env.sessions() as db:
        for model, identity in (
            (Integration, env.ids["integration"]),
            (IntegrationConfigSchema, env.ids["schema"]),
            (IntegrationMapping, env.ids["mapping"]),
            (Config, env.ids["config"]),
        ):
            assert await db.get(model, identity) is not None
        # Use the ordinary test connection and exact owned row. The existing
        # database guard must still reject deletion; roll back its transaction.
        with pytest.raises(
            DBAPIError, match="SolutionDeployment history cannot be deleted"
        ):
            await db.execute(
                delete(SolutionDeployment).where(
                    SolutionDeployment.id == env.ids["deployment"],
                    SolutionDeployment.solution_id == env.ids["solution"],
                )
            )
        await db.rollback()
    assert await capture.snapshot() == before
    async with redis.from_url(os.environ["BIFROST_REDIS_URL"]) as observer:
        for execution in env.executions:
            assert not await observer.exists(
                f"{PREFIX}{execution}:owner",
                f"{PREFIX}{execution}:requests",
                f"{PREFIX}{execution}:sources",
            )


async def test_core_pinned_unsettled_cleanup_retains_observer_evidence(
    pinned_environment,
):
    with pytest.raises(AssertionError, match="Owned execution authority missing"):
        async with pinned_environment(activate=False) as (env, adapter):
            execution = await env.allocate_execution("unsubmitted")
            assert env.capture is not None
            capture = env.capture
            before = await capture.snapshot()
    assert env.client.is_closed and adapter.client.is_closed
    assert await capture.snapshot() == before
    await env.verify_storage()
    async with redis.from_url(os.environ["BIFROST_REDIS_URL"]) as observer:
        assert await observer.exists(f"{PREFIX}{execution}:owner")


async def test_core_pinned_independent_python_installations(pinned_environment):
    async with (
        pinned_environment() as (left_env, left_adapter),
        pinned_environment() as (right_env, right_adapter),
    ):
        left = await execute_readiness(left_adapter, left_env, before=left_env.before)
        right = await execute_readiness(
            right_adapter, right_env, before=right_env.before
        )
        assert_pinned_ready(left, left_env)
        assert_pinned_ready(right, right_env)
        assert left_env.ids["solution"] != right_env.ids["solution"]
        assert left_env.ids["deployment"] != right_env.ids["deployment"]
        assert_pinned_parity(
            [*left_env.receipts, left],
            [*right_env.receipts, right],
            left_env,
            right_env,
        )


async def test_core_pinned_foreign_tenant_override_denied(pinned_environment):
    async with pinned_environment() as (env, adapter):
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
        for table in (
            "executions",
            "workflow_execution_attempts",
            "execution_attempts",
            "work_deliveries",
        ):
            assert not captured.observation.database[table]
        assert (
            not captured.observation.events
            and not captured.transport.sdk_requests
            and not captured.transport.source_requests
        )
        await env.assert_installation_state(active=True)


async def test_core_pinned_stale_evidence_cannot_activate(pinned_environment):
    async with pinned_environment(activate=False) as (env, _):
        before = await env.capture.snapshot()
        rejected = await env.activate("sha256:" + "0" * 64)
        assert rejected.observation.status == 409
        assert rejected.observation.body == {
            "detail": "initial workflow preflight evidence changed"
        }
        assert rejected.observation.database == before
        assert (
            not rejected.observation.events
            and not rejected.transport.sdk_requests
            and not rejected.transport.source_requests
        )
        await env.assert_installation_state(active=False)
        accepted = await env.activate()
        assert accepted.observation.status == 200
        await env.assert_installation_state(active=True)


async def test_core_pinned_owned_runtime_source_drift_rejected(pinned_environment):
    async with pinned_environment(activate=False) as (env, _):
        before = await env.capture.snapshot()
        await env.mutate_runtime_source()
        rejected = await env.installation_request(
            "drift-preflight", f"{env.base}/preflight", {"reviewed_recipe": env.recipe}
        )
        assert rejected.observation.status == 422
        assert rejected.observation.body == {
            "detail": "source archive or runtime bytes differ from reviewed evidence"
        }
        assert rejected.observation.database == before
        assert (
            not rejected.observation.events
            and not rejected.transport.sdk_requests
            and not rejected.transport.source_requests
        )
        await env.assert_installation_state(active=False)


def source_registry():
    solution, deployment = str(uuid4()), str(uuid4())
    return {
        "solution_id": solution,
        "deployment_id": deployment,
        "source": {
            "path": f"_solutions/{solution}/{deployment}/{SOURCE_PATH}",
            "sha256": SOURCE_SHA256,
        },
    }


@pytest.mark.parametrize("plane", ["method", "path", "query", "body", "pin", "foreign"])
def test_core_pinned_source_request_rejects_unowned_vectors(plane):
    registry = source_registry()
    method, path, query, raw = (
        "GET",
        "api/sdk/modules/" + registry["source"]["path"],
        b"",
        b"",
    )
    if plane == "method":
        method = "POST"
    elif plane == "path":
        path += "/unexpected"
    elif plane == "query":
        query = b"secret=unexpected"
    elif plane == "body":
        raw = b"unexpected-secret"
    elif plane == "pin":
        registry["source"]["sha256"] = "0" * 64
    else:
        registry["solution_id"] = str(uuid4())
    with pytest.raises(AssertionError) as error:
        assert_source_request(method, path, query, raw, registry)
    assert "unexpected-secret" not in str(error.value)
    assert "secret=unexpected" not in str(error.value)


@pytest.mark.parametrize(
    "plane", ["status", "content", "hash", "path", "extra", "generation"]
)
def test_core_pinned_source_response_rejects_unapproved_vectors(plane):
    registry = source_registry()
    body = {
        "content": SOURCE.read_text(),
        "hash": SOURCE_SHA256,
        "path": registry["source"]["path"],
    }
    status = 200
    if plane == "status":
        status = 302
    elif plane == "content":
        body["content"] += "\n# unexpected-secret"
    elif plane == "hash":
        body["hash"] = "0" * 64
    elif plane == "path":
        body["path"] += "/foreign"
    elif plane == "extra":
        body["secret"] = "unexpected-secret"
    else:
        body["generation"] = "unfenced-workspace"
    with pytest.raises(AssertionError) as error:
        assert_source_response(status, body, registry)
    assert "unexpected-secret" not in str(error.value)


def test_core_pinned_observer_exact_solution_extension_preserves_r0():
    registry = {
        "request_name": "synthetic",
        "org_id": str(uuid4()),
        "solution_id": str(uuid4()),
    }
    body = {
        "name": registry["request_name"],
        "scope": registry["org_id"],
        "solution": registry["solution_id"],
    }
    assert (
        assert_synthetic_request(
            "POST", "api/sdk/integrations/get", b"", json.dumps(body).encode(), registry
        )
        == body
    )
    r0 = {key: registry[key] for key in ("request_name", "org_id")}
    with pytest.raises(AssertionError, match="Unapproved owned SDK request body"):
        assert_synthetic_request(
            "POST", "api/sdk/integrations/get", b"", json.dumps(body).encode(), r0
        )
    changed = deepcopy(body)
    changed["solution"] = str(uuid4())
    with pytest.raises(AssertionError, match="Unapproved owned SDK request body"):
        assert_synthetic_request(
            "POST",
            "api/sdk/integrations/get",
            b"",
            json.dumps(changed).encode(),
            registry,
        )
    with pytest.raises(AssertionError, match="Unapproved owned SDK method"):
        assert_synthetic_request("GET", "api/sdk/modules/foreign", b"", b"", r0)


@pytest.mark.parametrize(
    "plane",
    [
        "source-path",
        "storage-path",
        "manifest-hash",
        "runtime-hash",
        "preflight-hash",
        "caller",
        "fence",
        "workflow-id",
    ],
)
async def test_core_pinned_profile_rejects_captured_mutants(pinned_environment, plane):
    # Profile self-tests mutate copies of a REAL captured pinned backend run.
    # Actual owned storage corruption and its HTTP rejection are separate above.
    async with pinned_environment() as (env, adapter):
        reference = await execute_readiness(adapter, env, before=env.before)
        assert_pinned_ready(reference, env)
        candidate = deepcopy(reference)
        deployment = candidate.observation.database["solution_deployments"][0]
        execution = candidate.observation.database["executions"][0]
        if plane == "source-path":
            candidate.transport.source_requests[0]["response"]["path"] += "/foreign"
        elif plane == "storage-path":
            execution["runtime_evidence"]["runtime_storage_prefix"] += "foreign/"
        elif plane == "manifest-hash":
            deployment["compiled_manifest_hash"] = "sha256:" + "0" * 64
        elif plane == "runtime-hash":
            execution["runtime_evidence_hash"] = "sha256:" + "0" * 64
        elif plane == "preflight-hash":
            deployment["validation_result"]["preflight_evidence_id"] = (
                "sha256:" + "0" * 64
            )
        elif plane == "caller":
            candidate.transport.source_requests[0]["authorization"]["claims"][
                "delegated_user_id"
            ] = str(env.ids["setup_user"])
        elif plane == "fence":
            candidate.transport.source_requests[0]["authorization"]["claims"][
                "engine_attempt_token_digest"
            ] = "0" * 64
        else:
            deployment["validation_result"]["workflow_ids"] = [
                str(env.ids["foreign_org"])
            ]
        with pytest.raises((AssertionError, ValueError)):
            assert_pinned_parity([reference], [candidate], env, env)
        # The raw reference retains the actual scoped evidence unchanged.
        assert (
            reference.observation.database["solution_deployments"][0][
                "compiled_manifest_hash"
            ]
            != "sha256:" + "0" * 64
        )
