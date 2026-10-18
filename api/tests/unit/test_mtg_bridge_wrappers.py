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
async def test_worker_mounts_canonical_bridge_handlers_but_engine_is_refused(
    async_session_factory,
):
    from datetime import datetime, timezone
    import httpx
    from uuid import uuid4

    from src.core.security import mint_engine_token
    from src.models.orm.executions import WorkflowExecutionAttempt
    from src.routers.roles import router as roles_router
    from src.services.execution.worker_sdk_http import (
        ROLES_ROUTE_METHODS,
        build_worker_sdk_app,
    )
    from tests.helpers.live_execution import (
        create_live_execution,
        delete_live_execution,
    )

    path = "/api/roles/{role_id}/bridge-grants"
    assert ROLES_ROUTE_METHODS[path] == frozenset({"GET", "PUT"})
    app = build_worker_sdk_app()
    canonical = [
        route for route in roles_router.routes if getattr(route, "path", None) == path
    ]
    mounted = [route for route in app.routes if getattr(route, "path", None) == path]
    assert len(canonical) == len(mounted) == 2
    assert {id(route) for route in mounted} == {id(route) for route in canonical}

    # Reused APIRoutes retain their original dependency provider. Exercise real
    # bearer authentication instead of app overrides that never reach them.
    execution_id, attempt_token = uuid4(), uuid4()
    await create_live_execution(async_session_factory, str(execution_id))
    try:
        now = datetime.now(timezone.utc)
        async with async_session_factory() as db:
            db.add(
                WorkflowExecutionAttempt(
                    execution_id=execution_id,
                    attempt_number=1,
                    claim_token=attempt_token,
                    status="claimed",
                    phase="claim",
                    published_at=now,
                    claimed_at=now,
                )
            )
            await db.commit()
        token, _ = mint_engine_token(
            execution_id=str(execution_id),
            attempt_token=str(attempt_token),
            global_repo_access=True,
            timeout_seconds=300,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://worker-local"
        ) as client:
            url = f"/api/roles/{uuid4()}/bridge-grants"
            anonymous = await client.get(url)
            headers = {"Authorization": f"Bearer {token}"}
            read = await client.get(url, headers=headers)
            write = await client.put(
                url,
                headers=headers,
                json={
                    "permissions": ["devices.execute"],
                    "expected_permissions": [],
                    "expected_version": "a" * 64,
                },
            )
        assert anonymous.status_code == 401
        assert read.status_code == write.status_code == 403
        assert "Only a Platform Admin" in read.json()["detail"]
        assert "Only a Platform Admin" in write.json()["detail"]
    finally:
        await delete_live_execution(async_session_factory, str(execution_id))
