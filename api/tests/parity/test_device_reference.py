"""Executable reference spec at real HTTP, committed DB and Redis seams."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.models.orm.device_jobs import DeviceJob
from src.models.orm.devices import Device
from tests.parity.compare import assert_parity, canonicalize
from tests.parity.harness import BackendUnavailable, DeviceAdapter, rust_adapter
from tests.parity.scenarios import ROUTES, expect, lifecycle, python_control, reclaim_and_loss, route_body

pytestmark = pytest.mark.e2e


async def test_python_reference_lifecycle(python_adapter, parity_environment):
    trace = await lifecycle(python_adapter, parity_environment)
    actual = canonicalize(trace, parity_environment.bindings)
    # Emit reviewable evidence; freeze a vector only after actual backend proof.
    Path("/tmp/bifrost/parity-lifecycle.json").write_text(json.dumps(actual, indent=2) + "\n")
    assert actual


@pytest.mark.parametrize("loss_reason", ["silence", "backstop"])
async def test_python_reference_reclaim_loss(python_adapter, parity_environment, loss_reason):
    trace = await reclaim_and_loss(python_adapter, parity_environment, loss_reason)
    assert canonicalize(trace, parity_environment.bindings)


async def test_independent_seeded_python_differential(independent_python_environment):
    async with independent_python_environment() as (left_env, left):
        reference = await lifecycle(left, left_env)
        async with independent_python_environment() as (right_env, right):
            assert set(left_env.bindings).isdisjoint(right_env.bindings)
            candidate = await lifecycle(right, right_env)
            assert_parity(reference, candidate, left_env.bindings, right_env.bindings)


@pytest.mark.parametrize("plane", ["status", "body", "database", "events"])
async def test_real_trace_comparator_detects_independent_drift(python_adapter, parity_environment, plane):
    reference = await lifecycle(python_adapter, parity_environment)
    candidate = deepcopy(reference)
    if plane == "status":
        candidate[0].status = 201
    elif plane == "body":
        candidate[0].body["cancel_requested"] = True
    elif plane == "database":
        candidate[6].database["jobs"][0]["log_sequence"] = 2
    else:
        candidate[6].events[0]["payload"]["entries"][0]["text"] = "delivery-drift"
    with pytest.raises(AssertionError, match=f"{plane} differs"):
        assert_parity(reference, candidate, parity_environment.bindings, parity_environment.bindings)


async def test_identity_mapping_preserves_opaque_payloads(python_adapter, parity_environment):
    trace = await lifecycle(python_adapter, parity_environment)
    opaque = str(parity_environment.ids["job"])
    trace[1].body["script_content"] = opaque
    trace[1].body["params"] = {"claim_token": opaque}
    trace[6].database["logs"][0]["text"] = opaque
    trace[6].database["jobs"][0]["result"] = opaque
    trace[6].events[0]["payload"]["entries"][0]["text"] = opaque
    observed = canonicalize(trace, parity_environment.bindings)
    assert observed[1]["body"]["script_content"] == opaque
    assert observed[1]["body"]["params"]["claim_token"] == opaque
    assert observed[6]["database"]["logs"][0]["text"] == opaque
    assert observed[6]["database"]["jobs"][0]["result"] == opaque
    assert observed[6]["events"][0]["payload"]["entries"][0]["text"] == opaque


async def test_capture_observes_wrong_channel_publication(python_adapter, parity_environment):
    env = parity_environment
    path, body = route_body(env, "heartbeat")
    reference = await python_adapter.request("event-capture", path, body, env.keys["device"])
    channel = f"bifrost:unexpected-channel:{env.ids['job']}"
    # Capture self-test only; this is neither an API publication nor Go proof.
    await python_adapter.capture.redis.publish(channel, json.dumps({"type": "capture-probe", "job_id": str(env.ids["job"])}))
    candidate = await python_adapter.request("event-capture", path, body, env.keys["device"])
    assert candidate.events == [{"channel": channel, "payload": {"type": "capture-probe", "job_id": str(env.ids["job"])}}]
    with pytest.raises(AssertionError, match="events differs"):
        assert_parity([reference], [candidate], env.bindings, env.bindings)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("credential,expected,code", [
    ("missing", 422, None), ("malformed", 401, "invalid_key"),
    ("wrong_secret", 401, "invalid_key"), ("unknown_id", 401, "invalid_key"),
    ("wrong_prefix", 401, "invalid_key"), ("uppercase_uuid", 401, "invalid_key"),
    ("disabled", 403, "device_disabled"), ("key_disabled", 401, "invalid_key"),
    ("disabled_and_key_disabled", 403, "device_disabled"),
])
async def test_auth_reference(python_adapter, parity_environment, route, credential, expected, code):
    env = parity_environment
    key = env.keys["device"]
    if credential == "missing":
        key = None
    elif credential == "malformed":
        key = "invalid-synthetic-format"
    elif credential == "wrong_secret":
        key = key[:-1] + ("A" if key[-1] != "A" else "B")
    elif credential == "unknown_id":
        key = key.replace(str(env.ids["device"]), str(env.ids["unknown_job"]))
    elif credential == "wrong_prefix":
        key = "bfck_" + key[5:]
    elif credential == "uppercase_uuid":
        key = key.replace(str(env.ids["device"]), str(env.ids["device"]).upper())
    elif credential == "disabled":
        await env.update(Device, "device", status="disabled")
    elif credential == "key_disabled":
        await env.update(Device, "device", api_key_enabled=False)
    else:
        await env.update(Device, "device", status="disabled", api_key_enabled=False)
    before = await python_adapter.capture.snapshot()
    path, body = route_body(env, route)
    observed = await python_adapter.request(f"auth-{route}-{credential}", path, body, key)
    expect(observed, expected, code)
    assert observed.database == before
    assert observed.events == []
    if credential == "missing":
        assert observed.body["detail"][0]["loc"] == ["header", "X-Bifrost-Key"]
    else:
        assert observed.body == {"error": {"code": code, "message": "device is disabled" if expected == 403 else "invalid device key", "retryable": False}}


@pytest.mark.parametrize("route", ["running", "logs", "result"])
@pytest.mark.parametrize("target", ["foreign_device", "unknown_job"])
async def test_ownership_precedes_fence(python_adapter, parity_environment, route, target):
    env = parity_environment
    path, body = route_body(env, route)
    key = env.keys["foreign_device"] if target == "foreign_device" else env.keys["device"]
    if target == "unknown_job":
        path = path.replace(str(env.ids["job"]), str(env.ids["unknown_job"]))
    observed = await python_adapter.request(f"scope-{route}-{target}", path, body, key)
    expect(observed, 404, "unknown_job")
    assert observed.body["error"]["message"] == "job not found"
    assert observed.database["jobs"][0]["status"] == "pending"
    touched = next(row for row in observed.database["devices"] if row["id"] == str(env.ids[target if target == "foreign_device" else "device"]))
    assert touched["last_seen_at"] is not None
    assert observed.events == []


VALIDATION = [
    ("heartbeat", {}, 422, None),
    ("heartbeat", {"agent_version": "a" * 65}, 422, None),
    ("heartbeat", {"agent_version": 1}, 422, None),
    ("running", {"claim_token": "invalid"}, 422, None),
    ("logs", {"entries": []}, 422, None),
    ("logs", {"entries": [{"seq": 0, "stream": "stdout", "text": "x"}]}, 422, None),
    ("logs", {"entries": [{"seq": 1, "stream": "other", "text": "x"}]}, 422, None),
    ("logs", {"entries": [{"seq": 1, "stream": "stdout", "text": "x" * 65537}]}, 422, None),
    ("logs", {"entries": [{"seq": 1, "stream": "stdout", "text": "x"}] * 513}, 422, None),
    ("logs", {"entries": [{"seq": 1, "stream": "stdout", "text": "x"}] * 2}, 422, "invalid_parameter"),
    ("logs", {"entries": [{"seq": index + 1, "stream": "stdout", "text": "x" * 65536} for index in range(17)]}, 413, "payload_too_large"),
    ("result", {"status": "lost"}, 422, None),
    ("result", {"output": "x" * 65537}, 422, None),
    ("result", {"error": "x" * 4097}, 422, None),
    ("result", {"duration_ms": -1}, 422, None),
]


@pytest.mark.parametrize("route,changes,status,code", VALIDATION, ids=[f"validation-{index}" for index in range(len(VALIDATION))])
async def test_validation_reference(python_adapter, parity_environment, route, changes, status, code):
    env = parity_environment
    path, body = route_body(env, route)
    if route == "heartbeat" and not changes:
        body = {}
    else:
        body.update(changes)
    before = await python_adapter.capture.snapshot()
    observed = await python_adapter.request("validation", path, body, env.keys["device"])
    expect(observed, status, code)
    assert observed.events == []
    if code:
        assert observed.database["devices"][0]["last_seen_at"] is not None
    else:
        assert observed.database == before
        assert observed.body["detail"]
        assert all(error["loc"][0] == "body" for error in observed.body["detail"])
    assert observed.database["jobs"] == before["jobs"]
    assert observed.database["logs"] == []


@pytest.mark.parametrize("state", ["pending", "claimed", "running"])
async def test_python_cancel_coexistence(python_adapter, parity_environment, state):
    env = parity_environment
    token = str(env.ids["wrong_token"])
    await env.update(DeviceJob, "job", status=state, claim_token=env.ids["wrong_token"], agent_session_id=env.ids["session"])
    cancelled = await python_control(python_adapter, env, "cancel")
    job = cancelled.database["jobs"][0]
    assert job["status"] == ("running" if state == "running" else "cancelled")
    assert job["cancel_requested_at"] is not None
    path, body = route_body(env, "heartbeat")
    observed = await python_adapter.request("heartbeat-after-cancel", path, body, env.keys["device"])
    expect(observed, 200)
    assert observed.body["cancel_requested"] is (state == "running")
    if state == "running":
        wrong = await python_adapter.request("non-owner-cancel-hidden", path, {"agent_session_id": str(env.ids["other_session"])}, env.keys["device"])
        assert wrong.body["cancel_requested"] is False
    path, body = route_body(env, "result", token)
    body["status"] = "cancelled"
    finished = await python_adapter.request("result-after-cancel", path, body, env.keys["device"])
    expect(finished, 200 if state == "running" else 409, None if state == "running" else "job_terminal")


async def test_shared_database_competing_adapters(python_adapter, parity_environment):
    # This exact sharing seam accepts a real Rust adapter when W2 exists.
    second = DeviceAdapter("python-second-writer", str(python_adapter.client.base_url), python_adapter.capture)
    env = parity_environment
    path, body = route_body(env, "claim")
    try:
        # Requests race; evidence is captured after both complete to avoid
        # competing readers on the shared Redis subscription.
        responses = await asyncio.gather(*[
            adapter.client.post(path, json=body, headers={"X-Bifrost-Key": env.keys["device"]})
            for adapter in (python_adapter, second)
        ])
        assert sorted(response.status_code for response in responses) == [200, 204]
        snapshot = await python_adapter.capture.snapshot()
        winner = next(response.json() for response in responses if response.status_code == 200)
        assert snapshot["jobs"][0]["claim_token"] == winner["claim_token"]
        assert await python_adapter.capture.drain_events() == []
    finally:
        await second.close()


def test_rust_backend_is_explicitly_unavailable():
    with pytest.raises(BackendUnavailable, match="unavailable in W0"):
        rust_adapter()


@pytest.mark.parametrize("age,status", [(55, 204), (65, 200)])
async def test_claim_lease_near_sixty_second_boundary(python_adapter, parity_environment, age, status):
    env = parity_environment
    claimed_at = datetime.now(timezone.utc) - timedelta(seconds=age)
    await env.update(DeviceJob, "job", status="claimed", claimed_at=claimed_at, claim_token=env.ids["wrong_token"], agent_session_id=env.ids["session"])
    path, body = route_body(env, "claim")
    observed = await python_adapter.request("lease-boundary", path, body, env.keys["device"])
    # Fail clearly if an unhealthy environment lets the fresh fixture cross
    # the boundary; elapsed time is evidence, never normalized or retried.
    if age == 55:
        assert (observed.after - claimed_at).total_seconds() < 60, "fixture crossed lease boundary during HTTP request"
    else:
        assert (observed.before - claimed_at).total_seconds() > 60
    expect(observed, status)
    token = observed.database["jobs"][0]["claim_token"]
    assert (token == str(env.ids["wrong_token"])) is (status == 204)
    assert observed.events == []


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "timeout", "cancelled"])
async def test_result_from_claimed_and_inclusive_limits(python_adapter, parity_environment, terminal):
    env = parity_environment
    path, body = route_body(env, "claim")
    claim = await python_adapter.request("claim", path, body, env.keys["device"])
    expect(claim, 200)
    token = claim.body["claim_token"]
    path, body = route_body(env, "logs", token)
    body["entries"] = [{"seq": str(index + 1), "stream": "stdout", "text": ""} for index in range(512)]
    body["entries"][0]["text"] = "x" * 65536
    logs = await python_adapter.request("log-inclusive-limits-and-coercion", path, body, env.keys["device"])
    expect(logs, 204)
    assert len(logs.database["logs"]) == 512
    assert logs.database["jobs"][0]["log_sequence"] == 512
    assert len(logs.events) == 1
    path, body = route_body(env, "result", token)
    body.update(status=terminal, output="x" * 65536, error="e" * 4096, exit_code="7", duration_ms="0")
    finished = await python_adapter.request("finish-claimed", path, body, env.keys["device"])
    expect(finished, 200)
    assert finished.database["jobs"][0]["status"] == terminal
    assert finished.database["jobs"][0]["exit_code"] == 7
    assert finished.database["jobs"][0]["result"] == "x" * 65536
