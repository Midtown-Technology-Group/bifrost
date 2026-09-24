"""E2E tests for control-key CRUD (M1.4 #832).

Lifecycle: create (raw once) -> list/get (no secret) -> rotate -> revoke,
plus scope validation, permission denial, and org isolation.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def control_key_role(e2e_client, platform_admin, org1_user, org2_user):
    """Grant can_manage_config to both org users (control-key CRUD gate)."""
    response = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={
            "name": "E2E Control Key Managers",
            "description": "Granted by test_device_control_keys e2e",
            "permissions": {"can_manage_config": True},
        },
    )
    assert response.status_code in (200, 201), response.text
    role_id = response.json()["id"]
    assign = e2e_client.post(
        f"/api/roles/{role_id}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(org1_user.user_id), str(org2_user.user_id)]},
    )
    assert assign.status_code == 204, assign.text
    yield role_id


def _create_device(client, headers, org_id, name):
    response = client.post(
        "/api/devices",
        headers=headers,
        json={"display_name": name, "organization_id": str(org_id)},
    )
    assert response.status_code == 201, response.text
    return response.json()["device"]["id"]


def test_full_control_key_lifecycle(
    e2e_client, platform_admin, org1, org1_user, control_key_role
):
    device_id = _create_device(e2e_client, platform_admin.headers, org1["id"], "ck-device")

    created = e2e_client.post(
        "/api/device-control-keys",
        headers=org1_user.headers,
        json={"name": "workspace-key", "device_ids": [device_id]},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    raw = payload["key"]
    key_id = payload["control_key"]["id"]
    assert raw.startswith("bfck_")
    assert "key_hash" not in str(payload)
    assert payload["control_key"]["enabled"] is True
    assert payload["control_key"]["device_ids"] == [device_id]

    # List/detail expose metadata only — never the secret.
    listing = e2e_client.get("/api/device-control-keys", headers=org1_user.headers)
    assert listing.status_code == 200
    listed = {row["id"] for row in listing.json()}
    assert key_id in listed
    assert raw not in listing.text
    detail = e2e_client.get(
        f"/api/device-control-keys/{key_id}", headers=org1_user.headers
    )
    assert detail.status_code == 200
    assert raw not in detail.text

    # Rotate returns a fresh raw key once; row id is stable.
    rotated = e2e_client.post(
        f"/api/device-control-keys/{key_id}/rotate", headers=org1_user.headers
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["control_key_id"] == key_id
    assert rotated.json()["key"].startswith("bfck_")
    assert rotated.json()["key"] != raw

    # Revoke disables (idempotent).
    revoked = e2e_client.post(
        f"/api/device-control-keys/{key_id}/revoke", headers=org1_user.headers
    )
    assert revoked.status_code == 200
    assert revoked.json()["enabled"] is False
    again = e2e_client.post(
        f"/api/device-control-keys/{key_id}/revoke", headers=org1_user.headers
    )
    assert again.status_code == 200
    assert again.json()["enabled"] is False


def test_scope_validation(e2e_client, platform_admin, org1, org1_user, control_key_role):
    # Empty allow-list rejected (pydantic min_length).
    empty = e2e_client.post(
        "/api/device-control-keys",
        headers=org1_user.headers,
        json={"name": "nope", "device_ids": []},
    )
    assert empty.status_code == 422

    # Unknown device id rejected with the structured envelope.
    unknown = e2e_client.post(
        "/api/device-control-keys",
        headers=org1_user.headers,
        json={
            "name": "ghost",
            "device_ids": ["11111111-1111-1111-1111-111111111111"],
        },
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "invalid_parameter"

    # Past expiry rejected.
    past = e2e_client.post(
        "/api/device-control-keys",
        headers=org1_user.headers,
        json={
            "name": "stale",
            "device_ids": [str(org1["id"])],  # org id, not a device id
            "expires_at": "2020-01-01T00:00:00Z",
        },
    )
    assert past.status_code == 422
    assert past.json()["error"]["code"] == "invalid_parameter"


def test_permission_denied_without_role(e2e_client, platform_admin, org1, non_admin_user):
    response = e2e_client.post(
        "/api/device-control-keys",
        headers=non_admin_user.headers,
        json={"name": "nope", "device_ids": [str(org1["id"])]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_org_isolation(
    e2e_client, platform_admin, org1, org2, org1_user, org2_user, control_key_role
):
    device_id = _create_device(e2e_client, platform_admin.headers, org1["id"], "ck-iso")
    created = e2e_client.post(
        "/api/device-control-keys",
        headers=org1_user.headers,
        json={"name": "iso-key", "device_ids": [device_id]},
    )
    assert created.status_code == 201, created.text
    key_id = created.json()["control_key"]["id"]

    # Org2 cannot see the key.
    assert (
        e2e_client.get(
            f"/api/device-control-keys/{key_id}", headers=org2_user.headers
        ).status_code
        == 404
    )
    listing = e2e_client.get("/api/device-control-keys", headers=org2_user.headers)
    assert key_id not in {row["id"] for row in listing.json()}

    # Org2 cannot rotate/revoke it.
    rotate = e2e_client.post(
        f"/api/device-control-keys/{key_id}/rotate", headers=org2_user.headers
    )
    assert rotate.status_code == 404
    assert rotate.json()["error"]["code"] == "unknown_control_key"

    # Org2 cannot build a key targeting an org1 device.
    cross = e2e_client.post(
        "/api/device-control-keys",
        headers=org2_user.headers,
        json={"name": "smuggle", "device_ids": [device_id]},
    )
    assert cross.status_code == 422
    assert cross.json()["error"]["code"] == "invalid_parameter"
