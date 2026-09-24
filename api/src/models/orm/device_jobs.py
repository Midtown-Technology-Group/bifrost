"""Durable domain execution record for ad-hoc device jobs (epic #818, M2).

`device_jobs` is a domain execution/transport record — sibling of workflow
`executions` — **not** a PlatformJob (M0 freeze:
docs/architecture/device-control-plane.md §Domain record). The platform
records and fences; a remote device agent executes.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base

# Server-authoritative lifecycle. Agents may only submit claims, logs, and
# agent-terminal results; `lost` is written exclusively by the server.
JOB_STATUS_PENDING = "pending"
JOB_STATUS_CLAIMED = "claimed"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_TIMEOUT = "timeout"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_LOST = "lost"

ACTIVE_JOB_STATUSES = (
    JOB_STATUS_PENDING,
    JOB_STATUS_CLAIMED,
    JOB_STATUS_RUNNING,
)
# Statuses an agent may report in a result payload (lost excluded — frozen).
AGENT_TERMINAL_STATUSES = (
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_FAILED,
    JOB_STATUS_TIMEOUT,
    JOB_STATUS_CANCELLED,
)
TERMINAL_JOB_STATUSES = AGENT_TERMINAL_STATUSES + (JOB_STATUS_LOST,)

# M0 freeze constants.
SCRIPT_MAX_BYTES = 256 * 1024
PARAMS_MAX_BYTES = 32 * 1024
TIMEOUT_DEFAULT_SECONDS = 120
TIMEOUT_MAX_SECONDS = 900
MAX_OUTPUT_DEFAULT_BYTES = 1024 * 1024
MAX_OUTPUT_MIN_BYTES = 1024
MAX_OUTPUT_MAX_BYTES = 16 * 1024 * 1024
CLAIM_LEASE_SECONDS = 60
RUNNING_LOST_SECONDS = 90
TIMEOUT_BACKSTOP_GRACE_SECONDS = 60


class DeviceJob(Base):
    __tablename__ = "device_jobs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    device_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=JOB_STATUS_PENDING,
        server_default=JOB_STATUS_PENDING,
    )
    script_name: Mapped[str] = mapped_column(String(255), nullable=False)
    script_content: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=TIMEOUT_DEFAULT_SECONDS
    )
    max_output_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=MAX_OUTPUT_DEFAULT_BYTES
    )

    # Attribution (M0 feedback #2): user XOR/OR control key + optional
    # caller-asserted workflow/execution ids. Audit stores ids, never secrets.
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    requested_by_api_key_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    requested_by_workflow_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    requested_by_execution_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    # Fencing / lease state.
    claim_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agent_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    last_agent_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Cooperative cancel flag (M0: no kill guarantee by the platform).
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Terminal observation.
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

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
        CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 900",
            name="ck_device_jobs_timeout_seconds",
        ),
        CheckConstraint(
            "max_output_bytes >= 1024 AND max_output_bytes <= 16777216",
            name="ck_device_jobs_max_output_bytes",
        ),
        # One active job per device (M0 busy contract: second create = 409,
        # never a silent queue).
        Index(
            "uq_device_jobs_one_active",
            "device_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'claimed', 'running')"),
        ),
        Index("ix_device_jobs_org_created", "organization_id", "created_at"),
        Index("ix_device_jobs_device_status", "device_id", "status"),
        Index("ix_device_jobs_status_activity", "status", "last_agent_activity_at"),
    )
