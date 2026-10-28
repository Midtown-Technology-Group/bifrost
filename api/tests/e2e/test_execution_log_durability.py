"""Native execution-log durability contracts. Zero dispatched jobs.

Delivery is at-least-once, not exactly-once: replay and competing flushers
may insert duplicate messages and sequence values (the index is not unique).
Run through ./test.sh tests/e2e/test_execution_log_durability.py -v --durations=0
"""

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete, select

from bifrost._logging import flush_logs_to_postgres
from src.core.cache import cleanup_execution_cache, get_redis
from src.core.cache.keys import execution_logs_stream_key
from src.models.orm import Execution, ExecutionLog

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def entry(message):
    return {
        "timestamp": "2026-10-28T13:00:00+00:00",
        "level": "INFO",
        "message": message,
        "metadata": "{}",
    }


@pytest_asyncio.fixture
async def log_stream(async_session_factory):
    execution_id = uuid4()
    key = execution_logs_stream_key(str(execution_id))
    async with get_redis() as redis:
        try:
            async with async_session_factory() as session:
                session.add(Execution(
                    id=execution_id,
                    workflow_name="log-durability",
                    executed_by_name="log-durability-test",
                ))
                await session.commit()
            await redis.xadd(key, entry("original"))
            yield execution_id, key, redis
        finally:
            try:
                await redis.delete(key)
            finally:
                async with async_session_factory() as session:
                    await session.execute(delete(ExecutionLog).where(
                        ExecutionLog.execution_id == execution_id,
                    ))
                    await session.execute(delete(Execution).where(
                        Execution.id == execution_id,
                    ))
                    await session.commit()


async def messages(factory, execution_id):
    async with factory() as session:
        return list(await session.scalars(select(ExecutionLog.message).where(
            ExecutionLog.execution_id == execution_id,
        ).order_by(ExecutionLog.id)))


async def test_caller_rollback_preserves_source_and_unrelated_state(
    log_stream, async_session_factory,
):
    execution_id, key, redis = log_stream
    async with async_session_factory() as session:
        parent = await session.get(Execution, execution_id)
        parent.workflow_name = "must-not-commit"
        assert await flush_logs_to_postgres(execution_id, session=session) == 1
        await session.flush()
        await session.rollback()
    assert await messages(async_session_factory, execution_id) == []
    async with async_session_factory() as session:
        assert (await session.get(Execution, execution_id)).workflow_name == "log-durability"
    assert [data["message"] for _, data in await redis.xrange(key)] == ["original"]


async def test_concurrent_append_survives_owned_flush_and_terminal_cleanup(
    log_stream, async_session_factory, monkeypatch,
):
    execution_id, key, producer = log_stream
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)
    real_xrange = Redis.xrange

    async def append_after_snapshot(reader, name, *args, **kwargs):
        snapshot = await real_xrange(reader, name, *args, **kwargs)
        if name == key:
            assert reader is not producer
            await producer.xadd(key, entry("late"))
        return snapshot

    with monkeypatch.context() as patch:
        patch.setattr(Redis, "xrange", append_after_snapshot)
        assert await flush_logs_to_postgres(execution_id) == 1
    await cleanup_execution_cache(str(execution_id))
    assert await messages(async_session_factory, execution_id) == ["original"]
    assert [data["message"] for _, data in await producer.xrange(key)] == ["late"]


async def test_owned_commit_failure_retains_source(log_stream, async_session_factory, monkeypatch):
    execution_id, key, redis = log_stream
    async with async_session_factory() as session:
        await session.execute(delete(Execution).where(Execution.id == execution_id))
        await session.commit()
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)
    assert await flush_logs_to_postgres(execution_id) == 0
    assert await messages(async_session_factory, execution_id) == []
    assert [data["message"] for _, data in await redis.xrange(key)] == ["original"]


async def test_failed_ack_replays_at_least_once(log_stream, async_session_factory, monkeypatch):
    execution_id, key, redis = log_stream
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)

    async def failed_ack(*args, **kwargs):
        raise ConnectionError("ack unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(Redis, "xdel", failed_ack)
        # Return value counts durable rows even if acknowledgement fails.
        assert await flush_logs_to_postgres(execution_id) == 1
    assert await messages(async_session_factory, execution_id) == ["original"]
    assert len(await redis.xrange(key)) == 1
    assert await flush_logs_to_postgres(execution_id) == 1
    assert await messages(async_session_factory, execution_id) == ["original", "original"]
    assert await redis.xrange(key) == []
    assert await flush_logs_to_postgres(execution_id) == 0


async def test_competing_flushers_allow_duplicates_without_loss(
    log_stream, async_session_factory, monkeypatch,
):
    execution_id, key, redis = log_stream
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)
    real_xrange = Redis.xrange
    both_read = asyncio.Event()
    readers = 0

    async def snapshot_barrier(reader, name, *args, **kwargs):
        nonlocal readers
        snapshot = await real_xrange(reader, name, *args, **kwargs)
        if name == key:
            readers += 1
            if readers == 2:
                both_read.set()
            await both_read.wait()
        return snapshot

    with monkeypatch.context() as patch:
        patch.setattr(Redis, "xrange", snapshot_barrier)
        counts = await asyncio.gather(
            flush_logs_to_postgres(execution_id), flush_logs_to_postgres(execution_id),
        )
    assert counts == [1, 1]
    assert await messages(async_session_factory, execution_id) == ["original", "original"]
    assert await redis.xrange(key) == []
