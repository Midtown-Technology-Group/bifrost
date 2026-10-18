import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.services.execution.agent_run_service import (
    enqueue_agent_run,
    enqueue_agent_run_once,
    get_executable_agent,
)


@pytest.fixture(autouse=True)
def mock_agent_run_database():
    db = MagicMock()
    db.execute = AsyncMock()
    stored = {}

    def add(row):
        stored["row"] = row

    async def get(_model, _row_id):
        return stored.get("row")

    db.add.side_effect = add
    db.get = AsyncMock(side_effect=get)
    db.commit = AsyncMock()

    @asynccontextmanager
    async def db_context():
        yield db

    with patch("src.core.database.get_db_context", side_effect=db_context):
        yield db


def _redis_context(mock_get_redis):
    redis = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=redis)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_get_redis.return_value = mock_ctx
    return redis


class TestEnqueueAgentRun:
    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_returns_run_id(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="event",
            input_data={"ticket_id": 123},
            lineage=None,
        )

        assert run_id is not None
        mock_publish.assert_called_once()
        assert mock_publish.call_args[0][0] == "agent-runs"

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_runs_lifecycle_hook_before_queue_publish(
        self, mock_get_redis, mock_publish, mock_agent_run_database
    ):
        _redis_context(mock_get_redis)
        calls = []
        mock_agent_run_database.commit.side_effect = lambda: calls.append("commit")
        before_publish = AsyncMock(side_effect=lambda *_: calls.append("lifecycle"))
        mock_publish.side_effect = lambda *_: calls.append("publish")

        run_id = await enqueue_agent_run(
            agent_id=None,
            trigger_type="chat",
            before_queue_publish=before_publish,
            lineage=None,
        )

        before_publish.assert_awaited_once_with(run_id)
        assert calls == ["commit", "lifecycle", "publish", "commit"]

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_stores_context_in_redis(
        self, mock_get_redis, _mock_publish
    ):
        redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx
        org_id = str(uuid4())

        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            input_data={"task": "analyze"},
            output_schema={"action": {"type": "string"}},
            org_id=org_id,
            caller_user_id=str(uuid4()),
            lineage=None,
        )

        redis.set.assert_awaited_once()
        context = json.loads(redis.set.call_args.args[1])
        assert context["caller"]["organization_id"] == org_id

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_run_as_is_recorded_on_the_row_only(
        self, mock_get_redis, mock_publish, mock_agent_run_database
    ):
        redis = _redis_context(mock_get_redis)
        acting_user_id = uuid4()
        launch = {
            "agent_id": str(uuid4()),
            "trigger_type": "api",
            "org_id": str(uuid4()),
            "caller_user_id": str(uuid4()),
            "run_id": str(uuid4()),
            "lineage": None,
        }

        await enqueue_agent_run(**launch)
        await enqueue_agent_run(**{**launch, "run_id": str(uuid4())}, run_as_user_id=acting_user_id)

        plain_row, run_as_row = (call.args[0] for call in mock_agent_run_database.add.call_args_list)
        plain_context, run_as_context = (call.args[1] for call in redis.set.call_args_list)
        plain_message, run_as_message = (call.args[1] for call in mock_publish.call_args_list)
        assert (plain_row.run_as_user_id, run_as_row.run_as_user_id) == (None, acting_user_id)
        plain_payload, run_as_payload = json.loads(plain_context), json.loads(run_as_context)
        plain_payload.pop("run_id")
        run_as_payload.pop("run_id")
        assert run_as_payload == plain_payload
        assert {k: v for k, v in run_as_message.items() if k != "run_id"} == {
            k: v for k, v in plain_message.items() if k != "run_id"
        }

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_supports_agentless_conversation_runs(
        self, mock_get_redis, mock_publish, mock_agent_run_database
    ):
        redis = _redis_context(mock_get_redis)
        conversation_id = str(uuid4())

        await enqueue_agent_run(
            agent_id=None,
            trigger_type="chat",
            conversation_id=conversation_id,
            input_data={"content": "hello"},
            lineage=None,
        )

        queued_run = mock_agent_run_database.add.call_args.args[0]
        assert queued_run.agent_id is None
        assert str(queued_run.conversation_id) == conversation_id
        context = json.loads(redis.set.call_args.args[1])
        assert context["conversation_id"] == conversation_id
        assert mock_publish.call_args.args[1]["agent_id"] is None

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_uses_provided_run_id(
        self, mock_get_redis, _mock_publish
    ):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx
        expected_run_id = str(uuid4())

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            run_id=expected_run_id,
            lineage=None,
        )

        assert run_id == expected_run_id

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_message_contains_sync_flag(
        self, mock_get_redis, mock_publish
    ):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            sync=True,
            lineage=None,
        )

        call_args = mock_publish.call_args
        message = call_args[0][1]
        assert message["sync"] is True

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_retry_reuses_durable_run_without_republishing(
        self,
        mock_get_redis,
        mock_publish,
        mock_agent_run_database,
    ):
        expected_run_id = str(uuid4())
        mock_agent_run_database.get.side_effect = None
        mock_agent_run_database.get.return_value = object()

        run_id, reused = await enqueue_agent_run_once(
            agent_id=str(uuid4()),
            trigger_type="delegation",
            lineage=None,
            run_id=expected_run_id,
        )

        assert run_id == expected_run_id
        assert reused is True
        mock_get_redis.assert_not_called()
        mock_publish.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_scheduled_retry_publishes_once_and_marks_queued(
        self,
        mock_get_redis,
        mock_publish,
        mock_agent_run_database,
    ):
        expected_run_id = str(uuid4())
        scheduled = MagicMock(status="scheduled")
        mock_agent_run_database.get.side_effect = [scheduled, scheduled]
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        run_id, reused = await enqueue_agent_run_once(
            agent_id=str(uuid4()),
            trigger_type="delegation",
            lineage=None,
            run_id=expected_run_id,
        )

        assert run_id == expected_run_id
        assert reused is True
        assert scheduled.status == "queued"
        mock_publish.assert_awaited_once()
        mock_agent_run_database.commit.assert_awaited_once()

    @pytest.mark.asyncio
    @patch(
        "src.services.execution.agent_run_service.publish_message",
        side_effect=RuntimeError("broker unavailable"),
    )
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_publication_failure_leaves_scheduled_run_retryable(
        self,
        mock_get_redis,
        mock_publish,
        mock_agent_run_database,
    ):
        scheduled = MagicMock(status="scheduled")
        mock_agent_run_database.get.side_effect = [None, scheduled]
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        with pytest.raises(RuntimeError, match="broker unavailable"):
            await enqueue_agent_run_once(
                agent_id=str(uuid4()),
                trigger_type="delegation",
                lineage=None,
                run_id=str(uuid4()),
            )

        assert scheduled.status == "scheduled"
        mock_publish.assert_awaited_once()
        # Only the durable pre-publication row was committed.
        mock_agent_run_database.commit.assert_awaited_once()


    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_persists_queued_run_before_publish(
        self, mock_get_redis, mock_publish, mock_agent_run_database
    ):
        _redis_context(mock_get_redis)
        calls = []
        mock_agent_run_database.commit.side_effect = lambda: calls.append("commit")
        mock_publish.side_effect = lambda *_: calls.append("publish")

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="event",
            input_data={"ticket_id": 123},
            lineage=None,
        )

        queued_run = mock_agent_run_database.add.call_args.args[0]
        assert str(queued_run.id) == run_id
        assert queued_run.status == "queued"
        assert queued_run.input == {"ticket_id": 123}
        assert calls == ["commit", "publish", "commit"]
        assert mock_publish.call_args.args[0] == "agent-runs"



    @pytest.mark.asyncio
    @patch(
        "src.services.execution.agent_run_service.publish_message",
        new_callable=AsyncMock,
    )
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_publish_failure_leaves_durable_row_retryable(
        self, mock_get_redis, mock_publish, mock_agent_run_database
    ):
        redis = _redis_context(mock_get_redis)
        mock_publish.side_effect = RuntimeError("queue unavailable")

        with pytest.raises(RuntimeError, match="queue unavailable"):
            await enqueue_agent_run(
                agent_id=str(uuid4()),
                trigger_type="sdk",
                lineage=None,
            )

        pending_run = mock_agent_run_database.add.call_args.args[0]
        assert pending_run.status == "scheduled"
        assert pending_run.completed_at is None
        redis.delete.assert_not_awaited()
        mock_agent_run_database.commit.assert_awaited_once()



class TestGetExecutableAgent:
    @pytest.mark.asyncio
    @patch(
        "src.services.execution.agent_run_service.load_agent_by_name_for_user",
        new_callable=AsyncMock,
    )
    async def test_returns_standalone_agent(self, mock_load):
        agent = MagicMock(solution_id=None)
        mock_load.return_value = agent
        db = AsyncMock()

        result = await get_executable_agent(db, "agent", MagicMock())

        assert result is agent
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    @patch(
        "src.services.execution.agent_run_service.load_agent_by_name_for_user",
        new_callable=AsyncMock,
    )
    async def test_raises_not_found(self, mock_load):
        mock_load.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_executable_agent(AsyncMock(), "missing", MagicMock())

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Agent 'missing' not found"

    @pytest.mark.asyncio
    @patch(
        "src.services.execution.agent_run_service.load_agent_by_name_for_user",
        new_callable=AsyncMock,
    )
    async def test_rejects_agent_from_inactive_solution(self, mock_load):
        agent = MagicMock(solution_id=uuid4(), name="dormant-agent")
        mock_load.return_value = agent
        db = AsyncMock()
        solution_result = MagicMock()
        solution_result.scalar_one_or_none.return_value = "inactive"
        db.execute.return_value = solution_result

        with pytest.raises(HTTPException) as exc_info:
            await get_executable_agent(db, "dormant-agent", MagicMock())

        assert exc_info.value.status_code == 409
        assert "inactive solution" in exc_info.value.detail


@pytest.mark.asyncio
@patch("src.services.execution.agent_run_service.publish_message")
@patch("src.services.execution.agent_run_service.get_redis")
async def test_queued_run_records_its_run_user(mock_get_redis, mock_publish, mock_agent_run_database):
    from shared.run_lineage import person_lineage

    _redis_context(mock_get_redis)
    person = uuid4()

    await enqueue_agent_run(
        agent_id=str(uuid4()),
        trigger_type="api",
        input_data={},
        lineage=person_lineage(person),
    )

    assert mock_agent_run_database.add.call_args.args[0].run_user_id == person
