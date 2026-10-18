"""SDK/MCP and CLI preserve the bridge's mandatory concurrency preconditions."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from bifrost.commands.roles import roles_group
from src.services.mcp_server.tools import roles


@asynccontextmanager
async def _http(_context):
    yield object()


@pytest.mark.asyncio
async def test_mcp_forwards_exact_bridge_set_and_version_to_rest():
    context = object()
    call_rest = AsyncMock(return_value=(200, {"permissions": ["devices.execute"]}))
    with (
        patch.object(roles, "rest_client", _http),
        patch("bifrost.refs.RefResolver") as resolver,
        patch.object(roles, "call_rest", call_rest),
    ):
        resolver.return_value.resolve = AsyncMock(return_value="role-uuid")
        await roles.bifrost_role_bridge_grants_update(
            context, "role", ["devices.execute"], ["agents.promote"], "a" * 64
        )
    call_rest.assert_awaited_once_with(
        context,
        "PUT",
        "/api/roles/role-uuid/bridge-grants",
        json_body={
            "permissions": ["devices.execute"],
            "expected_permissions": ["agents.promote"],
            "expected_version": "a" * 64,
        },
    )


@pytest.mark.asyncio
async def test_mcp_refuses_nonbridge_permission_before_rest():
    with patch.object(roles, "call_rest", new_callable=AsyncMock) as call:
        result = await roles.bifrost_role_bridge_grants_update(
            object(), "role", ["roles.readwrite"], [], "a" * 64
        )
    assert "Invalid bridge grants" in result.structured_content["error"]
    call.assert_not_awaited()


def test_cli_requires_version_and_rejects_nonbridge_choices():
    runner = CliRunner()
    missing = runner.invoke(
        roles_group, ["set-bridge-grants", "role", "--permission", "devices.execute"]
    )
    assert missing.exit_code == 2
    assert "--expected-version" in missing.output
    invalid = runner.invoke(
        roles_group,
        [
            "set-bridge-grants",
            "role",
            "--permission",
            "roles.readwrite",
            "--expected-version",
            "a" * 64,
        ],
    )
    assert invalid.exit_code == 2
    assert "Invalid value" in invalid.output


@pytest.mark.asyncio
async def test_worker_mounts_canonical_bridge_handlers_but_engine_is_refused():
    import httpx
    from uuid import uuid4

    from src.core.auth import get_current_active_user
    from src.core.database import get_db
    from src.core.principal import UserPrincipal
    from src.services.execution.worker_sdk_http import (
        ROLES_ROUTE_METHODS,
        build_worker_sdk_app,
    )

    path = "/api/roles/{role_id}/bridge-grants"
    assert ROLES_ROUTE_METHODS[path] == frozenset({"GET", "PUT"})
    app = build_worker_sdk_app()
    principal = UserPrincipal(
        user_id=uuid4(),
        email="engine@example.test",
        organization_id=None,
        is_superuser=True,
        engine_execution_id=uuid4(),
        is_engine_token=True,
    )

    async def current_engine():
        return principal

    async def database():
        yield object()

    app.dependency_overrides[get_current_active_user] = current_engine
    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://worker-local"
    ) as client:
        url = f"/api/roles/{uuid4()}/bridge-grants"
        read = await client.get(url)
        write = await client.put(
            url,
            json={
                "permissions": ["devices.execute"],
                "expected_permissions": [],
                "expected_version": "a" * 64,
            },
        )
    assert read.status_code == write.status_code == 403
    assert "Only a Platform Admin" in read.json()["detail"]
