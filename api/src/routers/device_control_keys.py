"""Device-scoped control-key REST (M1.4 #832).

Authorization follows the M0 matrix: registry operations require org
membership plus ``can_manage_config`` (or platform superuser). Org scoping
goes through ``resolve_org_filter`` + ``org_filter_clause`` — never inline
comparisons. The raw key is returned exactly once from create/rotate.
"""

from uuid import UUID

from fastapi import APIRouter, Query, status
from starlette.responses import JSONResponse

from src.core.auth import Context, CurrentUser
from src.models.contracts.device_control_keys import (
    ControlKeyCreate,
    ControlKeyCreatedResponse,
    ControlKeyKeyResponse,
    ControlKeyPublic,
)
from src.services.device_control_keys import (
    create_control_key,
    get_control_key_scoped,
    list_control_keys_stmt,
    revoke_control_key_route,
    rotate_control_key_route,
)
from src.services.devices import (
    DeviceOperationError,
    require_device_manage_permission,
)

router = APIRouter(prefix="/api/device-control-keys", tags=["Device Control Keys"])


@router.post(
    "",
    response_model=ControlKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a device-scoped control key (raw returned once)",
)
async def create_control_key_route(
    body: ControlKeyCreate,
    ctx: Context,
    user: CurrentUser,
) -> ControlKeyCreatedResponse | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
        row, raw = await create_control_key(ctx.db, user, body)
    except DeviceOperationError as exc:
        return exc.to_response()
    return ControlKeyCreatedResponse(
        control_key=ControlKeyPublic.model_validate(row),
        key=raw,
    )


@router.get(
    "",
    response_model=list[ControlKeyPublic],
    summary="List control keys in the caller's organization",
)
async def list_control_keys_route(
    ctx: Context,
    user: CurrentUser,
    scope: str | None = Query(default=None),
) -> list[ControlKeyPublic] | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
    except DeviceOperationError as exc:
        return exc.to_response()
    stmt = list_control_keys_stmt(user, scope)
    rows = (await ctx.db.execute(stmt)).scalars().all()
    return [ControlKeyPublic.model_validate(row) for row in rows]


@router.get(
    "/{key_id}",
    response_model=ControlKeyPublic,
    summary="Get one control key",
)
async def get_control_key_route(
    key_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> ControlKeyPublic | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
        row = await get_control_key_scoped(ctx.db, user, key_id)
    except DeviceOperationError as exc:
        return exc.to_response()
    return ControlKeyPublic.model_validate(row)


@router.post(
    "/{key_id}/rotate",
    response_model=ControlKeyKeyResponse,
    summary="Rotate the control key (raw returned once, same row id)",
)
async def rotate_control_key_endpoint(
    key_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> ControlKeyKeyResponse | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
        row, raw = await rotate_control_key_route(ctx.db, user, key_id)
    except DeviceOperationError as exc:
        return exc.to_response()
    return ControlKeyKeyResponse(control_key_id=row.id, key=raw)


@router.post(
    "/{key_id}/revoke",
    response_model=ControlKeyPublic,
    summary="Revoke a control key (idempotent)",
)
async def revoke_control_key_endpoint(
    key_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> ControlKeyPublic | JSONResponse:
    try:
        await require_device_manage_permission(ctx.db, user)
        row = await revoke_control_key_route(ctx.db, user, key_id)
    except DeviceOperationError as exc:
        return exc.to_response()
    return ControlKeyPublic.model_validate(row)
