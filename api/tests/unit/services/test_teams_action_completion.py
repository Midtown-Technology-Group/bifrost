from unittest.mock import AsyncMock, patch
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.models.enums import ExecutionStatus
from src.models.orm import AgentRun, Event, EventSource, Execution
from src.services.teams_action_completion import (
    emit_teams_action_completion,
    register_teams_action_completion,
)


@pytest.mark.asyncio
async def test_only_tagged_terminal_execution_emits() -> None:
    execution_id = uuid4()
    org_id = uuid4()
    run_id = uuid4()
    event_id = uuid4()
    row = type("ExecutionRow", (), {
        "status": ExecutionStatus.SUCCESS,
        "organization_id": org_id,
        "execution_context": {},
    })()
    db = AsyncMock()
    db.get.return_value = row
    with patch("src.services.events.emit_event", new_callable=AsyncMock) as emit:
        await emit_teams_action_completion(db, execution_id)
        emit.assert_not_awaited()
        row.execution_context = {"teams_action_completion": {
            "run_id": str(run_id), "webhook_event_id": str(event_id),
        }}
        await emit_teams_action_completion(db, execution_id)
        emit.assert_awaited_once()
        assert emit.await_args.args[0] == "microsoft_teams.action_completed"
        assert emit.await_args.args[1]["execution_id"] == str(execution_id)


@pytest.mark.asyncio
async def test_registration_requires_original_linked_user_and_org() -> None:
    execution_id, run_id, event_id, user_id, org_id, message_id = (uuid4() for _ in range(6))
    workflow_id = uuid4()
    run = SimpleNamespace(
        id=run_id, status="completed", input={"teams_event_id": str(event_id), "user_message_id": str(message_id)},
        org_id=org_id, conversation_id=uuid4(), caller_user_id=str(user_id),
    )
    event = SimpleNamespace(
        event_type="microsoft_teams.message", event_source_id=uuid4(),
        data={"channel_id": "msteams", "teams_direct_enqueued": True},
    )
    execution = SimpleNamespace(
        status=ExecutionStatus.PENDING, organization_id=org_id, executed_by=user_id,
        workflow_id=workflow_id, execution_context=None,
    )
    source = SimpleNamespace(is_active=True)
    webhook = SimpleNamespace(adapter_name="microsoft_bot_framework")
    message = SimpleNamespace(
        id=uuid4(), role="tool_call", tool_name="wf_teams_run_remote_powershell",
        tool_input={"apply": True},
        tool_result={"execution_id": str(execution_id), "workflow_id": str(workflow_id)},
    )
    db = AsyncMock()
    db.get.side_effect = lambda cls, _id: {
        AgentRun: run, Event: event, Execution: execution, EventSource: source,
    }[cls]
    db.scalar.return_value = webhook
    db.scalars.return_value = SimpleNamespace(all=lambda: [SimpleNamespace(id=message_id), message])
    with patch("src.services.teams_action_completion._lock_execution", new_callable=AsyncMock):
        await register_teams_action_completion(
            db, execution_id=execution_id, run_id=run_id, webhook_event_id=event_id,
        )
        assert execution.execution_context["teams_action_completion"]["run_id"] == str(run_id)
        execution.executed_by = uuid4()
        with pytest.raises(HTTPException) as cross_user:
            await register_teams_action_completion(
                db, execution_id=execution_id, run_id=run_id, webhook_event_id=event_id,
            )
        assert cross_user.value.status_code == 403
        execution.executed_by = user_id
        execution.organization_id = uuid4()
        with pytest.raises(HTTPException) as cross_org:
            await register_teams_action_completion(
                db, execution_id=execution_id, run_id=run_id, webhook_event_id=event_id,
            )
        assert cross_org.value.status_code == 403
