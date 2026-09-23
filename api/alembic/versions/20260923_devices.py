"""Add the devices table for the device control plane.

Revision ID: 20260923_devices
Revises: 20260920_ai_delivery_fences
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260923_devices"
down_revision: str | Sequence[str] = "20260920_ai_delivery_fences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending_enrolled",
            nullable=False,
        ),
        sa.Column("agent_version", sa.String(length=64), nullable=True),
        sa.Column("os", sa.String(length=128), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("api_key_hash", sa.String(length=255), nullable=True),
        sa.Column(
            "api_key_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("enrollment_token_hash", sa.String(length=255), nullable=True),
        sa.Column("enrollment_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_devices_api_key_hash",
        "devices",
        ["api_key_hash"],
        postgresql_where=sa.text("api_key_hash IS NOT NULL"),
    )
    op.create_index(
        "ix_devices_org_status", "devices", ["organization_id", "status"]
    )
    op.create_index("ix_devices_external_ref", "devices", ["external_ref"])


def downgrade() -> None:
    op.drop_index("ix_devices_external_ref", table_name="devices")
    op.drop_index("ix_devices_org_status", table_name="devices")
    op.drop_index("ix_devices_api_key_hash", table_name="devices")
    op.drop_table("devices")
