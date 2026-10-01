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
        is_provider_org=True,
        is_external=False,
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
        is_provider_org=True,
        is_external=False,
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
async def test_approval_gated_workflow_rejects_task_without_proposal():
    workflow_id = uuid4()
    db = AsyncMock()
    db.add = MagicMock()
    db.get.return_value = SimpleNamespace(
        id=workflow_id, is_active=True, tags=["approval_required"]
    )

    @asynccontextmanager
    async def fake_db():
        yield db

    with (
        patch("src.core.database.get_db_context", fake_db),
        patch(
            "src.services.execution.agent_helpers.agent_workflow_granted",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        response = await execute_agent_workflow_tool(
            workflow_id=workflow_id,
            workflow_name="reset_device",
            parameters={},
            caller=AgentWorkflowCaller(
                user_id=str(uuid4()), email="jane@example.com", name="Jane",
                organization_id=uuid4(), agent_id=uuid4(),
            ),
            task_requested=True,
        )

    assert response.error_type == "approval_task_unsupported"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_approval_rejects_malformed_caller_id_without_proposal():
    workflow_id = uuid4()
    db = AsyncMock()
    db.add = MagicMock()
    db.get.return_value = SimpleNamespace(
        id=workflow_id, is_active=True, tags=["approval_required"]
    )

    @asynccontextmanager
    async def fake_db():
        yield db

    with (
        patch("src.core.database.get_db_context", fake_db),
        patch(
            "src.services.execution.agent_helpers.agent_workflow_granted",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.services.execution.service.execute_tool", new_callable=AsyncMock
        ) as execute,
    ):
        with pytest.raises(ValueError):
            await execute_agent_workflow_tool(
                workflow_id=workflow_id,
                workflow_name="reset_device",
                parameters={},
                caller=AgentWorkflowCaller(
                    user_id="not-a-uuid",
                    email="jane@example.com",
                    name="Jane",
                    organization_id=uuid4(),
                    agent_id=uuid4(),
                ),
            )

    db.add.assert_not_called()
    execute.assert_not_awaited()


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("admin", "provider", "external"),
    [(False, True, False), (False, False, False), (True, False, False), (False, False, True)],
)
async def test_original_caller_authority_reaches_frozen_workflow_dispatch(
    monkeypatch, admin, provider, external,
):
    """Use the real helper, context constructor and frozen dispatch projection."""
    from src.services.execution import service
    from src.services.execution.async_executor import (
        _dispatch_request_identity,
        _pending_dispatch_envelope,
    )

    workflow_id, org_id, user_id, agent_id = uuid4(), uuid4(), uuid4(), uuid4()
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id=workflow_id, is_active=True, tags=[])

    @asynccontextmanager
    async def fake_db():
        yield db

    frozen = []

    async def enqueue(**kwargs):
        context = kwargs["context"]
        request = _dispatch_request_identity(
            context, context.execution_id, kwargs["workflow_id"], kwargs["parameters"],
            form_id=None, sync=False, api_key_id=None, file_path=None,
            org_id_override=None,
        )
        frozen.append(_pending_dispatch_envelope(
            request, solution_deployment_id=None, runtime_evidence=None,
            runtime_mode="repo-v1",
        ))
        return "queued"

    monkeypatch.setattr("src.core.database.get_db_context", fake_db)
    monkeypatch.setattr(
        "src.services.execution.agent_helpers.agent_workflow_granted",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(service, "_enqueue_workflow_async", enqueue)
    caller = AgentWorkflowCaller(
        user_id=str(user_id), email="caller@example.test", name="Caller",
        organization_id=org_id, agent_id=agent_id,
        is_platform_admin=admin, is_provider_org=provider, is_external=external,
    )
    assert await execute_agent_workflow_tool(
        workflow_id=workflow_id, workflow_name="tool", parameters={}, caller=caller, sync=False,
    ) == "queued"
    for projection in (frozen[0]["request"], frozen[0]["publish"]):
        assert projection["user_id"] == str(user_id)
        assert projection["org_id"] == str(org_id)
        assert projection["is_platform_admin"] is admin
        assert projection["is_provider_org"] is provider
        assert projection["is_external"] is external

    # The existing worker mints an admin transport bearer while delegating the
    # frozen original-caller flags. Transport admin must not become caller admin.
    from src.core.security import decode_token, mint_engine_token

    pending = frozen[0]["publish"]
    token, _ = mint_engine_token(
        organization_id=pending["org_id"], delegated_user_id=pending["user_id"],
        delegated_email=pending["user_email"], delegated_name=pending["user_name"],
        delegated_is_superuser=pending["is_platform_admin"],
        delegated_is_provider_org=pending["is_provider_org"],
        delegated_is_external=pending["is_external"],
    )
    claims = decode_token(token)
    assert claims is not None
    assert claims["is_superuser"] is True
    assert claims["delegated_is_superuser"] is admin
    assert claims["delegated_is_provider_org"] is provider
    assert claims["delegated_is_external"] is external


def test_shared_caller_authority_defaults_are_unprivileged():
    caller = AgentWorkflowCaller("system", "system@example.test", "System", None)
    assert not caller.is_platform_admin
    assert not caller.is_provider_org
    assert not caller.is_external
