"""HTTP authorization tripwires for the opt-in lighthouse admission surface."""

import base64
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

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


@pytest.mark.e2e
async def test_admin_first_revoke_receipt_is_readable_by_session_owner(
    e2e_client, platform_admin, org1, org1_user, org2_user,
    execute_device_role, async_session_factory,
):
    """One admin-owned cleanup job has a scoped owner-readable HTTP receipt."""
    from unittest.mock import patch
    from sqlalchemy import delete
    from src.main import app
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.platform_jobs import enqueue_platform_job

    device_id, _ = _enroll(e2e_client, platform_admin.headers, org1["id"], "peer-receipt")
    session_id = uuid4()
    async with async_session_factory() as db:
        db.add(PlatformJob(id=session_id, job_type="device.peer_lighthouse.start",
            payload={}, status="succeeded", organization_id=UUID(org1["id"]),
            requested_by_user_id=str(org1_user.user_id), requested_by_email="test@example.com",
            requested_by_name="Session owner", resource_type="device", resource_id=device_id,
            title="Seeded session"))
        await db.commit()

    async def deferred_enqueue(db, *args, **kwargs):
        # Real admission/dedupe against PostgreSQL, but no launcher effects.
        job, reused = await enqueue_platform_job(db, *args, **kwargs)
        job.available_at = datetime.now(timezone.utc) + timedelta(days=1)
        await db.flush()
        return job, reused

    path = f"/api/devices/{device_id}/peer-sessions/{session_id}/revoke"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("src.services.platform_jobs.enqueue_platform_job", side_effect=deferred_enqueue):
                first = await client.post(path, headers=platform_admin.headers)
                assert first.status_code == 202, first.text
                second = await client.post(path, headers=org1_user.headers)
                assert second.status_code == 202, second.text
            receipt = second.json()
            assert receipt["reused"] is True
            assert receipt["job_id"] == first.json()["job_id"]
            job_id = receipt["job_id"]
            # Generic visibility and cancellation retain their requester boundary.
            assert (await client.get(f"/api/platform-jobs/{job_id}", headers=org1_user.headers)).status_code == 404
            assert (await client.post(f"/api/platform-jobs/{job_id}/cancel", headers=org1_user.headers)).status_code == 404
            status = await client.get(receipt["status_url"], headers=org1_user.headers)
            assert status.status_code == 200, status.text
            assert status.json()["id"] == job_id
            assert status.json()["requested_by_user_id"] == str(platform_admin.user_id)
            assert status.json()["can_cancel"] is False
            assert (await client.get(receipt["status_url"], headers=org2_user.headers)).status_code == 404
            assert (await client.get(receipt["status_url"].replace(job_id, str(session_id)), headers=org1_user.headers)).status_code == 404
            async with async_session_factory() as db:
                job = await db.get(PlatformJob, UUID(job_id))
                assert job is not None
                job.status = "succeeded"
                job.result = {"session_job_id": str(session_id), "lighthouse_revoked": True,
                              "endpoint_disconnect_confirmed": False}
                await db.commit()
            done = await client.get(receipt["status_url"], headers=org1_user.headers)
            assert done.status_code == 200, done.text
            assert done.json()["result"]["lighthouse_revoked"] is True
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(PlatformJob).where(PlatformJob.resource_id == device_id))
            await db.commit()
