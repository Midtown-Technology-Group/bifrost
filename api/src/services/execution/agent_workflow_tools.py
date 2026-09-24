"""Shared execution boundary for workflow-backed agent tools."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from src.models.contracts.executions import WorkflowExecutionResponse


@dataclass(frozen=True)
class AgentWorkflowCaller:
    """Caller identity propagated into an agent-originated workflow run."""

    user_id: str
    email: str
    name: str
    organization_id: UUID | str | None
    is_platform_admin: bool = False
    agent_id: UUID | None = None
    agent_run_id: UUID | None = None


async def execute_agent_workflow_tool(
    *,
    workflow_id: UUID | str,
    workflow_name: str,
    parameters: dict[str, Any],
    caller: AgentWorkflowCaller,
    execution_id: str | None = None,
    artifact_workspace_id: str | None = None,
    sync: bool = True,
) -> WorkflowExecutionResponse:
    """Execute a workflow tool with one canonical agent execution context."""
    from src.core.database import get_db_context
    from src.core.constants import SYSTEM_USER_ID
    from src.models.enums import ExecutionStatus
    from src.models.orm.agent_action_approvals import AgentActionApproval
    from src.models.orm.workflows import Workflow
    from src.services.audit import emit_audit
    from src.services.audit_context import ActorContext
    from src.services.execution.agent_helpers import agent_workflow_granted

    async with get_db_context() as db:
        workflow = await db.get(Workflow, UUID(str(workflow_id)))
        if workflow is None or not workflow.is_active:
            raise ValueError("Workflow tool is unavailable")
        if caller.agent_id is None or not await agent_workflow_granted(
            db, caller.agent_id, workflow.id
        ):
            raise ValueError("Workflow tool is not granted to this agent")
        if "approval_required" in (workflow.tags or []):
            requested_by = None
            if caller.user_id != SYSTEM_USER_ID:
                try:
                    requested_by = UUID(caller.user_id)
                except ValueError:
                    pass
            org_id = UUID(str(caller.organization_id)) if caller.organization_id else None
            proposal = AgentActionApproval(
                id=uuid4(), agent_id=caller.agent_id,
                agent_run_id=caller.agent_run_id,
                workflow_id=workflow.id, organization_id=org_id,
                requested_by_user_id=requested_by,
                parameters=parameters,
                caller={
                    "user_id": caller.user_id, "email": caller.email,
                    "name": caller.name, "organization_id": str(org_id) if org_id else None,
                },
            )
            db.add(proposal)
            await db.flush()
            await emit_audit(
                db, "agent_action.approval.requested",
                resource_type="agent_action_approval", resource_id=proposal.id,
                details={
                    "agent_run_id": str(caller.agent_run_id) if caller.agent_run_id else None,
                    "agent_id": str(caller.agent_id), "workflow_id": str(workflow.id),
                },
                actor_override=ActorContext(
                    user_id=requested_by, organization_id=org_id, source="agent",
                ),
                strict=True,
            )
            return WorkflowExecutionResponse(
                execution_id="", workflow_id=str(workflow.id),
                workflow_name=workflow_name, status=ExecutionStatus.PENDING,
                error=f"Approval required: {proposal.id}",
                error_type="approval_required",
                details={"approval_id": str(proposal.id)},
            )

    from src.services.execution.service import execute_tool

    return await execute_tool(
        workflow_id=str(workflow_id),
        workflow_name=workflow_name,
        parameters=parameters,
        user_id=caller.user_id,
        user_email=caller.email,
        user_name=caller.name,
        org_id=str(caller.organization_id) if caller.organization_id else None,
        is_platform_admin=caller.is_platform_admin,
        is_agent=True,
        execution_id=execution_id,
        artifact_workspace_id=artifact_workspace_id,
        sync=sync,
    )
