from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from src.models.enums import ExecutionStatus
from src.models.orm import AgentRun, Event, EventSource, Execution
from src.services.teams_action_completion import (
    emit_teams_action_completion,
    register_teams_action_completion,
    register_teams_action_for_run,
)
from src.services.teams_chat_bridge import (
    emit_teams_chat_completion,
    recover_teams_chat_completions,
)


@pytest.mark.asyncio
async def test_only_tagged_terminal_execution_emits() -> None:
    execution_id = uuid4()
    org_id = uuid4()
    run_id = uuid4()
    event_id = uuid4()
    row = type(
        "ExecutionRow",
        (),
        {
            "status": ExecutionStatus.SUCCESS,
            "organization_id": org_id,
            "execution_context": {},
        },
    )()
    db = AsyncMock()
    db.get.return_value = row
    processor = SimpleNamespace(
        emit_topic=AsyncMock(return_value=(uuid4(), 1)),
        queue_event_deliveries=AsyncMock(),
    )
    with (
        patch("src.services.events.processor.EventProcessor", return_value=processor),
        patch(
            "src.services.teams_action_completion._lock_execution",
            new_callable=AsyncMock,
        ),
    ):
        await emit_teams_action_completion(db, execution_id)
        processor.emit_topic.assert_not_awaited()
        row.execution_context = {
            "teams_action_completion": {
                "run_id": str(run_id),
                "webhook_event_id": str(event_id),
            }
        }
        await emit_teams_action_completion(db, execution_id)
        processor.emit_topic.assert_awaited_once()
        assert db.get.await_args.kwargs == {"populate_existing": True}
        assert processor.emit_topic.await_args.kwargs["topic"] == (
            "microsoft_teams.action_completed"
        )
        assert processor.emit_topic.await_args.kwargs["data"]["execution_id"] == str(
            execution_id
        )
        processor.queue_event_deliveries.assert_awaited_once()
        await emit_teams_action_completion(db, execution_id)
        processor.emit_topic.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_subscriber_attempt_stays_retryable() -> None:
    execution_id = uuid4()
    row = SimpleNamespace(
        status=ExecutionStatus.SUCCESS,
        organization_id=uuid4(),
        execution_context={
            "teams_action_completion": {
                "run_id": str(uuid4()),
                "webhook_event_id": str(uuid4()),
            }
        },
    )
    db = AsyncMock()
    db.get.return_value = row
    processor = SimpleNamespace(
        emit_topic=AsyncMock(side_effect=[(uuid4(), 0), (uuid4(), 1)]),
        queue_event_deliveries=AsyncMock(),
    )
    with (
        patch("src.services.events.processor.EventProcessor", return_value=processor),
        patch(
            "src.services.teams_action_completion._lock_execution",
            new_callable=AsyncMock,
        ),
    ):
        assert not await emit_teams_action_completion(db, execution_id)
        binding = row.execution_context["teams_action_completion"]
        assert binding["last_attempt_at"]
        assert "emitted_at" not in binding
        assert await emit_teams_action_completion(db, execution_id)
        assert row.execution_context["teams_action_completion"]["emitted_at"]
        assert not await emit_teams_action_completion(db, execution_id)
        assert processor.emit_topic.await_count == 2
        processor.queue_event_deliveries.assert_awaited_once()


@pytest.mark.asyncio
async def test_event_is_not_queued_when_marker_commit_fails() -> None:
    execution_id = uuid4()
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(
        status=ExecutionStatus.SUCCESS,
        organization_id=uuid4(),
        execution_context={
            "teams_action_completion": {
                "run_id": str(uuid4()),
                "webhook_event_id": str(uuid4()),
            }
        },
    )
    db.commit.side_effect = RuntimeError("commit failed")
    processor = SimpleNamespace(
        emit_topic=AsyncMock(return_value=(uuid4(), 1)),
        queue_event_deliveries=AsyncMock(),
    )
    with (
        patch("src.services.events.processor.EventProcessor", return_value=processor),
        patch(
            "src.services.teams_action_completion._lock_execution",
            new_callable=AsyncMock,
        ),
        pytest.raises(RuntimeError, match="commit failed"),
    ):
        await emit_teams_action_completion(db, execution_id)
    processor.emit_topic.assert_awaited_once()
    processor.queue_event_deliveries.assert_not_awaited()


@pytest.mark.asyncio
async def test_registration_requires_original_linked_user_and_org() -> None:
    execution_id, run_id, event_id, user_id, org_id, message_id = (
        uuid4() for _ in range(6)
    )
    workflow_id = uuid4()
    run = SimpleNamespace(
        id=run_id,
        status="completed",
        input={"teams_event_id": str(event_id), "user_message_id": str(message_id)},
        org_id=org_id,
        conversation_id=uuid4(),
        caller_user_id=str(user_id),
    )
    event = SimpleNamespace(
        event_type="microsoft_teams.message",
        event_source_id=uuid4(),
        data={"channel_id": "msteams", "teams_direct_enqueued": True},
    )
    execution = SimpleNamespace(
        status=ExecutionStatus.PENDING,
        organization_id=org_id,
        executed_by=user_id,
        workflow_id=workflow_id,
        execution_context=None,
    )
    source = SimpleNamespace(is_active=True)
    webhook = SimpleNamespace(adapter_name="microsoft_bot_framework")
    message = SimpleNamespace(
        id=uuid4(),
        role="tool_call",
        tool_name="wf_teams_run_remote_powershell",
        tool_input={"apply": True},
        tool_result={
            "execution_id": str(execution_id),
            "workflow_id": str(workflow_id),
        },
    )
    db = AsyncMock()
    db.get.side_effect = lambda cls, _id, **_kwargs: {
        AgentRun: run,
        Event: event,
        Execution: execution,
        EventSource: source,
    }[cls]
    db.scalar.return_value = webhook
    db.scalars.return_value = SimpleNamespace(
        all=lambda: [SimpleNamespace(id=message_id), message]
    )
    with patch(
        "src.services.teams_action_completion._lock_execution", new_callable=AsyncMock
    ):
        await register_teams_action_completion(
            db,
            execution_id=execution_id,
            run_id=run_id,
            webhook_event_id=event_id,
        )
        assert execution.execution_context["teams_action_completion"]["run_id"] == str(
            run_id
        )
        execution.executed_by = uuid4()
        with pytest.raises(HTTPException) as cross_user:
            await register_teams_action_completion(
                db,
                execution_id=execution_id,
                run_id=run_id,
                webhook_event_id=event_id,
            )
        assert cross_user.value.status_code == 403
        execution.executed_by = user_id
        execution.organization_id = uuid4()
        with pytest.raises(HTTPException) as cross_org:
            await register_teams_action_completion(
                db,
                execution_id=execution_id,
                run_id=run_id,
                webhook_event_id=event_id,
            )
        assert cross_org.value.status_code == 403


@pytest.mark.asyncio
async def test_transient_registration_failure_remains_recoverable() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        input={"teams_event_id": str(uuid4())},
        status="completed",
        org_id=uuid4(),
        run_metadata={},
    )
    db = AsyncMock()
    db.get.return_value = run
    db.scalars.return_value = SimpleNamespace(all=lambda: [run])

    class SessionFactory:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    register = AsyncMock(side_effect=[RuntimeError("transient"), None])
    with (
        patch("src.core.database.get_session_factory", return_value=SessionFactory),
        patch("src.services.events.emit_event", new_callable=AsyncMock) as emit,
        patch(
            "src.services.teams_action_completion.register_teams_action_for_run",
            register,
        ),
    ):
        emit.return_value = (uuid4(), 1)
        with pytest.raises(RuntimeError, match="transient"):
            await emit_teams_chat_completion(run)
        assert "teams_completion_emitted_at" not in run.run_metadata
        assert await recover_teams_chat_completions() == 1
        assert "teams_completion_emitted_at" in run.run_metadata
        assert register.await_count == 2


@pytest.mark.asyncio
async def test_registers_each_distinct_approved_action_in_turn() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        input={"teams_event_id": str(uuid4()), "user_message_id": str(uuid4())},
        status="completed",
        conversation_id=uuid4(),
    )
    first, second = uuid4(), uuid4()
    messages = [SimpleNamespace(id=run.input["user_message_id"])] + [
        SimpleNamespace(
            id=uuid4(),
            role="tool_call",
            tool_name="wf_teams_run_remote_powershell",
            tool_input={"apply": True},
            tool_result={"execution_id": str(execution_id)},
        )
        for execution_id in (first, first, second)
    ]
    db = AsyncMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: messages)
    with patch(
        "src.services.teams_action_completion.register_teams_action_completion",
        new_callable=AsyncMock,
    ) as register:
        await register_teams_action_for_run(db, run)
    assert [call.kwargs["execution_id"] for call in register.await_args_list] == [
        first,
        second,
    ]


@pytest.mark.asyncio
async def test_invalid_action_does_not_block_later_approved_action() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        input={"teams_event_id": str(uuid4()), "user_message_id": str(uuid4())},
        status="completed",
        conversation_id=uuid4(),
    )
    invalid_id, valid_id = uuid4(), uuid4()
    messages = [SimpleNamespace(id=run.input["user_message_id"])] + [
        SimpleNamespace(
            id=uuid4(),
            role="tool_call",
            tool_name="wf_teams_run_remote_powershell",
            tool_input={"apply": True},
            tool_result={"execution_id": str(execution_id)},
        )
        for execution_id in (invalid_id, valid_id)
    ]
    db = AsyncMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: messages)
    with patch(
        "src.services.teams_action_completion.register_teams_action_completion",
        new_callable=AsyncMock,
    ) as register:
        register.side_effect = [HTTPException(403, "invalid binding"), None]
        await register_teams_action_for_run(db, run)
    assert [call.kwargs["execution_id"] for call in register.await_args_list] == [
        invalid_id,
        valid_id,
    ]
    db.rollback.assert_awaited_once()
