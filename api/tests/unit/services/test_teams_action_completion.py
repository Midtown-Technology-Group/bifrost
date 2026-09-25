from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.models.enums import ExecutionStatus
from src.services.teams_action_completion import emit_teams_action_completion


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
