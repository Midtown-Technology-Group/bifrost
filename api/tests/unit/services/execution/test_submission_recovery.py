from __future__ import annotations

from contextlib import asynccontextmanager
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
    embed_session_binding,
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
async def test_capability_issue_is_durable_only_after_redis_succeeds(
    monkeypatch,
) -> None:
    redis = SimpleNamespace(setex=AsyncMock())
    monkeypatch.setattr(
        submission_recovery,
        "get_redis",
        lambda: _RedisContext(redis),
    )
    execution_id = uuid4()
    execution = _execution(
        id=execution_id,
        embed_session_binding=embed_session_binding("session-a"),
        embed_capability_issued=False,
    )
    db = SimpleNamespace(
        execute=AsyncMock(),
        get=AsyncMock(return_value=execution),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def db_context():
        yield db

    monkeypatch.setattr("src.core.database.get_db_context", db_context)

    await submission_recovery.issue_execution_embed_capability(
        "session-a",
        execution_id,
    )

    redis.setex.assert_awaited_once()
    db.get.assert_awaited_once()
    assert execution.embed_capability_issued is True
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_capability_issue_does_not_release_publication_after_redis_failure(
    monkeypatch,
) -> None:
    redis = SimpleNamespace(setex=AsyncMock(side_effect=RuntimeError("redis down")))
    monkeypatch.setattr(
        submission_recovery,
        "get_redis",
        lambda: _RedisContext(redis),
    )
    execution = _execution(
        embed_session_binding=embed_session_binding("session-a"),
        embed_capability_issued=False,
    )
    db = SimpleNamespace(
        execute=AsyncMock(),
        get=AsyncMock(return_value=execution),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def db_context():
        yield db

    monkeypatch.setattr("src.core.database.get_db_context", db_context)

    with pytest.raises(RuntimeError, match="redis down"):
        await submission_recovery.issue_execution_embed_capability(
            "session-a",
            execution.id,
        )

    assert execution.embed_capability_issued is False
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_capability_issue_validates_durable_owner_before_redis(
    monkeypatch,
) -> None:
    redis = SimpleNamespace(setex=AsyncMock())
    monkeypatch.setattr(
        submission_recovery,
        "get_redis",
        lambda: _RedisContext(redis),
    )
    execution = _execution(
        embed_session_binding=embed_session_binding("session-a"),
        embed_capability_issued=False,
    )
    db = SimpleNamespace(
        execute=AsyncMock(),
        get=AsyncMock(return_value=execution),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def db_context():
        yield db

    monkeypatch.setattr("src.core.database.get_db_context", db_context)

    with pytest.raises(EmbedExecutionRecoveryDeniedError, match="Access denied"):
        await submission_recovery.issue_execution_embed_capability(
            "session-b",
            execution.id,
        )

    redis.setex.assert_not_awaited()
    assert execution.embed_capability_issued is False
    db.commit.assert_not_awaited()


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


def test_durable_embed_binding_is_one_way() -> None:
    binding = embed_session_binding("session-a")

    assert binding.startswith("sha256:")
    assert "session-a" not in binding
    assert len(binding) == 71


@pytest.mark.asyncio
async def test_recovery_remints_capability_from_matching_durable_owner(
    monkeypatch,
) -> None:
    existing = _execution(embed_session_binding=embed_session_binding("session-a"))
    issue_capability = AsyncMock()
    monkeypatch.setattr(
        submission_recovery,
        "issue_execution_embed_capability",
        issue_capability,
    )

    result = await recover_execution_submission(
        _Db(existing),
        execution_id=existing.id,
        workflow_id=existing.workflow_id,
        parameters=existing.parameters,
        executed_by=existing.executed_by,
        organization_id=existing.organization_id,
        form_id=existing.form_id,
        embed_session_jti="session-a",
    )

    assert result is not None
    issue_capability.assert_awaited_once_with("session-a", existing.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("session_jti", [None, "session-b"])
async def test_recovery_rejects_any_non_owner_of_durable_embed_execution(
    monkeypatch,
    session_jti,
) -> None:
    existing = _execution(embed_session_binding=embed_session_binding("session-a"))
    issue_capability = AsyncMock()
    monkeypatch.setattr(
        submission_recovery,
        "issue_execution_embed_capability",
        issue_capability,
    )

    with pytest.raises(EmbedExecutionRecoveryDeniedError, match="Access denied"):
        await recover_execution_submission(
            _Db(existing),
            execution_id=existing.id,
            workflow_id=existing.workflow_id,
            parameters=existing.parameters,
            executed_by=existing.executed_by,
            organization_id=existing.organization_id,
            form_id=existing.form_id,
            embed_session_jti=session_jti,
        )

    issue_capability.assert_not_awaited()


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
