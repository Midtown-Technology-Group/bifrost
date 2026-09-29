"""Allow evidenced supersession of old Workspace source obligations.

Revision ID: 20260929_ws_source_supersession
Revises: 20260925_mtg_workspace_import
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260929_ws_source_supersession"
down_revision: str | None = "20260925_mtg_workspace_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_workspace_source_release_disposition",
        "workspace_source_releases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workspace_source_release_disposition",
        "workspace_source_releases",
        "disposition IN ('pending', 'attention_required', 'released', "
        "'deferred', 'non_production', 'superseded')",
    )
    op.create_check_constraint(
        "ck_workspace_source_release_superseded_evidence",
        "workspace_source_releases",
        "disposition <> 'superseded' OR "
        "(reason IS NOT NULL AND completion_evidence IS NOT NULL "
        "AND resolved_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workspace_source_release_superseded_evidence",
        "workspace_source_releases",
        type_="check",
    )
    op.execute(
        "UPDATE workspace_source_releases SET disposition = 'attention_required' "
        "WHERE disposition = 'superseded'"
    )
    op.drop_constraint(
        "ck_workspace_source_release_disposition",
        "workspace_source_releases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workspace_source_release_disposition",
        "workspace_source_releases",
        "disposition IN ('pending', 'attention_required', 'released', "
        "'deferred', 'non_production')",
    )
