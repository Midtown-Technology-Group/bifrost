"""Public API bridge maintenance preserves unrelated rows and rejects stale saves."""

from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import importlib
import inspect

import pytest


@pytest.mark.e2e
def test_bridge_admin_current_set_version_contract(e2e_client, platform_admin):
    headers = platform_admin.headers
    created = e2e_client.post(
        "/api/roles", headers=headers, json={"name": f"bridge-api-{uuid4()}"}
    )
    assert created.status_code == 201, created.text
    role_id = created.json()["id"]
    path = f"/api/roles/{role_id}/bridge-grants"
    try:
        identity = e2e_client.put(
            f"/api/roles/{role_id}/permissions",
            headers=headers,
            json={"permissions": ["users.read"]},
        )
        assert identity.status_code == 200, identity.text
        before = e2e_client.get(path, headers=headers)
        assert before.status_code == 200, before.text
        body = {
            "permissions": ["devices.execute"],
            "expected_permissions": before.json()["permissions"],
            "expected_version": before.json()["version"],
        }
        saved = e2e_client.put(path, headers=headers, json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["permissions"] == ["devices.execute"]
        stale = e2e_client.put(path, headers=headers, json=body)
        assert stale.status_code == 409, stale.text
        held = e2e_client.get(f"/api/roles/{role_id}/permissions", headers=headers)
        assert held.status_code == 200, held.text
        assert {row["permission"] for row in held.json()["permissions"]} == {
            "users.read",
            "devices.execute",
        }
        invalid = e2e_client.patch(
            f"/api/roles/{role_id}",
            headers=headers,
            json={"permissions": {"can_execute_devices": True}},
        )
        assert invalid.status_code == 422, invalid.text
    finally:
        removed = e2e_client.delete(f"/api/roles/{role_id}", headers=headers)
        assert removed.status_code == 204, removed.text


@pytest.mark.e2e
def test_concurrent_bridge_edits_have_one_exact_version_winner(
    e2e_client, platform_admin
):
    headers = platform_admin.headers
    created = e2e_client.post(
        "/api/roles", headers=headers, json={"name": f"bridge-race-{uuid4()}"}
    )
    assert created.status_code == 201, created.text
    role_id = created.json()["id"]
    path = f"/api/roles/{role_id}/bridge-grants"
    try:
        before = e2e_client.get(path, headers=headers)
        assert before.status_code == 200, before.text

        def save(permission):
            return e2e_client.put(
                path,
                headers=headers,
                json={
                    "permissions": [permission],
                    "expected_permissions": before.json()["permissions"],
                    "expected_version": before.json()["version"],
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(save, ["devices.execute", "agents.promote"]))
        assert sorted(response.status_code for response in responses) == [200, 409], [
            response.text for response in responses
        ]
        winner = next(
            response.json() for response in responses if response.status_code == 200
        )
        after = e2e_client.get(path, headers=headers)
        assert after.status_code == 200, after.text
        assert after.json() == winner
    finally:
        removed = e2e_client.delete(f"/api/roles/{role_id}", headers=headers)
        assert removed.status_code == 204, removed.text


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission", ["devices.readwrite", "devices.execute", "agents.promote"]
)
async def test_real_rest_and_mutation_wrappers_preserve_bridge_grant_ceiling(
    e2e_client,
    platform_admin,
    org1,
    async_session_factory,
    monkeypatch,
    permission,
):
    """A real privileged-access delegate remains below Admin at every tested surface."""
    from uuid import UUID

    import click
    import httpx

    from bifrost.client import BifrostAuthorizationError, BifrostClient
    from bifrost.commands.roles import roles_group, set_bridge_grants
    from bifrost.refs import RefResolver
    from src.services.mcp_server.server import MCPContext
    from src.services.mcp_server.tools.roles import bifrost_role_bridge_grants_update
    from src.services.role_permissions import set_role_permissions
    from tests.e2e.fixtures.device_bridge import set_bridge_grants as api_bridge_grants
    from tests.e2e.fixtures.run_as import USER_ROLE_ID, ok
    from tests.e2e.fixtures.setup import _register_and_authenticate_user
    from tests.e2e.fixtures.users import E2EUser

    admin = platform_admin.headers
    tag = uuid4().hex
    role_ids, user_ids = [], []
    monkeypatch.setenv("BIFROST_MCP_HTTP_BRIDGE_URL", str(e2e_client.base_url))

    def create_role(label):
        role = ok(
            e2e_client.post(
                "/api/roles", headers=admin, json={"name": f"bridge-{label}-{tag}"}
            ),
            201,
        )
        role_ids.append(role["id"])
        return role["id"]

    def assignments(user_id):
        return ok(
            e2e_client.get(f"/api/users/{user_id}/role-assignments", headers=admin)
        )

    try:
        bridge_id, delegate_role = create_role("protected"), create_role("delegate")
        api_bridge_grants(e2e_client, admin, bridge_id, [permission])
        actor = E2EUser(
            email=f"bridge-delegate-{tag}@example.com",
            password="BridgeDelegate123!",
            name=f"Bridge Delegate {tag}",
            organization_id=UUID(org1["id"]),
        )
        created_actor = ok(
            e2e_client.post(
                "/api/users",
                headers=admin,
                json={
                    "email": actor.email,
                    "name": actor.name,
                    "organization_id": org1["id"],
                    "trigger_automation": False,
                },
            ),
            201,
        )
        user_ids.append(created_actor["id"])
        actor = _register_and_authenticate_user(actor)
        assert actor.user_id == UUID(created_actor["id"])
        target = ok(
            e2e_client.post(
                "/api/users",
                headers=admin,
                json={
                    "email": f"bridge-target-{tag}@example.com",
                    "name": "Bridge Target",
                    "organization_id": org1["id"],
                    "trigger_automation": False,
                },
            ),
            201,
        )
        target_id = target["id"]
        user_ids.append(target_id)

        async def seed_exact_delegate_permissions():
            # Privileged Access is intentionally absent from the general identity
            # editor. Seed this adversarial persona in the test DB; every action
            # under test still crosses the real authenticated REST boundary.
            async with async_session_factory() as session:
                await set_role_permissions(
                    session,
                    role_id=UUID(delegate_role),
                    permissions=frozenset(
                        {
                            "roles.read",
                            "roles.readwrite",
                            "roleassignments.read",
                            "roleassignments.readwrite",
                            "privilegedaccess.readwrite",
                            "userlifecycle.readwrite",
                        }
                    ),
                )
                await session.commit()

        await seed_exact_delegate_permissions()
        ok(
            e2e_client.put(
                f"/api/users/{actor.user_id}/role-assignments",
                headers=admin,
                json={
                    "base_role_id": USER_ROLE_ID,
                    "additional": [
                        {
                            "role_id": delegate_role,
                            "boundaries": [
                                {"kind": "organization", "organization_id": org1["id"]},
                                {"kind": "platform"},
                            ],
                        }
                    ],
                },
            )
        )
        delegate = actor.headers
        before = assignments(target_id)
        denied = e2e_client.post(
            f"/api/roles/{bridge_id}/users",
            headers=delegate,
            json={"user_ids": [target_id]},
        )
        assert denied.status_code == 403, denied.text
        assert assignments(target_id) == before

        async def sdk_and_editor_wrappers():
            sdk_module = importlib.import_module("bifrost.roles")
            for person, allowed in ((actor, False), (platform_admin, True)):
                assert person.access_token is not None
                client = BifrostClient(str(e2e_client.base_url), person.access_token)
                try:
                    with monkeypatch.context() as scope:
                        scope.setattr(sdk_module, "get_client", lambda: client)
                        if allowed:
                            await sdk_module.roles.assign_users(bridge_id, [target_id])
                        else:
                            with pytest.raises(BifrostAuthorizationError):
                                await sdk_module.roles.assign_users(
                                    bridge_id, [target_id]
                                )
                    # Invoke the actual CLI async callback with its real client.
                    # Click flag parsing is covered separately by wrapper tests.
                    state = ok(
                        e2e_client.get(
                            f"/api/roles/{bridge_id}/bridge-grants", headers=admin
                        )
                    )
                    callback = inspect.unwrap(set_bridge_grants.callback)
                    kwargs = dict(
                        ctx=click.Context(roles_group),
                        ref=bridge_id,
                        permissions=(),
                        expected_permissions=(permission,),
                        expected_version=state["version"],
                        client=client,
                        resolver=RefResolver(client),
                    )
                    if allowed:
                        await callback(**kwargs)
                    else:
                        with pytest.raises(httpx.HTTPStatusError) as refused:
                            await callback(**kwargs)
                        assert refused.value.response.status_code == 403
                    state = ok(
                        e2e_client.get(
                            f"/api/roles/{bridge_id}/bridge-grants", headers=admin
                        )
                    )
                    assert state["permissions"] == ([] if allowed else [permission])
                    context = MCPContext(
                        user_id=person.user_id,
                        org_id=person.organization_id,
                        user_email=person.email,
                        is_platform_admin=allowed,
                    )
                    result = await bifrost_role_bridge_grants_update(
                        context,
                        bridge_id,
                        [permission] if allowed else [],
                        state["permissions"],
                        state["version"],
                    )
                    assert result.structured_content is not None
                    if allowed:
                        assert result.structured_content.get("permissions") == [
                            permission
                        ]
                    else:
                        assert "HTTP 403" in result.structured_content["error"]
                    after = ok(
                        e2e_client.get(
                            f"/api/roles/{bridge_id}/bridge-grants", headers=admin
                        )
                    )
                    assert after["permissions"] == [permission]
                finally:
                    await client.close()

        await sdk_and_editor_wrappers()
        assigned = assignments(target_id)
        assert {item["role_id"] for item in assigned["additional"]} == {bridge_id}
        denied = e2e_client.delete(
            f"/api/roles/{bridge_id}/users/{target_id}", headers=delegate
        )
        assert denied.status_code == 403, denied.text
        assert assignments(target_id) == assigned
        ok(
            e2e_client.delete(
                f"/api/roles/{bridge_id}/users/{target_id}", headers=admin
            ),
            204,
        )

        for base in (bridge_id, USER_ROLE_ID):
            body = {"base_role_id": base, "additional": []}
            denied = e2e_client.put(
                f"/api/users/{target_id}/role-assignments", headers=delegate, json=body
            )
            assert denied.status_code == 403, denied.text
            saved = ok(
                e2e_client.put(
                    f"/api/users/{target_id}/role-assignments", headers=admin, json=body
                )
            )
            assert saved["base_role"]["id"] == base
        for wanted in ([bridge_id], []):
            body = {
                "operation": "replace_roles",
                "user_ids": [target_id],
                "role_ids": wanted,
            }
            before = assignments(target_id)
            denied = ok(
                e2e_client.patch("/api/users/bulk", headers=delegate, json=body)
            )
            assert denied["succeeded"] == []
            assert [item["user_id"] for item in denied["failed"]] == [target_id]
            assert assignments(target_id) == before
            saved = ok(e2e_client.patch("/api/users/bulk", headers=admin, json=body))
            assert saved == {"succeeded": [target_id], "failed": []}
        rejected = e2e_client.put(
            f"/api/roles/{delegate_role}/permissions",
            headers=delegate,
            json={"permissions": [permission]},
        )
        assert rejected.status_code == 422, rejected.text
        held = ok(
            e2e_client.get(f"/api/roles/{delegate_role}/permissions", headers=admin)
        )
        assert permission not in {item["permission"] for item in held["permissions"]}
    finally:
        for user_id in reversed(user_ids):
            inactive = e2e_client.patch(
                f"/api/users/{user_id}", headers=admin, json={"is_active": False}
            )
            assert inactive.status_code == 200, inactive.text
            ok(e2e_client.delete(f"/api/users/{user_id}", headers=admin), 204)
        for role_id in reversed(role_ids):
            ok(e2e_client.delete(f"/api/roles/{role_id}", headers=admin), 204)
