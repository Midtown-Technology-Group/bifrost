"""Current DB activity gates stale-token device and promotion requests."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from src.models.orm.users import User
from tests.e2e.api.test_device_protocol import _enroll
from tests.e2e.fixtures.device_bridge import set_bridge_grants
from tests.e2e.fixtures.run_as import create_person, ok

pytestmark = pytest.mark.e2e


async def test_disabled_holder_stale_jwt_cannot_use_any_bridge_power(
    e2e_client, platform_admin, org1, async_session_factory,
):
    admin, tag = platform_admin.headers, uuid4().hex[:8]
    role = ok(e2e_client.post("/api/roles", headers=admin,
                              json={"name": f"disabled-bridge-{tag}"}), 201)
    role_id = role["id"]
    set_bridge_grants(e2e_client, admin, role_id,
                      ["devices.readwrite", "devices.execute", "agents.promote"])
    person = create_person(e2e_client, admin, org1, tag, "disabled-bridge", additional=[{
        "role_id": role_id,
        "boundaries": [{"kind": "organization", "organization_id": org1["id"]}],
    }])
    agent_id = None
    try:
        device_id, _ = _enroll(e2e_client, admin, org1["id"], f"disabled-bridge-{tag}")
        stale_headers = dict(person.headers)
        # Warm the same session and grant paths before DB-only deactivation.
        ok(e2e_client.get(f"/api/devices/{device_id}/jobs", headers=stale_headers))
        issued = ok(e2e_client.post("/api/device-control-keys", headers=stale_headers,
                                     json={"name": "before-disable", "device_ids": [device_id]}), 201)
        key_id = issued["control_key"]["id"]
        agent = ok(e2e_client.post("/api/agents", headers=stale_headers, json={
            "name": f"Disabled promoter {tag}", "system_prompt": "Reply briefly.",
            "channels": ["chat"], "access_level": "private",
        }), 201)
        agent_id = agent["id"]
        # Preserve token and role rows intentionally: this isolates current DB
        # status enforcement from lifecycle logout/cache side effects.
        async with async_session_factory() as db:
            await db.execute(update(User).where(User.id == person.user_id).values(is_active=False))
            await db.commit()
        requests = [
            ("GET", f"/api/devices/{device_id}", None),
            ("GET", f"/api/devices/{device_id}/jobs", None),
            ("POST", f"/api/devices/{device_id}/jobs",
             {"script_name": "disabled", "script_content": "Write-Output 'disabled'"}),
            ("POST", "/api/device-control-keys", {"name": "after-disable", "device_ids": [device_id]}),
            ("POST", f"/api/device-control-keys/{key_id}/rotate", None),
            ("POST", f"/api/agents/{agent_id}/promote", {"access_level": "authenticated"}),
        ]
        for method, path, body in requests:
            response = e2e_client.request(method, path, headers=stale_headers, json=body)
            assert response.status_code == 403, f"{method} {path}: {response.status_code} {response.text}"
        unchanged = ok(e2e_client.get(f"/api/agents/{agent_id}", headers=admin))
        assert unchanged["access_level"] == "private"
        assert UUID(unchanged["owner_user_id"]) == person.user_id
    finally:
        if agent_id is not None:
            removed = e2e_client.delete(f"/api/agents/{agent_id}", headers=admin)
            assert removed.status_code == 204, removed.text
        removed_user = e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
        assert removed_user.status_code == 204, removed_user.text
        removed_role = e2e_client.delete(f"/api/roles/{role_id}", headers=admin)
        assert removed_role.status_code == 204, removed_role.text
