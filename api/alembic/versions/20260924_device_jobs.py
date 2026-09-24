"""Add the device_jobs domain execution record.

Revision ID: 20260924_device_jobs
Revises: 20260923_dev_ctl_keys
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260924_device_jobs"
down_revision: str | Sequence[str] = "20260923_dev_ctl_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("script_name", sa.String(length=255), nullable=False),
        sa.Column("script_content", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_output_bytes", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "requested_by_api_key_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "requested_by_workflow_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "requested_by_execution_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_agent_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("log_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 900",
            name="ck_device_jobs_timeout_seconds",
        ),
        sa.CheckConstraint(
            "max_output_bytes >= 1024 AND max_output_bytes <= 16777216",
            name="ck_device_jobs_max_output_bytes",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_jobs_device_id", "device_jobs", ["device_id"])
    op.create_index(
        "uq_device_jobs_one_active",
        "device_jobs",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'claimed', 'running')"),
    )
    op.create_index(
        "ix_device_jobs_org_created", "device_jobs", ["organization_id", "created_at"]
    )
    op.create_index(
        "ix_device_jobs_device_status", "device_jobs", ["device_id", "status"]
    )
    op.create_index(
        "ix_device_jobs_status_activity",
        "device_jobs",
        ["status", "last_agent_activity_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_jobs_status_activity", table_name="device_jobs")
    op.drop_index("ix_device_jobs_device_status", table_name="device_jobs")
    op.drop_index("ix_device_jobs_org_created", table_name="device_jobs")
    op.drop_index("uq_device_jobs_one_active", table_name="device_jobs")
    op.drop_index("ix_device_jobs_device_id", table_name="device_jobs")
    op.drop_table("device_jobs")
