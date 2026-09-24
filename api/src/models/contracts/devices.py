"""Pydantic contracts for the device control plane (M0 freeze).

Hashes and raw secrets never appear in public models; raw key/token values
are returned exactly once from the routes documented in
docs/architecture/device-control-plane.md.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

_ENROLLMENT_TOKEN_PATTERN = (
    r"^bfen_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_[A-Za-z0-9_-]{43}$"
)


class DeviceCreate(BaseModel):
    """POST /api/devices body."""

    display_name: str = Field(..., min_length=1, max_length=255)
    external_ref: str | None = Field(default=None, min_length=1, max_length=255)
    # Superusers without an org must name the target org; org callers are
    # always pinned to their own organization_id (value ignored if sent).
    organization_id: UUID | None = None
    # One-time enrollment token TTL; M0 default 15 minutes.
    enrollment_ttl_seconds: int = Field(default=900, ge=60, le=86400)


class DevicePublic(BaseModel):
    """Public device view — never includes key or enrollment hashes."""

    model_config = {"from_attributes": True}

    id: UUID
    organization_id: UUID
    display_name: str
    external_ref: str | None
    status: str
    agent_version: str | None
    os: str | None
    hostname: str | None
    api_key_enabled: bool
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DeviceCreateResponse(BaseModel):
    """Create response: public device plus the one-time enrollment token."""

    device: DevicePublic
    enrollment_token: str
    enrollment_expires_at: datetime


class DeviceEnrollRequest(BaseModel):
    """POST /api/devices/enroll body (CSRF-exempt, no user session)."""

    enrollment_token: str = Field(
        ..., min_length=32, max_length=512, pattern=_ENROLLMENT_TOKEN_PATTERN
    )


class DeviceEnrollResponse(BaseModel):
    """Frozen enrollment success shape (enrollment.schema.json)."""

    device_id: UUID
    device_key: str
    status: str = "active"
    enrollment_token_expires_at: datetime | None = None


class DeviceKeyResponse(BaseModel):
    """One-time raw key response for rotate-key."""

    device_id: UUID
    device_key: str


class DeviceFreshness(BaseModel):
    """Reduced device view returned to control-key callers (approved M0
    delta: Midtown-Technology-Group/bifrost#818 comment 5815371680).

    The workspace must observe pre-accept freshness for transport
    fail-closed; a control key may read exactly these three fields, and
    only for devices on its own allow-list. No names, external_ref, or
    hashes ever leave through this view.
    """

    model_config = {"from_attributes": True}

    id: UUID
    status: str
    last_seen_at: datetime | None
