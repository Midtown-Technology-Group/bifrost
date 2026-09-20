"""Fence derived AI completion writes to PostgreSQL delivery ownership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260920_ai_delivery_fences"
down_revision = "20260919_package_worker_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("summary_delivery_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM agent_runs WHERE summary_delivery_id IS NOT NULL)"
    )).scalar():
        raise RuntimeError("Retain summary delivery fences during image rollback")
    op.drop_column("agent_runs", "summary_delivery_id")
