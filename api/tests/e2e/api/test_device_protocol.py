"""E2E: simulated agent completes a job over the device HTTP protocol (#834).

Gate for M2.2: a simulated agent (device key in X-Bifrost-Key) can run
setup -> heartbeat -> claim -> logs (idempotent) -> result against the
local stack, with fencing, cross-device scoping, and disabled-device
rejections proven.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.models.orm.device_jobs import DeviceJob


def _key_headers(device_key: str) -> dict:
    return {"X-Bifrost-Key": device_key}


def _anon_client():
    import httpx

    from tests.e2e.fixtures.setup import API_BASE_URL

    return httpx.Client(base_url=API_BASE_URL)


def _enroll(client, headers, org_id, name) -> tuple[str, str]:
    """Create a device via the registry and enroll it; returns (id, key)."""
    created = client.post(
        "/api/devices",
        headers=headers,
        json={"display_name": name, "organization_id": str(org_id)},
    )
    assert created.status_code == 201, created.text
    token = created.json()["enrollment_token"]
    device_id = created.json()["device"]["id"]
    with _anon_client() as anon:
        enrolled = anon.post(
            "/api/devices/enroll", json={"enrollment_token": token}
        )
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["device_id"] == device_id
    return device_id, enrolled.json()["device_key"]


async def _insert_job(db_session, org_id: UUID, device_id: UUID, **overrides) -> DeviceJob:
    job = DeviceJob(
        organization_id=org_id,
        device_id=device_id,
        status=overrides.pop("status", "pending"),
        script_name=overrides.pop("script_name", "e2e-probe"),
        script_content=overrides.pop("script_content", "Write-Output hi"),
        params={},
        timeout_seconds=60,
        max_output_bytes=1024 * 1024,
        log_sequence=0,
        **overrides,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


async def test_full_agent_lifecycle(e2e_client, platform_admin, org1, db_session):
    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "proto-device"
    )
    job = await _insert_job(db_session, UUID(org1["id"]), UUID(device_id))
    headers = _key_headers(key)
    session_id = str(uuid4())

    # Heartbeat while pending: poll hint shortens to 5s, last_seen updates.
    hb = e2e_client.post(
        "/api/device/heartbeat", headers=headers,
        json={"agent_session_id": session_id},
    )
    assert hb.status_code == 200, hb.text
    assert hb.json()["poll_interval_seconds"] == 5
    assert hb.json()["cancel_requested"] is False

    # Claim binds the job to this agent session.
    claim = e2e_client.post(
        "/api/device/jobs/claim", headers=headers,
        json={"agent_session_id": session_id},
    )
    assert claim.status_code == 200, claim.text
    body = claim.json()
    assert body["job_id"] == str(job.id)
    assert body["script_content"] == "Write-Output hi"
    assert body["timeout_seconds"] == 60
    assert body["max_output_bytes"] == 1024 * 1024
    assert body["claim_lease_seconds"] == 60
    claim_token = body["claim_token"]

    # No work left for a second claim.
    again = e2e_client.post(
        "/api/device/jobs/claim", headers=headers,
        json={"agent_session_id": session_id},
    )
    assert again.status_code == 204

    # Heartbeat after claim: default poll interval, no cancel pending.
    hb2 = e2e_client.post(
        "/api/device/heartbeat", headers=headers,
        json={"agent_session_id": session_id},
    )
    assert hb2.status_code == 200
    assert hb2.json()["poll_interval_seconds"] == 10
    assert hb2.json()["cancel_requested"] is False

    # Logs: fenced, idempotent, conflict on changed content.
    batch = {
        "claim_token": claim_token,
        "entries": [
            {"seq": 1, "stream": "stdout", "text": "line1"},
            {"seq": 2, "stream": "stderr", "text": "line2"},
        ],
    }
    logs = e2e_client.post(
        f"/api/device/jobs/{job.id}/logs", headers=headers, json=batch
    )
    assert logs.status_code == 204, logs.text

    replay = e2e_client.post(
        f"/api/device/jobs/{job.id}/logs", headers=headers, json=batch
    )
    assert replay.status_code == 204, replay.text

    changed = {
        "claim_token": claim_token,
        "entries": [{"seq": 1, "stream": "stdout", "text": "DIFFERENT"}],
    }
    conflict = e2e_client.post(
        f"/api/device/jobs/{job.id}/logs", headers=headers, json=changed
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "log_seq_conflict"

    wrong_token = {
        "claim_token": str(uuid4()),
        "entries": [{"seq": 3, "stream": "stdout", "text": "x"}],
    }
    fenced = e2e_client.post(
        f"/api/device/jobs/{job.id}/logs", headers=headers, json=wrong_token
    )
    assert fenced.status_code == 409
    assert fenced.json()["error"]["code"] == "fence_violation"

    await db_session.refresh(job)
    assert job.log_sequence == 2

    # Terminal result.
    result = e2e_client.post(
        f"/api/device/jobs/{job.id}/result",
        headers=headers,
        json={
            "claim_token": claim_token,
            "status": "succeeded",
            "exit_code": 0,
            "output": "done",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json() == {"job_id": str(job.id), "status": "succeeded"}

    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.exit_code == 0
    assert job.result == "done"

    # Late result after terminal: rejected, job stays succeeded.
    late = e2e_client.post(
        f"/api/device/jobs/{job.id}/result",
        headers=headers,
        json={"claim_token": claim_token, "status": "failed", "exit_code": 1},
    )
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "job_terminal"

    # Logs after terminal: rejected with the terminal envelope too.
    late_logs = e2e_client.post(
        f"/api/device/jobs/{job.id}/logs", headers=headers, json=batch
    )
    assert late_logs.status_code == 409
    assert late_logs.json()["error"]["code"] == "job_terminal"


async def test_auth_failures_and_cross_device_scoping(
    e2e_client, platform_admin, org1, db_session
):
    device_id_a, key_a = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "proto-a"
    )
    device_id_b, key_b = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "proto-b"
    )
    headers_a = _key_headers(key_a)
    session_id = str(uuid4())

    # Malformed and well-formed-but-unknown keys: 401 invalid_key.
    for bad in (
        "not-a-key",
        f"bfdk_{uuid4()}_{ 'a' * 43 }",
        key_a[:-1] + ("b" if key_a[-1] != "b" else "c"),
    ):
        resp = e2e_client.post(
            "/api/device/heartbeat",
            headers={"X-Bifrost-Key": bad},
            json={"agent_session_id": session_id},
        )
        assert resp.status_code == 401, (bad[:12], resp.status_code, resp.text)
        assert resp.json()["error"]["code"] == "invalid_key"

    # Cross-device: device B cannot touch device A's job even with A's token.
    job = await _insert_job(db_session, UUID(org1["id"]), UUID(device_id_a))
    claim = e2e_client.post(
        "/api/device/jobs/claim", headers=headers_a,
        json={"agent_session_id": session_id},
    )
    assert claim.status_code == 200, claim.text
    claim_token = claim.json()["claim_token"]

    cross = e2e_client.post(
        f"/api/device/jobs/{job.id}/logs",
        headers=_key_headers(key_b),
        json={
            "claim_token": claim_token,
            "entries": [{"seq": 1, "stream": "stdout", "text": "intruder"}],
        },
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "unknown_job"

    cross_result = e2e_client.post(
        f"/api/device/jobs/{job.id}/result",
        headers=_key_headers(key_b),
        json={"claim_token": claim_token, "status": "succeeded"},
    )
    assert cross_result.status_code == 404


async def test_disabled_device_rejected(
    e2e_client, platform_admin, org1, db_session
):
    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "proto-disabled"
    )
    disabled = e2e_client.post(
        f"/api/devices/{device_id}/disable", headers=platform_admin.headers
    )
    assert disabled.status_code == 200, disabled.text

    resp = e2e_client.post(
        "/api/device/heartbeat",
        headers=_key_headers(key),
        json={"agent_session_id": str(uuid4())},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "device_disabled"

    claim = e2e_client.post(
        "/api/device/jobs/claim",
        headers=_key_headers(key),
        json={"agent_session_id": str(uuid4())},
    )
    assert claim.status_code == 403


async def test_heartbeat_reports_cooperative_cancel(
    e2e_client, platform_admin, org1, db_session
):
    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "proto-cancel"
    )
    session_id = str(uuid4())
    claim = e2e_client.post(
        "/api/device/jobs/claim",
        headers=_key_headers(key),
        json={"agent_session_id": session_id},
    )
    # No job yet: claim is 204; create one first.
    assert claim.status_code == 204

    job = await _insert_job(db_session, UUID(org1["id"]), UUID(device_id))
    claim = e2e_client.post(
        "/api/device/jobs/claim",
        headers=_key_headers(key),
        json={"agent_session_id": session_id},
    )
    assert claim.status_code == 200, claim.text

    job.cancel_requested_at = datetime.now(timezone.utc)
    await db_session.commit()

    hb = e2e_client.post(
        "/api/device/heartbeat",
        headers=_key_headers(key),
        json={"agent_session_id": session_id},
    )
    assert hb.status_code == 200, hb.text
    assert hb.json()["cancel_requested"] is True
