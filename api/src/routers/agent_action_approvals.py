"""Admin decisions for durable agent workflow proposals."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from shared.models import AgentActionApprovalResponse
from sqlalchemy import select

from src.core.auth import CurrentSuperuser
from src.core.db_deps import DbSession
from src.models.orm.agent_action_approvals import AgentActionApproval
from src.models.orm.agents import Agent
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.services.audit import emit_audit
from src.services.agent_run_access import load_agent_for_user
from src.services.events.external_actors import (
    ExternalActorResolutionError,
    resolve_run_external_actor,
)
from src.services.execution.agent_helpers import (
    agent_workflow_granted,
    caller_can_access_workflow_tool,
)
from src.services.execution.service import execute_tool

router = APIRouter(
    prefix="/api/agent-action-approvals", tags=["Agent Action Approvals"]
)


def _response(row: AgentActionApproval) -> AgentActionApprovalResponse:
    return AgentActionApprovalResponse.model_validate(row, from_attributes=True)


@router.get("", response_model=list[AgentActionApprovalResponse])
async def list_approvals(
    user: CurrentSuperuser, db: DbSession, status: str = "pending"
) -> list[AgentActionApprovalResponse]:
    rows = (
        (
            await db.execute(
                select(AgentActionApproval)
                .where(AgentActionApproval.status == status)
                .order_by(AgentActionApproval.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [_response(row) for row in rows]


@router.post("/{approval_id}/deny", response_model=AgentActionApprovalResponse)
async def deny_approval(
    approval_id: UUID, user: CurrentSuperuser, db: DbSession
) -> AgentActionApprovalResponse:
    row = await db.get(AgentActionApproval, approval_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="Approval is already decided")
    row.status = "denied"
    row.approved_by_user_id = user.user_id
    row.decided_at = datetime.now(UTC)
    await emit_audit(
        db,
        "agent_action.approval.denied",
        resource_type="agent_action_approval",
        resource_id=row.id,
        details={"agent_run_id": str(row.agent_run_id) if row.agent_run_id else None},
        strict=True,
    )
    await db.commit()
    return _response(row)


@router.post("/{approval_id}/approve", response_model=AgentActionApprovalResponse)
async def approve_approval(
    approval_id: UUID, user: CurrentSuperuser, db: DbSession
) -> AgentActionApprovalResponse:
    row = await db.get(AgentActionApproval, approval_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if row.status == "dispatched":
        return _response(row)
    if row.status not in {"pending", "approved"}:
        raise HTTPException(status_code=409, detail="Approval is already decided")
    if row.requested_by_user_id == user.user_id:
        raise HTTPException(
            status_code=403, detail="Requester cannot approve their own action"
        )

    workflow = await db.get(Workflow, row.workflow_id)
    agent = await db.get(Agent, row.agent_id)
    if (
        workflow is None
        or agent is None
        or not workflow.is_active
        or not agent.is_active
    ):
        raise HTTPException(status_code=409, detail="Action is no longer available")
    if "approval_required" not in (workflow.tags or []):
        raise HTTPException(status_code=409, detail="Action approval policy changed")
    if not await agent_workflow_granted(db, agent.id, workflow.id):
        raise HTTPException(
            status_code=409, detail="Agent no longer has this tool grant"
        )
    requester = (
        await db.get(User, row.requested_by_user_id)
        if row.requested_by_user_id
        else None
    )
    try:
        external_resolution = (
            await resolve_run_external_actor(db, row.agent_run_id, row.requested_by_user_id)
            if row.agent_run_id else None
        )
    except ExternalActorResolutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    effective_org_id = (
        external_resolution[0].organization_id
        if external_resolution else requester.organization_id if requester else None
    )
    if external_resolution and await load_agent_for_user(
        db, agent.id, external_resolution[0]
    ) is None:
        raise HTTPException(status_code=409, detail="Requesting caller lost agent access")
    if row.requested_by_user_id and (
        requester is None or not requester.is_active
        or effective_org_id != row.organization_id
    ):
        raise HTTPException(
            status_code=409, detail="Requesting caller is no longer authorized"
        )
    if not await caller_can_access_workflow_tool(
        workflow,
        agent,
        db,
        caller_user_id=row.requested_by_user_id,
        caller_is_platform_admin=(
            external_resolution[0].is_superuser if external_resolution
            else bool(requester and requester.is_superuser)
        ),
        **(
            {"caller_access": (
                requester.id, effective_org_id, external_resolution[0].is_superuser,
            )}
            if external_resolution and requester else {}
        ),
    ):
        raise HTTPException(
            status_code=409, detail="Requesting caller can no longer access workflow"
        )

    if row.status == "pending":
        row.status = "approved"
        row.approved_by_user_id = user.user_id
        row.decided_at = datetime.now(UTC)
        row.execution_id = uuid4()
        await emit_audit(
            db,
            "agent_action.approval.granted",
            resource_type="agent_action_approval",
            resource_id=row.id,
            details={
                "agent_run_id": str(row.agent_run_id) if row.agent_run_id else None,
                "workflow_id": str(row.workflow_id),
                "execution_id": str(row.execution_id),
            },
            strict=True,
        )
        await db.commit()

    response = await execute_tool(
        workflow_id=str(row.workflow_id),
        workflow_name=workflow.name,
        parameters=row.parameters,
        user_id=row.caller["user_id"],
        user_email=row.caller["email"],
        user_name=row.caller["name"],
        org_id=row.caller.get("organization_id"),
        is_platform_admin=(
            external_resolution[0].is_superuser if external_resolution
            else bool(requester and requester.is_superuser)
        ),
        is_agent=True,
        execution_id=str(row.execution_id),
        sync=False,
    )
    if response.execution_id != str(row.execution_id):
        raise RuntimeError("Workflow execution ID changed during approval dispatch")
    row.status = "dispatched"
    await emit_audit(
        db,
        "agent_action.approval.dispatched",
        resource_type="agent_action_approval",
        resource_id=row.id,
        details={
            "agent_run_id": str(row.agent_run_id) if row.agent_run_id else None,
            "workflow_id": str(row.workflow_id),
            "execution_id": str(row.execution_id),
        },
        strict=True,
    )
    await db.commit()
    return _response(row)
