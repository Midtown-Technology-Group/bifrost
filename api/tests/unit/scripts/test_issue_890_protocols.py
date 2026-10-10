"""Capacity probes retain tenant/result checks and count uncertain work once."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scripts import issue_890_load as load


def response(request, payload, status=200):
    return httpx.Response(status, json=payload, request=request)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "completed", "failed", "wrong-result", "pending", "timeout"])
async def test_workflow_polling_preserves_original_execution_and_validates_result(monkeypatch, case):
    calls = []
    def transport(request):
        calls.append(request)
        if request.method == "POST":
            status = "Pending" if case in {"pending", "timeout"} else "Failed" if case == "failed" else "Completed" if case == "completed" else "Success"
            return response(request, {"execution_id": "original", "status": status,
                                      "result": {"value": 7 if case != "wrong-result" else 99, "ok": True}})
        return response(request, {"execution_id": "original", "status": "Success", "result": {"value": 7, "ok": True}})
    monkeypatch.setattr(load.asyncio, "sleep", AsyncMock())
    if case == "timeout":
        monkeypatch.setattr(load, "time", SimpleNamespace(monotonic=MagicMock(side_effect=[0, 121])))
    async with httpx.AsyncClient(base_url="https://isolated.invalid", transport=httpx.MockTransport(transport)) as client:
        if case in {"failed", "wrong-result", "timeout"}:
            with pytest.raises(TimeoutError if case == "timeout" else ValueError):
                await load._run_workflow(client, "workflow", {"fixture": "admin"}, "workflow", "org", 7)
        else:
            assert await load._run_workflow(client, "workflow", {}, "workflow", "org", 7) == "original"
    assert sum(r.method == "POST" for r in calls) == 1
    assert all(r.url.path == "/api/executions/original" for r in calls if r.method == "GET")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,valid", [("api-read", True), ("api-read", False), ("agent", True), ("agent", False), ("external-http", True)])
async def test_request_modes_require_exact_scope_tool_loop_and_fixture_result(scenario, valid):
    calls = []
    def transport(request):
        calls.append(request)
        if scenario == "api-read":
            payload = {"organization_id": "org" if valid else "other"}
        elif scenario == "agent":
            payload = {"status": "completed", "iterations_used": 2 if valid else 1}
        else:
            payload = {"status": "Success", "execution_id": "original", "result": {"value": 1, "source": "fixture"}}
        return response(request, payload)
    async with httpx.AsyncClient(base_url="https://isolated.invalid", transport=httpx.MockTransport(transport)) as client:
        if valid:
            await load._run_request(client, scenario, {}, {}, "target", "org", 1)
        else:
            with pytest.raises(ValueError):
                await load._run_request(client, scenario, {}, {}, "target", "org", 1)
    assert len(calls) == 1
    if scenario == "external-http":
        import json
        body = json.loads(calls[0].content)
        assert body["input_data"] == {"value": 1, "delay_ms": 50}
        assert body["org_id"] == "org"


@pytest.mark.asyncio
async def test_attempt_sampling_keeps_successful_history_and_reports_read_gaps():
    def transport(request):
        if request.url.path.endswith("missing"):
            return response(request, {}, 503)
        return response(request, {"attempt_history": {"attempts": [
            {"status": "failed"},
            {"status": "succeeded", "created_at": "2026-10-07T00:00:00+00:00", "published_at": "2026-10-07T00:00:01+00:00"},
        ]}})
    async with httpx.AsyncClient(base_url="https://isolated.invalid", transport=httpx.MockTransport(transport)) as client:
        assert await load._sample_attempt_stages(client, {}, []) == ([], 0)
        samples, errors = await load._sample_attempt_stages(client, {}, ["observed", "missing"])
    assert samples == [{"dispatch": 1.0}]
    assert errors == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_capacity_level_counts_each_request_once_and_never_replays_failed_dispatch(monkeypatch, failed):
    dispatched = []
    def transport(request):
        if request.url.path == "/api/platform/workers":
            return response(request, {"pools": []})
        if request.method == "POST":
            dispatched.append(request)
            return response(request, {}, 503) if failed else response(request, {
                "status": "Success", "execution_id": "original", "result": {"value": len(dispatched) - 1, "ok": True},
            })
        return response(request, {"attempt_history": {"attempts": []}})
    original = httpx.AsyncClient
    monkeypatch.setattr(load.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(transport)))
    result = await load.run_level("https://isolated.invalid", "workflow", {}, {}, "target", "org", 1, 4)
    assert len(dispatched) == 4
    assert result["requested"] == 4
    assert result["completed"] == (0 if failed else 4)
    assert result["failed"] == (4 if failed else 0)


@pytest.mark.asyncio
async def test_pool_sampling_records_transport_gap_instead_of_capacity_claim():
    stop = asyncio.Event()
    samples = []
    def transport(request):
        stop.set()
        return response(request, {}, 503)
    async with httpx.AsyncClient(base_url="https://isolated.invalid", transport=httpx.MockTransport(transport)) as client:
        await load._sample_pool(client, {}, samples, stop)
    assert samples == [{"error": "HTTPStatusError"}]


def test_empty_and_incomplete_pool_arithmetic_does_not_invent_capacity():
    assert load.percentile([], 0.9) == 0
    result = load.summarize_fresh_pools({"pools": [{"last_heartbeat": "invalid"}, {
        "last_heartbeat": datetime.now(UTC).isoformat(), "busy_count": 1,
    }]}, datetime.now(UTC))
    assert result["fresh_pools"] == 1
    assert result["stale_pools"] == 1
    assert result["capacity"] is None
    assert result["available"] is None
