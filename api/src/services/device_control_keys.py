"""Device-scoped control-key registry services (M1.4 #832).

Create/list/rotate/revoke against the M0 freeze
(docs/architecture/device-control-plane.md): every key carries a non-empty
device allow-list scoped to one organization; the raw `bfck_` key is
returned exactly once; audit uses the key id, never the secret.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.org_filter import OrgFilterType, org_filter_clause, resolve_org_filter
from src.core.principal import UserPrincipal
from src.models.contracts.device_control_keys import ControlKeyCreate
from src.models.orm.device_control_keys import DeviceControlKey
from src.models.orm.devices import Device
from src.services.device_keys import (
    generate_control_key,
    revoke_control_key,
    rotate_control_key,
    validate_control_key_scope,
)
from src.services.devices import DeviceOperationError, resolve_target_org


async def create_control_key(
    db: AsyncSession,
    user: UserPrincipal,
    body: ControlKeyCreate,
) -> tuple[DeviceControlKey, str]:
    """Create a control key and return (row, raw_key) — raw shown once."""
    organization_id = resolve_target_org(user, body.organization_id)

    try:
        device_ids = validate_control_key_scope(list(body.device_ids))
    except ValueError as exc:
        raise DeviceOperationError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_parameter",
            str(exc),
        ) from exc

    expires_at: datetime | None = None
    if body.expires_at is not None:
        expires_at = (
            body.expires_at
            if body.expires_at.tzinfo
            else body.expires_at.replace(tzinfo=timezone.utc)
        )
        if expires_at <= datetime.now(timezone.utc):
            raise DeviceOperationError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_parameter",
                "expires_at must be in the future",
            )

    # Every allow-listed device must exist in the RESOLVED TARGET org —
    # otherwise a key for org X could persist device ids from org Y (an
    # unscoped superuser has no caller-side org filter to stop it).
    # key_can_target would deny cross-org use, but the scope itself must
    # never be persisted invalid.
    clause = org_filter_clause(
        Device.organization_id, OrgFilterType.ORG_ONLY, organization_id
    )
    stmt = select(Device.id).where(Device.id.in_(device_ids))
    if clause is not None:
        stmt = stmt.where(clause)
    visible = {row for row in (await db.execute(stmt)).scalars().all()}
    if visible != set(device_ids):
        raise DeviceOperationError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_parameter",
            "one or more device_ids do not exist in the caller's organization",
        )

    key_id, raw, key_hash = generate_control_key()
    row = DeviceControlKey(
        id=key_id,
        organization_id=organization_id,
        name=body.name,
        key_hash=key_hash,
        enabled=True,
        expires_at=expires_at,
        device_ids=device_ids,
        created_by=str(user.user_id),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, raw


async def get_control_key_scoped(
    db: AsyncSession,
    user: UserPrincipal,
    key_id: UUID,
    *,
    with_lock: bool = False,
) -> DeviceControlKey:
    """Load one control key under the caller's org scope.

    ``with_lock=True`` takes a row lock (``SELECT ... FOR UPDATE``) so
    eligibility checks followed by writes — e.g. rotate's enabled-check then
    hash update — serialize against a concurrent revoke instead of racing it.
    """
    filter_type, filter_org_id = resolve_org_filter(user)
    stmt = select(DeviceControlKey).where(DeviceControlKey.id == key_id)
    clause = org_filter_clause(
        DeviceControlKey.organization_id, filter_type, filter_org_id
    )
    if clause is not None:
        stmt = stmt.where(clause)
    if with_lock:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise DeviceOperationError(
            status.HTTP_404_NOT_FOUND,
            "unknown_control_key",
            "control key not found",
        )
    return row


def list_control_keys_stmt(
    user: UserPrincipal,
    scope: str | None = None,
) -> "object":
    """Build the org-scoped list statement (no inline org comparisons)."""
    filter_type, filter_org_id = resolve_org_filter(user, scope)
    stmt = select(DeviceControlKey)
    clause = org_filter_clause(
        DeviceControlKey.organization_id, filter_type, filter_org_id
    )
    if clause is not None:
        stmt = stmt.where(clause)
    return stmt.order_by(DeviceControlKey.created_at.desc())


async def rotate_control_key_route(
    db: AsyncSession,
    user: UserPrincipal,
    key_id: UUID,
) -> tuple[DeviceControlKey, str]:
    # Row lock: serialize against a concurrent revoke so the eligibility
    # check and the hash update commit atomically w.r.t. it.
    row = await get_control_key_scoped(db, user, key_id, with_lock=True)
    # Rotation preserves enabled/expiry, so it cannot make an unusable key
    # usable — reject instead of minting a new raw secret that would silently
    # fail at verification (revoked keys must be replaced, not rotated).
    if not row.enabled:
        raise DeviceOperationError(
            status.HTTP_409_CONFLICT,
            "control_key_inactive",
            "control key is revoked; create a new key instead of rotating",
        )
    if row.expires_at is not None:
        expires_at = (
            row.expires_at
            if row.expires_at.tzinfo
            else row.expires_at.replace(tzinfo=timezone.utc)
        )
        if expires_at <= datetime.now(timezone.utc):
            raise DeviceOperationError(
                status.HTTP_409_CONFLICT,
                "control_key_inactive",
                "control key is expired; create a new key instead of rotating",
            )
    raw = rotate_control_key(row)
    await db.commit()
    await db.refresh(row)
    return row, raw


async def revoke_control_key_route(
    db: AsyncSession,
    user: UserPrincipal,
    key_id: UUID,
) -> DeviceControlKey:
    """Revoke (idempotent): repeat calls stay revoked, never fail open."""
    row = await get_control_key_scoped(db, user, key_id)
    revoke_control_key(row)
    await db.commit()
    await db.refresh(row)
    return row
