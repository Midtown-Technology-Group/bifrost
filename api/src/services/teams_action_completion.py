"""Opt-in terminal notifications for Teams actions started by a linked chat run."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text

from src.models.enums import ExecutionStatus
from src.models.orm import (
    AgentRun,
    Event,
    EventSource,
    Execution,
    Message,
    WebhookSource,
)
from src.repositories.executions import EXECUTION_ADVISORY_LOCK_SQL

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
        text(EXECUTION_ADVISORY_LOCK_SQL),
        {"execution_id": str(execution_id)},
    )


async def register_teams_action_completion(
    db, *, execution_id: UUID, run_id: UUID, webhook_event_id: UUID
) -> dict:
    """Bind a real tool execution to its authenticated Teams turn."""
    await _lock_execution(db, execution_id)
    run = await db.get(AgentRun, run_id)
    event = await db.get(Event, webhook_event_id)
    execution = await db.get(Execution, execution_id, populate_existing=True)
    if run is None or event is None or execution is None:
        raise HTTPException(404, "Teams action binding not found")
    if (
        run.status != "completed"
        or (run.input or {}).get("teams_event_id") != str(webhook_event_id)
        or event.event_type != "microsoft_teams.message"
        or (event.data or {}).get("channel_id") != "msteams"
        or not (event.data or {}).get("teams_direct_enqueued")
        or run.org_id is None
        or run.org_id != execution.organization_id
        or not run.conversation_id
        or not run.caller_user_id
        or str(execution.executed_by) != str(run.caller_user_id)
    ):
        raise HTTPException(403, "Teams action binding mismatch")
    source = await db.get(EventSource, event.event_source_id)
    webhook = await db.scalar(
        select(WebhookSource).where(
            WebhookSource.event_source_id == event.event_source_id
        )
    )
    if (
        source is None
        or not source.is_active
        or webhook is None
        or webhook.adapter_name != "microsoft_bot_framework"
    ):
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
        if (
            str(message.role.value if hasattr(message.role, "value") else message.role)
            == "user"
        ):
            break
        result = message.tool_result or {}
        if (
            message.tool_name in ACTION_TOOLS
            and isinstance(result, dict)
            and result.get("execution_id") == str(execution_id)
            and result.get("workflow_id") == str(execution.workflow_id)
            and isinstance(message.tool_input, dict)
            and message.tool_input.get("apply") is True
        ):
            matched = True
            break
    if not matched:
        raise HTTPException(
            403, "Execution was not an approved action in this Teams turn"
        )
    binding = {
        "run_id": str(run_id),
        "webhook_event_id": str(webhook_event_id),
    }
    context = dict(execution.execution_context or {})
    previous = context.get("teams_action_completion")
    if previous is not None and (
        not isinstance(previous, dict)
        or any(previous.get(key) != value for key, value in binding.items())
    ):
        raise HTTPException(409, "Execution already has a different Teams binding")
    context["teams_action_completion"] = previous or binding
    execution.execution_context = context
    await db.commit()
    if execution.status in TERMINAL:
        await emit_teams_action_completion(db, execution_id)
    return {"execution_id": str(execution_id), "status": execution.status.value}


async def register_teams_action_for_run(db, run: AgentRun) -> None:
    """Server-side discovery avoids granting a workflow callback an admin API."""
    event_id = (run.input or {}).get("teams_event_id")
    user_message_id = (run.input or {}).get("user_message_id")
    if (
        run.status != "completed"
        or not event_id
        or not user_message_id
        or not run.conversation_id
    ):
        return
    messages = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == run.conversation_id)
            .order_by(Message.sequence)
        )
    ).all()
    started = False
    registered: set[UUID] = set()
    for message in messages:
        if str(message.id) == str(user_message_id):
            started = True
            continue
        if not started:
            continue
        if (
            str(message.role.value if hasattr(message.role, "value") else message.role)
            == "user"
        ):
            break
        result = message.tool_result or {}
        if (
            message.tool_name in ACTION_TOOLS
            and isinstance(result, dict)
            and isinstance(message.tool_input, dict)
            and message.tool_input.get("apply") is True
        ):
            try:
                execution_id = UUID(str(result["execution_id"]))
            except (KeyError, TypeError, ValueError):
                continue
            if execution_id in registered:
                continue
            try:
                await register_teams_action_completion(
                    db,
                    execution_id=execution_id,
                    run_id=run.id,
                    webhook_event_id=UUID(str(event_id)),
                )
            except HTTPException as exc:
                await db.rollback()
                logging.getLogger(__name__).warning(
                    "Skipping invalid Teams action %s for run %s: %s",
                    execution_id,
                    run.id,
                    exc.detail,
                )
                continue
            registered.add(execution_id)


async def emit_teams_action_completion(db, execution_id: UUID) -> bool:
    """Emit only for executions explicitly registered by a verified Teams turn."""
    await _lock_execution(db, execution_id)
    execution = await db.get(Execution, execution_id, populate_existing=True)
    if execution is None or execution.status not in TERMINAL:
        return False
    binding = (execution.execution_context or {}).get("teams_action_completion")
    if not isinstance(binding, dict) or binding.get("emitted_at"):
        return False
    from src.services.events import emit_event

    _event_id, subscribers = await emit_event(
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
    context = dict(execution.execution_context or {})
    now = datetime.now(timezone.utc).isoformat()
    context["teams_action_completion"] = {
        **binding,
        "last_attempt_at": now,
        **({"emitted_at": now} if subscribers else {}),
    }
    execution.execution_context = context
    await db.commit()
    return bool(subscribers)


async def recover_teams_action_completions(*, limit: int = 50) -> int:
    """Retry only tagged terminal actions whose topic emission was not recorded."""
    import logging

    from src.core.database import get_session_factory

    async with get_session_factory()() as db:
        execution_ids = (
            await db.scalars(
                select(Execution.id)
                .where(
                    Execution.status.in_(TERMINAL),
                    Execution.execution_context.has_key("teams_action_completion"),
                    Execution.execution_context["teams_action_completion"][
                        "emitted_at"
                    ].astext.is_(None),
                    Execution.completed_at
                    < datetime.now(timezone.utc) - timedelta(seconds=15),
                )
                .order_by(
                    Execution.execution_context["teams_action_completion"][
                        "last_attempt_at"
                    ]
                    .astext.asc()
                    .nullsfirst(),
                    Execution.completed_at,
                )
                .limit(limit)
            )
        ).all()
    recovered = 0
    for execution_id in execution_ids:
        try:
            async with get_session_factory()() as db:
                if await emit_teams_action_completion(db, execution_id):
                    recovered += 1
        except Exception:
            logging.getLogger(__name__).exception(
                "Teams action completion recovery failed for %s", execution_id
            )
    return recovered
