from contextlib import asynccontextmanager

import pytest
from src.core import websocket_tickets
from src.core.cache.keys import TTL_WEBSOCKET_TICKET


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int, bool]] = []

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)


@pytest.mark.asyncio
async def test_ticket_is_hashed_short_lived_and_single_use(monkeypatch) -> None:
    redis = FakeRedis()

    @asynccontextmanager
    async def fake_redis():
        yield redis

    monkeypatch.setattr(websocket_tickets, "get_redis", fake_redis)
    monkeypatch.setattr(
        websocket_tickets.secrets,
        "token_urlsafe",
        lambda size: "a" * 43,
    )

    ticket = await websocket_tickets.issue_websocket_ticket("access.jwt")

    assert ticket == "a" * 43
    key, value, ttl, nx = redis.set_calls[0]
    assert ticket not in key
    assert value == "access.jwt"
    assert ttl == TTL_WEBSOCKET_TICKET
    assert nx is True
    assert await websocket_tickets.consume_websocket_ticket(ticket) == "access.jwt"
    assert await websocket_tickets.consume_websocket_ticket(ticket) is None


@pytest.mark.asyncio
async def test_malformed_ticket_never_touches_redis(monkeypatch) -> None:
    called = False

    @asynccontextmanager
    async def fake_redis():
        nonlocal called
        called = True
        yield FakeRedis()

    monkeypatch.setattr(websocket_tickets, "get_redis", fake_redis)

    assert await websocket_tickets.consume_websocket_ticket("not a ticket") is None
    assert called is False
