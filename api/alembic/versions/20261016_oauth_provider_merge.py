"""Merge OAuth provider dedupe and work-delivery repair heads.

Revision ID: 20261016_oauth_provider_merge
Revises: 20261011_oauth_provider_dedupe, 20261012_work_delivery_started
Create Date: 2026-10-16
"""

revision = "20261016_oauth_provider_merge"
down_revision = (
    "20261011_oauth_provider_dedupe",
    "20261012_work_delivery_started",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
