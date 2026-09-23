"""Estate devices registered for the device control plane (epic #818)."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base

# Statuses: pending_enrolled -> active <-> disabled (see
# docs/architecture/device-control-plane.md).
DEVICE_STATUS_PENDING_ENROLLED = "pending_enrolled"
DEVICE_STATUS_ACTIVE = "active"
DEVICE_STATUS_DISABLED = "disabled"


class Device(Base):
    """One enrolled (or pending) estate device.

    Identity entity: addressed by UUID inside an explicit organization;
    never resolved through the org-to-global name cascade.
    """

    __tablename__ = "devices"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DEVICE_STATUS_PENDING_ENROLLED,
        server_default=DEVICE_STATUS_PENDING_ENROLLED,
    )
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # bcrypt hash of the bfdk_ key's secret component (the device UUID routes
    # the lookup); raw is returned once, never stored.
    api_key_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    enrollment_token_hash: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "ix_devices_api_key_hash",
            "api_key_hash",
            postgresql_where=text("api_key_hash IS NOT NULL"),
        ),
        Index("ix_devices_org_status", "organization_id", "status"),
    )
