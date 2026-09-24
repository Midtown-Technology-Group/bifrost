"""E2E: M2.5 failure-mode suite (M2 gate, #837).

Dedicated failure drills for the frozen contracts. Cases already proven in
sibling suites are NOT duplicated here (per #837): wrong-token fencing and
disabled-device rejection live in test_device_protocol.py / test_device_ws.py;
WS query-key and cross-device-channel denial live in test_device_ws.py.

New drills in this file:
- concurrent claim: exactly one winner (one 200, one 204)
- stale `claimed` reclaimed with a FRESH token; the old token is then fenced
- late result/logs after server-written `lost` are rejected
- `running` loss -> terminal `lost`, never re-queued; explicit retry = new job
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from src.models.orm.device_jobs import CLAIM_LEASE_SECONDS, DeviceJob
from src.services.device_jobs import sweep_device_jobs
from tests.e2e.api.test_device_protocol import _enroll
from tests.e2e.fixtures.setup import API_BASE_URL


def _headers(device_key: str) -> dict:
    return {"X-Bifrost-Key": device_key}


def _job_body(**extra):
    body = {
        "script_name": "failure-drill",
        "script_content": "Write-Output drill",
        "timeout_seconds": 60,
    }
    body.update(extra)
    return body


async def _seed_job(db_session, org_id: str, device_id: str, **overrides) -> DeviceJob:
    job = DeviceJob(
        organization_id=UUID(org_id),
        device_id=UUID(device_id),
        status=overrides.pop("status", "pending"),
        script_name=overrides.pop("script_name", "drill-seed"),
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
@pytest.mark.asyncio
async def test_concurrent_claim_has_exactly_one_winner(
    e2e_client, platform_admin, org1, db_session
):
    """Two simultaneous claims: SKIP LOCKED yields one 200 and one 204."""
    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "race-device"
    )
    await _seed_job(db_session, org1["id"], device_id)
    session_id = str(uuid4())

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30) as client:
        request = {
            "json": {"agent_session_id": session_id},
            "headers": _headers(key),
        }
        r1, r2 = await asyncio.gather(
            client.post("/api/device/jobs/claim", **request),
            client.post("/api/device/jobs/claim", **request),
        )

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 204], f"expected one winner, got {statuses}"
    winner = r1 if r1.status_code == 200 else r2
    assert winner.json()["claim_token"]


@pytest.mark.e2e
async def test_stale_claimed_reclaimed_with_fresh_token_old_one_fenced(
    e2e_client, platform_admin, org1, db_session
):
    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "stale-device"
    )
    headers = _headers(key)
    session_a = str(uuid4())
    session_b = str(uuid4())

    job = await _seed_job(db_session, org1["id"], device_id)
    first = e2e_client.post(
        "/api/device/jobs/claim",
        headers=headers,
        json={"agent_session_id": session_a},
    )
    assert first.status_code == 200, first.text
    old_token = first.json()["claim_token"]

    # Age the claim past the 60s lease.
    job.claimed_at = datetime.now(timezone.utc) - timedelta(
        seconds=CLAIM_LEASE_SECONDS + 5
    )
    await db_session.commit()

    second = e2e_client.post(
        "/api/device/jobs/claim",
        headers=headers,
        json={"agent_session_id": session_b},
    )
    assert second.status_code == 200, second.text
    new_token = second.json()["claim_token"]
    assert new_token != old_token

    # The superseded token is fenced on logs and on the running report.
    logs = e2e_client.post(
        f"/api/device/jobs/{job.id}/logs",
        headers=headers,
        json={
            "claim_token": old_token,
            "entries": [{"seq": 1, "stream": "stdout", "text": "stale"}],
        },
    )
    assert logs.status_code == 409
    assert logs.json()["error"]["code"] == "fence_violation"

    running = e2e_client.post(
        f"/api/device/jobs/{job.id}/running",
        headers=headers,
        json={"claim_token": old_token, "agent_session_id": session_a},
    )
    assert running.status_code == 409
    assert running.json()["error"]["code"] == "fence_violation"


@pytest.mark.e2e
async def test_late_reports_after_lost_are_rejected(
    e2e_client, platform_admin, org1, db_session
):
    """Server-written `lost` is terminal: a late agent result/logs with the
    ORIGINAL (still-current) token are rejected — no resurrection."""
    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "lost-device"
    )
    headers = _headers(key)
    session_id = str(uuid4())

    job = await _seed_job(db_session, org1["id"], device_id)
    claim = e2e_client.post(
        "/api/device/jobs/claim",
        headers=headers,
        json={"agent_session_id": session_id},
    )
    assert claim.status_code == 200, claim.text
    token = claim.json()["claim_token"]

    # Server writes `lost` (as the sweep would after activity silence).
    job.status = "lost"
    job.error = "agent activity lost after running (server watchdog)"
    await db_session.commit()

    late_result = e2e_client.post(
        f"/api/device/jobs/{job.id}/result",
        headers=headers,
        json={"claim_token": token, "status": "succeeded", "exit_code": 0},
    )
    assert late_result.status_code == 409
    assert late_result.json()["error"]["code"] == "job_terminal"

    late_logs = e2e_client.post(
        f"/api/device/jobs/{job.id}/logs",
        headers=headers,
        json={
            "claim_token": token,
            "entries": [{"seq": 1, "stream": "stdout", "text": "late"}],
        },
    )
    assert late_logs.status_code == 409

    # The row is still terminal `lost` — nothing was resurrected.
    await db_session.refresh(job)
    assert job.status == "lost"


@pytest.mark.e2e
async def test_running_loss_becomes_lost_and_requires_explicit_retry(
    e2e_client, platform_admin, org1, db_session
):
    """Drill for feedback #1: running + activity silence -> terminal `lost`
    (NOT re-queued); an explicit retry creates a NEW job id; the agent's
    late report for the lost job stays fenced."""
    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "watchdog-device"
    )
    headers = _headers(key)

    job = await _seed_job(
        db_session,
        org1["id"],
        device_id,
        status="running",
        claimed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        agent_session_id=uuid4(),
        last_agent_activity_at=datetime.now(timezone.utc) - timedelta(seconds=300),
    )
    if job.claim_token is None:
        job.claim_token = uuid4()
        await db_session.commit()
    claim_token = job.claim_token

    # Watchdog sweep: running + silent > 90s -> terminal lost, never pending.
    stats = await sweep_device_jobs(db_session, now=datetime.now(timezone.utc))
    await db_session.refresh(job)
    assert job.status == "lost"
    assert stats["lost_silence"] >= 1

    # Explicit retry: a NEW job on the same device is accepted (lost is
    # terminal, so the one-active index is free) — new id, audited create.
    retry = e2e_client.post(
        f"/api/devices/{device_id}/jobs",
        headers=platform_admin.headers,
        json=_job_body(),
    )
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] != str(job.id)

    # The agent from the lost run still cannot report into the lost job.
    stale_result = e2e_client.post(
        f"/api/device/jobs/{job.id}/result",
        headers=headers,
        json={"claim_token": str(claim_token), "status": "failed", "exit_code": 1},
    )
    assert stale_result.status_code == 409
    assert stale_result.json()["error"]["code"] in ("job_terminal", "fence_violation")

    await db_session.refresh(job)
    assert job.status == "lost"
