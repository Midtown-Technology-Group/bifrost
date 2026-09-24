"""Tests for the shared agent workflow-tool execution boundary."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from src.services.execution.agent_workflow_tools import (
    AgentWorkflowCaller,
    execute_agent_workflow_tool,
)


@pytest.mark.asyncio
async def test_execute_agent_workflow_tool_builds_canonical_agent_context():
    workflow_id = uuid4()
    organization_id = uuid4()
    response = MagicMock()
    caller = AgentWorkflowCaller(
        user_id=str(uuid4()),
        email="person@example.com",
        name="Person",
        organization_id=organization_id,
        is_platform_admin=True,
        agent_id=uuid4(),
    )

    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id=workflow_id, is_active=True, tags=[])

    @asynccontextmanager
    async def fake_db():
        yield db

    with (
        patch("src.core.database.get_db_context", fake_db),
        patch("src.services.execution.agent_helpers.agent_workflow_granted", new_callable=AsyncMock, return_value=True),
        patch(
            "src.services.execution.service.execute_tool",
            new_callable=AsyncMock,
            return_value=response,
        ) as execute_tool,
    ):
        result = await execute_agent_workflow_tool(
            workflow_id=workflow_id,
            workflow_name="ticket_lookup",
            parameters={"ticket_id": 42},
            caller=caller,
            execution_id="execution-1",
            artifact_workspace_id="workspace-1",
            sync=False,
        )

    assert result is response
    execute_tool.assert_awaited_once_with(
        workflow_id=str(workflow_id),
        workflow_name="ticket_lookup",
        parameters={"ticket_id": 42},
        user_id=caller.user_id,
        user_email=caller.email,
        user_name=caller.name,
        org_id=str(organization_id),
        is_platform_admin=True,
        is_agent=True,
        execution_id="execution-1",
        artifact_workspace_id="workspace-1",
        sync=False,
    )


@pytest.mark.asyncio
async def test_approval_gated_workflow_creates_proposal_without_execution():
    workflow_id, agent_id, run_id, org_id = uuid4(), uuid4(), uuid4(), uuid4()
    db = AsyncMock()
    db.add = MagicMock()
    db.get.return_value = SimpleNamespace(
        id=workflow_id,
        is_active=True,
        tags=["approval_required"],
    )

    @asynccontextmanager
    async def fake_db():
        yield db

    caller = AgentWorkflowCaller(
        user_id=str(uuid4()),
        email="jane@example.com",
        name="Jane",
        organization_id=org_id,
        agent_id=agent_id,
        agent_run_id=run_id,
    )
    with (
        patch("src.core.database.get_db_context", fake_db),
        patch("src.services.execution.agent_helpers.agent_workflow_granted", new_callable=AsyncMock, return_value=True),
        patch("src.services.audit.emit_audit", new_callable=AsyncMock) as audit,
        patch(
            "src.services.execution.service.execute_tool", new_callable=AsyncMock
        ) as execute,
    ):
        response = await execute_agent_workflow_tool(
            workflow_id=workflow_id,
            workflow_name="reset_device",
            parameters={"device": "PC123"},
            caller=caller,
        )
    assert response.error_type == "approval_required"
    assert response.details["approval_id"]
    execute.assert_not_awaited()
    proposal = db.add.call_args.args[0]
    assert proposal.agent_id == agent_id
    assert proposal.agent_run_id == run_id
    assert proposal.workflow_id == workflow_id
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ungranted_workflow_tool_never_executes():
    workflow_id = uuid4()
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id=workflow_id, is_active=True, tags=[])

    @asynccontextmanager
    async def fake_db():
        yield db

    caller = AgentWorkflowCaller(
        user_id=str(uuid4()), email="jane@example.com", name="Jane",
        organization_id=uuid4(), agent_id=uuid4(),
    )
    with (
        patch("src.core.database.get_db_context", fake_db),
        patch("src.services.execution.agent_helpers.agent_workflow_granted", new_callable=AsyncMock, return_value=False),
        patch("src.services.execution.service.execute_tool", new_callable=AsyncMock) as execute,
    ):
        with pytest.raises(ValueError, match="not granted"):
            await execute_agent_workflow_tool(
                workflow_id=workflow_id, workflow_name="reset_device",
                parameters={}, caller=caller,
            )
    execute.assert_not_awaited()
