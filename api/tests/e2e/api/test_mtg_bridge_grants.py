"""Public API bridge maintenance preserves unrelated rows and rejects stale saves."""

from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

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
