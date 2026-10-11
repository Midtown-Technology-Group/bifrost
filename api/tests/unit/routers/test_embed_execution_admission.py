"""Concurrent embed admission must freeze one session before granting access."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.executions import WorkflowExecutionRequest, WorkflowExecutionResponse
from src.models.enums import ExecutionStatus
from src.routers import workflows, websocket
from src.services.execution.async_executor import _persist_execution_pin
from tests.unit.routers.test_workflows_register_validate_coverage import (
    _ctx, _Db, _dispatch_metadata, _exec_user, _workflow, _WorkflowRepo,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("different_input,redis_failure", [(False, False), (True, False), (False, True)])
async def test_two_sessions_miss_lookup_but_only_admitted_session_gets_access(monkeypatch, different_input, redis_failure):
    """Pause both lookups, then race the real locked pin and router readback."""
    workflow = _workflow()
    execution_id = uuid4()
    lock = asyncio.Lock()
    both_missed = asyncio.Event()
    misses = 0
    committed = {}
    grants = set()

    class Database:
        """Model the PostgreSQL transaction lock, visibility and rollback."""

        def __init__(self):
            self.locked = False
            self.pending = None

        async def execute(self, _statement, _parameters):
            await lock.acquire()
            self.locked = True

        async def get(self, _model, identity):
            return committed.get(identity)

        def add(self, row):
            self.pending = row

        async def flush(self):
            pass

        async def commit(self):
            committed[self.pending.id] = self.pending

    @asynccontextmanager
    async def database_context():
        db = Database()
        try:
            yield db
        finally:
            if db.locked:
                lock.release()

    async def miss_lookup(*_args, **_kwargs):
        nonlocal misses
        misses += 1
        if misses == 2:
            both_missed.set()
        await both_missed.wait()
        return None

    async def grant(jti, identity):
        if redis_failure:
            raise RuntimeError("capability store unavailable")
        grants.add((jti, identity))

    async def dispatch(*, context, workflow_id, input_data, **_kwargs):
        await _persist_execution_pin(
            context, context.execution_id, workflow_id, input_data, None,
            form_id=None, sync=False, api_key_id=None, file_path=None,
        )
        return WorkflowExecutionResponse(
            execution_id=context.execution_id, workflow_id=workflow_id,
            workflow_name=workflow.name, status=ExecutionStatus.PENDING,
        )

    redis = SimpleNamespace(exists=AsyncMock(side_effect=lambda key: any(
        key == f"bifrost:embed:exec:{jti}:{identity}" for jti, identity in grants
    )))

    @asynccontextmanager
    async def redis_context():
        yield redis

    monkeypatch.setattr("src.core.database.get_db_context", database_context)
    monkeypatch.setattr("src.core.cache.redis_client.get_redis", redis_context)
    monkeypatch.setattr("src.repositories.WorkflowRepository", lambda *_args, **_kwargs: _WorkflowRepo(workflow=workflow))
    monkeypatch.setattr("src.services.execution.submission_recovery.recover_execution_submission", miss_lookup)
    monkeypatch.setattr("src.services.execution.service.get_workflow_for_execution", AsyncMock(return_value=_dispatch_metadata(workflow)))
    monkeypatch.setattr("src.services.execution.service.run_workflow", dispatch)
    monkeypatch.setattr("src.core.embed_middleware.register_embed_execution", grant)
    monkeypatch.setattr("src.services.workspace_release_projection.acquire_runtime_admission_lock", AsyncMock())
    monkeypatch.setattr("src.services.solutions.deployment_runtime.pin_workflow_runtime", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.workspace_release_runtime.pin_workspace_runtime", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.execution.retry_policy.workflow_retry_policy_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr("src.services.execution.attempts.ensure_dispatch_attempt", AsyncMock())
    service_user_id, organization_id, app_id = uuid4(), uuid4(), uuid4()
    users = [_exec_user(
        user_id=service_user_id, organization_id=organization_id, app_id=app_id,
        embed=True, embed_kind="app", jti=f"session-{i}",
    ) for i in range(2)]

    results = await asyncio.gather(*(
        workflows.execute_workflow(
            WorkflowExecutionRequest(workflow_id=str(workflow.id), input_data={"value": i if different_input else 0}),
            _ctx(user), _Db(), user, execution_request_id=execution_id,
        ) for i, user in enumerate(users)
    ), return_exceptions=True)
    if redis_failure:
        assert all(isinstance(result, HTTPException) and result.status_code == 500 for result in results)
        assert not committed
        assert not grants
        return
    winners = [i for i, result in enumerate(results) if isinstance(result, WorkflowExecutionResponse)]
    assert len(winners) == 1, results
    winner = winners[0]
    loser = 1 - winner
    assert isinstance(results[loser], HTTPException)
    assert results[loser].status_code == 403
    assert grants == {(users[winner].jti, str(execution_id))}
    row = committed[execution_id]
    assert row.dispatch_evidence["request"]["embed_session_jti"] == users[winner].jti
    assert "embed_session_jti" not in row.dispatch_evidence["publish"]
    from src.core.embed_middleware import has_embed_execution_access

    assert await has_embed_execution_access(users[winner].jti, str(execution_id))
    assert not await has_embed_execution_access(users[loser].jti, str(execution_id))
    assert await websocket.can_access_execution(users[winner], str(execution_id))
    assert not await websocket.can_access_execution(users[loser], str(execution_id))
    # A leftover grant from a failed transaction must never authorize the loser.
    grants.add((users[loser].jti, str(execution_id)))
    assert not await has_embed_execution_access(users[loser].jti, str(execution_id))
    assert not await websocket.can_access_execution(users[loser], str(execution_id))
