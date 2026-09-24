"""Tenant-scoped links from authenticated external actors to Bifrost users."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class ExternalIdentity(Base):
    __tablename__ = "external_identities"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    authorized_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), default=None
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index(
            "ix_external_identities_actor",
            "provider",
            "external_scope_id",
            "external_user_id",
            unique=True,
        ),
        Index("ix_external_identities_user_id", "user_id"),
    )
