"""PostgreSQL delivery fencing for summary and tuning handlers.

These tests use a real PostgreSQL session and delivery claim. Only provider,
metering, and publication boundaries are replaced, so transaction boundaries
and durable JSONB outcomes remain covered.
"""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from src.models.orm.agent_run_flag_conversations import AgentRunFlagConversation
from src.models.orm.agent_runs import AgentRun
from src.models.orm.work_deliveries import WorkDelivery
from src.services.llm import LLMResponse
from src.services.work_delivery_store import (
    DeliveryOwnershipLost,
    claim_deliveries,
    current_delivery,
    enqueue_delivery,
    interrupt_delivery,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def ai_rows(async_session_factory):
    run_ids: list = []
    delivery_ids: list = []

    async def register(run_id, delivery_id):
        run_ids.append(run_id)
        delivery_ids.append(delivery_id)

    yield register
    async with async_session_factory() as db:
        await db.execute(delete(WorkDelivery).where(WorkDelivery.id.in_(delivery_ids)))
        await db.execute(
            delete(AgentRunFlagConversation).where(
                AgentRunFlagConversation.run_id.in_(run_ids)
            )
        )
        await db.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        await db.commit()


async def _new_run(db, *, summary_status="pending"):
    run = AgentRun(
        id=uuid4(),
        trigger_type="postgres-ai-handler-test",
        status="completed",
        input={"question": "reset password"},
        output={"result": "queued for support"},
        summary_status=summary_status,
    )
    db.add(run)
    await db.flush()
    return run


async def _claim(db, queue_name, run_id):
    await enqueue_delivery(
        db,
        queue_name=queue_name,
        message_id=str(run_id),
        envelope={"body": {"run_id": str(run_id)}, "headers": {}},
    )
    await db.commit()
    (lease,) = await claim_deliveries(db, queue_name=queue_name, owner="ai-test")
    await db.commit()
    return lease


def _response(content):
    return LLMResponse(
        content=content,
        input_tokens=11,
        output_tokens=7,
        model="test-model",
    )


async def test_summary_delivery_persists_once_and_replay_skips_provider(
    async_session_factory, ai_rows, monkeypatch
):
    from src.services.execution import run_summarizer as module

    async with async_session_factory() as db:
        run = await _new_run(db)
        lease = await _claim(db, f"ai-summary-{uuid4()}", run.id)
    await ai_rows(run.id, lease.id)

    client = type("Client", (), {})()
    client.provider_name = "test"
    client.complete = AsyncMock(return_value=_response(
        '{"asked":"reset password","did":"queued","confidence":0.9}'
    ))
    monkeypatch.setattr(module, "get_summarization_client", _async_value((client, "test-model")))
    monkeypatch.setattr(module, "record_ai_usage", AsyncMock())
    monkeypatch.setattr(module, "get_shared_redis", AsyncMock())
    monkeypatch.setattr(module, "_broadcast_run", AsyncMock())

    scope = current_delivery.set(lease)
    try:
        await module.summarize_run(run.id, async_session_factory)
        await module.summarize_run(run.id, async_session_factory)
    finally:
        current_delivery.reset(scope)

    async with async_session_factory() as db:
        stored = await db.get(AgentRun, run.id)
        assert stored.summary_status == "completed"
        assert stored.summary_delivery_id == lease.id
        assert stored.asked == "reset password"
    assert client.complete.await_count == 1


async def test_summary_expired_delivery_cannot_commit_provider_result(
    async_session_factory, ai_rows, monkeypatch
):
    from src.services.execution import run_summarizer as module

    async with async_session_factory() as db:
        run = await _new_run(db)
        lease = await _claim(db, f"ai-summary-expiry-{uuid4()}", run.id)
    await ai_rows(run.id, lease.id)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_complete(**_kwargs):
        entered.set()
        await release.wait()
        return _response('{"asked":"stale","did":"stale"}')

    client = type("Client", (), {})()
    client.provider_name = "test"
    client.complete = blocked_complete
    monkeypatch.setattr(module, "get_summarization_client", _async_value((client, "test-model")))
    monkeypatch.setattr(module, "record_ai_usage", AsyncMock())
    monkeypatch.setattr(module, "get_shared_redis", AsyncMock())
    monkeypatch.setattr(module, "_broadcast_run", AsyncMock())

    scope = current_delivery.set(lease)
    task = asyncio.create_task(module.summarize_run(run.id, async_session_factory))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        async with async_session_factory() as db:
            assert await interrupt_delivery(db, lease)
            await db.commit()
        release.set()
        with pytest.raises(DeliveryOwnershipLost):
            await task
    finally:
        current_delivery.reset(scope)
        if not task.done():
            release.set()
            await task

    async with async_session_factory() as db:
        stored = await db.get(AgentRun, run.id)
        assert stored.summary_status != "completed"
        assert stored.asked is None


async def test_tune_delivery_replay_and_concurrent_turns_are_durable(
    async_session_factory, ai_rows, monkeypatch
):
    from src.services.execution import tuning_service as module

    async with async_session_factory() as db:
        run = await _new_run(db)
        first = await _claim(db, f"ai-tune-{uuid4()}", run.id)
        second = await _claim(db, f"ai-tune-{uuid4()}", run.id)
        third = await _claim(db, f"ai-tune-{uuid4()}", run.id)
    await ai_rows(run.id, first.id)
    await ai_rows(run.id, second.id)
    await ai_rows(run.id, third.id)

    calls = 0
    concurrent_admissions = asyncio.Event()

    async def complete(**_kwargs):
        nonlocal calls
        calls += 1
        number = calls
        lease = current_delivery.get()
        # The provider starts after admission committed, not while a lease
        # row lock is held. Check from an independent database transaction.
        async with async_session_factory() as db:
            persisted = (await db.execute(select(AgentRunFlagConversation).where(
                AgentRunFlagConversation.run_id == run.id
            ))).scalar_one()
            assert any(
                message.get("delivery_id") == str(lease.id)
                and message["kind"] == "user"
                for message in persisted.messages
            )
        if number == 3:
            concurrent_admissions.set()
        if number >= 2:
            await asyncio.wait_for(concurrent_admissions.wait(), 10)
        return _response(f"reply-{number}")

    client = type("Client", (), {})()
    client.provider_name = "test"
    client.complete = complete
    monkeypatch.setattr(module, "get_tuning_client", _async_value((client, "test-model")))
    monkeypatch.setattr(module, "record_ai_usage", AsyncMock())
    monkeypatch.setattr(module, "get_shared_redis", AsyncMock())

    async def turn(lease, text):
        scope = current_delivery.set(lease)
        try:
            async with async_session_factory() as db:
                return await module.append_user_message_and_reply(run.id, text, db)
        finally:
            current_delivery.reset(scope)

    await turn(first, "first turn")
    await turn(first, "first turn")
    await asyncio.gather(turn(second, "second turn"), turn(third, "third turn"))

    async with async_session_factory() as db:
        conversation = (
            await db.execute(
                select(AgentRunFlagConversation).where(
                    AgentRunFlagConversation.run_id == run.id
                )
            )
        ).scalar_one()
        kinds = [message["kind"] for message in conversation.messages]
        assert kinds.count("user") == 3
        assert kinds.count("assistant") == 3
        for lease in (first, second, third):
            assert [
                item["kind"] for item in conversation.messages
                if item.get("delivery_id") == str(lease.id)
            ] == ["user", "assistant"]
    assert calls == 3


def _async_value(value):
    async def resolve(*_args, **_kwargs):
        return value

    return resolve
