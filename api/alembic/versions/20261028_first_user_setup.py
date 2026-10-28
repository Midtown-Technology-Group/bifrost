"""Persist first-run setup completion without changing existing role assignments.

Revision ID: 20261028_first_user_setup
Revises: 20261025_remote_worker_ownership
"""

import sqlalchemy as sa
from alembic import op

revision = "20261028_first_user_setup"
down_revision = "20261025_remote_worker_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_setup",
        sa.Column("id", sa.Boolean(), primary_key=True),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.CheckConstraint("id = true", name="ck_platform_setup_singleton"),
    )
    # Upgrades are closed even if all previous humans were deleted. Offline
    # migration scripts also fail closed (there is no online revision evidence).
    if not op.get_context().config.attributes.get("bifrost_fresh_install", False):
        op.execute("INSERT INTO platform_setup (id, completed) VALUES (true, true)")
        return
    # Frozen first-human predicate; never import mutable application code.
    op.execute("""
        INSERT INTO platform_setup (id, completed)
        SELECT true, EXISTS (
            SELECT 1 FROM users
            WHERE id != '00000000-0000-0000-0000-000000000001'::uuid
              AND organization_id IS NOT NULL AND identity_kind IS NULL
        )
    """)


def downgrade() -> None:
    op.drop_table("platform_setup")
