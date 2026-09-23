"""Device registry REST: CRUD, enrollment, key rotation (M1 #831).

Authorization follows the M0 matrix (docs/architecture/device-control-plane.md):
registry operations require org membership plus ``can_manage_config`` (or
platform superuser); ``POST /api/devices/enroll`` authenticates with the
single-use enrollment token only (CSRF-exempt). Org scoping goes through
``resolve_org_filter`` + ``org_filter_clause`` — never inline comparisons.
"""

from uuid import UUID

from fastapi import APIRouter, Query, status
from starlette.responses import JSONResponse

from src.core.auth import Context, CurrentUser
from src.models.contracts.devices import (
    DeviceCreate,
    DeviceCreateResponse,
    DeviceEnrollRequest,
    DeviceEnrollResponse,
    DeviceKeyResponse,
    DevicePublic,
)
from src.services.devices import (
    DeviceOperationError,
    create_device,
    enroll_device,
    get_device_scoped,
    list_devices_stmt,
    require_device_manage_permission,
    rotate_device_key_route,
    set_device_enabled,
)

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
    ctx: Context,
) -> DeviceEnrollResponse | JSONResponse:
    try:
        device, raw_key = await enroll_device(ctx.db, body.enrollment_token)
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
    response_model=DevicePublic,
    summary="Get one device",
)
async def get_device_route(
    device_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> DevicePublic | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
        device = await get_device_scoped(ctx.db, user, device_id)
    except DeviceOperationError as exc:
        return exc.to_response()
    return DevicePublic.model_validate(device)


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
