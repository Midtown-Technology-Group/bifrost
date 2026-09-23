"""Generation-fenced runtime maintenance controls for platform administrators."""

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.config import get_settings
from src.core.admission_pause import admission_tracker
from src.core.auth import CurrentSuperuser
from src.core.db_deps import DbSession
from src.services.runtime_maintenance import (
    RuntimeMaintenanceActive,
    RuntimeMaintenanceError,
    enter_runtime_maintenance,
    exit_runtime_maintenance,
    invalidate_runtime_maintenance_cache,
    read_runtime_maintenance_state,
    runtime_drain_counts,
    seal_runtime_maintenance,
)

router = APIRouter(
    prefix="/api/platform/runtime-maintenance",
    tags=["Platform Admin - Runtime Maintenance"],
)


class MaintenanceEnterRequest(BaseModel):
    generation: UUID
    reason: str = Field(min_length=1, max_length=500)


def _state_payload(state) -> dict:
    return {
        "active": state.active,
        "phase": state.phase,
        "generation": str(state.generation) if state.generation else None,
        "requested_at": (
            state.requested_at.isoformat() if state.requested_at else None
        ),
        "requested_by": state.requested_by,
        "reason": state.reason,
    }


async def _status(db: DbSession) -> dict:
    state = await read_runtime_maintenance_state(db)
    counts = await runtime_drain_counts(db)
    ordinary_inflight = admission_tracker.ordinary_inflight
    counts_payload = asdict(counts)
    counts_payload["accepted_work"] = counts.accepted_work
    ready_to_seal = (
        state.phase == "draining" and ordinary_inflight == 0 and counts.drained
    )
    return {
        **_state_payload(state),
        "static_admissions_paused": get_settings().admissions_paused,
        "ordinary_requests_inflight": ordinary_inflight,
        "ordinary_requests_scope": "current_api_process",
        "single_api_process_asserted": (
            get_settings().runtime_maintenance_single_api_process
        ),
        "claims_sealed": state.sealed,
        "ready_to_seal": ready_to_seal,
        "drained": state.sealed and ordinary_inflight == 0 and counts.drained,
        "work_delivery_backend": get_settings().work_delivery_backend,
        "counts": counts_payload,
    }


@router.get("", summary="Get runtime maintenance and drain state")
async def runtime_maintenance_status(
    _admin: CurrentSuperuser,
    db: DbSession,
) -> dict:
    try:
        return await _status(db)
    except RuntimeMaintenanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/enter", summary="Close admissions and begin a bounded drain")
async def enter_maintenance(
    admin: CurrentSuperuser,
    db: DbSession,
    request: MaintenanceEnterRequest,
) -> dict:
    settings = get_settings()
    if not settings.runtime_maintenance_single_api_process:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Runtime maintenance requires an explicitly verified single-API-"
                "process deployment; cross-replica request drain is not supported"
            ),
        )
    if settings.work_delivery_backend != "postgres":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Runtime maintenance currently requires PostgreSQL delivery; "
                f"configured backend is {settings.work_delivery_backend}"
            ),
        )
    actor = admin.email or str(admin.user_id)
    try:
        async with admission_tracker.lock:
            state = await enter_runtime_maintenance(
                db,
                requested_by=actor,
                reason=request.reason,
                generation=request.generation,
            )
            await db.commit()
            invalidate_runtime_maintenance_cache()
            close_tasks = admission_tracker.start_websocket_closes()
        await admission_tracker.finish_websocket_closes(close_tasks)
        return {
            **_state_payload(state),
            "ordinary_requests_inflight": admission_tracker.ordinary_inflight,
        }
    except RuntimeMaintenanceActive as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except RuntimeMaintenanceError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/{generation}/seal", summary="Seal claims after accepted work drains")
async def seal_maintenance(
    generation: UUID,
    _admin: CurrentSuperuser,
    db: DbSession,
) -> dict:
    try:
        async with admission_tracker.lock:
            if admission_tracker.ordinary_inflight:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ordinary API requests are still in flight",
                )
            state, counts = await seal_runtime_maintenance(db, generation=generation)
            if not counts.drained:
                await db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Accepted work is not drained",
                        "counts": asdict(counts),
                    },
                )
            await db.commit()
            invalidate_runtime_maintenance_cache()
        return {**_state_payload(state), "counts": asdict(counts), "drained": True}
    except RuntimeMaintenanceError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.post("/{generation}/exit", summary="Exit one owned maintenance generation")
async def exit_maintenance(
    generation: UUID,
    _admin: CurrentSuperuser,
    db: DbSession,
) -> dict:
    try:
        async with admission_tracker.lock:
            state = await exit_runtime_maintenance(db, generation=generation)
            await db.commit()
            invalidate_runtime_maintenance_cache()
        return _state_payload(state)
    except RuntimeMaintenanceError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
