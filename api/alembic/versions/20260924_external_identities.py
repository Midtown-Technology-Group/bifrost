"""Add authenticated external actor links and event provenance.

Revision ID: 20260924_external_identity
Revises: 20260924_device_job_logs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260924_external_identity"
down_revision: str | Sequence[str] = "20260924_device_job_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_scope_id", sa.String(255), nullable=False),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("authorized_organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="SET NULL")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_external_identities_actor", "external_identities", ["provider", "external_scope_id", "external_user_id"], unique=True)
    op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"])
    op.add_column("events", sa.Column("external_identity_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("events", sa.Column("authenticated_actor", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "authenticated_actor")
    op.drop_column("events", "external_identity_id")
    op.drop_index("ix_external_identities_user_id", table_name="external_identities")
    op.drop_index("ix_external_identities_actor", table_name="external_identities")
    op.drop_table("external_identities")
