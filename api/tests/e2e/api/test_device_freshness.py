"""E2E: control-key device freshness read (approved M0 delta #818/5815371680).

A control key may read exactly {id, status, last_seen_at} — only for
devices on its allow-list — so the workspace can implement pre-accept
fail-closed. Full registry detail stays user-only.
"""

from __future__ import annotations

from uuid import uuid4

from tests.e2e.api.test_device_protocol import _enroll

FRESHNESS_FIELDS = {"id", "status", "last_seen_at"}


def _make_key(client, headers, org_id, device_ids):
    response = client.post(
        "/api/device-control-keys",
        headers=headers,
        json={"name": "freshness", "device_ids": device_ids, "organization_id": str(org_id)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_control_key_reads_only_freshness_fields(
    e2e_client, platform_admin, org1
):
    device_id, device_key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "fresh-device"
    )
    # A freshly enrolled device has never heartbeated (last_seen_at is null
    # = not fresh). Run one agent heartbeat to populate the freshness feed.
    hb = e2e_client.post(
        "/api/device/heartbeat",
        headers={"X-Bifrost-Key": device_key},
        json={"agent_session_id": str(uuid4())},
    )
    assert hb.status_code == 200, hb.text

    created = _make_key(e2e_client, platform_admin.headers, org1["id"], [device_id])
    raw = created["key"]

    response = e2e_client.get(
        f"/api/devices/{device_id}", headers={"X-Bifrost-Control-Key": raw}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Exactly the three approved fields — nothing else leaks.
    assert set(body.keys()) == FRESHNESS_FIELDS
    assert body["id"] == device_id
    assert body["status"] == "active"
    assert body["last_seen_at"] is not None
    # Names/hashes/external_ref must not appear anywhere in the payload.
    for forbidden in ("display_name", "api_key_hash", "enrollment_token_hash", "external_ref", "hostname"):
        assert forbidden not in body


def test_out_of_scope_device_is_denied(
    e2e_client, platform_admin, org1
):
    device_a, _ka = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "fresh-in"
    )
    device_b, _kb = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "fresh-out"
    )
    created = _make_key(e2e_client, platform_admin.headers, org1["id"], [device_a])
    raw = created["key"]

    response = e2e_client.get(
        f"/api/devices/{device_b}", headers={"X-Bifrost-Control-Key": raw}
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "out_of_scope"


def test_invalid_and_unknown_keys_rejected(
    e2e_client, platform_admin, org1
):
    device_id, _key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "fresh-bad"
    )
    malformed = e2e_client.get(
        f"/api/devices/{device_id}",
        headers={"X-Bifrost-Control-Key": "bfck_not-a-uuid_short"},
    )
    assert malformed.status_code == 401
    assert malformed.json()["error"]["code"] == "invalid_key"

    unknown = e2e_client.get(
        f"/api/devices/{device_id}",
        headers={"X-Bifrost-Control-Key": f"bfck_{uuid4()}_{'a' * 43}"},
    )
    assert unknown.status_code == 401


def test_no_credential_get_is_unauthenticated(
    e2e_client, platform_admin, org1
):
    import httpx

    from tests.e2e.fixtures.setup import API_BASE_URL

    device_id, _key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "fresh-anon"
    )
    with httpx.Client(base_url=API_BASE_URL) as anon:
        response = anon.get(f"/api/devices/{device_id}")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
