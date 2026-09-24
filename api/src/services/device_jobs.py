"""device_jobs service: create, fenced claim/run/finish, reclaim, lost.

Implements the M0 freeze (docs/architecture/device-control-plane.md
§Job lifecycle) exactly:

- create: structured busy 409 (never a queue), caps enforced, device must be
  active and in the same org;
- claim: ``FOR UPDATE SKIP LOCKED`` over ``pending`` or lease-expired
  ``claimed`` rows; each claim mints a fresh ``claim_token``;
- ``running`` only from ``claimed`` with the current token (agent reports it
  only after a real spawn);
- finish: fenced on ``claim_token``; agents may post only agent-terminal
  statuses — never ``lost``;
- reclaim: only ``pending``/``claimed``; ``running`` loss (activity silence
  or timeout backstop) writes terminal **``lost``** and never re-queues.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.devices import DEVICE_STATUS_ACTIVE, Device
from src.models.orm.device_jobs import (
    ACTIVE_JOB_STATUSES,
    AGENT_TERMINAL_STATUSES,
    CLAIM_LEASE_SECONDS,
    JOB_STATUS_CLAIMED,
    JOB_STATUS_LOST,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    MAX_OUTPUT_DEFAULT_BYTES,
    MAX_OUTPUT_MAX_BYTES,
    MAX_OUTPUT_MIN_BYTES,
    PARAMS_MAX_BYTES,
    RUNNING_LOST_SECONDS,
    SCRIPT_MAX_BYTES,
    TERMINAL_JOB_STATUSES,
    TIMEOUT_BACKSTOP_GRACE_SECONDS,
    TIMEOUT_DEFAULT_SECONDS,
    TIMEOUT_MAX_SECONDS,
    DeviceJob,
)
from src.services.devices import DeviceOperationError


def _busy(active: DeviceJob | None) -> DeviceOperationError:
    return DeviceOperationError(
        status.HTTP_409_CONFLICT,
        "device_busy",
        "device already has an active job",
        job_id=str(active.id) if active is not None else None,
    )


async def create_device_job(
    db: AsyncSession,
    *,
    organization_id: UUID,
    device_id: UUID,
    script_name: str,
    script_content: str,
    params: dict | None = None,
    timeout_seconds: int = TIMEOUT_DEFAULT_SECONDS,
    max_output_bytes: int = MAX_OUTPUT_DEFAULT_BYTES,
    requested_by_user_id: UUID | None = None,
    requested_by_api_key_id: UUID | None = None,
    requested_by_workflow_id: UUID | None = None,
    requested_by_execution_id: UUID | None = None,
    now: datetime | None = None,
) -> DeviceJob:
    """Create a pending job (structured busy on an active job)."""
    script_bytes = len(script_content.encode("utf-8"))
    if script_bytes > SCRIPT_MAX_BYTES:
        raise DeviceOperationError(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "script_too_large",
            f"script_content exceeds {SCRIPT_MAX_BYTES} bytes",
        )
    if params:
        params_bytes = len(
            json.dumps(params, separators=(",", ":"), default=str).encode("utf-8")
        )
        if params_bytes > PARAMS_MAX_BYTES:
            raise DeviceOperationError(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "payload_too_large",
                f"params exceed {PARAMS_MAX_BYTES} bytes serialized",
            )
    if not 1 <= timeout_seconds <= TIMEOUT_MAX_SECONDS:
        raise DeviceOperationError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_parameter",
            f"timeout_seconds must be 1..{TIMEOUT_MAX_SECONDS}",
        )
    if not MAX_OUTPUT_MIN_BYTES <= max_output_bytes <= MAX_OUTPUT_MAX_BYTES:
        raise DeviceOperationError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_parameter",
            f"max_output_bytes must be {MAX_OUTPUT_MIN_BYTES}..{MAX_OUTPUT_MAX_BYTES}",
        )

    device = await db.get(Device, device_id)
    if device is None or device.organization_id != organization_id:
        raise DeviceOperationError(
            status.HTTP_404_NOT_FOUND, "unknown_device", "device not found"
        )
    if device.status != DEVICE_STATUS_ACTIVE:
        raise DeviceOperationError(
            status.HTTP_403_FORBIDDEN, "device_disabled", "device is disabled"
        )

    # Fast-path busy check; the partial unique index is the authority for
    # the concurrent-create race (handled below).
    active_result = await db.execute(
        select(DeviceJob)
        .where(
            DeviceJob.device_id == device_id,
            DeviceJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .limit(1)
    )
    active = active_result.scalar_one_or_none()
    if active is not None:
        raise _busy(active)

    job = DeviceJob(
        organization_id=organization_id,
        device_id=device_id,
        status=JOB_STATUS_PENDING,
        script_name=script_name,
        script_content=script_content,
        params=params or {},
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        requested_by_user_id=requested_by_user_id,
        requested_by_api_key_id=requested_by_api_key_id,
        requested_by_workflow_id=requested_by_workflow_id,
        requested_by_execution_id=requested_by_execution_id,
        log_sequence=0,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent create won the partial unique index — structured busy.
        await db.rollback()
        race_result = await db.execute(
            select(DeviceJob)
            .where(
                DeviceJob.device_id == device_id,
                DeviceJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .limit(1)
        )
        raise _busy(race_result.scalar_one_or_none()) from None
    await db.refresh(job)
    return job


async def claim_next(
    db: AsyncSession,
    *,
    device_id: UUID,
    agent_session_id: UUID,
    now: datetime | None = None,
) -> DeviceJob | None:
    """Claim the next pending or lease-expired claimed job for a device.

    Returns None when there is no work (route answers 204). Exactly one
    concurrent claimer can win a row (SKIP LOCKED + row re-check).
    """
    current = now if now is not None else datetime.now(timezone.utc)
    lease_cutoff = current - timedelta(seconds=CLAIM_LEASE_SECONDS)
    stmt = (
        select(DeviceJob)
        .where(
            DeviceJob.device_id == device_id,
            or_(
                DeviceJob.status == JOB_STATUS_PENDING,
                and_(
                    DeviceJob.status == JOB_STATUS_CLAIMED,
                    DeviceJob.claimed_at < lease_cutoff,
                ),
            ),
        )
        .order_by(DeviceJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        return None

    job.status = JOB_STATUS_CLAIMED
    job.claim_token = uuid4()
    job.claimed_at = current
    job.agent_session_id = agent_session_id
    job.last_agent_activity_at = current
    await db.commit()
    await db.refresh(job)
    return job


async def _locked_job(db: AsyncSession, job_id: UUID) -> DeviceJob:
    result = await db.execute(
        select(DeviceJob).where(DeviceJob.id == job_id).with_for_update()
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise DeviceOperationError(
            status.HTTP_404_NOT_FOUND, "unknown_job", "job not found"
        )
    return job


def _assert_fenced(job: DeviceJob, claim_token: UUID) -> None:
    if job.claim_token is None or job.claim_token != claim_token:
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "fence_violation",
            "claim_token does not match the current claim",
        )
    if job.status in TERMINAL_JOB_STATUSES:
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "job_terminal",
            "job is already terminal",
        )


async def mark_running(
    db: AsyncSession,
    *,
    job_id: UUID,
    claim_token: UUID,
    agent_session_id: UUID,
    now: datetime | None = None,
) -> DeviceJob:
    """Report a real spawn: claimed -> running, fenced on claim_token.

    Idempotent for the current token (network-retry safe). The agent may
    only call this after cmd.Start() succeeded (feedback #1).
    """
    current = now if now is not None else datetime.now(timezone.utc)
    job = await _locked_job(db, job_id)
    if job.claim_token != claim_token:
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "fence_violation",
            "claim_token does not match the current claim",
        )
    if job.status == JOB_STATUS_RUNNING:
        # Idempotent replay from the same claim.
        job.last_agent_activity_at = current
        await db.commit()
        await db.refresh(job)
        return job
    if job.status in TERMINAL_JOB_STATUSES:
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "job_terminal",
            "job is already terminal",
        )
    if job.status != JOB_STATUS_CLAIMED:
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "job_terminal",
            f"job is {job.status}, not claimable as running",
        )
    job.status = JOB_STATUS_RUNNING
    job.agent_session_id = agent_session_id
    job.last_agent_activity_at = current
    if job.started_at is None:
        job.started_at = current
    await db.commit()
    await db.refresh(job)
    return job


async def record_activity(
    db: AsyncSession,
    *,
    job_id: UUID,
    claim_token: UUID,
    now: datetime | None = None,
) -> DeviceJob:
    """Fenced activity renewal for a live claim (heartbeat/log piggyback).

    Keeps `last_agent_activity_at` fresh so the sweep watchdog does not mark
    a healthy long-running job `lost`. Fenced exactly like logs/results: a
    stale token gets fence_violation, a terminal job gets job_terminal.
    """
    current = now if now is not None else datetime.now(timezone.utc)
    job = await _locked_job(db, job_id)
    _assert_fenced(job, claim_token)
    if job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING):
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "job_terminal",
            f"job is {job.status}, not live",
        )
    job.last_agent_activity_at = current
    await db.commit()
    await db.refresh(job)
    return job


async def finish(
    db: AsyncSession,
    *,
    job_id: UUID,
    claim_token: UUID,
    job_status: str,
    exit_code: int | None = None,
    result: str | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> DeviceJob:
    """Fenced terminal result from the agent.

    Allowed statuses are the agent-terminal set only: ``lost`` is
    server-authoritative and rejects with invalid_parameter.
    """
    if job_status not in AGENT_TERMINAL_STATUSES:
        raise DeviceOperationError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_parameter",
            "agents may only report succeeded/failed/timeout/cancelled",
        )
    current = now if now is not None else datetime.now(timezone.utc)
    job = await _locked_job(db, job_id)
    _assert_fenced(job, claim_token)
    if job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING):
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "job_terminal",
            "job is not running",
        )
    job.status = job_status
    job.exit_code = exit_code
    job.result = result
    job.error = error
    job.last_agent_activity_at = current
    await db.commit()
    await db.refresh(job)
    return job


async def sweep_device_jobs(
    db: AsyncSession,
    now: datetime | None = None,
) -> dict:
    """Server watchdog: running loss -> terminal ``lost`` (never re-queued).

    - activity silence: ``last_agent_activity_at`` older than 90s (only the
      owning agent session renews it — see M0);
    - timeout backstop: running past ``timeout_seconds + 60s`` with no
      terminal report (a healthy agent posts ``timeout`` itself first).

    Stale ``claimed`` rows are intentionally NOT modified: ``claim_next``
    reclaims them in place with a fresh token.
    """
    current = now if now is not None else datetime.now(timezone.utc)
    result = await db.execute(
        select(DeviceJob)
        .where(DeviceJob.status == JOB_STATUS_RUNNING)
        .with_for_update(skip_locked=True)
    )
    running_rows = result.scalars().all()

    silence_cutoff = current - timedelta(seconds=RUNNING_LOST_SECONDS)
    lost_silence = 0
    lost_backstop = 0
    for job in running_rows:
        silent = job.last_agent_activity_at is None or (
            job.last_agent_activity_at < silence_cutoff
        )
        past_deadline_anchor = job.started_at or job.claimed_at
        past_deadline = past_deadline_anchor is not None and past_deadline_anchor < (
            current
            - timedelta(
                seconds=job.timeout_seconds + TIMEOUT_BACKSTOP_GRACE_SECONDS
            )
        )
        if silent:
            job.status = JOB_STATUS_LOST
            job.error = "agent activity lost after running (server watchdog)"
            lost_silence += 1
        elif past_deadline:
            job.status = JOB_STATUS_LOST
            job.error = (
                "no terminal report past timeout deadline (server watchdog)"
            )
            lost_backstop += 1

    stale_claimed_result = await db.execute(
        select(DeviceJob)
        .where(
            DeviceJob.status == JOB_STATUS_CLAIMED,
            DeviceJob.claimed_at < current - timedelta(seconds=CLAIM_LEASE_SECONDS),
        )
        .limit(100)
    )
    stale_claimed = len(stale_claimed_result.scalars().all())

    await db.commit()
    return {
        "lost_silence": lost_silence,
        "lost_backstop": lost_backstop,
        "stale_claimed_reclaimable": stale_claimed,
    }
