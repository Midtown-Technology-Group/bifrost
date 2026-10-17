from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.models.enums import ExecutionStatus
from src.services.execution import submission_recovery
from src.services.execution.submission_recovery import (
    EmbedExecutionRecoveryDeniedError,
    ExecutionSubmissionConflictError,
    assert_execution_bound_to_embed_session,
    bind_execution_to_embed_session,
    recover_execution_submission,
)


class _Db:
    def __init__(self, execution):
        self.execution = execution
        self.requested_id = None

    async def get(self, _model, execution_id):
        self.requested_id = execution_id
        return self.execution


def _execution(**overrides):
    data = {
        "id": uuid4(),
        "workflow_id": uuid4(),
        "workflow_name": "reconcile",
        "parameters": {"ring": 3},
        "executed_by": uuid4(),
        "organization_id": uuid4(),
        "form_id": None,
        "status": ExecutionStatus.SUCCESS,
        "result": {"ok": True},
        "error_message": None,
        "duration_ms": 25,
        "started_at": None,
        "completed_at": None,
        "scheduled_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class _RedisContext:
    def __init__(self, redis):
        self.redis = redis

    async def __aenter__(self):
        return self.redis

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_binds_execution_to_exact_embed_session(monkeypatch) -> None:
    redis = SimpleNamespace(setex=AsyncMock())
    monkeypatch.setattr(
        submission_recovery,
        "get_redis",
        lambda: _RedisContext(redis),
    )
    execution_id = uuid4()

    await bind_execution_to_embed_session("session-a", execution_id)

    redis.setex.assert_awaited_once_with(
        f"bifrost:embed:exec:session-a:{execution_id}",
        86400,
        "1",
    )


@pytest.mark.asyncio
async def test_recovery_requires_exact_embed_session_binding(monkeypatch) -> None:
    redis = SimpleNamespace(exists=AsyncMock(side_effect=[1, 0]))
    monkeypatch.setattr(
        submission_recovery,
        "get_redis",
        lambda: _RedisContext(redis),
    )
    execution_id = uuid4()

    await assert_execution_bound_to_embed_session("session-a", execution_id)
    with pytest.raises(
        EmbedExecutionRecoveryDeniedError,
        match="Access denied",
    ):
        await assert_execution_bound_to_embed_session("session-b", execution_id)

    assert [call.args[0] for call in redis.exists.await_args_list] == [
        f"bifrost:embed:exec:session-a:{execution_id}",
        f"bifrost:embed:exec:session-b:{execution_id}",
    ]


@pytest.mark.asyncio
async def test_returns_none_for_unused_submission_identity() -> None:
    execution_id = uuid4()
    db = _Db(None)

    result = await recover_execution_submission(
        db,
        execution_id=execution_id,
        workflow_id=uuid4(),
        parameters={},
        executed_by=uuid4(),
        organization_id=None,
        form_id=None,
    )

    assert result is None
    assert db.requested_id == execution_id


@pytest.mark.asyncio
async def test_returns_matching_persisted_execution() -> None:
    existing = _execution()

    result = await recover_execution_submission(
        _Db(existing),
        execution_id=existing.id,
        workflow_id=existing.workflow_id,
        parameters=existing.parameters,
        executed_by=existing.executed_by,
        organization_id=existing.organization_id,
        form_id=existing.form_id,
    )

    assert result is not None
    assert result.execution_id == str(existing.id)
    assert result.workflow_id == str(existing.workflow_id)
    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == {"ok": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("workflow_id", uuid4()),
        ("parameters", {"ring": 4}),
        ("executed_by", uuid4()),
        ("organization_id", uuid4()),
        ("form_id", uuid4()),
    ],
)
async def test_rejects_reuse_for_different_work(field, replacement) -> None:
    existing = _execution()
    supplied = {
        "workflow_id": existing.workflow_id,
        "parameters": existing.parameters,
        "executed_by": existing.executed_by,
        "organization_id": existing.organization_id,
        "form_id": existing.form_id,
    }
    supplied[field] = replacement
    db = _Db(existing)

    with pytest.raises(ExecutionSubmissionConflictError, match="different work"):
        await recover_execution_submission(
            db,
            execution_id=existing.id,
            **supplied,
        )
