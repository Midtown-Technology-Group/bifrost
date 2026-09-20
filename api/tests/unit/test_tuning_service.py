"""Tuning service: ``append_user_message_and_reply`` + ``get_or_create_conversation``.

Validates the Task 15 implementation: the service loads the flagged
``AgentRun``, appends the user's turn, calls the tuning LLM for a reply,
persists both turns on the ``AgentRunFlagConversation`` JSONB column, and
records an ``AIUsage`` row for cost tracking.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.models.orm.agent_run_flag_conversations import AgentRunFlagConversation
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage
from src.services.llm import LLMResponse
from src.services.execution.tuning_service import (
    append_user_message_and_reply,
    get_or_create_conversation,
)


@pytest_asyncio.fixture
async def seed_flagged_run(db_session, seed_agent):
    """Insert a completed, thumbs-down AgentRun ready for flag-conversation tests."""
    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="completed",
        iterations_used=1,
        tokens_used=100,
        input={"message": "help me"},
        output={"text": "routed to support"},
        verdict="down",
        verdict_set_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()
    yield run
    # Manual cleanup — service calls commit(), so the session's rollback won't undo it.
    await db_session.execute(
        delete(AgentRunFlagConversation).where(
            AgentRunFlagConversation.run_id == run.id
        )
    )
    await db_session.execute(delete(AIUsage).where(AIUsage.agent_run_id == run.id))
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.commit()


def _build_mock_llm_response(
    content: str,
    input_tokens: int = 500,
    output_tokens: int = 50,
    model: str = "claude-sonnet-4-6",
):
    """Construct the real stable response contract used by all providers."""
    return LLMResponse(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
    )


def _build_mock_client(response):
    """Construct a mock LLM client whose ``complete`` returns ``response``."""
    client = MagicMock()
    client.complete = AsyncMock(return_value=response)
    client.provider_name = "anthropic"
    return client


@pytest.mark.asyncio
async def test_append_user_message_and_reply_happy_path(
    db_session, seed_flagged_run
):
    """User message appended; LLM called; assistant reply appended; AIUsage recorded."""
    from src.services.execution import tuning_service as mod

    mock_client = _build_mock_client(
        _build_mock_llm_response("I see — routing was overeager.")
    )

    with patch.object(
        mod,
        "get_tuning_client",
        new=AsyncMock(return_value=(mock_client, "claude-sonnet-4-6")),
    ):
        conv = await append_user_message_and_reply(
            seed_flagged_run.id, "This was wrong.", db_session
        )

    assert len(conv.messages) == 2
    assert conv.messages[0]["kind"] == "user"
    assert conv.messages[0]["content"] == "This was wrong."
    assert conv.messages[1]["kind"] == "assistant"
    assert "routing" in conv.messages[1]["content"]

    # AIUsage row persisted for cost tracking
    usages = (
        (
            await db_session.execute(
                select(AIUsage).where(AIUsage.agent_run_id == seed_flagged_run.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(u.model == "claude-sonnet-4-6" for u in usages)


@pytest.mark.asyncio
async def test_get_or_create_conversation_returns_existing(
    db_session, seed_flagged_run
):
    """Second call returns the same conversation row (no duplicate)."""
    conv1 = await get_or_create_conversation(seed_flagged_run.id, db_session)
    await db_session.commit()
    conv2 = await get_or_create_conversation(seed_flagged_run.id, db_session)
    assert conv1.id == conv2.id
    assert conv2.messages == []


@pytest.mark.asyncio
async def test_append_user_message_appends_across_calls(
    db_session, seed_flagged_run
):
    """Second call appends to existing messages, doesn't replace them."""
    from src.services.execution import tuning_service as mod

    mock_client = _build_mock_client(_build_mock_llm_response("First reply."))
    with patch.object(
        mod,
        "get_tuning_client",
        new=AsyncMock(return_value=(mock_client, "claude-sonnet-4-6")),
    ):
        await append_user_message_and_reply(
            seed_flagged_run.id, "First question.", db_session
        )

    mock_client.complete.return_value = _build_mock_llm_response("Second reply.")
    with patch.object(
        mod,
        "get_tuning_client",
        new=AsyncMock(return_value=(mock_client, "claude-sonnet-4-6")),
    ):
        conv = await append_user_message_and_reply(
            seed_flagged_run.id, "Second question.", db_session
        )

    assert len(conv.messages) == 4
    assert conv.messages[0]["content"] == "First question."
    assert conv.messages[1]["content"] == "First reply."
    assert conv.messages[2]["content"] == "Second question."
    assert conv.messages[3]["content"] == "Second reply."


@pytest.mark.asyncio
async def test_append_user_message_replay_with_delivery_id_short_circuits_provider(
    db_session, seed_flagged_run
):
    """A committed PostgreSQL delivery replay must not call the tuning model again."""
    from src.services.execution import tuning_service as mod
    from src.services.work_delivery_store import DeliveryLease, current_delivery

    delivery_id = uuid4()
    scope = current_delivery.set(
        DeliveryLease(
            id=delivery_id,
            token=uuid4(),
            queue_name="agent-tuning-chat",
            message_id=str(delivery_id),
            envelope={"body": {}},
            claim_count=2,
        )
    )
    conv = await get_or_create_conversation(seed_flagged_run.id, db_session)
    conv.messages = [
        {
            "kind": "user",
            "content": "Already handled",
            "turn_id": str(delivery_id),
            "delivery_id": str(delivery_id),
        },
        {
            "kind": "assistant",
            "content": "Existing reply",
            "turn_id": str(delivery_id),
            "delivery_id": str(delivery_id),
        },
    ]
    await db_session.commit()

    try:
        with patch.object(mod, "get_tuning_client", new=AsyncMock()) as get_client:
            replay = await append_user_message_and_reply(
                seed_flagged_run.id, "Already handled", db_session
            )
    finally:
        current_delivery.reset(scope)

    assert replay.messages == conv.messages
    get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_pg_admission_persists_user_turn_before_provider_call(
    db_session, seed_flagged_run
):
    """A fresh PG delivery has a durable user marker before its LLM call."""
    from src.services.execution import tuning_service as mod
    from src.services.work_delivery_store import DeliveryLease, current_delivery

    delivery_id = uuid4()
    scope = current_delivery.set(
        DeliveryLease(
            id=delivery_id,
            token=uuid4(),
            queue_name="agent-tuning-chat",
            message_id=str(delivery_id),
            envelope={"body": {}},
            claim_count=1,
        )
    )
    mock_client = _build_mock_client(_build_mock_llm_response("Fresh reply."))
    try:
        with (
            patch.object(
                mod,
                "get_tuning_client",
                new=AsyncMock(return_value=(mock_client, "claude-sonnet-4-6")),
            ),
            patch.object(mod, "require_delivery_ownership", new=AsyncMock()),
            patch.object(mod, "record_ai_usage", new=AsyncMock()),
            patch.object(mod, "get_shared_redis", new=AsyncMock()),
        ):
            result = await append_user_message_and_reply(
                seed_flagged_run.id, "Fresh question", db_session
            )
    finally:
        current_delivery.reset(scope)

    assert [message["kind"] for message in result.messages] == [
        "user",
        "assistant",
    ]
    assert result.messages[0]["delivery_id"] == str(delivery_id)
    mock_client.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_with_committed_user_turn_does_not_append_duplicate_user(
    db_session, seed_flagged_run
):
    """A bounded retry reuses the durable user turn after an unknown call."""
    from src.services.execution import tuning_service as mod
    from src.services.work_delivery_store import DeliveryLease, current_delivery

    delivery_id = uuid4()
    scope = current_delivery.set(
        DeliveryLease(
            id=delivery_id,
            token=uuid4(),
            queue_name="agent-tuning-chat",
            message_id=str(delivery_id),
            envelope={"body": {}},
            claim_count=2,
        )
    )
    conv = await get_or_create_conversation(seed_flagged_run.id, db_session)
    conv.messages = [
        {
            "kind": "user",
            "content": "Already admitted",
            "turn_id": str(delivery_id),
            "delivery_id": str(delivery_id),
        }
    ]
    await db_session.commit()
    mock_client = _build_mock_client(_build_mock_llm_response("Retry reply."))

    try:
        with (
            patch.object(
                mod,
                "get_tuning_client",
                new=AsyncMock(return_value=(mock_client, "claude-sonnet-4-6")),
            ),
            patch.object(mod, "require_delivery_ownership", new=AsyncMock()),
            patch.object(mod, "record_ai_usage", new=AsyncMock()),
            patch.object(mod, "get_shared_redis", new=AsyncMock()),
        ):
            result = await append_user_message_and_reply(
                seed_flagged_run.id, "Already admitted", db_session
            )
    finally:
        current_delivery.reset(scope)

    assert [message["kind"] for message in result.messages] == [
        "user",
        "assistant",
    ]
    assert result.messages[0]["delivery_id"] == str(delivery_id)
    mock_client.complete.assert_awaited_once()
