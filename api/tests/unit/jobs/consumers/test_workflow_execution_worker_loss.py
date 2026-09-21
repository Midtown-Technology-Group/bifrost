"""Manager shutdown retries only an accepted, explicitly opted-in attempt."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.jobs.consumers import workflow_execution
from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.models.enums import ExecutionStatus


@pytest.fixture
def failure_case(monkeypatch):
    monkeypatch.setenv("BIFROST_WORKFLOW_EXECUTION_MAX_ATTEMPTS", "2")
    execution = SimpleNamespace(
        id=uuid4(),
        status=ExecutionStatus.RUNNING,
        retry_policy={
            "version": "execution-retry/v1",
            "enabled": True,
            "max_attempts": 2,
            "retry_on": ["worker_lost"],
        },
        started_at=object(),
        completed_at=object(),
        duration_ms=100,
        error_message="previous failure",
    )
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.scalar.side_effect = lambda *_args: execution
    order = []
    session.commit.side_effect = lambda: order.append("commit")
    with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
        consumer = WorkflowExecutionConsumer()
    consumer._redis_client = AsyncMock()
    consumer._load_completion_metadata = AsyncMock(return_value=({"sync": False}, False))
    consumer._lock_execution = AsyncMock()
    consumer._run_derived_step = AsyncMock()
    result = {
        "execution_id": str(execution.id),
        "attempt_token": str(uuid4()),
        "sync": False,
        "success": False,
        "error": "Worker manager stopped during execution",
        "error_type": "WorkerShutdownError",
        "duration_ms": 100,
    }
    with (
        patch("src.core.database.get_session_factory", return_value=lambda: session),
        patch("src.services.execution.attempts.finalize_attempt", new_callable=AsyncMock, return_value=True) as finalize,
        patch("src.services.execution.attempts.has_recorded_attempt", new_callable=AsyncMock, return_value=True),
        patch("src.services.execution.async_executor.republish_execution_from_dispatch", new_callable=AsyncMock) as republish,
        patch.object(workflow_execution, "update_execution", new_callable=AsyncMock, return_value=ExecutionStatus.FAILED) as update,
        patch("bifrost._sync.flush_pending_changes", new_callable=AsyncMock, return_value=0),
        patch("src.core.cache.cleanup_execution_cache", new_callable=AsyncMock),
    ):
        session.scalar.side_effect = [execution, 1]
        finalize.side_effect = lambda *_args, **_kwargs: order.append("finalize") or True
        republish.side_effect = lambda *_args, **_kwargs: order.append("republish")
        yield SimpleNamespace(
            consumer=consumer, execution=execution, result=result,
            session=session, finalize=finalize, republish=republish,
            update=update, order=order,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type,kind", [
    ("WorkerShutdownError", "worker_lost"),
    ("ProcessCrashError", "subprocess_crash"),
])
async def test_fenced_engine_failure_republishes_same_execution(failure_case, error_type, kind):
    case = failure_case
    case.result["error_type"] = error_type
    case.execution.retry_policy["retry_on"] = [kind]

    await case.consumer._process_failure(str(case.execution.id), case.result)

    assert case.order == ["finalize", "republish", "commit"]
    assert str(case.finalize.await_args.args[2]) == case.result["attempt_token"]
    assert case.finalize.await_args.kwargs["status"] == "worker_lost"
    case.republish.assert_awaited_once_with(case.execution, db=case.session)
    assert case.execution.status == ExecutionStatus.PENDING
    assert all(getattr(case.execution, key) is None for key in (
        "started_at", "completed_at", "duration_ms", "error_message",
    ))
    case.update.assert_not_awaited()
    case.consumer._run_derived_step.assert_not_awaited()
    case.consumer._redis_client.delete_pending_execution.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", [
    "disabled", "exhausted", "operator_ceiling", "wrong_kind", "cancelling",
    "cancelled_result", "tenant_error", "orphan", "stale_token", "terminal",
])
async def test_worker_loss_retry_preserves_no_replay_guards(failure_case, monkeypatch, guard):
    case = failure_case
    if guard == "disabled":
        case.execution.retry_policy["enabled"] = False
    elif guard == "exhausted":
        case.session.scalar.side_effect = [case.execution, 2]
    elif guard == "operator_ceiling":
        monkeypatch.setenv("BIFROST_WORKFLOW_EXECUTION_MAX_ATTEMPTS", "1")
    elif guard == "wrong_kind":
        case.execution.retry_policy["retry_on"] = ["subprocess_crash"]
    elif guard == "cancelling":
        case.execution.status = ExecutionStatus.CANCELLING
    elif guard in {"cancelled_result", "tenant_error", "orphan"}:
        case.result["error_type"] = {
            "cancelled_result": "CancelledError", "tenant_error": "ExecutionError",
            "orphan": "OrphanedExecution",
        }[guard]
    elif guard == "stale_token":
        case.finalize.side_effect = None
        case.finalize.return_value = False
    elif guard == "terminal":
        case.execution.status = ExecutionStatus.CANCELLED

    await case.consumer._process_failure(str(case.execution.id), case.result)

    case.republish.assert_not_awaited()
    assert case.execution.status != ExecutionStatus.PENDING
    if guard in {"stale_token", "terminal"}:
        case.session.rollback.assert_awaited_once()
        case.session.commit.assert_not_awaited()
        case.update.assert_not_awaited()
        case.consumer._run_derived_step.assert_not_awaited()
    else:
        case.update.assert_awaited_once()
        case.session.commit.assert_awaited_once()
    if guard in {"cancelling", "cancelled_result"}:
        assert case.finalize.await_args.kwargs["status"] == "cancelled"
    if guard == "terminal":
        case.finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_without_attempt_fence_cannot_republish(failure_case):
    case = failure_case
    del case.result["attempt_token"]
    with pytest.raises(RuntimeError, match="missing its attempt fence"):
        await case.consumer._process_failure(str(case.execution.id), case.result)
    case.republish.assert_not_awaited()
    case.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_republish_failure_does_not_commit_terminal_or_pending_execution(failure_case):
    case = failure_case
    case.republish.side_effect = RuntimeError("durable dispatch unavailable")
    with pytest.raises(RuntimeError, match="durable dispatch unavailable"):
        await case.consumer._process_failure(str(case.execution.id), case.result)
    case.session.commit.assert_not_awaited()
    case.update.assert_not_awaited()
    assert case.execution.status == ExecutionStatus.RUNNING
