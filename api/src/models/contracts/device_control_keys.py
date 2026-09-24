"""Pydantic contracts for device-scoped control keys (M1.4 #832).

The raw key is returned exactly once from create/rotate; public models
never expose `key_hash`.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ControlKeyCreate(BaseModel):
    """POST /api/device-control-keys body."""

    name: str = Field(..., min_length=1, max_length=255)
    # Non-empty allow-list is mandatory (M0: no org-wide implicit key); the
    # DB check constraint enforces it even if this gate is bypassed.
    device_ids: list[UUID] = Field(..., min_length=1, max_length=1000)
    # Superusers without an org must name the target org; org callers are
    # pinned to their own organization_id.
    organization_id: UUID | None = None
    expires_at: datetime | None = None


class ControlKeyPublic(BaseModel):
    """Public control-key view — never includes the key hash."""

    model_config = {"from_attributes": True}

    id: UUID
    organization_id: UUID
    name: str
    enabled: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    device_ids: list[UUID]
    created_by: str
    created_at: datetime
    updated_at: datetime


class ControlKeyCreatedResponse(BaseModel):
    """Create response: public row plus the one-time raw key."""

    control_key: ControlKeyPublic
    key: str


class ControlKeyKeyResponse(BaseModel):
    """One-time raw key response for rotate."""

    control_key_id: UUID
    key: str
