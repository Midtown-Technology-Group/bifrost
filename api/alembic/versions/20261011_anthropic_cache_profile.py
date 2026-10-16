"""Scope Anthropic prompt-cache capability to model profiles.

Revision ID: 20261011_anthropic_cache_profile
Revises: 20261012_work_delivery_started
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_anthropic_cache_profile"
down_revision: str | None = "20261012_work_delivery_started"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_model_profiles",
        sa.Column("anthropic_prompt_cache_supported", sa.Boolean(), nullable=True),
    )
    # Connection-wide observations cannot be attributed safely to every model.
    op.drop_column("ai_provider_connections", "anthropic_prompt_cache_supported")


def downgrade() -> None:
    op.add_column(
        "ai_provider_connections",
        sa.Column("anthropic_prompt_cache_supported", sa.Boolean(), nullable=True),
    )
    op.drop_column("ai_model_profiles", "anthropic_prompt_cache_supported")
