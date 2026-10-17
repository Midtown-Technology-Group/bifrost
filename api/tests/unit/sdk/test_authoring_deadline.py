"""Cancellation must finish, and a caller resumes the accepted run identity."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_wait_cancels_inflight_result_read_and_resumes_same_run(monkeypatch):
    module = importlib.import_module("bifrost.agents")
    reading, cleaned = asyncio.Event(), asyncio.Event()

    async def stalled_read(run_id):
        assert run_id == "accepted-run"
        reading.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    enqueue = AsyncMock(return_value=SimpleNamespace(run_id="accepted-run"))
    get_run = AsyncMock(side_effect=stalled_read)
    monkeypatch.setattr(module.agents, "enqueue", enqueue)
    monkeypatch.setattr(module.agents, "get_run", get_run)
    # This timeout is the behavior under test. Events establish entry/cleanup;
    # there is no sleep-based assumption about when a request started.
    pending = await asyncio.wait_for(
        module.agents.run("fixture", timeout=0.03), timeout=1
    )
    assert reading.is_set() and cleaned.is_set()
    assert pending.run_id == "accepted-run"
    assert pending.reason == "wait_timeout"
    get_run.side_effect = None
    get_run.return_value = SimpleNamespace(
        status="completed", output={"text": "persisted result"}
    )
    assert await module.agents.wait(pending.run_id, timeout=1) == "persisted result"
    enqueue.assert_awaited_once()
    assert [call.args for call in get_run.await_args_list] == [
        ("accepted-run",),
        ("accepted-run",),
    ]


@pytest.mark.asyncio
async def test_polling_and_final_response_share_one_remaining_budget(monkeypatch):
    module = importlib.import_module("bifrost.agents")
    clock = [100.0]
    budgets = []
    cleaned = asyncio.Event()
    requests = []

    async def result_read(run_id):
        requests.append(run_id)
        if len(requests) == 1:
            clock[0] += 0.02  # status polling consumed 20 ms of the same budget
            return SimpleNamespace(status="running")
        try:
            await asyncio.Event().wait()  # final response body never arrives
        finally:
            cleaned.set()

    async def bounded(awaitable, *, timeout):
        budgets.append(timeout)
        return await asyncio.wait_for(awaitable, timeout=timeout)

    # Replace module references, never the global event loop's clock/sleep.
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(
            wait_for=bounded, sleep=asyncio.sleep, TimeoutError=asyncio.TimeoutError
        ),
    )
    monkeypatch.setattr(module, "_poll_interval", lambda _elapsed: 0)
    enqueue = AsyncMock(return_value=SimpleNamespace(run_id="accepted-run"))
    monkeypatch.setattr(module.agents, "enqueue", enqueue)
    monkeypatch.setattr(module.agents, "get_run", result_read)
    pending = await asyncio.wait_for(
        module.agents.run("fixture", timeout=0.03), timeout=1
    )
    assert budgets == pytest.approx([0.03, 0.01])
    assert cleaned.is_set(), "in-flight response cleanup must complete before returning"
    assert pending.run_id == "accepted-run" and pending.last_known_status == "running"
    assert requests == ["accepted-run", "accepted-run"]
    enqueue.assert_awaited_once()
