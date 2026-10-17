"""Retain distinct App publication proof in the existing Root source ledger."""

import sqlalchemy as sa
from alembic import op

revision = "20261005_package_src_account"
down_revision = "20261003_native_src_account"
branch_labels = None
depends_on = None

NAME = "ck_workspace_source_release_released_evidence"
TABLE = "workspace_source_releases"


def completion_constraint(*, applications: bool) -> str:
    schemas = "'bifrost.solution-owned-source-completion/v1'"
    if applications:
        schemas += ", 'bifrost.package-owned-source-completion/v1'"
    return (
        "disposition <> 'released' OR "
        "(completion_evidence IS NOT NULL AND resolved_at IS NOT NULL AND "
        "(release_row_id IS NOT NULL OR COALESCE(("
        f"completion_evidence->>'schema_version' IN ({schemas}) "
        "AND completion_evidence->>'source_commit_sha' = source_commit_sha "
        "AND completion_evidence->>'source_tree_sha' = source_tree_sha "
        "AND jsonb_typeof(completion_evidence->'paths') = 'object' "
        "AND completion_evidence->'paths' <> '{}'::jsonb), FALSE)))"
    )


def upgrade() -> None:
    op.drop_constraint(NAME, TABLE, type_="check")
    op.create_check_constraint(NAME, TABLE, completion_constraint(applications=True))


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM workspace_source_releases WHERE disposition = 'released' "
        "AND release_row_id IS NULL AND completion_evidence->>'schema_version' "
        "= 'bifrost.package-owned-source-completion/v1')"
    )).scalar():
        raise RuntimeError("Retain verified App source accounting during application rollback")
    op.drop_constraint(NAME, TABLE, type_="check")
    op.create_check_constraint(NAME, TABLE, completion_constraint(applications=False))
