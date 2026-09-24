"""Agent-facing device protocol routes (M2.2 #834).

Auth = device `X-Bifrost-Key` (bfdk_ format, bcrypt-verified, device must be
active). CSRF-exempt prefix per the M0 freeze. WebSocket stays a lossy hint;
these HTTP routes are the authority: heartbeat, claim -> logs -> result.

Every authenticated call updates `devices.last_seen_at`.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy import select
from starlette.responses import JSONResponse

from src.core.auth import DbSession
from src.models.contracts.device_jobs import (
    DeviceClaimRequest,
    DeviceClaimResponse,
    DeviceHeartbeatRequest,
    DeviceHeartbeatResponse,
    DeviceLogBatch,
    DeviceResultRequest,
    DeviceResultResponse,
    DeviceRunningRequest,
    DeviceRunningResponse,
)
from src.models.orm.devices import DEVICE_STATUS_ACTIVE, Device
from src.models.orm.device_jobs import (
    CLAIM_LEASE_SECONDS,
    JOB_STATUS_PENDING,
    DeviceJob,
)
from src.services.device_jobs import (
    append_logs,
    broadcast_job_logs,
    claim_next,
    finish,
    mark_running as mark_running_svc,
    renew_from_heartbeat,
)
from src.services.device_keys import (
    DEVICE_KEY_PREFIX,
    parse_device_key,
    verify_key_hash,
)
from src.services.devices import DeviceOperationError

router = APIRouter(prefix="/api/device", tags=["Device Protocol"])

POLL_INTERVAL_DEFAULT_SECONDS = 10
POLL_INTERVAL_PENDING_SECONDS = 5


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "retryable": False}},
    )


def _domain_error(exc: DeviceOperationError) -> JSONResponse:
    payload: dict[str, object] = {
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
    }
    payload.update(exc.extra)
    return JSONResponse(status_code=exc.status_code, content={"error": payload})


async def device_agent(
    db: DbSession,
    x_bifrost_key: str = Header(..., alias="X-Bifrost-Key"),
) -> Device | JSONResponse:
    """Resolve and verify the device principal from X-Bifrost-Key.

    Returns the active Device, or an error envelope (FastAPI short-circuits
    on a returned Response): malformed/unknown key → 401 `invalid_key`,
    disabled device → 403 `device_disabled`.
    """
    parsed = parse_device_key(x_bifrost_key)
    if parsed is None or parsed.prefix != DEVICE_KEY_PREFIX:
        return _envelope(
            status.HTTP_401_UNAUTHORIZED, "invalid_key", "invalid device key"
        )
    device = await db.get(Device, parsed.key_id)
    if device is None:
        return _envelope(
            status.HTTP_401_UNAUTHORIZED, "invalid_key", "invalid device key"
        )
    if not verify_key_hash(x_bifrost_key, device.api_key_hash):
        return _envelope(
            status.HTTP_401_UNAUTHORIZED, "invalid_key", "invalid device key"
        )
    # Status before the enabled flag: a disabled device must report
    # device_disabled (M0), not a generic key error.
    if device.status != DEVICE_STATUS_ACTIVE:
        return _envelope(
            status.HTTP_403_FORBIDDEN, "device_disabled", "device is disabled"
        )
    if not device.api_key_enabled:
        return _envelope(
            status.HTTP_401_UNAUTHORIZED, "invalid_key", "invalid device key"
        )
    return device


async def _touch(db, device: Device) -> datetime:
    now = datetime.now(timezone.utc)
    device.last_seen_at = now
    await db.commit()
    return now


@router.post(
    "/heartbeat",
    response_model=DeviceHeartbeatResponse,
    summary="Device heartbeat: renew activity and receive the poll hint",
)
async def heartbeat_route(
    body: DeviceHeartbeatRequest,
    db: DbSession,
    agent: Device | JSONResponse = Depends(device_agent),
) -> DeviceHeartbeatResponse | JSONResponse:
    if isinstance(agent, JSONResponse):
        return agent
    now = await _touch(db, agent)
    owned = await renew_from_heartbeat(
        db, device_id=agent.id, agent_session_id=body.agent_session_id, now=now
    )
    pending = (
        await db.execute(
            select(DeviceJob.id)
            .where(
                DeviceJob.device_id == agent.id,
                DeviceJob.status == JOB_STATUS_PENDING,
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None
    return DeviceHeartbeatResponse(
        server_time=now,
        last_seen_at=agent.last_seen_at,
        poll_interval_seconds=(
            POLL_INTERVAL_PENDING_SECONDS
            if pending
            else POLL_INTERVAL_DEFAULT_SECONDS
        ),
        cancel_requested=bool(owned is not None and owned.cancel_requested_at),
    )


@router.post(
    "/jobs/claim",
    response_model=DeviceClaimResponse,
    summary="Claim the next job for this device (204 when idle)",
)
async def claim_route(
    body: DeviceClaimRequest,
    db: DbSession,
    agent: Device | JSONResponse = Depends(device_agent),
) -> DeviceClaimResponse | JSONResponse | Response:
    if isinstance(agent, JSONResponse):
        return agent
    await _touch(db, agent)
    job = await claim_next(
        db, device_id=agent.id, agent_session_id=body.agent_session_id
    )
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return DeviceClaimResponse(
        job_id=job.id,
        script_name=job.script_name,
        script_content=job.script_content,
        params=job.params,
        timeout_seconds=job.timeout_seconds,
        max_output_bytes=job.max_output_bytes,
        claim_token=job.claim_token,
        claimed_at=job.claimed_at,
        claim_lease_seconds=CLAIM_LEASE_SECONDS,
    )


@router.post(
    "/jobs/{job_id}/running",
    response_model=DeviceRunningResponse,
    summary="Report a real spawn: claimed -> running (fenced)",
)
async def running_route(
    job_id: UUID,
    body: DeviceRunningRequest,
    db: DbSession,
    agent: Device | JSONResponse = Depends(device_agent),
) -> DeviceRunningResponse | JSONResponse:
    """Additive route required by feedback #1: the agent may only call this
    after cmd.Start() succeeded, which is what makes pre-spawn reclaim safe
    and post-spawn ambiguity `lost` (M0 job lifecycle)."""
    if isinstance(agent, JSONResponse):
        return agent
    await _touch(db, agent)
    # Ownership pre-check before fence work.
    owned = await db.get(DeviceJob, job_id)
    if owned is None or owned.device_id != agent.id:
        return _envelope(status.HTTP_404_NOT_FOUND, "unknown_job", "job not found")
    try:
        job = await mark_running_svc(
            db,
            job_id=job_id,
            claim_token=body.claim_token,
            agent_session_id=body.agent_session_id,
        )
    except DeviceOperationError as exc:
        return _domain_error(exc)
    return DeviceRunningResponse(job_id=job.id, status=job.status)


@router.post(
    "/jobs/{job_id}/logs",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Fenced, idempotent log batch",
)
async def logs_route(
    job_id: UUID,
    body: DeviceLogBatch,
    db: DbSession,
    agent: Device | JSONResponse = Depends(device_agent),
) -> Response:
    if isinstance(agent, JSONResponse):
        return agent
    await _touch(db, agent)
    # Ownership pre-check: the job must belong to THIS device before any
    # fence work (defense in depth beyond the claim_token secret).
    owned = await db.get(DeviceJob, job_id)
    if owned is None or owned.device_id != agent.id:
        return _envelope(status.HTTP_404_NOT_FOUND, "unknown_job", "job not found")
    entries = [entry.model_dump() for entry in body.entries]
    try:
        job, inserted = await append_logs(
            db,
            job_id=job_id,
            claim_token=body.claim_token,
            entries=entries,
        )
    except DeviceOperationError as exc:
        return _domain_error(exc)
    if inserted:
        await broadcast_job_logs(
            job_id,
            [
                {
                    "seq": row.seq,
                    "stream": row.stream,
                    "text": row.text,
                    "ts": row.ts.isoformat() if row.ts else None,
                }
                for row in inserted
            ],
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/jobs/{job_id}/result",
    response_model=DeviceResultResponse,
    summary="Fenced terminal result",
)
async def result_route(
    job_id: UUID,
    body: DeviceResultRequest,
    db: DbSession,
    agent: Device | JSONResponse = Depends(device_agent),
) -> DeviceResultResponse | JSONResponse:
    if isinstance(agent, JSONResponse):
        return agent
    await _touch(db, agent)
    # Ownership pre-check: the job must belong to THIS device before any
    # fence work (defense in depth beyond the claim_token secret).
    owned = await db.get(DeviceJob, job_id)
    if owned is None or owned.device_id != agent.id:
        return _envelope(status.HTTP_404_NOT_FOUND, "unknown_job", "job not found")
    try:
        job = await finish(
            db,
            job_id=job_id,
            claim_token=body.claim_token,
            job_status=body.status,
            exit_code=body.exit_code,
            result=body.output,
            error=body.error,
        )
    except DeviceOperationError as exc:
        return _domain_error(exc)
    return DeviceResultResponse(job_id=job.id, status=job.status)
