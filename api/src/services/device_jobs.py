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

from src.core.org_filter import org_filter_clause, resolve_org_filter
from src.core.principal import UserPrincipal
from src.core.pubsub import manager as pubsub_manager
from src.models.orm.device_job_logs import DeviceJobLog
from src.models.orm.devices import DEVICE_STATUS_ACTIVE, Device
from src.models.orm.device_jobs import (
    ACTIVE_JOB_STATUSES,
    AGENT_TERMINAL_STATUSES,
    CLAIM_LEASE_SECONDS,
    JOB_STATUS_CANCELLED,
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


# Log batch bounds (M0 log-batch contract).
LOG_ENTRY_MAX_CHARS = 65536
LOG_BATCH_MAX_ENTRIES = 512
LOG_BATCH_MAX_CHARS = 1024 * 1024


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
        select(DeviceJob)
        .where(DeviceJob.id == job_id)
        .with_for_update()
        # Callers may have preloaded this identity (ownership pre-check);
        # refresh from the locked row so fence/status checks never read
        # stale claim_token/status/log_sequence from the identity map.
        .execution_options(populate_existing=True)
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


async def renew_from_heartbeat(
    db: AsyncSession,
    *,
    device_id: UUID,
    agent_session_id: UUID,
    agent_version: str | None = None,
    now: datetime | None = None,
) -> DeviceJob | None:
    """M0 heartbeat contract: renew activity only for the job this agent
    session owns. Returns the active job (so the route can report
    `cancel_requested`) or None when there is nothing to renew.

    Additive observability (epic #818): a non-empty ``agent_version``
    overwrites ``devices.agent_version``; absent/empty never touches it.
    """
    current = now if now is not None else datetime.now(timezone.utc)
    if agent_version:
        # Persist before the job query so the value survives the
        # no-active-job early return below.
        device = await db.get(Device, device_id)
        if device is not None and device.agent_version != agent_version:
            device.agent_version = agent_version
            await db.commit()
    result = await db.execute(
        select(DeviceJob)
        .where(
            DeviceJob.device_id == device_id,
            DeviceJob.status.in_((JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)),
        )
        .with_for_update()
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return None
    if job.agent_session_id != agent_session_id:
        # A different (e.g. restarted) session must not keep the old
        # claim's lease alive — the watchdog should be able to see loss.
        return None
    job.last_agent_activity_at = current
    await db.commit()
    await db.refresh(job)
    return job


async def append_logs(
    db: AsyncSession,
    *,
    job_id: UUID,
    claim_token: UUID,
    entries: list[dict],
    now: datetime | None = None,
) -> tuple[DeviceJob, list[DeviceJobLog]]:
    """Fenced, idempotent log append (M0 log-batch contract).

    The job row is locked for the duration, so per-job writers serialize:
    first write for a (job, seq) wins, an identical replay is a no-op, and
    different content for an accepted seq is `log_seq_conflict` (409).
    Returns (job, newly_inserted_entries).
    """
    current = now if now is not None else datetime.now(timezone.utc)

    if len(entries) > LOG_BATCH_MAX_ENTRIES:
        raise DeviceOperationError(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "payload_too_large",
            f"log batch exceeds {LOG_BATCH_MAX_ENTRIES} entries",
        )
    total_chars = 0
    seen: set[int] = set()
    for entry in entries:
        seq = entry.get("seq")
        stream = entry.get("stream")
        text = entry.get("text", "")
        if not isinstance(seq, int) or seq < 1:
            raise DeviceOperationError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_parameter",
                "entry seq must be an integer >= 1",
            )
        if seq in seen:
            raise DeviceOperationError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_parameter",
                f"duplicate seq {seq} within one batch",
            )
        seen.add(seq)
        if stream not in ("stdout", "stderr"):
            raise DeviceOperationError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_parameter",
                "entry stream must be stdout or stderr",
            )
        if len(text) > LOG_ENTRY_MAX_CHARS:
            raise DeviceOperationError(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "payload_too_large",
                f"log entry exceeds {LOG_ENTRY_MAX_CHARS} characters",
            )
        total_chars += len(text)
    if total_chars > LOG_BATCH_MAX_CHARS:
        raise DeviceOperationError(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "payload_too_large",
            f"log batch exceeds {LOG_BATCH_MAX_CHARS} characters",
        )

    job = await _locked_job(db, job_id)
    _assert_fenced(job, claim_token)
    if job.status not in (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING):
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "job_terminal",
            f"job is {job.status}, not live",
        )

    incoming_seqs = sorted(seen)
    existing_result = await db.execute(
        select(DeviceJobLog).where(
            DeviceJobLog.job_id == job_id,
            DeviceJobLog.seq.in_(incoming_seqs),
        )
    )
    existing = {row.seq: row for row in existing_result.scalars().all()}

    inserted: list[DeviceJobLog] = []
    highest = job.log_sequence or 0
    for entry in sorted(entries, key=lambda e: e["seq"]):
        seq = entry["seq"]
        prior = existing.get(seq)
        if prior is not None:
            # Idempotent replay must be byte-identical; a changed body for an
            # accepted seq is a fencing/idempotency conflict.
            if prior.stream != entry["stream"] or prior.text != entry["text"]:
                raise DeviceOperationError(
                    status.HTTP_409_CONFLICT,
                    "log_seq_conflict",
                    f"seq {seq} was already accepted with different content",
                )
            continue
        row = DeviceJobLog(
            job_id=job_id,
            seq=seq,
            stream=entry["stream"],
            text=entry["text"],
            ts=entry.get("ts"),
        )
        db.add(row)
        inserted.append(row)
        highest = max(highest, seq)

    job.last_agent_activity_at = current
    if highest > (job.log_sequence or 0):
        job.log_sequence = highest
    await db.commit()
    for row in inserted:
        await db.refresh(row)
    return job, inserted


async def broadcast_job_available(device_id: UUID, job_id: UUID) -> None:
    """Lossy WS hint on device:{device_id} after a create commits (M0)."""
    await pubsub_manager.broadcast(
        f"device:{device_id}",
        {
            "type": "device_job_available",
            "job_id": str(job_id),
            "device_id": str(device_id),
        },
    )


async def broadcast_job_logs(job_id: UUID, entries: list[dict]) -> None:
    """Log fanout on device_job:{job_id} for user observation channels."""
    await pubsub_manager.broadcast(
        f"device_job:{job_id}",
        {
            "type": "device_job_logs",
            "job_id": str(job_id),
            "entries": entries,
        },
    )


# ---------------------------------------------------------------------------
# User/workspace observation + cooperative cancel (M2.4 #836)
# ---------------------------------------------------------------------------


async def get_job_scoped(
    db: AsyncSession,
    user: UserPrincipal,
    job_id: UUID,
    *,
    with_lock: bool = False,
) -> DeviceJob:
    """Load one job under the caller's resolved org scope (no inline org
    comparisons in routers — the clause is built here from the helper).

    ``with_lock=True`` takes ``SELECT ... FOR UPDATE`` (refreshing any
    preloaded identity) so a status decision made here — e.g. cancel vs an
    agent's concurrent ``mark_running`` — serializes against the agent
    path instead of racing it.
    """
    filter_type, filter_org_id = resolve_org_filter(user)
    stmt = select(DeviceJob).where(DeviceJob.id == job_id)
    clause = org_filter_clause(DeviceJob.organization_id, filter_type, filter_org_id)
    if clause is not None:
        stmt = stmt.where(clause)
    if with_lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise DeviceOperationError(
            status.HTTP_404_NOT_FOUND, "unknown_job", "job not found"
        )
    return job


def list_jobs_stmt(
    user: UserPrincipal,
    device_id: UUID | None = None,
    scope: str | None = None,
):
    """Org-scoped (optionally device-filtered) job history statement."""
    filter_type, filter_org_id = resolve_org_filter(user, scope)
    stmt = select(DeviceJob)
    clause = org_filter_clause(DeviceJob.organization_id, filter_type, filter_org_id)
    if clause is not None:
        stmt = stmt.where(clause)
    if device_id is not None:
        stmt = stmt.where(DeviceJob.device_id == device_id)
    return stmt.order_by(DeviceJob.created_at.desc())


async def list_job_logs(
    db: AsyncSession,
    job_id: UUID,
    *,
    after_seq: int = 0,
    limit: int = 5000,
) -> list[DeviceJobLog]:
    result = await db.execute(
        select(DeviceJobLog)
        .where(DeviceJobLog.job_id == job_id, DeviceJobLog.seq > after_seq)
        .order_by(DeviceJobLog.seq)
        .limit(limit)
    )
    return list(result.scalars().all())


async def request_cancel(
    db: AsyncSession,
    user: UserPrincipal,
    job_id: UUID,
    now: datetime | None = None,
) -> DeviceJob:
    """Cooperative cancel (M0 ownership): pending/claimed cancel immediately;
    running only gets the cancel flag (agent observes via heartbeat); the
    platform never guarantees a process kill. Idempotent for running jobs.
    """
    current = now if now is not None else datetime.now(timezone.utc)
    # Row lock: cancel must serialize with the agent's mark_running/finish.
    # Without it, a cancel could commit `cancelled` over a just-started
    # `running` job, freeing the one-active index while the script still
    # runs (double execution) and swallowing the agent's terminal report.
    job = await get_job_scoped(db, user, job_id, with_lock=True)

    if job.status in TERMINAL_JOB_STATUSES:
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "job_terminal",
            "job is already terminal",
        )
    if job.status == JOB_STATUS_RUNNING:
        if job.cancel_requested_at is None:
            job.cancel_requested_at = current
            await db.commit()
            await db.refresh(job)
        return job

    # pending / claimed: no side effect started — terminal immediately.
    job.status = JOB_STATUS_CANCELLED
    job.cancel_requested_at = job.cancel_requested_at or current
    job.error = job.error or "cancelled before running"
    await db.commit()
    await db.refresh(job)
    return job


async def validate_workflow_attribution(
    db: AsyncSession,
    organization_id: UUID,
    workflow_id: UUID | None,
    execution_id: UUID | None,
) -> None:
    """Caller-asserted attribution must resolve inside the target org (M0)."""
    from src.core.org_filter import OrgFilterType
    from src.models.orm.executions import Execution
    from src.models.orm.workflows import Workflow

    if workflow_id is not None:
        stmt = select(Workflow.id).where(Workflow.id == workflow_id)
        stmt = stmt.where(
            org_filter_clause(
                Workflow.organization_id, OrgFilterType.ORG_ONLY, organization_id
            )
        )
        if (await db.execute(stmt)).scalar_one_or_none() is None:
            raise DeviceOperationError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_parameter",
                "workflow_id does not exist in the target organization",
            )
    if execution_id is not None:
        stmt = select(Execution.id).where(Execution.id == execution_id)
        stmt = stmt.where(
            org_filter_clause(
                Execution.organization_id, OrgFilterType.ORG_ONLY, organization_id
            )
        )
        if (await db.execute(stmt)).scalar_one_or_none() is None:
            raise DeviceOperationError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_parameter",
                "execution_id does not exist in the target organization",
            )


# ---------------------------------------------------------------------------
# Control-key read path (M0 matrix: a key reads only jobs it created)
# ---------------------------------------------------------------------------


def list_jobs_for_control_key_stmt(key, device_id: UUID | None = None):
    stmt = select(DeviceJob).where(
        DeviceJob.organization_id == key.organization_id,
        DeviceJob.requested_by_api_key_id == key.id,
    )
    if device_id is not None:
        stmt = stmt.where(DeviceJob.device_id == device_id)
    return stmt.order_by(DeviceJob.created_at.desc())


async def get_job_for_control_key(db: AsyncSession, key, job_id: UUID) -> DeviceJob:
    stmt = select(DeviceJob).where(
        DeviceJob.id == job_id,
        DeviceJob.organization_id == key.organization_id,
        DeviceJob.requested_by_api_key_id == key.id,
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise DeviceOperationError(
            status.HTTP_404_NOT_FOUND, "unknown_job", "job not found"
        )
    return job
