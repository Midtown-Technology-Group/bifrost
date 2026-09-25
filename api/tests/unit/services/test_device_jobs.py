"""Service-level tests for device_jobs (M2.1 #833).

Covers the M0 freeze: busy 409 (fast path + unique-index race), caps,
fenced claim/mark_running/finish, agent status restrictions (never `lost`),
and the running-loss watchdog that writes terminal `lost` without re-queue.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import status
from sqlalchemy.exc import IntegrityError

from src.models.orm.devices import DEVICE_STATUS_ACTIVE, DEVICE_STATUS_DISABLED, Device
from src.models.orm.device_jobs import (
    CLAIM_LEASE_SECONDS,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_CLAIMED,
    JOB_STATUS_LOST,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RUNNING_LOST_SECONDS,
    DeviceJob,
)
from src.services.device_jobs import (
    append_logs,
    broadcast_job_available,
    broadcast_job_logs,
    claim_next,
    create_device_job,
    finish,
    get_job_for_control_key,
    list_jobs_for_control_key_stmt,
    list_jobs_stmt,
    mark_running,
    record_activity,
    renew_from_heartbeat,
    request_cancel,
    sweep_device_jobs,
    validate_workflow_attribution,
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
        started_at=overrides.pop("started_at", None),
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
        assert got.started_at == NOW

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
        original_start = NOW - timedelta(seconds=30)
        job = _job(
            status=JOB_STATUS_RUNNING,
            claim_token=token,
            started_at=original_start,
        )
        session.execute.return_value = _result(scalar=job)
        got = await mark_running(
            session, job_id=job.id, claim_token=token, agent_session_id=uuid4(),
            now=NOW,
        )
        assert got.status == JOB_STATUS_RUNNING
        # A replay must not move the timeout-backstop anchor.
        assert got.started_at == original_start
        session.commit.assert_awaited()


class TestRecordActivity:
    async def test_renews_for_live_claim(self):
        session = _session()
        token = uuid4()
        job = _job(
            status=JOB_STATUS_RUNNING,
            claim_token=token,
            last_agent_activity_at=NOW - timedelta(seconds=60),
        )
        session.execute.return_value = _result(scalar=job)
        got = await record_activity(
            session, job_id=job.id, claim_token=token, now=NOW
        )
        assert got.last_agent_activity_at == NOW
        session.commit.assert_awaited()

    async def test_wrong_token_fenced(self):
        session = _session()
        job = _job(status=JOB_STATUS_RUNNING, claim_token=uuid4())
        session.execute.return_value = _result(scalar=job)
        with pytest.raises(DeviceOperationError) as exc:
            await record_activity(
                session, job_id=job.id, claim_token=uuid4(), now=NOW
            )
        assert exc.value.code == "fence_violation"

    async def test_terminal_job_rejected(self):
        session = _session()
        token = uuid4()
        job = _job(status=JOB_STATUS_LOST, claim_token=token)
        session.execute.return_value = _result(scalar=job)
        with pytest.raises(DeviceOperationError) as exc:
            await record_activity(
                session, job_id=job.id, claim_token=token, now=NOW
            )
        assert exc.value.code == "job_terminal"


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
            # Claim was recent, but execution started long ago: the backstop
            # must anchor on started_at, not claimed_at.
            claimed_at=NOW - timedelta(seconds=10),
            started_at=NOW - timedelta(seconds=120 + 60 + 1),
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


def _append_session(job, existing=None) -> AsyncMock:
    session = _session()
    session.execute.side_effect = [
        _result(scalar=job),
        _result(rows=existing or []),
    ]
    return session


class TestAppendLogs:
    async def test_validation_bounds(self):
        session = _session()
        token = uuid4()
        job_id = uuid4()
        with pytest.raises(DeviceOperationError) as exc:
            await append_logs(
                session, job_id=job_id, claim_token=token,
                entries=[{"seq": 0, "stream": "stdout", "text": "x"}],
            )
        assert exc.value.code == "invalid_parameter"

        with pytest.raises(DeviceOperationError) as exc:
            await append_logs(
                session, job_id=job_id, claim_token=token,
                entries=[
                    {"seq": 1, "stream": "stdout", "text": "a"},
                    {"seq": 1, "stream": "stdout", "text": "b"},
                ],
            )
        assert exc.value.code == "invalid_parameter"

        with pytest.raises(DeviceOperationError) as exc:
            await append_logs(
                session, job_id=job_id, claim_token=token,
                entries=[{"seq": 1, "stream": "stdin", "text": "a"}],
            )
        assert exc.value.code == "invalid_parameter"

        with pytest.raises(DeviceOperationError) as exc:
            await append_logs(
                session, job_id=job_id, claim_token=token,
                entries=[{"seq": 1, "stream": "stdout", "text": "x" * (65536 + 1)}],
            )
        assert exc.value.code == "payload_too_large"

        with pytest.raises(DeviceOperationError) as exc:
            await append_logs(
                session, job_id=job_id, claim_token=token,
                entries=[
                    {"seq": i + 1, "stream": "stdout", "text": "y" * 65536}
                    for i in range(17)
                ],
            )
        assert exc.value.code == "payload_too_large"

        with pytest.raises(DeviceOperationError) as exc:
            await append_logs(
                session, job_id=job_id, claim_token=token,
                entries=[
                    {"seq": i + 1, "stream": "stdout", "text": "z"} for i in range(513)
                ],
            )
        assert exc.value.code == "payload_too_large"
        session.execute.assert_not_awaited()

    async def test_fence_and_terminal(self):
        token = uuid4()
        live = _job(status=JOB_STATUS_RUNNING, claim_token=token)
        session = _append_session(live)
        with pytest.raises(DeviceOperationError) as exc:
            await append_logs(
                session, job_id=live.id, claim_token=uuid4(), entries=
                [{"seq": 1, "stream": "stdout", "text": "x"}], now=NOW,
            )
        assert exc.value.code == "fence_violation"

        terminal = _job(status=JOB_STATUS_SUCCEEDED, claim_token=token)
        session = _append_session(terminal)
        with pytest.raises(DeviceOperationError) as exc:
            await append_logs(
                session, job_id=terminal.id, claim_token=token, entries=
                [{"seq": 1, "stream": "stdout", "text": "x"}], now=NOW,
            )
        assert exc.value.code == "job_terminal"

    async def test_happy_appends_and_renews_activity(self):
        token = uuid4()
        job = _job(
            status=JOB_STATUS_RUNNING,
            claim_token=token,
            last_agent_activity_at=NOW - timedelta(seconds=40),
            log_sequence=0,
        )
        session = _append_session(job, existing=[])
        got_job, inserted = await append_logs(
            session,
            job_id=job.id,
            claim_token=token,
            entries=[
                {"seq": 2, "stream": "stdout", "text": "b"},
                {"seq": 1, "stream": "stderr", "text": "a"},
            ],
            now=NOW,
        )
        assert len(inserted) == 2
        assert got_job.last_agent_activity_at == NOW
        assert got_job.log_sequence == 2
        assert session.add.call_count == 2
        session.commit.assert_awaited()

    async def test_identical_replay_is_noop(self):
        from src.models.orm.device_job_logs import DeviceJobLog

        token = uuid4()
        job = _job(status=JOB_STATUS_RUNNING, claim_token=token, log_sequence=1)
        existing = DeviceJobLog(
            job_id=job.id, seq=1, stream="stdout", text="same"
        )
        session = _append_session(job, existing=[existing])
        _got, inserted = await append_logs(
            session,
            job_id=job.id,
            claim_token=token,
            entries=[{"seq": 1, "stream": "stdout", "text": "same"}],
            now=NOW,
        )
        assert inserted == []
        session.add.assert_not_called()
        session.commit.assert_awaited()  # activity still renewed

    async def test_changed_content_for_same_seq_conflicts(self):
        from src.models.orm.device_job_logs import DeviceJobLog

        token = uuid4()
        job = _job(status=JOB_STATUS_RUNNING, claim_token=token, log_sequence=1)
        existing = DeviceJobLog(
            job_id=job.id, seq=1, stream="stdout", text="original"
        )
        session = _append_session(job, existing=[existing])
        with pytest.raises(DeviceOperationError) as exc:
            await append_logs(
                session,
                job_id=job.id,
                claim_token=token,
                entries=[{"seq": 1, "stream": "stdout", "text": "CHANGED"}],
                now=NOW,
            )
        assert exc.value.code == "log_seq_conflict"
        assert exc.value.status_code == 409
        session.add.assert_not_called()


class TestRenewFromHeartbeat:
    async def test_owning_session_renews(self):
        session = _session()
        agent = uuid4()
        job = _job(
            status=JOB_STATUS_RUNNING,
            agent_session_id=agent,
            last_agent_activity_at=NOW - timedelta(seconds=60),
        )
        session.execute.return_value = _result(scalar=job)
        got = await renew_from_heartbeat(
            session, device_id=job.device_id, agent_session_id=agent, now=NOW
        )
        assert got is job
        assert job.last_agent_activity_at == NOW
        session.commit.assert_awaited()

    async def test_foreign_session_does_not_renew(self):
        session = _session()
        job = _job(
            status=JOB_STATUS_RUNNING,
            agent_session_id=uuid4(),
            last_agent_activity_at=NOW - timedelta(seconds=60),
        )
        session.execute.return_value = _result(scalar=job)
        got = await renew_from_heartbeat(
            session, device_id=job.device_id, agent_session_id=uuid4(), now=NOW
        )
        assert got is None
        assert job.last_agent_activity_at == NOW - timedelta(seconds=60)
        session.commit.assert_not_awaited()

    async def test_no_active_job_returns_none(self):
        session = _session()
        session.execute.return_value = _result(scalar=None)
        got = await renew_from_heartbeat(
            session, device_id=uuid4(), agent_session_id=uuid4(), now=NOW
        )
        assert got is None

    async def test_agent_version_reported_persists_to_device(self):
        session = _session()
        device = _device(agent_version=None)
        session.get = AsyncMock(return_value=device)
        session.execute.return_value = _result(scalar=None)
        got = await renew_from_heartbeat(
            session,
            device_id=device.id,
            agent_session_id=uuid4(),
            agent_version="1.2.3",
            now=NOW,
        )
        assert got is None
        assert device.agent_version == "1.2.3"
        session.get.assert_awaited_once()
        session.commit.assert_awaited()

    async def test_agent_version_unchanged_is_not_rewritten(self):
        session = _session()
        device = _device(agent_version="1.2.3")
        session.get = AsyncMock(return_value=device)
        session.execute.return_value = _result(scalar=None)
        await renew_from_heartbeat(
            session,
            device_id=device.id,
            agent_session_id=uuid4(),
            agent_version="1.2.3",
            now=NOW,
        )
        assert device.agent_version == "1.2.3"
        session.commit.assert_not_awaited()

    async def test_agent_version_absent_or_empty_never_touches_column(self):
        for reported in (None, ""):
            session = _session()
            device = _device(agent_version="1.2.3")
            session.get = AsyncMock(return_value=device)
            session.execute.return_value = _result(scalar=None)
            got = await renew_from_heartbeat(
                session,
                device_id=device.id,
                agent_session_id=uuid4(),
                agent_version=reported,
                now=NOW,
            )
            assert got is None
            assert device.agent_version == "1.2.3"
            session.get.assert_not_awaited()
            session.commit.assert_not_awaited()


class TestCancel:
    async def test_pending_cancels_immediately(self):
        from src.core.principal import UserPrincipal

        session = _session()
        job = _job(status=JOB_STATUS_PENDING)
        session.execute.return_value = _result(scalar=job)
        user = UserPrincipal(
            user_id=uuid4(), email="u@example.com",
            organization_id=job.organization_id, name="U",
        )
        got = await request_cancel(session, user, job.id, now=NOW)
        assert got.status == JOB_STATUS_CANCELLED
        assert got.cancel_requested_at == NOW
        assert got.error == "cancelled before running"

    async def test_running_only_gets_flag_and_is_idempotent(self):
        from src.core.principal import UserPrincipal

        session = _session()
        user = UserPrincipal(
            user_id=uuid4(), email="u@example.com", organization_id=uuid4(), name="U",
        )
        job = _job(status=JOB_STATUS_RUNNING)
        session.execute.return_value = _result(scalar=job)
        first = await request_cancel(session, user, job.id, now=NOW)
        assert first.status == JOB_STATUS_RUNNING
        assert first.cancel_requested_at == NOW

        second_now = NOW + timedelta(seconds=5)
        again = await request_cancel(session, user, job.id, now=second_now)
        assert again.status == JOB_STATUS_RUNNING
        # Idempotent: the flag keeps the first observation.
        assert again.cancel_requested_at == NOW

    async def test_terminal_job_rejected(self):
        from src.core.principal import UserPrincipal

        session = _session()
        user = UserPrincipal(
            user_id=uuid4(), email="u@example.com", organization_id=uuid4(), name="U",
        )
        job = _job(status=JOB_STATUS_SUCCEEDED)
        session.execute.return_value = _result(scalar=job)
        with pytest.raises(DeviceOperationError) as exc:
            await request_cancel(session, user, job.id, now=NOW)
        assert exc.value.code == "job_terminal"
        assert exc.value.status_code == 409

    async def test_cross_org_job_is_404(self):
        from src.core.principal import UserPrincipal

        session = _session()
        user = UserPrincipal(
            user_id=uuid4(), email="u@example.com", organization_id=uuid4(), name="U",
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute.return_value = result
        with pytest.raises(DeviceOperationError) as exc:
            await request_cancel(session, user, uuid4(), now=NOW)
        assert exc.value.code == "unknown_job"


class TestScopedReads:
    def test_job_list_scopes_org_and_device(self):
        from src.core.principal import UserPrincipal

        user = UserPrincipal(
            user_id=uuid4(), email="u@example.com", organization_id=uuid4(), name="U",
        )
        rendered = str(list_jobs_stmt(user, device_id=uuid4()))
        assert "device_jobs.organization_id = " in rendered
        assert "device_jobs.device_id = " in rendered
        assert "ORDER BY device_jobs.created_at DESC" in rendered

    def test_superuser_unscoped_job_list(self):
        from src.core.principal import UserPrincipal

        user = UserPrincipal(
            user_id=uuid4(), email="u@example.com", organization_id=None,
            name="U", is_superuser=True,
        )
        rendered = str(list_jobs_stmt(user))
        assert "WHERE" not in rendered

    def test_control_key_read_is_limited_to_its_own_jobs(self):
        key = SimpleNamespace(id=uuid4(), organization_id=uuid4())
        rendered = str(list_jobs_for_control_key_stmt(key, device_id=uuid4()))
        assert "requested_by_api_key_id" in rendered
        assert "device_jobs.organization_id" in rendered

    async def test_control_key_detail_404_for_foreign_job(self):
        session = _session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute.return_value = result
        key = SimpleNamespace(id=uuid4(), organization_id=uuid4())
        with pytest.raises(DeviceOperationError) as exc:
            await get_job_for_control_key(session, key, uuid4())
        assert exc.value.code == "unknown_job"
        assert exc.value.status_code == 404


class TestAttributionValidation:
    async def test_no_ids_skips_lookup(self):
        session = _session()
        await validate_workflow_attribution(session, uuid4(), None, None)
        session.execute.assert_not_awaited()

    async def test_unknown_workflow_rejected(self):
        session = _session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute.return_value = result
        with pytest.raises(DeviceOperationError) as exc:
            await validate_workflow_attribution(session, uuid4(), uuid4(), None)
        assert exc.value.code == "invalid_parameter"
        assert exc.value.status_code == 422

    async def test_unknown_execution_rejected(self):
        session = _session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute.return_value = result
        with pytest.raises(DeviceOperationError) as exc:
            await validate_workflow_attribution(session, uuid4(), None, uuid4())
        assert exc.value.code == "invalid_parameter"


class TestBroadcasts:
    async def test_job_available_hint_shape(self, monkeypatch):
        from src.core.pubsub import manager as pubsub_manager

        fake = AsyncMock()
        monkeypatch.setattr(pubsub_manager, "broadcast", fake)
        device_id, job_id = uuid4(), uuid4()
        await broadcast_job_available(device_id, job_id)
        fake.assert_awaited_once_with(
            f"device:{device_id}",
            {
                "type": "device_job_available",
                "job_id": str(job_id),
                "device_id": str(device_id),
            },
        )

    async def test_log_fanout_shape(self, monkeypatch):
        from src.core.pubsub import manager as pubsub_manager

        fake = AsyncMock()
        monkeypatch.setattr(pubsub_manager, "broadcast", fake)
        job_id = uuid4()
        entries = [{"seq": 1, "stream": "stdout", "text": "x", "ts": None}]
        await broadcast_job_logs(job_id, entries)
        fake.assert_awaited_once_with(
            f"device_job:{job_id}",
            {"type": "device_job_logs", "job_id": str(job_id), "entries": entries},
        )
