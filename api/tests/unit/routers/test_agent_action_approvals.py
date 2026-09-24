"""Approved agent actions use the original caller and cannot self-approve."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.orm.agent_action_approvals import AgentActionApproval
from src.models.orm.agents import Agent
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.routers.agent_action_approvals import approve_approval


def _proposal():
    requester_id = uuid4()
    org_id = uuid4()
    row = AgentActionApproval(
        id=uuid4(), agent_id=uuid4(), agent_run_id=uuid4(),
        workflow_id=uuid4(), organization_id=org_id,
        requested_by_user_id=requester_id,
        parameters={"device": "PC123"},
        caller={
            "user_id": str(requester_id), "email": "jane@example.com",
            "name": "Jane", "organization_id": str(org_id),
        },
        status="pending", created_at=datetime.now(UTC),
    )
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_granted", [False, True])
async def test_approval_dispatches_exact_proposal_as_original_caller(provider_granted):
    row = _proposal()
    approver = SimpleNamespace(user_id=uuid4())
    workflow = SimpleNamespace(
        id=row.workflow_id, name="reset_device", is_active=True,
        tags=["approval_required"],
    )
    agent = SimpleNamespace(id=row.agent_id, is_active=True)
    requester = SimpleNamespace(
        id=row.requested_by_user_id,
        organization_id=uuid4() if provider_granted else row.organization_id,
        is_active=True, is_superuser=False,
    )
    scoped_principal = SimpleNamespace(organization_id=row.organization_id, is_superuser=False)
    db = AsyncMock()
    db.get.side_effect = lambda model, *_args, **_kwargs: {
        AgentActionApproval: row, Workflow: workflow, Agent: agent, User: requester,
    }[model]

    async def dispatch(**kwargs):
        return SimpleNamespace(execution_id=kwargs["execution_id"])

    with (
        patch("src.routers.agent_action_approvals.agent_workflow_granted", new=AsyncMock(return_value=True)),
        patch("src.routers.agent_action_approvals.load_agent_for_user", new=AsyncMock(return_value=agent)),
        patch("src.routers.agent_action_approvals.resolve_run_external_actor", new=AsyncMock(return_value=(scoped_principal, uuid4(), {}) if provider_granted else None)),
        patch("src.routers.agent_action_approvals.caller_can_access_workflow_tool", new=AsyncMock(return_value=True)) as access,
        patch("src.routers.agent_action_approvals.execute_tool", new=AsyncMock(side_effect=dispatch)) as execute,
        patch("src.routers.agent_action_approvals.emit_audit", new=AsyncMock()) as audit,
    ):
        response = await approve_approval(row.id, user=approver, db=db)

    assert response.status == "dispatched"
    assert response.parameters == {"device": "PC123"}
    assert response.approved_by_user_id == approver.user_id
    assert execute.await_args.kwargs["parameters"] == {"device": "PC123"}
    assert execute.await_args.kwargs["user_id"] == str(row.requested_by_user_id)
    assert execute.await_args.kwargs["org_id"] == str(row.organization_id)
    assert execute.await_args.kwargs["execution_id"] == str(row.execution_id)
    assert audit.await_count == 2
    assert db.commit.await_count == 2
    if provider_granted:
        assert access.await_args.kwargs["caller_access"] == (
            requester.id, row.organization_id, False,
        )


@pytest.mark.asyncio
async def test_requester_cannot_approve_own_action():
    row = _proposal()
    db = AsyncMock()
    db.get.return_value = row
    with patch("src.routers.agent_action_approvals.execute_tool", new=AsyncMock()) as execute:
        with pytest.raises(HTTPException) as exc:
            await approve_approval(
                row.id, user=SimpleNamespace(user_id=row.requested_by_user_id), db=db,
            )
    assert exc.value.status_code == 403
    execute.assert_not_awaited()
