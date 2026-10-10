"""Bind immutable native artifacts to existing Solution workflow deployments.

Isolated implementation candidate. No dispatch, credential or lifecycle writer
is enabled by installing these records. Authority/session schema and mechanical
writer exclusion must be reviewed together before owner acceptance.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261009_runtime_artifacts"
down_revision = "20261006_mtg_upstream_sdk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workflow_id_solution", "workflows", ["id", "solution_id"]
    )
    op.create_table(
        "runtime_deployment_artifacts",
        sa.Column("deployment_id", postgresql.UUID(), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(), nullable=False),
        sa.Column("solution_id", postgresql.UUID(), nullable=False),
        sa.Column("artifact_id", sa.String(71), nullable=False),
        sa.Column("runtime", sa.String(64), nullable=False),
        sa.Column("runtime_protocol", sa.String(64), nullable=False),
        sa.Column("artifact", postgresql.JSONB(), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(), nullable=False),
        sa.Column("build_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("reviewed_by", postgresql.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("deployment_id", "workflow_id"),
        sa.UniqueConstraint(
            "deployment_id",
            "workflow_id",
            "artifact_id",
            name="uq_runtime_deployment_artifact_identity",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id", "solution_id"],
            ["solution_deployments.id", "solution_deployments.solution_id"],
            name="fk_runtime_artifact_deployment_solution",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "solution_id"],
            ["workflows.id", "workflows.solution_id"],
            name="fk_runtime_artifact_workflow_solution",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "artifact_id ~ '^sha256:[0-9a-f]{64}$'", name="ck_runtime_artifact_id"
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' AND build_evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_artifact_evidence_digests",
        ),
        sa.CheckConstraint(
            "runtime ~ '^[a-z][a-z0-9-]*/v[1-9][0-9]*$' AND runtime_protocol = 'bifrost.runtime/v1'",
            name="ck_runtime_artifact_protocol",
        ),
        sa.CheckConstraint(
            "COALESCE(jsonb_typeof(artifact) = 'object' AND "
            "artifact->>'kind' = 'native-executable/v1' AND "
            "artifact->>'artifact_id' = artifact_id AND "
            "artifact->>'runtime_protocol' = runtime_protocol AND "
            "artifact->>'build_evidence_sha256' = build_evidence_sha256, FALSE)",
            name="ck_runtime_artifact_descriptor_binding",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_schema) = 'object' AND jsonb_typeof(output_schema) = 'object'",
            name="ck_runtime_artifact_schema_objects",
        ),
    )
    op.execute("""
        CREATE FUNCTION enforce_runtime_artifact_immutability() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
                RAISE EXCEPTION 'runtime artifact association is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER runtime_artifact_immutable
            BEFORE UPDATE OR DELETE ON runtime_deployment_artifacts
            FOR EACH ROW EXECUTE FUNCTION enforce_runtime_artifact_immutability()
    """)
    op.execute("REVOKE ALL ON runtime_deployment_artifacts FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION enforce_runtime_artifact_immutability() FROM PUBLIC"
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM runtime_deployment_artifacts)"))
        .scalar()
    ):
        raise RuntimeError("Retain runtime artifact custody evidence before downgrade")
    op.drop_table("runtime_deployment_artifacts")
    op.execute("DROP FUNCTION enforce_runtime_artifact_immutability()")
    op.drop_constraint("uq_workflow_id_solution", "workflows", type_="unique")
