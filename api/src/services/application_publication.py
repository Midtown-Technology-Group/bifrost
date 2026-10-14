"""Persisted App identity/control evidence for publication recovery."""

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.services.operation_receipts import canonical_request_fingerprint


async def publication_controls_hash(db: AsyncSession, app_id: UUID, *, lock: bool = False) -> str:
    # Publication changes only these bookkeeping columns. Read persisted columns
    # explicitly; do not expire an ORM entity through relationship eager loads.
    columns = [c for c in Application.__table__.columns
               if c.name not in {"published_at", "published_snapshot", "updated_at"}]
    query = select(*columns).where(Application.id == app_id)
    roles = select(AppRole.role_id).where(AppRole.app_id == app_id)
    if lock:
        query, roles = query.with_for_update(), roles.with_for_update()
    row = (await db.execute(query)).mappings().one_or_none()
    if row is None:
        raise ValueError("Application no longer exists")
    values = {key: ("sha256:" + hashlib.sha256(value).hexdigest() if isinstance(value, bytes) else value)
              for key, value in row.items()}
    values["role_ids"] = sorted(str(value) for value in (await db.scalars(roles)).all())
    return "sha256:" + canonical_request_fingerprint(values)
