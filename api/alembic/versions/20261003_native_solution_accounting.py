"""Allow actual immutable Solution completion without fabricating a deploy job."""

import sqlalchemy as sa
from alembic import op

revision = "20261003_native_src_account"
down_revision = "20261003_workflow_retirement"
branch_labels = None
depends_on = None

CHECK = (
    "disposition <> 'released' OR "
            "(solution_id IS NOT NULL AND "
            "candidate_id IS NOT NULL AND source_artifact_sha256 IS NOT NULL AND "
            "completion_evidence IS NOT NULL AND resolved_at IS NOT NULL AND "
            "(deploy_job_id IS NOT NULL OR COALESCE(("
            "completion_evidence->>'schema_version' = 'bifrost.native-solution-deploy-completion/v1' AND "
            "completion_evidence->>'source_commit_sha' = source_commit_sha AND "
            "completion_evidence->>'source_tree_sha' = source_tree_sha AND "
            "completion_evidence->>'source_subtree_sha' = source_subtree_sha AND "
            "completion_evidence->>'source_content_id' = source_content_id AND "
            "completion_evidence->>'solution_id' = solution_id::text AND "
            "completion_evidence->>'candidate_id' = candidate_id AND "
            "completion_evidence->>'source_artifact_sha256' = source_artifact_sha256 AND "
            "candidate_id = 'sha256:' || source_artifact_sha256 AND "
            "source_artifact_sha256 ~ '^[0-9a-f]{64}$' AND "
            "jsonb_typeof(completion_evidence->'target_installs') = 'object' AND "
            "completion_evidence->'target_installs' <> '{}'::jsonb AND "
            "jsonb_typeof(completion_evidence->'installations') = 'object' AND "
            "completion_evidence->'installations' <> '{}'::jsonb"
            "), FALSE)))"
)


def upgrade() -> None:
    op.drop_constraint("ck_solution_deploy_obligation_released_evidence",
        "solution_deploy_obligations", type_="check")
    op.create_check_constraint("ck_solution_deploy_obligation_released_evidence",
        "solution_deploy_obligations", CHECK)


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM solution_deploy_obligations "
        "WHERE disposition = 'released' AND deploy_job_id IS NULL)"
    )).scalar():
        raise RuntimeError("Preserve native Solution completion evidence before downgrade")
    op.drop_constraint("ck_solution_deploy_obligation_released_evidence",
        "solution_deploy_obligations", type_="check")
    op.create_check_constraint("ck_solution_deploy_obligation_released_evidence",
        "solution_deploy_obligations", (
        "disposition <> 'released' OR "
        "(solution_id IS NOT NULL AND deploy_job_id IS NOT NULL AND "
        "candidate_id IS NOT NULL AND source_artifact_sha256 IS NOT NULL AND "
        "completion_evidence IS NOT NULL AND resolved_at IS NOT NULL)"))
