"""Persisted canonical-route results consumed through the real HTTP SDK client.

Seed terminal runs directly: these result-mapping cases need no paid LLM job.
The existing enqueue lifecycle test owns actual queue acceptance.
"""
from __future__ import annotations

import importlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from bifrost.client import (
    BifrostClient,
    _clear_engine_socket,
    _install_engine_socket,
    get_engine_socket_path,
)
from bifrost.models import AgentRunHandle
from sqlalchemy import delete
from src.core.security import mint_engine_token
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

mod = importlib.import_module("bifrost.agents")
pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize("transport", ["http", "socket"])
@pytest.mark.parametrize("output,status,error", [
    ({"text": "persisted answer"}, "completed", None),
    ({"answer": 42}, "completed", None),
    ({"text": "partial"}, "budget_exceeded", "Token budget exhausted"),
    ({"text": "partial"}, "cancelled", "Cancelled by caller"),
])
async def test_run_consumes_canonical_persisted_result(
    db_session, e2e_api_url, platform_admin, monkeypatch, output, status, error, transport,
):
    agent = Agent(
        id=uuid4(), name=f"SDK Compatibility {uuid4().hex[:8]}",
        system_prompt="test", channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
        is_active=True, knowledge_sources=[], system_tools=[],
        created_by="test@example.com",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    db_session.add(agent)
    await db_session.flush()
    run = AgentRun(
        id=uuid4(), agent_id=agent.id, trigger_type="api", status=status,
        output=output, error=error, caller_email="caller@example.com",
        caller_name="Original caller", created_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.commit()
    run_id = str(run.id)
    enqueue = AsyncMock(return_value=AgentRunHandle(run_id=run_id))
    monkeypatch.setattr(mod.agents, "enqueue", enqueue)
    server = None
    sdk = None
    execution = None
    attempt = None
    previous_socket = get_engine_socket_path()
    try:
        _clear_engine_socket()
        access_token = platform_admin.headers["Authorization"].removeprefix("Bearer ")
        api_url = e2e_api_url
        if transport == "socket":
            execution = Execution(id=uuid4(), workflow_name="sdk-compatibility-proof", executed_by_name="Engine")
            execution.attempt_tracking_version = "v1"
            db_session.add(execution)
            await db_session.flush()
            now = datetime.now(UTC)
            attempt = WorkflowExecutionAttempt(
                execution_id=execution.id, attempt_number=1, claim_token=uuid4(),
                status="claimed", phase="claim", published_at=now, claimed_at=now,
            )
            db_session.add(attempt)
            await db_session.commit()
            access_token, _ = mint_engine_token(
                execution_id=str(execution.id), attempt_token=str(attempt.claim_token),
                solution_id=None, global_repo_access=True, timeout_seconds=120,
            )
            server = WorkerSdkHttpServer()
            await server.start()
            assert server.socket_path is not None
            _install_engine_socket(server.socket_path)
            # A socket success cannot be a silent network fallback.
            api_url = "http://127.0.0.1:9"
        sdk = BifrostClient(api_url, access_token)
        monkeypatch.setattr(mod, "get_client", lambda: sdk)
        async with httpx.AsyncClient(base_url=e2e_api_url, headers=platform_admin.headers) as client:
            # Literal canonical route also records this test's API ownership.
            response = await client.get(f"/api/agent-runs/{run_id}")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["output"] == output
            assert body["caller_email"] == "caller@example.com"
            assert body["caller_name"] == "Original caller"
            if error:
                with pytest.raises(RuntimeError, match=error):
                    await mod.agents.run(agent.name)
            else:
                assert await mod.agents.run(agent.name) == output
            enqueue.assert_awaited_once()
            persisted = await mod.agents.get_run(run_id)
            assert persisted.id == run_id
            assert persisted.status == status
            assert persisted.caller_email == "caller@example.com"
    finally:
        if sdk is not None:
            await sdk.close()
        _clear_engine_socket()
        if previous_socket is not None:
            _install_engine_socket(previous_socket)
        if server is not None:
            await server.stop()
        if attempt is not None:
            await db_session.execute(delete(WorkflowExecutionAttempt).where(WorkflowExecutionAttempt.id == attempt.id))
        if execution is not None:
            await db_session.execute(delete(Execution).where(Execution.id == execution.id))
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.execute(delete(Agent).where(Agent.id == agent.id))
        await db_session.commit()
