"""Terminal callback failures retain safe evidence without replaying workflows."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.execution.process_pool import (
    ExecutionInfo,
    ProcessHandle,
    ProcessPoolManager,
    ProcessState,
)


def _active_execution(execution_id: str, *, sync: bool = False) -> dict:
    return {
        "execution_id": execution_id,
        "workflow_id": "workflow-1",
        "workflow_name": "long_scan",
        "org_id": "org-1",
        "user_id": "user-1",
        "user_name": "Operator",
        "user_email": "operator@example.com",
        "sync": sync,
        "event": None,
    }


@pytest.mark.asyncio
async def test_original_callback_class_survives_synthetic_failure_without_message():
    callback = AsyncMock(side_effect=[TimeoutError("secret SQL payload")] * 3 + [None])
    pool = ProcessPoolManager(max_workers=1, on_result=callback)
    handle = ProcessHandle(
        id="test",
        process=MagicMock(),
        pid=123,
        state=ProcessState.BUSY,
        work_queue=MagicMock(),
        result_queue=MagicMock(),
        started_at=datetime.now(timezone.utc),
        current_execution=ExecutionInfo(
            execution_id="exec",
            started_at=datetime.now(timezone.utc),
            timeout_seconds=300,
            active_execution=_active_execution("exec"),
        ),
    )
    pool.processes[handle.id] = handle
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await pool._handle_result(
            handle, {"success": True, "result": {"business": "succeeded"}}
        )
    assert handle.result_callback_failed
    assert not handle.result_reported
    await pool._report_orphan(handle)
    terminal = callback.await_args_list[-1].args[0]
    assert terminal["error_type"] == "ResultPersistenceError"
    assert terminal["success"] is False
    assert terminal["logs"] is True
    assert terminal["execution_context"] == {
        "result_persistence_failure": {
            "exception_class": "TimeoutError",
            "phase": "terminal_callback",
            "attempt_count": 3,
        }
    }
    assert "secret" not in str(terminal)
    assert "business" not in str(terminal)
    assert callback.await_count == 4


@pytest.mark.asyncio
async def test_success_after_transient_failure_clears_diagnostics():
    callback = AsyncMock(side_effect=[TimeoutError("temporary"), None])
    pool = ProcessPoolManager(max_workers=1, on_result=callback)
    handle = MagicMock(result_callback_diagnostics=None)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        assert await pool._deliver_result({"success": True}, handle=handle)
    assert handle.result_callback_diagnostics is None
    assert callback.await_count == 2
