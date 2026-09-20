"""The selected publish path must preserve caller transaction ownership."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.config import Settings
from src.jobs import rabbitmq as delivery


def test_backend_flag_defaults_to_rabbit_and_rejects_typo(monkeypatch):
    monkeypatch.delenv("BIFROST_WORK_DELIVERY_BACKEND", raising=False)
    assert Settings(_env_file=None).work_delivery_backend == "rabbitmq"
    monkeypatch.setenv("BIFROST_WORK_DELIVERY_BACKEND", "postgress")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


@pytest.mark.asyncio
async def test_postgres_publish_joins_caller_transaction(monkeypatch):
    from src.services import work_delivery_store

    monkeypatch.setattr(
        delivery,
        "get_settings",
        lambda: SimpleNamespace(work_delivery_backend="postgres"),
    )
    broker = AsyncMock(
        side_effect=AssertionError("PostgreSQL publish must not open Rabbit")
    )
    monkeypatch.setattr(delivery.rabbitmq, "init_pools", broker)
    enqueue = AsyncMock()
    monkeypatch.setattr(work_delivery_store, "enqueue_delivery", enqueue)
    db = AsyncMock()
    await delivery.publish_message("agent-runs", {"run_id": "run-one"}, db=db)
    enqueue.assert_awaited_once()
    assert enqueue.call_args.args == (db,)
    assert enqueue.call_args.kwargs["message_id"] == "run-one"
    assert enqueue.call_args.kwargs["envelope"]["body"] == {"run_id": "run-one"}
    db.commit.assert_not_awaited()
    broker.assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_publish_commits_before_return(monkeypatch):
    from src.core import database
    from src.services import work_delivery_store

    monkeypatch.setattr(
        delivery,
        "get_settings",
        lambda: SimpleNamespace(work_delivery_backend="postgres"),
    )
    db = AsyncMock()
    calls = []

    @asynccontextmanager
    async def session():
        yield db

    async def enqueue(*args, **kwargs):
        calls.append("enqueue")

    async def commit():
        calls.append("commit")

    db.commit.side_effect = commit
    monkeypatch.setattr(database, "get_db_context", session)
    monkeypatch.setattr(work_delivery_store, "enqueue_delivery", enqueue)
    await delivery.publish_message("agent-summarization", {"run_id": "run-one"})
    assert calls == ["enqueue", "commit"]


@pytest.mark.asyncio
async def test_failed_commit_is_not_reported_as_acceptance(monkeypatch):
    from src.core import database
    from src.services import work_delivery_store

    monkeypatch.setattr(
        delivery,
        "get_settings",
        lambda: SimpleNamespace(work_delivery_backend="postgres"),
    )
    db = AsyncMock()
    db.commit.side_effect = ConnectionError("commit not acknowledged")

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(database, "get_db_context", session)
    monkeypatch.setattr(work_delivery_store, "enqueue_delivery", AsyncMock())
    with pytest.raises(ConnectionError, match="commit not acknowledged"):
        await delivery.publish_message("agent-summarization", {"run_id": "run-one"})
