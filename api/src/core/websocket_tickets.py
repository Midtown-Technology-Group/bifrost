"""Short-lived, single-use authentication tickets for browser WebSockets."""

from __future__ import annotations

import hashlib
import re
import secrets

from src.core.cache.keys import TTL_WEBSOCKET_TICKET, websocket_ticket_key
from src.core.cache.redis_client import get_redis

_TICKET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _ticket_key(ticket: str) -> str | None:
    if _TICKET_PATTERN.fullmatch(ticket) is None:
        return None
    digest = hashlib.sha256(ticket.encode("ascii")).hexdigest()
    return websocket_ticket_key(digest)


async def issue_websocket_ticket(access_token: str) -> str:
    """Store an access token behind an opaque ticket for at most 30 seconds."""

    # A collision is cryptographically negligible, but NX keeps the contract
    # explicit and the small retry loop avoids ever replacing another ticket.
    async with get_redis() as redis:
        for _ in range(3):
            ticket = secrets.token_urlsafe(32)
            key = _ticket_key(ticket)
            if key is not None and await redis.set(
                key,
                access_token,
                ex=TTL_WEBSOCKET_TICKET,
                nx=True,
            ):
                return ticket
    raise RuntimeError("Unable to allocate WebSocket authentication ticket")


async def consume_websocket_ticket(ticket: str) -> str | None:
    """Atomically consume a ticket and return its access token once."""

    key = _ticket_key(ticket)
    if key is None:
        return None
    async with get_redis() as redis:
        return await redis.getdel(key)
