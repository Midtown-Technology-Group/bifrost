"""Common owner/session identity candidate; no lifecycle writer is enabled.

These records retain associations, not proof of admission or process custody.
Writer roles, source admission, shared lock order and the remaining Start/grant/
receipt transactions must be reviewed and verified before writer acceptance.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261010_runtime_sessions"
down_revision = "20261009_runtime_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_execution_runtime_source",
        "executions",
        ["id", "workflow_id", "solution_deployment_id"],
    )
    op.create_unique_constraint(
        "uq_workflow_attempt_runtime_fence",
        "workflow_execution_attempts",
        ["id", "execution_id", "claim_token", "worker_incarnation_id"],
    )
    op.create_table(
        "runtime_execution_owners",
        sa.Column("execution_id", postgresql.UUID(), primary_key=True),
        sa.Column("owner_incarnation_id", postgresql.UUID(), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(), nullable=False),
        sa.Column("artifact_id", sa.String(71), nullable=False),
        sa.Column("caller_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("caller_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "execution_id", "owner_incarnation_id", name="uq_runtime_owner_identity"
        ),
        sa.ForeignKeyConstraint(
            ["execution_id", "workflow_id", "deployment_id"],
            [
                "executions.id",
                "executions.workflow_id",
                "executions.solution_deployment_id",
            ],
            name="fk_runtime_owner_execution_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id", "workflow_id", "artifact_id"],
            [
                "runtime_deployment_artifacts.deployment_id",
                "runtime_deployment_artifacts.workflow_id",
                "runtime_deployment_artifacts.artifact_id",
            ],
            name="fk_runtime_owner_accepted_artifact",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(caller_snapshot) = 'object' AND "
            "caller_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_owner_caller_snapshot",
        ),
    )
    op.create_table(
        "runtime_sessions",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("execution_id", postgresql.UUID(), nullable=False),
        sa.Column("owner_incarnation_id", postgresql.UUID(), nullable=False),
        sa.Column("workflow_attempt_id", postgresql.UUID(), nullable=False),
        sa.Column("claim_token", postgresql.UUID(), nullable=False),
        sa.Column("worker_incarnation_id", postgresql.UUID(), nullable=False),
        sa.Column("supervisor_incarnation_id", postgresql.UUID(), nullable=False),
        sa.Column("runtime_incarnation_id", postgresql.UUID(), nullable=False),
        sa.Column("channel_custody_sha256", sa.String(64), nullable=False),
        sa.Column("binding_sha256", sa.String(64), nullable=False),
        sa.Column("prepare_id", postgresql.UUID(), nullable=False),
        sa.Column("prepare_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("close_reason", sa.String(64)),
        sa.UniqueConstraint("workflow_attempt_id", name="uq_runtime_session_attempt"),
        sa.UniqueConstraint(
            "id",
            "execution_id",
            "owner_incarnation_id",
            "workflow_attempt_id",
            name="uq_runtime_session_identity",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id", "owner_incarnation_id"],
            [
                "runtime_execution_owners.execution_id",
                "runtime_execution_owners.owner_incarnation_id",
            ],
            name="fk_runtime_session_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "workflow_attempt_id",
                "execution_id",
                "claim_token",
                "worker_incarnation_id",
            ],
            [
                "workflow_execution_attempts.id",
                "workflow_execution_attempts.execution_id",
                "workflow_execution_attempts.claim_token",
                "workflow_execution_attempts.worker_incarnation_id",
            ],
            name="fk_runtime_session_attempt_fence",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "channel_custody_sha256 ~ '^[0-9a-f]{64}$' AND "
            "binding_sha256 ~ '^[0-9a-f]{64}$' AND "
            "prepare_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_session_digests",
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL AND close_reason IS NULL) OR "
            "(closed_at IS NOT NULL AND closed_at >= created_at AND "
            "close_reason IS NOT NULL AND close_reason ~ '^[a-z][a-z0-9_-]{0,63}$')",
            name="ck_runtime_session_close_shape",
        ),
    )
    op.execute("""
        CREATE FUNCTION enforce_runtime_owner_immutability() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
                RAISE EXCEPTION 'runtime owner association is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER runtime_owner_immutable BEFORE UPDATE OR DELETE
        ON runtime_execution_owners FOR EACH ROW
        EXECUTE FUNCTION enforce_runtime_owner_immutability()
    """)
    op.execute("""
        CREATE FUNCTION enforce_runtime_session_tombstone() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'runtime session tombstone must be retained'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF (to_jsonb(NEW) - 'closed_at' - 'close_reason') IS DISTINCT FROM
               (to_jsonb(OLD) - 'closed_at' - 'close_reason') OR
               (OLD.closed_at IS NOT NULL AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN
                RAISE EXCEPTION 'runtime session identity or tombstone is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER runtime_session_tombstone BEFORE UPDATE OR DELETE
        ON runtime_sessions FOR EACH ROW EXECUTE FUNCTION enforce_runtime_session_tombstone()
    """)
    for table in ("runtime_execution_owners", "runtime_sessions"):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    for function in (
        "enforce_runtime_owner_immutability",
        "enforce_runtime_session_tombstone",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function}() FROM PUBLIC")


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM runtime_execution_owners) OR "
                "EXISTS (SELECT 1 FROM runtime_sessions)"
            )
        )
        .scalar()
    ):
        raise RuntimeError(
            "Retain runtime owner and session custody evidence before downgrade"
        )
    op.drop_table("runtime_sessions")
    op.drop_table("runtime_execution_owners")
    op.execute("DROP FUNCTION enforce_runtime_session_tombstone()")
    op.execute("DROP FUNCTION enforce_runtime_owner_immutability()")
    op.drop_constraint(
        "uq_workflow_attempt_runtime_fence",
        "workflow_execution_attempts",
        type_="unique",
    )
    op.drop_constraint("uq_execution_runtime_source", "executions", type_="unique")
