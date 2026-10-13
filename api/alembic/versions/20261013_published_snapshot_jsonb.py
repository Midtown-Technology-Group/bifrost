"""Repair applications.published_snapshot to use PostgreSQL JSONB.

Revision ID: 20261013_pub_snapshot_jsonb
Revises: 20261013_form_field_tz
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261013_pub_snapshot_jsonb"
down_revision = "20261013_form_field_tz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    data_type = bind.execute(
        sa.text("""
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'applications'
              AND column_name = 'published_snapshot'
        """)
    ).scalar_one_or_none()
    if data_type == "json":
        op.alter_column(
            "applications",
            "published_snapshot",
            type_=postgresql.JSONB(),
            existing_type=sa.JSON(),
            postgresql_using="published_snapshot::jsonb",
        )


def downgrade() -> None:
    # JSON is representationally compatible with JSONB, but reverting would
    # discard the column's documented operator and indexing contract.
    pass
