"""E2E tests for the device registry (M1 #831).

Lifecycle: create -> enroll (no session) -> observe -> rotate -> disable ->
enable, plus permission denial, org isolation, and single-use token rules.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def device_manager_role(e2e_client, platform_admin, org1_user, org2_user):
    """Grant can_manage_config + can_execute_devices to both org users."""
    response = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={
            "name": "E2E Device Managers",
            "description": "Granted by test_device_registry e2e",
            "permissions": {
                "can_manage_config": True,
                "can_execute_devices": True,
            },
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


def _create_device(client, headers, org_id, name="e2e-device", **extra):
    body = {"display_name": name, "organization_id": str(org_id), **extra}
    response = client.post("/api/devices", headers=headers, json=body)
    assert response.status_code == 201, response.text
    payload = response.json()
    assert "enrollment_token" in payload
    assert "enrollment_token_hash" not in str(payload)
    return payload


def test_full_lifecycle(e2e_client, platform_admin, org1, org1_user, device_manager_role):
    org_id = org1["id"]

    created = _create_device(e2e_client, platform_admin.headers, org_id, "e2e-lifecycle")
    device_id = created["device"]["id"]
    token = created["enrollment_token"]
    assert created["device"]["status"] == "pending_enrolled"
    # Public views never expose hashes.
    assert "api_key_hash" not in created["device"]

    # Enroll with no user session at all (token auth + CSRF-exempt path).
    enroll = e2e_client.post(
        "/api/devices/enroll", json={"enrollment_token": token}
    )
    assert enroll.status_code == 200, enroll.text
    enrolled = enroll.json()
    assert enrolled["status"] == "active"
    assert enrolled["device_key"].startswith("bfdk_")
    assert enrolled["device_id"] == device_id

    # Single-use: the same token cannot enroll twice.
    reused = e2e_client.post(
        "/api/devices/enroll", json={"enrollment_token": token}
    )
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "enrollment_token_consumed"

    # Org user with the management role can read the device (no secrets).
    detail = e2e_client.get(f"/api/devices/{device_id}", headers=org1_user.headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "active"
    assert "api_key_hash" not in body
    assert "enrollment_token_hash" not in body

    listing = e2e_client.get("/api/devices", headers=org1_user.headers)
    assert listing.status_code == 200
    listed_ids = {row["id"] for row in listing.json()}
    assert device_id in listed_ids

    # Rotate returns a fresh raw key exactly once.
    rotated = e2e_client.post(
        f"/api/devices/{device_id}/rotate-key", headers=org1_user.headers
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["device_key"].startswith("bfdk_")
    assert rotated.json()["device_key"] != enrolled["device_key"]

    # Disable then re-enable.
    disabled = e2e_client.post(
        f"/api/devices/{device_id}/disable", headers=org1_user.headers
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    enabled = e2e_client.post(
        f"/api/devices/{device_id}/enable", headers=org1_user.headers
    )
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"


def test_disabled_device_cannot_enroll(
    e2e_client, platform_admin, org1, org1_user, device_manager_role
):
    created = _create_device(e2e_client, platform_admin.headers, org1["id"], "e2e-disabled")
    token = created["enrollment_token"]
    device_id = created["device"]["id"]

    disabled = e2e_client.post(
        f"/api/devices/{device_id}/disable", headers=org1_user.headers
    )
    assert disabled.status_code == 200

    enroll = e2e_client.post(
        "/api/devices/enroll", json={"enrollment_token": token}
    )
    assert enroll.status_code == 403
    assert enroll.json()["error"]["code"] == "device_disabled"


def test_permission_denied_without_role(e2e_client, platform_admin, org1, non_admin_user):
    response = e2e_client.post(
        "/api/devices",
        headers=non_admin_user.headers,
        json={"display_name": "nope", "organization_id": str(org1["id"])},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_org_isolation(e2e_client, platform_admin, org1, org2, org1_user, org2_user, device_manager_role):
    created = _create_device(e2e_client, platform_admin.headers, org1["id"], "e2e-iso")
    device_id = created["device"]["id"]

    # Same org: visible.
    assert (
        e2e_client.get(f"/api/devices/{device_id}", headers=org1_user.headers).status_code
        == 200
    )
    # Other org: not found (scoped), even with the management permission.
    cross = e2e_client.get(f"/api/devices/{device_id}", headers=org2_user.headers)
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "unknown_device"

    # Org2 listing must not contain the org1 device.
    listing = e2e_client.get("/api/devices", headers=org2_user.headers)
    assert listing.status_code == 200
    assert device_id not in {row["id"] for row in listing.json()}

    # Org user cannot create into another org.
    cross_create = e2e_client.post(
        "/api/devices",
        headers=org2_user.headers,
        json={"display_name": "smuggle", "organization_id": str(org1["id"])},
    )
    assert cross_create.status_code == 403
    assert cross_create.json()["error"]["code"] == "permission_denied"

    # Rotate from the wrong org is not found.
    cross_rotate = e2e_client.post(
        f"/api/devices/{device_id}/rotate-key", headers=org2_user.headers
    )
    assert cross_rotate.status_code == 404


def test_create_requires_auth(e2e_client, org1):
    # Use a cookie-less client: the shared e2e_client carries session cookies
    # from fixture logins (which correctly hit CSRF before auth on POST).
    import httpx

    from tests.e2e.fixtures.setup import API_BASE_URL

    with httpx.Client(base_url=API_BASE_URL) as anon:
        response = anon.post(
            "/api/devices",
            json={"display_name": "anon", "organization_id": str(org1["id"])},
        )
    assert response.status_code == 401


def test_enroll_rejects_garbage_token(e2e_client):
    response = e2e_client.post(
        "/api/devices/enroll", json={"enrollment_token": "bfen_not-a-uuid_short"}
    )
    # Fails pydantic pattern validation (422) or domain validation (401).
    assert response.status_code in (401, 422)
