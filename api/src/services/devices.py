"""Device registry services (M1 #831) for the device control plane.

Implements create / enroll / rotate / disable / enable against the M0
freeze (docs/architecture/device-control-plane.md). Errors carry the frozen
structured envelope codes from
docs/architecture/device-control-plane/error.schema.json.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from src.core.org_filter import org_filter_clause, resolve_org_filter
from src.core.principal import UserPrincipal
from src.models.contracts.devices import DeviceCreate
from src.models.orm.devices import (
    DEVICE_STATUS_ACTIVE,
    DEVICE_STATUS_DISABLED,
    DEVICE_STATUS_PENDING_ENROLLED,
    Device,
)
from src.services.device_keys import (
    ENROLLMENT_TOKEN_PREFIX,
    generate_device_key,
    generate_enrollment_token,
    parse_device_key,
    rotate_device_key,
    verify_key_hash,
)


class DeviceOperationError(Exception):
    """Domain error carrying the frozen device-control-plane envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        **extra: object,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.extra = extra

    def to_response(self) -> JSONResponse:
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        payload.update(self.extra)
        return JSONResponse(
            status_code=self.status_code, content={"error": payload}
        )


async def user_can_manage_devices(db: AsyncSession, user: UserPrincipal) -> bool:
    """Registry management gate: platform superuser bypass or the
    ``can_manage_config`` role permission (M0 authorization matrix)."""
    if user.is_superuser:
        return True
    from src.models.orm.users import Role, UserRole

    result = await db.execute(
        select(Role.permissions)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.user_id)
    )
    for permissions in result.scalars().all():
        if permissions and permissions.get("can_manage_config"):
            return True
    return False


async def require_device_manage_permission(db: AsyncSession, user: UserPrincipal) -> None:
    if not await user_can_manage_devices(db, user):
        raise DeviceOperationError(
            status.HTTP_403_FORBIDDEN,
            "permission_denied",
            "can_manage_config permission required for device registry operations",
        )


def _resolve_device_org_id(user: UserPrincipal, requested: UUID | None) -> UUID:
    if user.is_superuser:
        if requested is None:
            if user.organization_id is not None:
                return user.organization_id
            raise DeviceOperationError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_parameter",
                "organization_id is required when the caller has no organization",
            )
        return requested
    if user.organization_id is None:
        raise DeviceOperationError(
            status.HTTP_403_FORBIDDEN,
            "permission_denied",
            "caller has no organization",
        )
    if requested is not None and requested != user.organization_id:
        raise DeviceOperationError(
            status.HTTP_403_FORBIDDEN,
            "permission_denied",
            "cannot create devices outside the caller's organization",
        )
    return user.organization_id


async def create_device(
    db: AsyncSession,
    user: UserPrincipal,
    body: DeviceCreate,
) -> tuple[Device, str, datetime]:
    """Create a pending device and mint its one-time enrollment token.

    Returns (device, raw_enrollment_token, expires_at). The raw token is
    shown exactly once and never stored.
    """
    organization_id = _resolve_device_org_id(user, body.organization_id)

    device_id = uuid4()
    raw_token, token_hash = generate_enrollment_token(device_id)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=body.enrollment_ttl_seconds)

    device = Device(
        id=device_id,
        organization_id=organization_id,
        display_name=body.display_name,
        external_ref=body.external_ref,
        status=DEVICE_STATUS_PENDING_ENROLLED,
        enrollment_token_hash=token_hash,
        enrollment_expires_at=expires_at,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device, raw_token, expires_at


async def get_device_scoped(
    db: AsyncSession,
    user: UserPrincipal,
    device_id: UUID,
) -> Device:
    """Load one device under the caller's resolved org scope."""
    filter_type, filter_org_id = resolve_org_filter(user)
    stmt = select(Device).where(Device.id == device_id)
    clause = org_filter_clause(Device.organization_id, filter_type, filter_org_id)
    if clause is not None:
        stmt = stmt.where(clause)
    device = (await db.execute(stmt)).scalar_one_or_none()
    if device is None:
        raise DeviceOperationError(
            status.HTTP_404_NOT_FOUND, "unknown_device", "device not found"
        )
    return device


def list_devices_stmt(
    user: UserPrincipal,
    scope: str | None = None,
) -> "object":
    """Build the org-scoped list statement (no inline org comparisons)."""
    filter_type, filter_org_id = resolve_org_filter(user, scope)
    stmt = select(Device)
    clause = org_filter_clause(Device.organization_id, filter_type, filter_org_id)
    if clause is not None:
        stmt = stmt.where(clause)
    return stmt.order_by(Device.created_at.desc())


async def enroll_device(
    db: AsyncSession,
    enrollment_token: str,
    now: datetime | None = None,
) -> tuple[Device, str]:
    """Exchange a single-use enrollment token for a fresh device key.

    Returns (device, raw_device_key). Frozen codes:
    enrollment_token_invalid / enrollment_token_expired /
    enrollment_token_consumed / device_disabled.
    """
    current = now if now is not None else datetime.now(timezone.utc)

    parsed = parse_device_key(enrollment_token)
    if parsed is None or parsed.prefix != ENROLLMENT_TOKEN_PREFIX:
        raise DeviceOperationError(
            status.HTTP_401_UNAUTHORIZED,
            "enrollment_token_invalid",
            "enrollment token is not valid",
        )

    device = await db.get(Device, parsed.key_id)
    if device is None:
        raise DeviceOperationError(
            status.HTTP_401_UNAUTHORIZED,
            "enrollment_token_invalid",
            "enrollment token is not valid",
        )
    if device.status == DEVICE_STATUS_DISABLED:
        raise DeviceOperationError(
            status.HTTP_403_FORBIDDEN,
            "device_disabled",
            "device is disabled",
        )
    if device.enrollment_token_hash is None:
        raise DeviceOperationError(
            status.HTTP_401_UNAUTHORIZED,
            "enrollment_token_consumed",
            "enrollment token was already used",
        )
    if device.enrollment_expires_at is None or device.enrollment_expires_at <= current:
        raise DeviceOperationError(
            status.HTTP_401_UNAUTHORIZED,
            "enrollment_token_expired",
            "enrollment token expired",
        )
    if not verify_key_hash(enrollment_token, device.enrollment_token_hash):
        raise DeviceOperationError(
            status.HTTP_401_UNAUTHORIZED,
            "enrollment_token_invalid",
            "enrollment token is not valid",
        )

    raw_key, key_hash = generate_device_key(device.id)
    device.status = DEVICE_STATUS_ACTIVE
    device.api_key_hash = key_hash
    device.api_key_enabled = True
    device.enrollment_token_hash = None
    await db.commit()
    await db.refresh(device)
    return device, raw_key


async def rotate_device_key_route(
    db: AsyncSession,
    user: UserPrincipal,
    device_id: UUID,
) -> tuple[Device, str]:
    device = await get_device_scoped(db, user, device_id)
    if device.status != DEVICE_STATUS_ACTIVE or device.api_key_hash is None:
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "device_not_active",
            "device must be active with an issued key before rotate",
        )
    raw_key = rotate_device_key(device)
    await db.commit()
    await db.refresh(device)
    return device, raw_key


async def set_device_enabled(
    db: AsyncSession,
    user: UserPrincipal,
    device_id: UUID,
    enabled: bool,
) -> Device:
    device = await get_device_scoped(db, user, device_id)
    if enabled:
        if device.status != DEVICE_STATUS_DISABLED:
            raise DeviceOperationError(
                status.HTTP_409_CONFLICT,
                "invalid_parameter",
                "only disabled devices can be enabled",
            )
        device.status = (
            DEVICE_STATUS_ACTIVE
            if device.api_key_hash is not None
            else DEVICE_STATUS_PENDING_ENROLLED
        )
    else:
        if device.status == DEVICE_STATUS_DISABLED:
            raise DeviceOperationError(
                status.HTTP_409_CONFLICT,
                "invalid_parameter",
                "device is already disabled",
            )
        device.status = DEVICE_STATUS_DISABLED
        device.api_key_enabled = False
    await db.commit()
    await db.refresh(device)
    return device
