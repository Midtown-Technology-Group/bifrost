"""Opt-in terminal notifications for Teams actions started by a linked chat run."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text

from src.models.enums import ExecutionStatus
from src.models.orm import AgentRun, Event, EventSource, Execution, Message, WebhookSource

TOPIC = "microsoft_teams.action_completed"
ACTION_TOOLS = {"wf_teams_run_remote_powershell", "wf_teams_run_ninjaone_script"}
TERMINAL = {
    ExecutionStatus.SUCCESS,
    ExecutionStatus.FAILED,
    ExecutionStatus.TIMEOUT,
    ExecutionStatus.CANCELLED,
    ExecutionStatus.COMPLETED_WITH_ERRORS,
    ExecutionStatus.STUCK,
}


async def _lock_execution(db, execution_id: UUID) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || :execution_id))"),
        {"execution_id": str(execution_id)},
    )


async def register_teams_action_completion(
    db, *, execution_id: UUID, run_id: UUID, webhook_event_id: UUID
) -> dict:
    """Bind a real tool execution to its authenticated Teams turn."""
    await _lock_execution(db, execution_id)
    run = await db.get(AgentRun, run_id)
    event = await db.get(Event, webhook_event_id)
    execution = await db.get(Execution, execution_id)
    if run is None or event is None or execution is None:
        raise HTTPException(404, "Teams action binding not found")
    if (
        run.status != "completed"
        or (run.input or {}).get("teams_event_id") != str(webhook_event_id)
        or event.event_type != "microsoft_teams.message"
        or (event.data or {}).get("channel_id") != "msteams"
        or run.org_id is None
        or run.org_id != execution.organization_id
        or not run.conversation_id
        or not run.caller_user_id
        or str(execution.executed_by) != str(run.caller_user_id)
    ):
        raise HTTPException(403, "Teams action binding mismatch")
    source = await db.get(EventSource, event.event_source_id)
    webhook = await db.scalar(
        select(WebhookSource).where(WebhookSource.event_source_id == event.event_source_id)
    )
    if source is None or not source.is_active or webhook is None or webhook.adapter_name != "microsoft_bot_framework":
        raise HTTPException(403, "Teams action requires an authenticated bot event")
    input_message_id = (run.input or {}).get("user_message_id")
    if not input_message_id:
        raise HTTPException(403, "Teams action has no originating message")
    messages = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == run.conversation_id)
            .order_by(Message.sequence)
        )
    ).all()
    started = False
    matched = False
    for message in messages:
        if str(message.id) == str(input_message_id):
            started = True
            continue
        if not started:
            continue
        if str(message.role.value if hasattr(message.role, "value") else message.role) == "user":
            break
        result = message.tool_result or {}
        if (
            message.tool_name in ACTION_TOOLS
            and
            isinstance(result, dict)
            and result.get("execution_id") == str(execution_id)
            and result.get("workflow_id") == str(execution.workflow_id)
            and isinstance(message.tool_input, dict)
            and message.tool_input.get("apply") is True
        ):
            matched = True
            break
    if not matched:
        raise HTTPException(403, "Execution was not an approved action in this Teams turn")
    binding = {
        "run_id": str(run_id),
        "webhook_event_id": str(webhook_event_id),
    }
    context = dict(execution.execution_context or {})
    previous = context.get("teams_action_completion")
    if previous is not None and previous != binding:
        raise HTTPException(409, "Execution already has a different Teams binding")
    context["teams_action_completion"] = binding
    execution.execution_context = context
    await db.commit()
    if execution.status in TERMINAL:
        await emit_teams_action_completion(db, execution_id)
    return {"execution_id": str(execution_id), "status": execution.status.value}


async def emit_teams_action_completion(db, execution_id: UUID) -> None:
    """Emit only for executions explicitly registered by a verified Teams turn."""
    execution = await db.get(Execution, execution_id)
    if execution is None or execution.status not in TERMINAL:
        return
    binding = (execution.execution_context or {}).get("teams_action_completion")
    if not isinstance(binding, dict):
        return
    from src.services.events import emit_event

    await emit_event(
        TOPIC,
        {
            "execution_id": str(execution_id),
            "run_id": binding["run_id"],
            "webhook_event_id": binding["webhook_event_id"],
            "organization_id": str(execution.organization_id),
        },
        organization_id=execution.organization_id,
        triggered_by=f"execution:{execution_id}",
    )
