"""Allow verified Solution delivery to settle Root-source accountability.

Keep this constraint during application rollback: old code can still write Live
completion evidence and must not discard completed Solution accounting rows.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261001_solution_src_account"
down_revision = "20261001_ws_declaration_digest"
branch_labels = None
depends_on = None

NAME = "ck_workspace_source_release_released_evidence"
TABLE = "workspace_source_releases"
SOLUTION_COMPLETION = (
    "disposition <> 'released' OR "
    "(completion_evidence IS NOT NULL AND resolved_at IS NOT NULL AND "
    "(release_row_id IS NOT NULL OR COALESCE("
    "(completion_evidence->>'schema_version' = 'bifrost.solution-owned-source-completion/v1' "
    "AND completion_evidence->>'source_commit_sha' = source_commit_sha "
    "AND completion_evidence->>'source_tree_sha' = source_tree_sha "
    "AND jsonb_typeof(completion_evidence->'paths') = 'object' "
    "AND completion_evidence->'paths' <> '{}'::jsonb), FALSE)))"
)


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("accounting_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(NAME, TABLE, type_="check")
    op.create_check_constraint(NAME, TABLE, SOLUTION_COMPLETION)


def downgrade() -> None:
    # An old binary can retain these rows; the old constraint cannot. Refuse a
    # destructive downgrade until an operator has explicitly resolved them.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM workspace_source_releases
            WHERE disposition = 'released' AND release_row_id IS NULL)
        THEN RAISE EXCEPTION 'Retain Solution source accounting constraint during application rollback';
        END IF;
    END $$""")
    op.drop_constraint(NAME, TABLE, type_="check")
    op.create_check_constraint(NAME, TABLE,
        "disposition <> 'released' OR "
        "(release_row_id IS NOT NULL AND completion_evidence IS NOT NULL AND resolved_at IS NOT NULL)")
    op.drop_column(TABLE, "accounting_checked_at")
