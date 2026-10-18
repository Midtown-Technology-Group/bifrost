"""Admin bridge-grant setup through the public CAS API for device E2E tests."""


def set_bridge_grants(client, admin_headers, role_id, permissions):
    current = client.get(f"/api/roles/{role_id}/bridge-grants", headers=admin_headers)
    assert current.status_code == 200, current.text
    state = current.json()
    updated = client.put(
        f"/api/roles/{role_id}/bridge-grants", headers=admin_headers,
        json={
            "permissions": list(permissions),
            "expected_permissions": state["permissions"],
            "expected_version": state["version"],
        },
    )
    assert updated.status_code == 200, updated.text
    return updated.json()
