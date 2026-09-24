"""Device registry REST: CRUD, enrollment, key rotation (M1 #831).

Authorization follows the M0 matrix (docs/architecture/device-control-plane.md):
registry operations require org membership plus ``can_manage_config`` (or
platform superuser); ``POST /api/devices/enroll`` authenticates with the
single-use enrollment token only (CSRF-exempt). Org scoping goes through
``resolve_org_filter`` + ``org_filter_clause`` — never inline comparisons.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from starlette.responses import JSONResponse

from src.core.auth import Context, CurrentUser, get_current_user_optional
from src.core.db_deps import DbSession
from src.core.principal import UserPrincipal
from src.models.contracts.device_jobs import (
    DeviceJobCreate,
    DeviceJobDetail,
    DeviceJobLogPublic,
    DeviceJobPublic,
)
from src.models.contracts.devices import (
    DeviceCreate,
    DeviceCreateResponse,
    DeviceEnrollRequest,
    DeviceEnrollResponse,
    DeviceFreshness,
    DeviceKeyResponse,
    DevicePublic,
)
from src.models.orm.devices import DEVICE_STATUS_ACTIVE
from src.services.device_control_keys import resolve_control_key
from src.services.device_jobs import (
    broadcast_job_available,
    create_device_job,
    get_job_for_control_key,
    get_job_scoped,
    list_job_logs,
    list_jobs_for_control_key_stmt,
    list_jobs_stmt,
    request_cancel,
    validate_workflow_attribution,
)
from src.services.device_keys import key_can_target, touch_control_key_usage
from src.services.devices import (
    DeviceOperationError,
    create_device,
    enroll_device,
    get_device_for_org,
    get_device_scoped,
    list_devices_stmt,
    require_device_execute_permission,
    require_device_manage_permission,
    rotate_device_key_route,
    set_device_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["Devices"])


def _unknown_device() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "error": {
                "code": "unknown_device",
                "message": "device not found",
                "retryable": False,
            }
        },
    )


@router.post(
    "",
    response_model=DeviceCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a device and mint a one-time enrollment token",
)
async def create_device_route(
    body: DeviceCreate,
    ctx: Context,
    user: CurrentUser,
) -> DeviceCreateResponse | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
        device, raw_token, expires_at = await create_device(ctx.db, user, body)
    except DeviceOperationError as exc:
        return exc.to_response()
    return DeviceCreateResponse(
        device=DevicePublic.model_validate(device),
        enrollment_token=raw_token,
        enrollment_expires_at=expires_at,
    )


@router.post(
    "/enroll",
    response_model=DeviceEnrollResponse,
    summary="Enroll with a single-use token and receive the device key once",
)
async def enroll_device_route(
    body: DeviceEnrollRequest,
    db: DbSession,
) -> DeviceEnrollResponse | JSONResponse:
    # Deliberately NO Context/CurrentUser dependency: enrollment authenticates
    # with the one-time token only (M0: no user session; CSRF-exempt path).
    # `Context` embeds ExecutionContext.user and would 401 anonymous agents.
    try:
        device, raw_key = await enroll_device(db, body.enrollment_token)
    except DeviceOperationError as exc:
        return exc.to_response()
    return DeviceEnrollResponse(
        device_id=device.id,
        device_key=raw_key,
        status=device.status,
        enrollment_token_expires_at=device.enrollment_expires_at,
    )


@router.get(
    "",
    response_model=list[DevicePublic],
    summary="List devices in the caller's organization",
)
async def list_devices_route(
    ctx: Context,
    user: CurrentUser,
    scope: str | None = Query(default=None),
) -> list[DevicePublic] | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
    except DeviceOperationError as exc:
        return exc.to_response()
    stmt = list_devices_stmt(user, scope)
    rows = (await ctx.db.execute(stmt)).scalars().all()
    return [DevicePublic.model_validate(row) for row in rows]


@router.get(
    "/{device_id}",
    response_model=DevicePublic | DeviceFreshness,
    summary="Get one device (user: full view; control key: freshness only)",
)
async def get_device_route(
    device_id: UUID,
    db: DbSession,
    user: UserPrincipal | None = Depends(get_current_user_optional),
    x_bifrost_control_key: str | None = Header(
        default=None, alias="X-Bifrost-Control-Key"
    ),
) -> DevicePublic | DeviceFreshness | JSONResponse:
    try:
        if x_bifrost_control_key:
            # Approved M0 delta (#818 comment 5815371680): a control key may
            # read ONLY id/status/last_seen_at, and only for allow-listed
            # devices — the pre-accept freshness input for transport
            # fail-closed. Full registry detail stays user-only.
            key = await resolve_control_key(db, x_bifrost_control_key)
            device = await get_device_for_org(db, device_id, key.organization_id)
            if not key_can_target(key, device):
                raise DeviceOperationError(
                    status.HTTP_403_FORBIDDEN,
                    "out_of_scope",
                    "control key does not target this device",
                )
            return DeviceFreshness(
                id=device.id,
                status=device.status,
                last_seen_at=device.last_seen_at,
            )
        if user is None:
            raise DeviceOperationError(
                status.HTTP_401_UNAUTHORIZED,
                "unauthenticated",
                "user permission or device-scoped control key required",
            )
        await require_device_manage_permission(db, user)
        device = await get_device_scoped(db, user, device_id)
        return DevicePublic.model_validate(device)
    except DeviceOperationError as exc:
        return exc.to_response()


@router.post(
    "/{device_id}/rotate-key",
    response_model=DeviceKeyResponse,
    summary="Rotate the device key (raw returned once)",
)
async def rotate_device_key_endpoint(
    device_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> DeviceKeyResponse | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
        device, raw_key = await rotate_device_key_route(ctx.db, user, device_id)
    except DeviceOperationError as exc:
        return exc.to_response()
    return DeviceKeyResponse(device_id=device.id, device_key=raw_key)


@router.post(
    "/{device_id}/disable",
    response_model=DevicePublic,
    summary="Disable a device",
)
async def disable_device_route(
    device_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> DevicePublic | JSONResponse:
    return await _set_enabled(device_id, False, ctx, user)


@router.post(
    "/{device_id}/enable",
    response_model=DevicePublic,
    summary="Re-enable a disabled device",
)
async def enable_device_route(
    device_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> DevicePublic | JSONResponse:
    return await _set_enabled(device_id, True, ctx, user)


async def _set_enabled(
    device_id: UUID,
    enabled: bool,
    ctx: Context,
    user: CurrentUser,
) -> DevicePublic | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
        device = await set_device_enabled(ctx.db, user, device_id, enabled)
    except DeviceOperationError as exc:
        return exc.to_response()
    return DevicePublic.model_validate(device)


# ---------------------------------------------------------------------------
# Device jobs: create, observation, cooperative cancel (M2.4 #836)
#
# Dual auth (M0 matrix): user JWT + can_execute_devices, OR an
# X-Bifrost-Control-Key whose allow-list targets the device. DbSession (not
# Context) keeps cookie-less control-key callers out of user auth.
# ---------------------------------------------------------------------------


async def _create_job(
    db,
    user: UserPrincipal | None,
    control_key_header: str | None,
    device_id: UUID,
    body: DeviceJobCreate,
):
    """Shared create path for both principals; returns (job, key_or_None)."""
    if control_key_header:
        key = await resolve_control_key(db, control_key_header)
        device = await get_device_for_org(db, device_id, key.organization_id)
        if not key_can_target(key, device):
            raise DeviceOperationError(
                status.HTTP_403_FORBIDDEN,
                "out_of_scope",
                "control key does not target this device",
            )
        attribution = {"requested_by_api_key_id": key.id}
    elif user is not None:
        await require_device_execute_permission(db, user)
        device = await get_device_scoped(db, user, device_id)
        attribution = {"requested_by_user_id": user.user_id}
    else:
        raise DeviceOperationError(
            status.HTTP_401_UNAUTHORIZED,
            "unauthenticated",
            "user permission or device-scoped control key required",
        )

    if device.status != DEVICE_STATUS_ACTIVE:
        raise DeviceOperationError(
            status.HTTP_403_FORBIDDEN, "device_disabled", "device is disabled"
        )

    await validate_workflow_attribution(
        db, device.organization_id, body.workflow_id, body.execution_id
    )
    job = await create_device_job(
        db,
        organization_id=device.organization_id,
        device_id=device.id,
        script_name=body.script_name,
        script_content=body.script_content,
        params=body.params,
        timeout_seconds=body.timeout_seconds,
        max_output_bytes=body.max_output_bytes,
        requested_by_workflow_id=body.workflow_id,
        requested_by_execution_id=body.execution_id,
        **attribution,
    )
    if control_key_header:
        touch_control_key_usage(key)
        await db.commit()
    # WS hint is lossy by contract (M0): never fail a create on broadcast.
    try:
        await broadcast_job_available(job.device_id, job.id)
    except Exception as exc:  # noqa: BLE001 — hint-only path
        logger.warning("device_job_available broadcast failed: %s", exc)
    return job


@router.post(
    "/{device_id}/jobs",
    response_model=DeviceJobPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a device job (user permission or device-scoped control key)",
)
async def create_job_route(
    device_id: UUID,
    body: DeviceJobCreate,
    db: DbSession,
    user: UserPrincipal | None = Depends(get_current_user_optional),
    x_bifrost_control_key: str | None = Header(
        default=None, alias="X-Bifrost-Control-Key"
    ),
) -> DeviceJobPublic | JSONResponse:
    try:
        job = await _create_job(db, user, x_bifrost_control_key, device_id, body)
    except DeviceOperationError as exc:
        return exc.to_response()
    return DeviceJobPublic.model_validate(job)


async def _authorize_job_read(
    db,
    user: UserPrincipal | None,
    control_key_header: str | None,
    job_id: UUID | None = None,
    device_id: UUID | None = None,
):
    """Resolve the caller and scope for read/observation routes."""
    if control_key_header:
        key = await resolve_control_key(db, control_key_header)
        if job_id is not None:
            job = await get_job_for_control_key(db, key, job_id)
            if device_id is not None and job.device_id != device_id:
                raise DeviceOperationError(
                    status.HTTP_404_NOT_FOUND, "unknown_job", "job not found"
                )
            return key, job
        if device_id is not None:
            await get_device_for_org(db, device_id, key.organization_id)
        return key, None
    if user is not None:
        await require_device_execute_permission(db, user)
        if device_id is not None:
            await get_device_scoped(db, user, device_id)  # 404 when out of scope
        if job_id is not None:
            job = await get_job_scoped(db, user, job_id)
            if device_id is not None and job.device_id != device_id:
                raise DeviceOperationError(
                    status.HTTP_404_NOT_FOUND, "unknown_job", "job not found"
                )
            return None, job
        return None, None
    raise DeviceOperationError(
        status.HTTP_401_UNAUTHORIZED,
        "unauthenticated",
        "user permission or device-scoped control key required",
    )


@router.get(
    "/{device_id}/jobs",
    response_model=list[DeviceJobPublic],
    summary="List job history for a device (create-level read authz)",
)
async def list_jobs_route(
    device_id: UUID,
    db: DbSession,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    user: UserPrincipal | None = Depends(get_current_user_optional),
    x_bifrost_control_key: str | None = Header(
        default=None, alias="X-Bifrost-Control-Key"
    ),
) -> list[DeviceJobPublic] | JSONResponse:
    try:
        key, _ = await _authorize_job_read(
            db, user, x_bifrost_control_key, device_id=device_id
        )
        if key is not None:
            # Control keys read only jobs they created (M0 matrix).
            stmt = list_jobs_for_control_key_stmt(key, device_id=device_id)
        else:
            stmt = list_jobs_stmt(user, device_id=device_id)
        rows = (
            await db.execute(stmt.offset(offset).limit(limit))
        ).scalars().all()
        return [DeviceJobPublic.model_validate(row) for row in rows]
    except DeviceOperationError as exc:
        return exc.to_response()


@router.get(
    "/{device_id}/jobs/{job_id}",
    response_model=DeviceJobDetail,
    summary="Get one job (script body readable at create-level authz)",
)
async def get_job_route(
    device_id: UUID,
    job_id: UUID,
    db: DbSession,
    user: UserPrincipal | None = Depends(get_current_user_optional),
    x_bifrost_control_key: str | None = Header(
        default=None, alias="X-Bifrost-Control-Key"
    ),
) -> DeviceJobDetail | JSONResponse:
    try:
        _key, job = await _authorize_job_read(
            db, user, x_bifrost_control_key, job_id=job_id, device_id=device_id
        )
        return DeviceJobDetail.model_validate(job)
    except DeviceOperationError as exc:
        return exc.to_response()


@router.get(
    "/{device_id}/jobs/{job_id}/logs",
    response_model=list[DeviceJobLogPublic],
    summary="Read job logs (create-level authz; never device-readable)",
)
async def get_job_logs_route(
    device_id: UUID,
    job_id: UUID,
    db: DbSession,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=5000),
    user: UserPrincipal | None = Depends(get_current_user_optional),
    x_bifrost_control_key: str | None = Header(
        default=None, alias="X-Bifrost-Control-Key"
    ),
) -> list[DeviceJobLogPublic] | JSONResponse:
    try:
        _key, job = await _authorize_job_read(
            db, user, x_bifrost_control_key, job_id=job_id, device_id=device_id
        )
        rows = await list_job_logs(db, job.id, after_seq=after_seq, limit=limit)
        return [DeviceJobLogPublic.model_validate(row) for row in rows]
    except DeviceOperationError as exc:
        return exc.to_response()


@router.post(
    "/{device_id}/jobs/{job_id}/cancel",
    response_model=DeviceJobPublic,
    summary="Cooperative cancel (user JWT only; no kill guarantee)",
)
async def cancel_job_route(
    device_id: UUID,
    job_id: UUID,
    db: DbSession,
    user: UserPrincipal | None = Depends(get_current_user_optional),
    x_bifrost_control_key: str | None = Header(
        default=None, alias="X-Bifrost-Control-Key"
    ),
) -> DeviceJobPublic | JSONResponse:
    try:
        if x_bifrost_control_key:
            # M0 matrix: control keys submit jobs; they never cancel them.
            raise DeviceOperationError(
                status.HTTP_403_FORBIDDEN,
                "permission_denied",
                "control keys cannot cancel jobs",
            )
        if user is None:
            raise DeviceOperationError(
                status.HTTP_401_UNAUTHORIZED,
                "unauthenticated",
                "user permission or device-scoped control key required",
            )
        await require_device_execute_permission(db, user)
        device = await get_device_scoped(db, user, device_id)
        job = await get_job_scoped(db, user, job_id)
        if job.device_id != device.id:
            raise DeviceOperationError(
                status.HTTP_404_NOT_FOUND, "unknown_job", "job not found"
            )
        job = await request_cancel(db, user, job_id)
        return DeviceJobPublic.model_validate(job)
    except DeviceOperationError as exc:
        return exc.to_response()
