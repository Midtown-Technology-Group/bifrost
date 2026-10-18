from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.jobs.workflow_canary import (
    canary_context,
    canary_queue_name,
    require_successful_canary_result,
)


def test_canary_queue_requires_isolated_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIFROST_WORKFLOW_QUEUE_NAME", "workflow-executions")
    with pytest.raises(ValueError, match="isolated"):
        canary_queue_name()


def test_canary_queue_accepts_dedicated_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BIFROST_WORKFLOW_QUEUE_NAME", "workflow-executions-deployment-canary"
    )
    assert canary_queue_name() == "workflow-executions-deployment-canary"


def test_canary_result_requires_expected_payload() -> None:
    with pytest.raises(RuntimeError, match="failed"):
        require_successful_canary_result(
            {"status": "Success", "result": {"canary": "wrong"}}
        )


def test_canary_result_accepts_expected_payload() -> None:
    require_successful_canary_result(
        {"status": "Success", "result": {"canary": "ok"}}
    )


def test_canary_context_uses_persisted_user_uuid() -> None:
    user_id = uuid4()
    context = canary_context(
        SimpleNamespace(
            id=user_id,
            name="Production Operator",
            email="operator@example.com",
        )
    )

    assert UUID(context.user_id) == user_id
    assert context.name == "Production Operator"
    assert context.email == "operator@example.com"
    assert context.org_id is None
    assert context.is_platform_admin is True
    assert context.artifact_workspace_id is None
    assert UUID(context.execution_id)


@pytest.mark.asyncio
async def test_canary_dispatches_with_context_identity_and_lineage(monkeypatch):
    from unittest.mock import AsyncMock
    from shared.run_lineage import person_lineage
    from src.jobs import workflow_canary

    user_id = uuid4()
    context = canary_context(SimpleNamespace(id=user_id, name="Canary", email="canary@example.com"))
    enqueue = AsyncMock(return_value=context.execution_id)
    monkeypatch.setenv("BIFROST_WORKFLOW_QUEUE_NAME", "workflow-verification-canary")
    monkeypatch.setattr(workflow_canary, "init_db", AsyncMock())
    monkeypatch.setattr(workflow_canary, "close_db", AsyncMock())
    monkeypatch.setattr(workflow_canary, "resolve_canary_context", AsyncMock(return_value=context))
    monkeypatch.setattr(workflow_canary, "poison_depth", AsyncMock(return_value=0))
    monkeypatch.setattr(workflow_canary, "enqueue_code_execution", enqueue)
    monkeypatch.setattr(workflow_canary.rabbitmq, "close", AsyncMock())
    redis = SimpleNamespace(wait_for_result=AsyncMock(return_value={"status": "Success", "result": {"canary": "ok"}}))
    monkeypatch.setattr(workflow_canary, "get_redis_client", lambda: redis)

    await workflow_canary.run_canary()

    assert enqueue.await_args.args == (context,)
    assert enqueue.await_args.kwargs["execution_id"] == context.execution_id
    assert enqueue.await_args.kwargs["lineage"] == person_lineage(user_id)
