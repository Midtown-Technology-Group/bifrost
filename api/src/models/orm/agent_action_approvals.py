"""Durable proposals for agent workflow tools that require human approval."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class AgentActionApproval(Base):
    __tablename__ = "agent_action_approvals"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Immutable provenance IDs remain queryable after source rows are removed.
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column()
    workflow_id: Mapped[UUID] = mapped_column(nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column()
    requested_by_user_id: Mapped[UUID | None] = mapped_column()
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    caller: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column()
    execution_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_agent_action_approvals_status_created", "status", "created_at"),
        Index("ix_agent_action_approvals_run", "agent_run_id"),
    )
