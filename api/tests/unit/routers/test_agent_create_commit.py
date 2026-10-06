"""Agent create responses are not sent before their row is durable.

Regression test for the race where POST /api/agents returned 201 while the
row was only flushed: get_db commits during dependency teardown, which runs
after the response is sent, so an immediate POST /api/chat/conversations
could 404 with "Agent <id> not found".
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.contracts.agents import AgentCreate
from src.routers.agents import create_agent


@pytest.mark.asyncio
async def test_create_agent_commits_before_returning_response() -> None:
    events: list[str] = []
    stored_agent = MagicMock()
    response = MagicMock()

    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()

    result = MagicMock()
    result.scalar_one.return_value = stored_agent
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result

    async def commit() -> None:
        events.append("commit")

    db.commit = AsyncMock(side_effect=commit)

    user = MagicMock()
    user.user_id = uuid4()
    user.email = "admin@example.com"
    user.organization_id = uuid4()
    user.is_platform_admin = True
    user.is_external = False

    def to_public(*_args, **_kwargs):
        events.append("serialize")
        return response

    with (
        patch("src.routers.agents._agent_to_public", side_effect=to_public),
        patch(
            "src.routers.agents.sync_agent_roles_to_workflows",
            new=AsyncMock(),
        ),
    ):
        created = await create_agent(
            AgentCreate(
                name="Commit Boundary",
                description="durable before 201",
                system_prompt="Be helpful.",
                channels=["chat"],
                access_level="authenticated",
            ),
            db,
            user,
        )

    assert created is response
    assert events == ["serialize", "commit"]
    db.commit.assert_awaited_once()
