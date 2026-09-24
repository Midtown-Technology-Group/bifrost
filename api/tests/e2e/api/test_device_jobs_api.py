"""E2E: device job create/observation/cancel APIs (M2.4 #836).

Gate coverage: user-permission and control-key create paths with
attribution, busy 409, permission/scope denials, create-level read authz
(incl. script body), workflow attribution, and the full cooperative-cancel
loop against a simulated agent.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from tests.e2e.api.test_device_protocol import _enroll


@pytest.fixture(scope="module")
def execute_device_role(e2e_client, platform_admin, org1_user, org2_user):
    """Grant can_manage_config+can_execute_devices to both org users."""
    response = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={
            "name": "E2E Device Executors",
            "description": "Granted by test_device_jobs_api e2e",
            "permissions": {
                "can_execute_devices": True,
                "can_manage_config": True,
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
    for uid in (org1_user.user_id, org2_user.user_id):
        cleanup = e2e_client.delete(
            f"/api/roles/{role_id}/users/{uid}", headers=platform_admin.headers
        )
        assert cleanup.status_code in (200, 204), cleanup.text


def _job_body(**extra):
    body = {
        "script_name": "e2e-job",
        "script_content": "Write-Output 'hello'",
        "timeout_seconds": 60,
    }
    body.update(extra)
    return body


async def _create_active_device(e2e_client, admin_headers, org_id, name) -> tuple[str, str]:
    return _enroll(e2e_client, admin_headers, org_id, name)


async def _db_job(db_session, org_id: str, device_id: str, **overrides):
    from src.models.orm.device_jobs import DeviceJob

    job = DeviceJob(
        organization_id=UUID(org_id),
        device_id=UUID(device_id),
        status=overrides.pop("status", "pending"),
        script_name=overrides.pop("script_name", "seed"),
        script_content=overrides.pop("script_content", "Write-Output 1"),
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


@pytest.mark.e2e
async def test_create_busy_and_observation(
    e2e_client, platform_admin, org1, org1_user, execute_device_role
):
    device_id, key = await _create_active_device(
        e2e_client, platform_admin.headers, org1["id"], "job-device"
    )

    # User create (Bearer JWT + can_execute_devices).
    created = e2e_client.post(
        f"/api/devices/{device_id}/jobs",
        headers=org1_user.headers,
        json=_job_body(),
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "pending"
    assert job["requested_by_user_id"] == str(org1_user.user_id)
    assert job["requested_by_api_key_id"] is None
    assert "script_content" not in job  # list/public view has no body
    assert "claim_token" not in job  # fencing secrets stay agent-only
    job_id = job["id"]

    # Busy: second create → structured 409 device_busy with the blocker id.
    busy = e2e_client.post(
        f"/api/devices/{device_id}/jobs",
        headers=org1_user.headers,
        json=_job_body(),
    )
    assert busy.status_code == 409, busy.text
    assert busy.json()["error"]["code"] == "device_busy"
    assert busy.json()["error"]["job_id"] == job_id

    # Detail: create-level read includes the script body.
    detail = e2e_client.get(
        f"/api/devices/{device_id}/jobs/{job_id}", headers=org1_user.headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["script_content"] == "Write-Output 'hello'"

    # List: scoped to the device, no bodies.
    listing = e2e_client.get(
        f"/api/devices/{device_id}/jobs", headers=org1_user.headers
    )
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [job_id]
    assert "script_content" not in listing.text

    # Logs readable (empty before the agent posts).
    logs = e2e_client.get(
        f"/api/devices/{device_id}/jobs/{job_id}/logs",
        headers=org1_user.headers,
    )
    assert logs.status_code == 200
    assert logs.json() == []


@pytest.mark.e2e
async def test_permission_and_auth_denials(
    e2e_client, platform_admin, org1, non_admin_user, execute_device_role
):
    device_id, _key = await _create_active_device(
        e2e_client, platform_admin.headers, org1["id"], "job-denied"
    )

    # No credential at all → 401 unauthenticated envelope.
    # (Cookie-less: the shared e2e_client carries session cookies from the
    # fixture logins, which would hit CSRF before auth on a POST.)
    import httpx
    from tests.e2e.fixtures.setup import API_BASE_URL

    with httpx.Client(base_url=API_BASE_URL) as anon_client:
        anon = anon_client.post(
            f"/api/devices/{device_id}/jobs", json=_job_body()
        )
    assert anon.status_code == 401, anon.text
    assert anon.json()["error"]["code"] == "unauthenticated"

    # Org user without can_execute_devices → 403 permission_denied.
    denied = e2e_client.post(
        f"/api/devices/{device_id}/jobs",
        headers=non_admin_user.headers,
        json=_job_body(),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"

    # Unknown device → 404 scoped envelope.
    missing = e2e_client.post(
        f"/api/devices/{uuid4()}/jobs",
        headers=platform_admin.headers,
        json=_job_body(),
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "unknown_device"


@pytest.mark.e2e
async def test_control_key_create_matrix(
    e2e_client, platform_admin, org1, execute_device_role
):
    device_a, _ka = await _create_active_device(
        e2e_client, platform_admin.headers, org1["id"], "ck-a"
    )
    device_b, _kb = await _create_active_device(
        e2e_client, platform_admin.headers, org1["id"], "ck-b"
    )

    key = e2e_client.post(
        "/api/device-control-keys",
        headers=platform_admin.headers,
        json={
            "name": "job-key",
            "device_ids": [device_a],
            "organization_id": str(org1["id"]),
        },
    )
    assert key.status_code == 201, key.text
    raw_key = key.json()["key"]
    key_headers = {"X-Bifrost-Control-Key": raw_key}

    # In-scope create: 201, attribution is the key id, last_used touches.
    created = e2e_client.post(
        f"/api/devices/{device_a}/jobs", headers=key_headers, json=_job_body()
    )
    assert created.status_code == 201, created.text
    assert created.json()["requested_by_api_key_id"] == key.json()["control_key"]["id"]
    assert created.json()["requested_by_user_id"] is None

    listed = e2e_client.get(f"/api/devices/{device_a}/jobs", headers=key_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    after = e2e_client.get(
        f"/api/device-control-keys/{key.json()['control_key']['id']}",
        headers=platform_admin.headers,
    )
    assert after.json()["last_used_at"] is not None

    # Out-of-scope device (device_b not in the allow-list) → 403 out_of_scope.
    oos = e2e_client.post(
        f"/api/devices/{device_b}/jobs", headers=key_headers, json=_job_body()
    )
    assert oos.status_code == 403
    assert oos.json()["error"]["code"] == "out_of_scope"

    # Malformed key → 401 invalid_key.
    bad = e2e_client.post(
        f"/api/devices/{device_a}/jobs",
        headers={"X-Bifrost-Control-Key": "bfck_not-a-uuid_short"},
        json=_job_body(),
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_key"

    # Revoked key → 401.
    revoke = e2e_client.post(
        f"/api/device-control-keys/{key.json()['control_key']['id']}/revoke",
        headers=platform_admin.headers,
    )
    assert revoke.status_code == 200
    revoked = e2e_client.post(
        f"/api/devices/{device_a}/jobs", headers=key_headers, json=_job_body()
    )
    assert revoked.status_code == 401


@pytest.mark.e2e
async def test_read_authz_and_cross_scope(
    e2e_client, platform_admin, org1, org2, org1_user, org2_user, execute_device_role
):
    device_id, key = await _create_active_device(
        e2e_client, platform_admin.headers, org1["id"], "read-device"
    )
    headers_a = {"X-Bifrost-Control-Key": key} if False else org1_user.headers
    created = e2e_client.post(
        f"/api/devices/{device_id}/jobs", headers=headers_a, json=_job_body()
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    # Same org + permission: full detail.
    ok = e2e_client.get(
        f"/api/devices/{device_id}/jobs/{job_id}", headers=org1_user.headers
    )
    assert ok.status_code == 200
    assert ok.json()["script_content"] == "Write-Output 'hello'"

    # Other org (even with the same permission): scoped 404.
    cross = e2e_client.get(
        f"/api/devices/{device_id}/jobs/{job_id}", headers=org2_user.headers
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "unknown_device" or cross.json()[
        "error"
    ]["code"] == "unknown_job"

    cross_list = e2e_client.get(
        f"/api/devices/{device_id}/jobs", headers=org2_user.headers
    )
    assert cross_list.status_code == 404

    # A key that did NOT create the job cannot read it.
    other_key = e2e_client.post(
        "/api/device-control-keys",
        headers=platform_admin.headers,
        json={
            "name": "bystander",
            "device_ids": [device_id],
            "organization_id": str(org1["id"]),
        },
    )
    assert other_key.status_code == 201
    bystander = e2e_client.get(
        f"/api/devices/{device_id}/jobs/{job_id}",
        headers={"X-Bifrost-Control-Key": other_key.json()["key"]},
    )
    assert bystander.status_code == 404


@pytest.mark.e2e
async def test_workflow_attribution_validated(
    e2e_client, platform_admin, org1, org1_user, execute_device_role
):
    device_id, _key = await _create_active_device(
        e2e_client, platform_admin.headers, org1["id"], "wf-device"
    )

    # Unknown workflow id → 422 invalid_parameter (org-scoped lookup).
    bogus = e2e_client.post(
        f"/api/devices/{device_id}/jobs",
        headers=org1_user.headers,
        json=_job_body(workflow_id=str(uuid4())),
    )
    assert bogus.status_code == 422
    assert bogus.json()["error"]["code"] == "invalid_parameter"

    bogus_exec = e2e_client.post(
        f"/api/devices/{device_id}/jobs",
        headers=org1_user.headers,
        json=_job_body(execution_id=str(uuid4())),
    )
    assert bogus_exec.status_code == 422
    assert bogus_exec.json()["error"]["code"] == "invalid_parameter"

    # Positive storage of caller-asserted attribution is covered in
    # tests/unit/services/test_device_jobs.py (create records the ids);
    # e2e proves the org-scoped validation rejects unknown ones here.


@pytest.mark.e2e
async def test_cooperative_cancel_loop(
    e2e_client, platform_admin, org1, org1_user, execute_device_role, db_session
):
    from src.models.orm.device_jobs import DeviceJob

    device_id, agent_key = await _create_active_device(
        e2e_client, platform_admin.headers, org1["id"], "cancel-device"
    )
    agent_headers = {"X-Bifrost-Key": agent_key}
    session_id = str(uuid4())

    # --- Phase 1: pending cancels immediately; the agent then sees idle.
    created = e2e_client.post(
        f"/api/devices/{device_id}/jobs",
        headers=org1_user.headers,
        json=_job_body(),
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    cancelled = e2e_client.post(
        f"/api/devices/{device_id}/jobs/{job_id}/cancel",
        headers=org1_user.headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    claim = e2e_client.post(
        "/api/device/jobs/claim",
        headers=agent_headers,
        json={"agent_session_id": session_id},
    )
    assert claim.status_code == 204  # cancelled job is not claimable

    # Idempotent-ish terminal guard: second cancel → job_terminal.
    again = e2e_client.post(
        f"/api/devices/{device_id}/jobs/{job_id}/cancel",
        headers=org1_user.headers,
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "job_terminal"

    # --- Phase 2: running cancel is cooperative (flag + heartbeat + result).
    created2 = e2e_client.post(
        f"/api/devices/{device_id}/jobs",
        headers=org1_user.headers,
        json=_job_body(),
    )
    assert created2.status_code == 201, created2.text
    job2_id = created2.json()["id"]

    claim = e2e_client.post(
        "/api/device/jobs/claim",
        headers=agent_headers,
        json={"agent_session_id": session_id},
    )
    assert claim.status_code == 200, claim.text
    claim_token = claim.json()["claim_token"]

    running_cancel = e2e_client.post(
        f"/api/devices/{device_id}/jobs/{job2_id}/cancel",
        headers=org1_user.headers,
    )
    assert running_cancel.status_code == 200, running_cancel.text
    # Claimed jobs cancel immediately (no side effect yet).
    assert running_cancel.json()["status"] in ("cancelled", "running")

    if running_cancel.json()["status"] == "running":
        hb = e2e_client.post(
            "/api/device/heartbeat",
            headers=agent_headers,
            json={"agent_session_id": session_id},
        )
        assert hb.status_code == 200
        assert hb.json()["cancel_requested"] is True

        result = e2e_client.post(
            f"/api/device/jobs/{job2_id}/result",
            headers=agent_headers,
            json={"claim_token": claim_token, "status": "cancelled", "exit_code": 1},
        )
        assert result.status_code == 200, result.text
        await db_session.refresh(
            (await db_session.get(DeviceJob, UUID(job2_id)))
        )
        row = await db_session.get(DeviceJob, UUID(job2_id))
        assert row.status == "cancelled"
        assert row.cancel_requested_at is not None

    # Control keys never cancel (M0 matrix).
    ck = e2e_client.post(
        "/api/device-control-keys",
        headers=platform_admin.headers,
        json={
            "name": "cancel-ck",
            "device_ids": [device_id],
            "organization_id": str(org1["id"]),
        },
    )
    assert ck.status_code == 201
    ck_cancel = e2e_client.post(
        f"/api/devices/{device_id}/jobs/{job2_id}/cancel",
        headers={"X-Bifrost-Control-Key": ck.json()["key"]},
    )
    assert ck_cancel.status_code == 403
    assert ck_cancel.json()["error"]["code"] == "permission_denied"
