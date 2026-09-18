"""Record retirement of immutable Workspace releases.

Revision ID: 20260918_ws_release_retirement
Revises: 20260914_merge_mtg_cache
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260918_ws_release_retirement"
down_revision: str | None = "20260914_merge_mtg_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_promotion_releases",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_promotion_releases",
        sa.Column(
            "retirement_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.drop_constraint(
        "ck_workspace_promotion_release_activation_state",
        "workspace_promotion_releases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workspace_promotion_release_activation_state",
        "workspace_promotion_releases",
        "activation_state IN ('prepared', 'activating', 'live', "
        "'activation_failed', 'recovery_required', 'rolled_back', "
        "'superseded', 'retired')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workspace_promotion_release_activation_state",
        "workspace_promotion_releases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workspace_promotion_release_activation_state",
        "workspace_promotion_releases",
        "activation_state IN ('prepared', 'activating', 'live', "
        "'activation_failed', 'recovery_required', 'rolled_back', 'superseded')",
    )
    op.drop_column("workspace_promotion_releases", "retirement_evidence")
    op.drop_column("workspace_promotion_releases", "retired_at")
