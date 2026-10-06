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
from bifrost.models import AgentRunHandle
from sqlalchemy import delete
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent

mod = importlib.import_module("bifrost.agents")
pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize("output,status,error", [
    ({"text": "persisted answer"}, "completed", None),
    ({"answer": 42}, "completed", None),
    ({"text": "partial"}, "budget_exceeded", "Token budget exhausted"),
    ({"text": "partial"}, "cancelled", "Cancelled by caller"),
])
async def test_run_consumes_canonical_persisted_result(
    db_session, e2e_api_url, platform_admin, monkeypatch, output, status, error,
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
    try:
        async with httpx.AsyncClient(base_url=e2e_api_url, headers=platform_admin.headers) as client:
            monkeypatch.setattr(mod, "get_client", lambda: client)
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
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.execute(delete(Agent).where(Agent.id == agent.id))
        await db_session.commit()
