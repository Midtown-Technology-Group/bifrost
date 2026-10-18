"""Event administration uses REST's real admin dependency after MCP conversion."""

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


@pytest.fixture
def event_rest_boundary():
    """Keep the real router and admin dependency; replace only authentication and IO."""
    principal = UserPrincipal(user_id=uuid4(), email="test@example.com", organization_id=uuid4())
    database = AsyncMock()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_active_user] = lambda: principal
    app.dependency_overrides[get_execution_context] = lambda: SimpleNamespace(user=principal, org_id=principal.organization_id, db=database)

    async def db_override():
        yield database

    app.dependency_overrides[get_db] = db_override
    return app, principal, database


_SOURCE = "00000000-0000-4000-8000-000000000001"
_SUB = "00000000-0000-4000-8000-000000000002"


@pytest.mark.asyncio
@pytest.mark.parametrize("persona", ["member", "external", "provider"])
@pytest.mark.parametrize(("method", "path", "body"), [
    ("GET", "/sources", None),
    ("GET", f"/sources/{_SOURCE}", None),
    ("POST", "/sources", {"name": "topic", "source_type": "topic", "event_type": "test.topic"}),
    ("PATCH", f"/sources/{_SOURCE}", {"name": "renamed"}),
    ("DELETE", f"/sources/{_SOURCE}", None),
    ("GET", f"/sources/{_SOURCE}/subscriptions", None),
    ("POST", f"/sources/{_SOURCE}/subscriptions", {"workflow_id": _SUB}),
    ("PATCH", f"/sources/{_SOURCE}/subscriptions/{_SUB}", {"is_active": False}),
    ("DELETE", f"/sources/{_SOURCE}/subscriptions/{_SUB}", None),
    ("GET", "/adapters", None),
])
async def test_event_administration_refuses_non_admin_before_io(event_rest_boundary, persona, method, path, body):
    app, principal, database = event_rest_boundary
    principal.is_external = persona == "external"
    principal.is_provider_org = persona == "provider"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, "/api/events" + path, json=body)
    assert response.status_code == 403, response.text
    database.execute.assert_not_awaited()
    database.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_admin_can_list_adapters(event_rest_boundary):
    app, principal, _ = event_rest_boundary
    principal.is_superuser = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/events/adapters")
    assert response.status_code == 200, response.text
