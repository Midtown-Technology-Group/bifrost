"""Service-level tests for device_jobs (M2.1 #833).

Covers the M0 freeze: busy 409 (fast path + unique-index race), caps,
fenced claim/mark_running/finish, agent status restrictions (never `lost`),
and the running-loss watchdog that writes terminal `lost` without re-queue.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import status
from sqlalchemy.exc import IntegrityError

from src.models.orm.devices import DEVICE_STATUS_ACTIVE, DEVICE_STATUS_DISABLED, Device
from src.models.orm.device_jobs import (
    CLAIM_LEASE_SECONDS,
    JOB_STATUS_CLAIMED,
    JOB_STATUS_LOST,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RUNNING_LOST_SECONDS,
    DeviceJob,
)
from src.services.device_jobs import (
    claim_next,
    create_device_job,
    finish,
    mark_running,
    sweep_device_jobs,
)
from src.services.devices import DeviceOperationError

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _device(**overrides) -> Device:
    device = Device(
        id=overrides.pop("id", uuid4()),
        organization_id=overrides.pop("organization_id", uuid4()),
        display_name=overrides.pop("display_name", "d"),
    )
    for key, value in overrides.items():
        setattr(device, key, value)
    return device


def _job(**overrides) -> DeviceJob:
    job = DeviceJob(
        id=overrides.pop("id", uuid4()),
        organization_id=overrides.pop("organization_id", uuid4()),
        device_id=overrides.pop("device_id", uuid4()),
        status=overrides.pop("status", JOB_STATUS_PENDING),
        script_name=overrides.pop("script_name", "probe"),
        script_content=overrides.pop("script_content", "Write-Output 1"),
        timeout_seconds=overrides.pop("timeout_seconds", 120),
        max_output_bytes=overrides.pop("max_output_bytes", 1 << 20),
        claim_token=overrides.pop("claim_token", None),
        claimed_at=overrides.pop("claimed_at", None),
        agent_session_id=overrides.pop("agent_session_id", None),
        last_agent_activity_at=overrides.pop("last_agent_activity_at", None),
        log_sequence=overrides.pop("log_sequence", 0),
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _result(scalar=None, rows=None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.scalars.return_value.all.return_value = rows or []
    return result


def _create_kwargs(**overrides) -> dict:
    kwargs = dict(
        organization_id=uuid4(),
        device_id=uuid4(),
        script_name="probe",
        script_content="Write-Output 1",
        now=NOW,
    )
    kwargs.update(overrides)
    return kwargs


class TestCreate:
    async def test_script_size_cap(self):
        session = _session()
        session.get = AsyncMock(return_value=_device(status=DEVICE_STATUS_ACTIVE))
        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(
                session, **_create_kwargs(script_content="x" * (256 * 1024 + 1))
            )
        assert exc.value.code == "script_too_large"
        assert exc.value.status_code == 413

    async def test_params_size_cap(self):
        session = _session()
        session.get = AsyncMock(return_value=_device(status=DEVICE_STATUS_ACTIVE))
        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(
                session, **_create_kwargs(params={"blob": "y" * (32 * 1024 + 1)})
            )
        assert exc.value.code == "payload_too_large"

    async def test_timeout_and_output_bounds(self):
        session = _session()
        session.get = AsyncMock(return_value=_device(status=DEVICE_STATUS_ACTIVE))
        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(session, **_create_kwargs(timeout_seconds=0))
        assert exc.value.code == "invalid_parameter"
        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(session, **_create_kwargs(timeout_seconds=901))
        assert exc.value.code == "invalid_parameter"
        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(session, **_create_kwargs(max_output_bytes=512))
        assert exc.value.code == "invalid_parameter"

    async def test_unknown_or_cross_org_device(self):
        session = _session()
        session.get = AsyncMock(return_value=None)
        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(session, **_create_kwargs())
        assert exc.value.code == "unknown_device"

        session2 = _session()
        foreign = _device(status=DEVICE_STATUS_ACTIVE)
        session2.get = AsyncMock(return_value=foreign)
        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(session2, **_create_kwargs())
        assert exc.value.code == "unknown_device"

    async def test_disabled_device(self):
        session = _session()
        device = _device(status=DEVICE_STATUS_DISABLED)
        session.get = AsyncMock(return_value=device)
        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(
                session, **_create_kwargs(organization_id=device.organization_id)
            )
        assert exc.value.code == "device_disabled"
        assert exc.value.status_code == 403

    async def test_busy_fast_path_includes_active_job_id(self):
        session = _session()
        device = _device(status=DEVICE_STATUS_ACTIVE)
        session.get = AsyncMock(return_value=device)
        active = _job(status=JOB_STATUS_PENDING)
        session.execute.return_value = _result(scalar=active)
        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(
                session, **_create_kwargs(organization_id=device.organization_id)
            )
        assert exc.value.code == "device_busy"
        assert exc.value.status_code == status.HTTP_409_CONFLICT
        assert exc.value.extra.get("job_id") == str(active.id)
        session.add.assert_not_called()

    async def test_busy_race_through_unique_index(self):
        session = _session()
        device = _device(status=DEVICE_STATUS_ACTIVE)
        session.get = AsyncMock(return_value=device)
        winner = _job(status=JOB_STATUS_CLAIMED)

        async def commit_raises():
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

        # First execute: no active row (pre-check). After the failed commit:
        # the winner shows up.
        session.execute.side_effect = [
            _result(scalar=None),
            _result(scalar=winner),
        ]
        session.commit.side_effect = commit_raises

        with pytest.raises(DeviceOperationError) as exc:
            await create_device_job(
                session, **_create_kwargs(organization_id=device.organization_id)
            )
        assert exc.value.code == "device_busy"
        assert exc.value.extra.get("job_id") == str(winner.id)
        session.rollback.assert_awaited()

    async def test_success_records_attribution_and_caps(self):
        session = _session()
        device = _device(status=DEVICE_STATUS_ACTIVE)
        session.get = AsyncMock(return_value=device)
        session.execute.return_value = _result(scalar=None)
        user_id, key_id, workflow_id, exec_id = uuid4(), uuid4(), uuid4(), uuid4()
        job = await create_device_job(
            session,
            **_create_kwargs(
                organization_id=device.organization_id,
                requested_by_user_id=user_id,
                requested_by_api_key_id=key_id,
                requested_by_workflow_id=workflow_id,
                requested_by_execution_id=exec_id,
                timeout_seconds=900,
                max_output_bytes=16 * 1024 * 1024,
            ),
        )
        assert job.status == JOB_STATUS_PENDING
        assert job.requested_by_user_id == user_id
        assert job.requested_by_api_key_id == key_id
        assert job.requested_by_workflow_id == workflow_id
        assert job.requested_by_execution_id == exec_id
        assert job.timeout_seconds == 900
        assert job.log_sequence == 0
        session.add.assert_called_once()
        session.commit.assert_awaited()


class TestClaim:
    async def test_claim_uses_skip_locked_and_none_when_idle(self):
        session = _session()
        session.execute.return_value = _result(scalar=None)
        got = await claim_next(
            session, device_id=uuid4(), agent_session_id=uuid4(), now=NOW
        )
        assert got is None
        from sqlalchemy.dialects import postgresql

        compiled = session.execute.call_args[0][0].compile(
            dialect=postgresql.dialect()
        )
        stmt = str(compiled)
        assert "FOR UPDATE SKIP LOCKED" in stmt
        # The status predicates are bound parameters: pending OR
        # (claimed AND claimed_at < lease cutoff).
        assert "device_jobs.status" in stmt
        assert set(compiled.params.values()) >= {"pending", "claimed"}

    async def test_claim_pending_mints_token_and_activity(self):
        session = _session()
        agent = uuid4()
        pending = _job(status=JOB_STATUS_PENDING, claim_token=None)
        session.execute.return_value = _result(scalar=pending)
        got = await claim_next(
            session, device_id=pending.device_id, agent_session_id=agent, now=NOW
        )
        assert got is pending
        assert got.status == JOB_STATUS_CLAIMED
        assert got.claim_token is not None
        assert got.agent_session_id == agent
        assert got.claimed_at == NOW
        assert got.last_agent_activity_at == NOW
        session.commit.assert_awaited()

    async def test_stale_claimed_reclaimed_with_fresh_token(self):
        session = _session()
        stale = _job(
            status=JOB_STATUS_CLAIMED,
            claim_token=uuid4(),
            claimed_at=NOW - timedelta(seconds=CLAIM_LEASE_SECONDS + 5),
        )
        old_token = stale.claim_token
        session.execute.return_value = _result(scalar=stale)
        got = await claim_next(
            session, device_id=stale.device_id, agent_session_id=uuid4(), now=NOW
        )
        assert got.status == JOB_STATUS_CLAIMED
        assert got.claim_token is not None
        assert got.claim_token != old_token


class TestMarkRunning:
    async def test_happy_path_from_claimed(self):
        session = _session()
        token = uuid4()
        job = _job(status=JOB_STATUS_CLAIMED, claim_token=token)
        session.execute.return_value = _result(scalar=job)
        got = await mark_running(
            session, job_id=job.id, claim_token=token, agent_session_id=uuid4(),
            now=NOW,
        )
        assert got.status == JOB_STATUS_RUNNING
        assert got.last_agent_activity_at == NOW

    async def test_wrong_token_fenced(self):
        session = _session()
        job = _job(status=JOB_STATUS_CLAIMED, claim_token=uuid4())
        session.execute.return_value = _result(scalar=job)
        with pytest.raises(DeviceOperationError) as exc:
            await mark_running(
                session, job_id=job.id, claim_token=uuid4(),
                agent_session_id=uuid4(), now=NOW,
            )
        assert exc.value.code == "fence_violation"
        assert exc.value.status_code == 409

    async def test_terminal_job_rejected(self):
        session = _session()
        token = uuid4()
        job = _job(status=JOB_STATUS_LOST, claim_token=token)
        session.execute.return_value = _result(scalar=job)
        with pytest.raises(DeviceOperationError) as exc:
            await mark_running(
                session, job_id=job.id, claim_token=token,
                agent_session_id=uuid4(), now=NOW,
            )
        assert exc.value.code == "job_terminal"

    async def test_replay_is_idempotent_for_current_token(self):
        session = _session()
        token = uuid4()
        job = _job(status=JOB_STATUS_RUNNING, claim_token=token)
        session.execute.return_value = _result(scalar=job)
        got = await mark_running(
            session, job_id=job.id, claim_token=token, agent_session_id=uuid4(),
            now=NOW,
        )
        assert got.status == JOB_STATUS_RUNNING
        session.commit.assert_awaited()


class TestFinish:
    async def test_fenced_terminal_from_running(self):
        session = _session()
        token = uuid4()
        job = _job(status=JOB_STATUS_RUNNING, claim_token=token)
        session.execute.return_value = _result(scalar=job)
        got = await finish(
            session, job_id=job.id, claim_token=token,
            job_status=JOB_STATUS_SUCCEEDED, exit_code=0, result="ok", now=NOW,
        )
        assert got.status == JOB_STATUS_SUCCEEDED
        assert got.exit_code == 0
        assert got.result == "ok"

    async def test_finish_from_claimed_is_allowed(self):
        # Spawn+exit can beat a failed mark_running; the valid token proves
        # the claim, so a terminal report from claimed is accepted.
        session = _session()
        token = uuid4()
        job = _job(status=JOB_STATUS_CLAIMED, claim_token=token)
        session.execute.return_value = _result(scalar=job)
        got = await finish(
            session, job_id=job.id, claim_token=token,
            job_status="failed", exit_code=3, now=NOW,
        )
        assert got.status == "failed"

    async def test_agent_cannot_report_lost(self):
        session = _session()
        token = uuid4()
        job = _job(status=JOB_STATUS_RUNNING, claim_token=token)
        session.execute.return_value = _result(scalar=job)
        with pytest.raises(DeviceOperationError) as exc:
            await finish(
                session, job_id=job.id, claim_token=token,
                job_status=JOB_STATUS_LOST, now=NOW,
            )
        assert exc.value.code == "invalid_parameter"
        assert exc.value.status_code == 422

    async def test_wrong_token_fenced_on_lost_job(self):
        # Late agent result after the server wrote `lost` is rejected.
        session = _session()
        job = _job(status=JOB_STATUS_LOST, claim_token=uuid4())
        session.execute.return_value = _result(scalar=job)
        with pytest.raises(DeviceOperationError) as exc:
            await finish(
                session, job_id=job.id, claim_token=uuid4(),
                job_status=JOB_STATUS_SUCCEEDED, now=NOW,
            )
        assert exc.value.code == "fence_violation"

    async def test_already_terminal_rejected(self):
        session = _session()
        token = uuid4()
        job = _job(status=JOB_STATUS_SUCCEEDED, claim_token=token)
        session.execute.return_value = _result(scalar=job)
        with pytest.raises(DeviceOperationError) as exc:
            await finish(
                session, job_id=job.id, claim_token=token,
                job_status="failed", now=NOW,
            )
        assert exc.value.code == "job_terminal"


class TestSweepWatchdog:
    async def test_silent_running_becomes_lost(self):
        session = _session()
        silent = _job(
            status=JOB_STATUS_RUNNING,
            claimed_at=NOW - timedelta(seconds=30),
            last_agent_activity_at=NOW - timedelta(seconds=RUNNING_LOST_SECONDS + 1),
        )
        session.execute.return_value = _result(rows=[silent])
        stats = await sweep_device_jobs(session, now=NOW)
        assert silent.status == JOB_STATUS_LOST
        assert "watchdog" in (silent.error or "")
        assert stats["lost_silence"] == 1
        session.commit.assert_awaited()

    async def test_fresh_running_stays_running(self):
        session = _session()
        fresh = _job(
            status=JOB_STATUS_RUNNING,
            claimed_at=NOW - timedelta(seconds=30),
            last_agent_activity_at=NOW - timedelta(seconds=5),
        )
        # First execute: running rows. Second: stale-claimed count.
        session.execute.side_effect = [_result(rows=[fresh]), _result(rows=[])]
        stats = await sweep_device_jobs(session, now=NOW)
        assert fresh.status == JOB_STATUS_RUNNING
        assert stats == {
            "lost_silence": 0,
            "lost_backstop": 0,
            "stale_claimed_reclaimable": 0,
        }

    async def test_past_deadline_without_terminal_becomes_lost(self):
        session = _session()
        wedged = _job(
            status=JOB_STATUS_RUNNING,
            timeout_seconds=120,
            claimed_at=NOW - timedelta(seconds=120 + 60 + 1),
            last_agent_activity_at=NOW - timedelta(seconds=10),  # heartbeats alive
        )
        session.execute.return_value = _result(rows=[wedged])
        stats = await sweep_device_jobs(session, now=NOW)
        assert wedged.status == JOB_STATUS_LOST
        assert stats["lost_backstop"] == 1

    async def test_stale_claimed_left_for_claim_next(self):
        session = _session()
        stale = _job(
            status=JOB_STATUS_CLAIMED,
            claimed_at=NOW - timedelta(seconds=CLAIM_LEASE_SECONDS + 1),
            last_agent_activity_at=NOW - timedelta(seconds=CLAIM_LEASE_SECONDS - 1),
        )
        # First execute: running rows (none). Second: stale claimed count.
        session.execute.side_effect = [_result(rows=[]), _result(rows=[stale])]
        stats = await sweep_device_jobs(session, now=NOW)
        # Reclaim happens in claim_next, never here — no state change.
        assert stale.status == JOB_STATUS_CLAIMED
        assert stats["stale_claimed_reclaimable"] == 1
        assert stats["lost_silence"] == 0
