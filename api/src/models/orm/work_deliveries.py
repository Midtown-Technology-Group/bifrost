"""Transport ownership only; executions and PlatformJobs own domain outcomes."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkDelivery(Base):
    __tablename__ = "work_deliveries"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    queue_name: Mapped[str] = mapped_column(String(100), nullable=False)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Encrypt the entire envelope, including context and headers. Queue inspection
    # must not turn parameters, inline code or tuning prompts into public metadata.
    encrypted_envelope: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="queued"
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'claimed', 'completed', 'poison', 'interrupted')",
            name="ck_work_deliveries_status",
        ),
        CheckConstraint(
            "(status = 'claimed' AND lease_token IS NOT NULL AND "
            "lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'claimed' AND lease_token IS NULL AND "
            "lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_work_deliveries_lease",
        ),
        Index(
            "ix_work_deliveries_claim",
            "queue_name",
            "available_at",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_work_deliveries_expired",
            "lease_expires_at",
            "id",
            postgresql_where=text("status = 'claimed'"),
        ),
        Index(
            "uq_work_deliveries_active",
            "queue_name",
            "message_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'claimed', 'interrupted')"),
        ),
    )
