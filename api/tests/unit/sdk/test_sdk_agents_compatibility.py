"""Historical run() contract over the accepted enqueue/poll lifecycle."""
from __future__ import annotations

import importlib
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from bifrost.client import BifrostAPIError
from bifrost.models import AgentRun, AgentRunHandle, AgentRunPending

mod = importlib.import_module("bifrost.agents")


def completed(output, *, status="completed", error=None):
    return AgentRun(
        id="accepted-run", agent_id="agent", trigger_type="api",
        created_at=datetime.now(UTC), status=status, output=output, error=error,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("output,schema,expected", [
    ({"text": "hello"}, None, {"text": "hello"}),
    ({"text": "hello", "extra": 1}, None, {"text": "hello", "extra": 1}),
    ("hello", None, "hello"),
    ({"answer": 42}, {"type": "object"}, {"answer": 42}),
    ('{"answer":42}', {"type": "object"}, {"answer": 42}),
    ("invalid JSON", {"type": "object"}, "invalid JSON"),
])
async def test_run_preserves_historical_outputs(monkeypatch, output, schema, expected):
    enqueue = AsyncMock(return_value=AgentRunHandle(run_id="accepted-run"))
    get_run = AsyncMock(return_value=completed(output))
    monkeypatch.setattr(mod.agents, "enqueue", enqueue)
    monkeypatch.setattr(mod.agents, "get_run", get_run)

    assert await mod.agents.run("Agent", {"ticket": 42}, output_schema=schema) == expected
    enqueue.assert_awaited_once_with("Agent", {"ticket": 42}, output_schema=schema)
    get_run.assert_awaited_once_with("accepted-run")


def test_run_default_wait_is_thirty_minutes():
    assert inspect.signature(mod.agents.run).parameters["timeout"].default == 1800


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "budget_exceeded", "timeout", "cancelled"])
async def test_run_rejects_error_bearing_partial_results(monkeypatch, status):
    monkeypatch.setattr(mod.agents, "enqueue", AsyncMock(return_value=AgentRunHandle(run_id="accepted-run")))
    monkeypatch.setattr(mod.agents, "get_run", AsyncMock(return_value=completed(
        {"text": "partial"}, status=status, error="Budget or execution failure",
    )))
    with pytest.raises(RuntimeError, match="Budget or execution failure"):
        await mod.agents.run("Agent")


@pytest.mark.asyncio
async def test_run_timeout_is_http_compatible_and_resumes_same_run(monkeypatch):
    enqueue = AsyncMock(return_value=AgentRunHandle(run_id="accepted-run"))
    get_run = AsyncMock(return_value=completed({"text": "finished"}))
    monkeypatch.setattr(mod.agents, "enqueue", enqueue)
    monkeypatch.setattr(mod.agents, "get_run", get_run)
    with pytest.raises(BifrostAPIError) as exc:
        await mod.agents.run("Agent", timeout=0)
    error = exc.value
    assert isinstance(error, httpx.HTTPStatusError)
    assert error.response.status_code == 504
    assert error.response.json() == {
        "run_id": "accepted-run", "reason": "wait_timeout", "last_known_status": None,
    }
    assert error.run_id == "accepted-run"
    assert error.reason == "wait_timeout"
    get_run.assert_not_awaited()
    assert await mod.agents.wait(error.run_id) == "finished"
    enqueue.assert_awaited_once()
    get_run.assert_awaited_once_with("accepted-run")


@pytest.mark.asyncio
async def test_explicit_wait_retains_pending_result(monkeypatch):
    get_run = AsyncMock()
    monkeypatch.setattr(mod.agents, "get_run", get_run)
    pending = await mod.agents.wait("accepted-run", timeout=0)
    assert isinstance(pending, AgentRunPending)
    assert pending.run_id == "accepted-run"
    get_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_timeout_preserves_observed_status_and_caller_context(monkeypatch):
    enqueue = AsyncMock(return_value=AgentRunHandle(run_id="accepted-run"))
    get_run = AsyncMock(return_value=completed(None, status="running"))
    monkeypatch.setattr(mod.agents, "enqueue", enqueue)
    monkeypatch.setattr(mod.agents, "get_run", get_run)
    # Deterministically cross the wait deadline after the first status read.
    ticks = iter([0.0, 0.0, 1.0, 1.0, 2.0])
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())
    context = SimpleNamespace(workflow_deadline=None, user_id="caller", org_id="org")
    token = mod._execution_context.set(context)
    try:
        with pytest.raises(mod.AgentRunWaitTimeout) as exc:
            await mod.agents.run("Agent", timeout=1)
        assert mod._execution_context.get() is context
        assert (context.user_id, context.org_id) == ("caller", "org")
    finally:
        mod._execution_context.reset(token)
    assert exc.value.last_known_status == "running"
    enqueue.assert_awaited_once()
    get_run.assert_awaited_once_with("accepted-run")


@pytest.mark.asyncio
async def test_status_request_timeout_does_not_reenqueue(monkeypatch):
    enqueue = AsyncMock(return_value=AgentRunHandle(run_id="accepted-run"))
    monkeypatch.setattr(mod.agents, "enqueue", enqueue)
    monkeypatch.setattr(mod.agents, "get_run", AsyncMock(side_effect=TimeoutError))
    with pytest.raises(mod.AgentRunWaitTimeout, match="accepted-run"):
        await mod.agents.run("Agent", timeout=10)
    enqueue.assert_awaited_once()
