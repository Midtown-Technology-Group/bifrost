"""Execution log streams retain a hard Redis expiry through acknowledgement."""

from uuid import uuid4

import pytest

from bifrost._logging import (
    acknowledge_persisted_logs,
    append_log_to_stream,
    append_log_to_stream_async,
    close_thread_redis,
)
from src.core.cache import get_redis
from src.core.cache.keys import TTL_EXECUTION_LOGS, execution_logs_stream_key

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_execution_log_streams_expire_after_sync_async_and_acknowledgement():
    sync_execution_id = str(uuid4())
    async_execution_id = str(uuid4())
    sync_key = execution_logs_stream_key(sync_execution_id)
    async_key = execution_logs_stream_key(async_execution_id)

    try:
        sync_entry_id = append_log_to_stream(
            sync_execution_id, "INFO", "sync retention proof"
        )
        async_entry_id = await append_log_to_stream_async(
            async_execution_id, "INFO", "async retention proof"
        )
        assert sync_entry_id is not None
        assert async_entry_id is not None

        async with get_redis() as redis:
            for key in (sync_key, async_key):
                ttl = await redis.ttl(key)
                assert 0 < ttl <= TTL_EXECUTION_LOGS

        await acknowledge_persisted_logs(
            [(sync_key, [sync_entry_id]), (async_key, [async_entry_id])]
        )

        async with get_redis() as redis:
            for key in (sync_key, async_key):
                assert await redis.xlen(key) == 0
                ttl = await redis.ttl(key)
                assert 0 < ttl <= TTL_EXECUTION_LOGS
    finally:
        async with get_redis() as redis:
            await redis.delete(sync_key, async_key)
        close_thread_redis()
