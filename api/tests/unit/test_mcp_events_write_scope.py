"""Canonical event MCP mutations preserve REST's actual admin authorization."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from src.core.auth import get_current_active_user, get_execution_context
from src.core.database import get_db
from src.core.principal import UserPrincipal
from src.routers.events import router
from src.services.mcp_server.tools import events as events_tool


@pytest.mark.asyncio
@pytest.mark.parametrize("persona", ["member", "external", "provider"])
@pytest.mark.parametrize("global_scope", [True, False])
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_mcp_event_mutation_is_denied_by_real_rest_gate(monkeypatch, persona, global_scope, operation):
    principal = UserPrincipal(user_id=uuid4(), email="test@example.com", organization_id=uuid4(),
                              is_external=persona == "external", is_provider_org=persona == "provider")
    database = AsyncMock()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_active_user] = lambda: principal
    app.dependency_overrides[get_execution_context] = lambda: SimpleNamespace(user=principal, org_id=principal.organization_id, db=database)

    async def db_override():
        yield database

    app.dependency_overrides[get_db] = db_override
    source_id = str(uuid4())
    calls = []

    @asynccontextmanager
    async def no_http(_context):
        yield object()

    async def resolve_ref(_context, kind, value):
        assert kind == "event_source"
        assert value == source_id
        return value

    async def real_rest(_context, method, path, **kwargs):
        calls.append((method, path))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.request(method, path, json=kwargs.get("json_body"), params=kwargs.get("params"))
        return response.status_code, response.json()

    monkeypatch.setattr(events_tool, "call_rest", real_rest)
    monkeypatch.setattr(events_tool, "rest_client", no_http)
    monkeypatch.setattr(events_tool, "_resolve_ref", resolve_ref)
    if operation == "update":
        result = await events_tool.bifrost_event_source_update(principal, source_id, name="renamed", scope="global" if global_scope else None)
    else:
        result = await events_tool.bifrost_event_source_delete(principal, source_id)
    assert calls == [("PATCH" if operation == "update" else "DELETE", f"/api/events/sources/{source_id}")]
    assert result.structured_content["status_code"] == 403
    database.execute.assert_not_awaited()
    database.flush.assert_not_awaited()
