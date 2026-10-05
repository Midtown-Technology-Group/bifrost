"""HTTP authorization tripwires for the opt-in lighthouse admission surface."""

import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from tests.e2e.api import test_device_jobs_api as device_jobs_api

from tests.e2e.api.test_device_protocol import _enroll

execute_device_role = device_jobs_api.execute_device_role


def body():
    return {"request_id": str(uuid4()), "operator_key": base64.b64encode(b"o" * 32).decode(),
            "target_key": base64.b64encode(b"t" * 32).decode(), "destination": "127.0.0.1:443",
            "expires": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}


@pytest.mark.e2e
async def test_authorized_device_stays_disabled_without_launcher(e2e_client, platform_admin, org1, org1_user, org2_user, execute_device_role):
    device_id, _ = _enroll(e2e_client, platform_admin.headers, org1["id"], "peer-device")
    with httpx.Client(base_url=e2e_client.base_url, timeout=60.0) as anonymous_client:
        unauthorized = anonymous_client.post(f"/api/devices/{device_id}/peer-sessions", json=body())
    assert unauthorized.status_code == 401
    foreign = e2e_client.post(f"/api/devices/{device_id}/peer-sessions", headers=org2_user.headers, json=body())
    assert foreign.status_code == 404
    disabled = e2e_client.post(f"/api/devices/{device_id}/peer-sessions", headers=org1_user.headers, json=body())
    assert disabled.status_code == 503
    assert disabled.json()["error"]["code"] == "peer_unavailable"
    revoke = e2e_client.post(f"/api/devices/{device_id}/peer-sessions/{uuid4()}/revoke", headers=org2_user.headers)
    assert revoke.status_code == 404


@pytest.mark.e2e
async def test_invalid_destination_rejected_before_admission(e2e_client, platform_admin):
    invalid = body()
    invalid["destination"] = "localhost:443"
    response = e2e_client.post(f"/api/devices/{uuid4()}/peer-sessions", headers=platform_admin.headers, json=invalid)
    assert response.status_code == 422
