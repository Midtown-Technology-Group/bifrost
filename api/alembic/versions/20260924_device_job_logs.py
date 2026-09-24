"""Add durable device job log entries.

Revision ID: 20260924_device_job_logs
Revises: 20260924_device_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260924_device_job_logs"
down_revision: str | Sequence[str] = "20260924_device_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_job_logs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stream", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["device_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "seq"),
    )


def downgrade() -> None:
    op.drop_table("device_job_logs")
