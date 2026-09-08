"""Redis log acknowledgement follows commit and preserves concurrent appends."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from bifrost._logging import acknowledge_persisted_logs, flush_logs_to_postgres


@pytest.mark.asyncio
async def test_rollback_preserves_source_then_commit_acknowledges_exact_ids(
    monkeypatch,
):
    from src.core import cache

    stream = {
        "1-0": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "INFO",
            "message": "first",
            "metadata": "{}",
        }
    }

    async def read(*args, **kwargs):
        return list(stream.items())

    async def xdel(key, *ids):
        for entry in ids:
            stream.pop(entry, None)

    redis = SimpleNamespace(
        xrange=AsyncMock(side_effect=read),
        xdel=AsyncMock(side_effect=xdel),
        delete=AsyncMock(),
    )

    @asynccontextmanager
    async def get_redis():
        yield redis

    monkeypatch.setattr(cache, "get_redis", get_redis)
    session = SimpleNamespace(
        add_all=MagicMock(), commit=AsyncMock(), rollback=AsyncMock()
    )
    pending = []
    assert (
        await flush_logs_to_postgres(
            str(uuid4()), session=session, pending_acknowledgements=pending
        )
        == 1
    )
    await session.rollback()
    assert "1-0" in stream
    redis.xdel.assert_not_awaited()
    # A new transaction retries the insert from the intact source.
    pending = []
    assert (
        await flush_logs_to_postgres(
            str(uuid4()), session=session, pending_acknowledgements=pending
        )
        == 1
    )
    await session.commit()
    stream["2-0"] = {"message": "appended during commit"}
    await acknowledge_persisted_logs(pending)
    assert list(stream) == ["2-0"]
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_acknowledgement_failure_leaves_source_recoverable(monkeypatch):
    from src.core import cache

    redis = SimpleNamespace(xdel=AsyncMock(side_effect=ConnectionError("unavailable")))

    @asynccontextmanager
    async def get_redis():
        yield redis

    monkeypatch.setattr(cache, "get_redis", get_redis)
    pending = [("bifrost:logs:test", ["1-0"])]
    with pytest.raises(ConnectionError):
        await acknowledge_persisted_logs(pending)
    assert pending == [("bifrost:logs:test", ["1-0"])]
