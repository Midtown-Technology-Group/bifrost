"""Add durable agent action approval proposals.

Revision ID: 20260924_action_approval
Revises: 20260924_external_identity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260924_action_approval"
down_revision: str | Sequence[str] = "20260924_external_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_action_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True)),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("caller", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_agent_action_approvals_status_created", "agent_action_approvals", ["status", "created_at"])
    op.create_index("ix_agent_action_approvals_run", "agent_action_approvals", ["agent_run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_action_approvals_run", table_name="agent_action_approvals")
    op.drop_index("ix_agent_action_approvals_status_created", table_name="agent_action_approvals")
    op.drop_table("agent_action_approvals")
