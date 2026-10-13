from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.services.mcp_server.tools.db import get_tool_db


@pytest.mark.asyncio
async def test_get_tool_db_uses_attached_session() -> None:
    session = AsyncMock()
    async with get_tool_db(SimpleNamespace(session=session)) as yielded:
        assert yielded is session


@pytest.mark.asyncio
async def test_get_tool_db_falls_back_when_context_has_no_session_attribute() -> None:
    session = AsyncMock()

    @asynccontextmanager
    async def fallback():
        yield session

    with patch("src.core.database.get_db_context", fallback):
        async with get_tool_db(SimpleNamespace()) as yielded:
            assert yielded is session
