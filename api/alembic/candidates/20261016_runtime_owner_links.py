"""Common owner/session identity with detachable native links — REVIEW ONLY.

Proposed Revision ID: 20261016_runtime_owner_links
Proposed Revises: 20261012_work_delivery_started

Adapted from 56d1fb006d220b9dbb0747f1cca4def18677f918 common prototype.
This file is intentionally outside Alembic's active versions directory.
No role grant, lifecycle writer, complete admission or runtime profile is enabled.
Source/credential/physical-drain guards and payload disposition remain separate
required pieces of the common continuation before admission can be enabled.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261016_runtime_owner_links"
down_revision = "20261012_work_delivery_started"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workflow_attempt_runtime_fence",
        "workflow_execution_attempts",
        ["id", "execution_id", "claim_token", "worker_incarnation_id"],
        schema="public",
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
        *[
            sa.Column(
                column,
                postgresql.UUID(),
                sa.ForeignKey(target, ondelete="SET NULL"),
                nullable=True,
            )
            for column, target in (
                ("live_deployment_id", "public.solution_deployments.id"),
                ("live_workflow_id", "public.workflows.id"),
                ("live_solution_id", "public.solutions.id"),
                ("live_reviewed_by", "public.users.id"),
            )
        ],
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("deployment_id", "workflow_id"),
        sa.UniqueConstraint(
            "deployment_id",
            "workflow_id",
            "artifact_id",
            name="uq_runtime_deployment_artifact_identity",
        ),
        *[
            sa.CheckConstraint(
                f"{live} IS NULL OR {live} = {historical}",
                name=f"ck_runtime_artifact_{live}",
            )
            for live, historical in (
                ("live_deployment_id", "deployment_id"),
                ("live_workflow_id", "workflow_id"),
                ("live_solution_id", "solution_id"),
                ("live_reviewed_by", "reviewed_by"),
            )
        ],
        sa.CheckConstraint(
            "artifact_id ~ '^sha256:[0-9a-f]{64}$' AND "
            "source_sha256 ~ '^[0-9a-f]{64}$' AND "
            "build_evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_artifact_evidence_digests",
        ),
        sa.CheckConstraint(
            "runtime ~ '^[a-z][a-z0-9-]*/v[1-9][0-9]*$' AND "
            "runtime_protocol = 'bifrost.runtime/v1'",
            name="ck_runtime_artifact_protocol",
        ),
        sa.CheckConstraint(
            "COALESCE(jsonb_typeof(artifact) = 'object' AND "
            "artifact->>'artifact_id' = artifact_id AND "
            "artifact->>'runtime_protocol' = runtime_protocol AND "
            "((artifact->>'kind' = 'native-executable/v1' AND "
            "artifact->>'build_evidence_sha256' = build_evidence_sha256) OR "
            "(artifact->>'kind' = 'interpreted-runtime/v1' AND "
            "NOT (artifact ? 'build_evidence_sha256'))), FALSE)",
            name="ck_runtime_artifact_descriptor_binding",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_schema) = 'object' AND "
            "jsonb_typeof(output_schema) = 'object'",
            name="ck_runtime_artifact_schema_objects",
        ),
        schema="public",
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
            "live_execution_id",
            postgresql.UUID(),
            sa.ForeignKey("public.executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint(
            "live_execution_id", name="uq_runtime_owner_live_execution"
        ),
        sa.UniqueConstraint(
            "execution_id",
            "owner_incarnation_id",
            name="uq_runtime_owner_identity",
        ),
        sa.UniqueConstraint(
            "execution_id",
            "owner_incarnation_id",
            "caller_sha256",
            name="uq_runtime_owner_caller",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id", "workflow_id", "artifact_id"],
            [
                "public.runtime_deployment_artifacts.deployment_id",
                "public.runtime_deployment_artifacts.workflow_id",
                "public.runtime_deployment_artifacts.artifact_id",
            ],
            name="fk_runtime_owner_accepted_artifact",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "live_execution_id IS NULL OR live_execution_id = execution_id",
            name="ck_runtime_owner_live_execution",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(caller_snapshot) = 'object' AND "
            "caller_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_owner_caller_snapshot",
        ),
        schema="public",
    )
    op.create_table(
        "runtime_sessions",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("execution_id", postgresql.UUID(), nullable=False),
        sa.Column("owner_incarnation_id", postgresql.UUID(), nullable=False),
        sa.Column("workflow_attempt_id", postgresql.UUID(), nullable=False),
        sa.Column("claim_token", postgresql.UUID(), nullable=False),
        sa.Column(
            "claim_token_digest",
            sa.String(64),
            sa.Computed(
                "encode(sha256(('16:cred-p1/claim/v1,36:' || claim_token::text || ',')::bytea), 'hex')",
                persisted=True,
            ),
            nullable=False,
        ),
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
            server_default=sa.text("clock_timestamp()"),
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
        sa.UniqueConstraint(
            "id",
            "execution_id",
            "owner_incarnation_id",
            "workflow_attempt_id",
            "claim_token",
            "worker_incarnation_id",
            name="uq_runtime_session_live_fence",
        ),
        sa.UniqueConstraint(
            "id",
            "claim_token_digest",
            "worker_incarnation_id",
            "supervisor_incarnation_id",
            name="uq_runtime_session_sdk_fence",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id", "owner_incarnation_id"],
            [
                "public.runtime_execution_owners.execution_id",
                "public.runtime_execution_owners.owner_incarnation_id",
            ],
            name="fk_runtime_session_owner",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "channel_custody_sha256 ~ '^[0-9a-f]{64}$' AND "
            "binding_sha256 ~ '^[0-9a-f]{64}$' AND prepare_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_session_digests",
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL AND close_reason IS NULL) OR "
            "(closed_at IS NOT NULL AND closed_at >= created_at AND "
            "close_reason IS NOT NULL AND close_reason ~ '^[a-z][a-z0-9_-]{0,63}$')",
            name="ck_runtime_session_close_shape",
        ),
        schema="public",
    )
    op.create_table(
        "runtime_workflow_live_links",
        sa.Column("session_id", postgresql.UUID(), primary_key=True),
        sa.Column("execution_id", postgresql.UUID(), nullable=False),
        sa.Column("owner_incarnation_id", postgresql.UUID(), nullable=False),
        sa.Column("workflow_attempt_id", postgresql.UUID(), nullable=False),
        sa.Column("claim_token", postgresql.UUID(), nullable=False),
        sa.Column("worker_incarnation_id", postgresql.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            [
                "session_id",
                "execution_id",
                "owner_incarnation_id",
                "workflow_attempt_id",
                "claim_token",
                "worker_incarnation_id",
            ],
            [
                "public.runtime_sessions.id",
                "public.runtime_sessions.execution_id",
                "public.runtime_sessions.owner_incarnation_id",
                "public.runtime_sessions.workflow_attempt_id",
                "public.runtime_sessions.claim_token",
                "public.runtime_sessions.worker_incarnation_id",
            ],
            name="fk_runtime_live_common_fence",
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
                "public.workflow_execution_attempts.id",
                "public.workflow_execution_attempts.execution_id",
                "public.workflow_execution_attempts.claim_token",
                "public.workflow_execution_attempts.worker_incarnation_id",
            ],
            name="fk_runtime_live_native_fence",
            ondelete="CASCADE",
        ),
        schema="public",
    )
    _install_identity_guards()
    for table in (
        "runtime_deployment_artifacts",
        "runtime_execution_owners",
        "runtime_sessions",
        "runtime_workflow_live_links",
    ):
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")


def _install_identity_guards() -> None:
    op.execute("""
CREATE FUNCTION public.guard_runtime_artifact_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'retain accepted runtime artifact identity' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.live_deployment_id IS DISTINCT FROM NEW.deployment_id OR
           NEW.live_workflow_id IS DISTINCT FROM NEW.workflow_id OR
           NEW.live_solution_id IS DISTINCT FROM NEW.solution_id OR
           NEW.live_reviewed_by IS DISTINCT FROM NEW.reviewed_by THEN
            RAISE EXCEPTION 'new artifact requires matching live links' USING ERRCODE='23514';
        END IF;
        PERFORM 1 FROM public.solution_deployments d JOIN public.workflows w
          ON w.solution_id=d.solution_id JOIN public.users u ON u.id=NEW.reviewed_by
          JOIN public.solutions s ON s.id=d.solution_id
          WHERE d.id=NEW.deployment_id AND w.id=NEW.workflow_id AND d.solution_id=NEW.solution_id
          FOR UPDATE OF d,w,u,s NOWAIT;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'artifact native relationships differ' USING ERRCODE='23514';
        END IF;
    ELSE
        IF (to_jsonb(NEW)-'live_deployment_id'-'live_workflow_id'-'live_solution_id'-'live_reviewed_by')
           IS DISTINCT FROM
           (to_jsonb(OLD)-'live_deployment_id'-'live_workflow_id'-'live_solution_id'-'live_reviewed_by') OR
           (NEW.live_deployment_id IS DISTINCT FROM OLD.live_deployment_id AND
             (NEW.live_deployment_id IS NOT NULL OR EXISTS (SELECT 1 FROM public.solution_deployments WHERE id=OLD.live_deployment_id))) OR
           (NEW.live_workflow_id IS DISTINCT FROM OLD.live_workflow_id AND
             (NEW.live_workflow_id IS NOT NULL OR EXISTS (SELECT 1 FROM public.workflows WHERE id=OLD.live_workflow_id))) OR
           (NEW.live_solution_id IS DISTINCT FROM OLD.live_solution_id AND
             (NEW.live_solution_id IS NOT NULL OR EXISTS (SELECT 1 FROM public.solutions WHERE id=OLD.live_solution_id))) OR
           (NEW.live_reviewed_by IS DISTINCT FROM OLD.live_reviewed_by AND
             (NEW.live_reviewed_by IS NOT NULL OR EXISTS (SELECT 1 FROM public.users WHERE id=OLD.live_reviewed_by))) THEN
            RAISE EXCEPTION 'artifact identity or live-link reparent' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
""")
    op.execute("""
CREATE TRIGGER runtime_artifact_identity BEFORE INSERT OR UPDATE OR DELETE
 ON public.runtime_deployment_artifacts FOR EACH ROW EXECUTE FUNCTION public.guard_runtime_artifact_identity();
""")
    op.execute("""
REVOKE ALL ON FUNCTION public.guard_runtime_artifact_identity() FROM PUBLIC;
""")
    op.execute("""
CREATE FUNCTION public.guard_runtime_owner_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'retain runtime owner identity' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.live_execution_id IS DISTINCT FROM NEW.execution_id THEN
            RAISE EXCEPTION 'new owner requires live execution' USING ERRCODE='23514';
        END IF;
        PERFORM 1 FROM public.executions e JOIN public.runtime_deployment_artifacts a
          ON a.deployment_id=NEW.deployment_id AND a.workflow_id=NEW.workflow_id AND a.artifact_id=NEW.artifact_id
          WHERE e.id=NEW.execution_id AND e.workflow_id=NEW.workflow_id AND e.solution_deployment_id=NEW.deployment_id
            AND a.live_deployment_id=a.deployment_id AND a.live_workflow_id=a.workflow_id AND a.live_solution_id=a.solution_id
          FOR UPDATE OF e,a NOWAIT;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'owner native source relationships differ' USING ERRCODE='23514';
        END IF;
    ELSE
        IF (to_jsonb(NEW)-'live_execution_id') IS DISTINCT FROM (to_jsonb(OLD)-'live_execution_id') OR
           (NEW.live_execution_id IS DISTINCT FROM OLD.live_execution_id AND
            (NEW.live_execution_id IS NOT NULL OR EXISTS (SELECT 1 FROM public.executions WHERE id=OLD.live_execution_id))) THEN
            RAISE EXCEPTION 'runtime owner identity or live-link reparent' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
""")
    op.execute("""
CREATE TRIGGER runtime_owner_identity BEFORE INSERT OR UPDATE OR DELETE
 ON public.runtime_execution_owners FOR EACH ROW EXECUTE FUNCTION public.guard_runtime_owner_identity();
""")
    op.execute("""
REVOKE ALL ON FUNCTION public.guard_runtime_owner_identity() FROM PUBLIC;
""")
    op.execute("""
CREATE FUNCTION public.guard_runtime_session_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'retain runtime session tombstone' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.closed_at IS NOT NULL OR NEW.close_reason IS NOT NULL THEN
            RAISE EXCEPTION 'new runtime session must be open' USING ERRCODE='23514';
        END IF;
        PERFORM 1 FROM public.workflow_execution_attempts a JOIN public.runtime_execution_owners o
          ON o.execution_id=a.execution_id AND o.owner_incarnation_id=NEW.owner_incarnation_id
          WHERE a.id=NEW.workflow_attempt_id AND a.execution_id=NEW.execution_id
            AND a.claim_token=NEW.claim_token AND a.worker_incarnation_id=NEW.worker_incarnation_id
            AND o.live_execution_id=o.execution_id AND a.completed_at IS NULL
            AND a.status IN ('claimed','running')
          FOR UPDATE OF a,o NOWAIT;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'session native fence differs' USING ERRCODE='23514';
        END IF;
    ELSE
        IF (to_jsonb(NEW)-'closed_at'-'close_reason') IS DISTINCT FROM (to_jsonb(OLD)-'closed_at'-'close_reason') OR
           (OLD.closed_at IS NOT NULL AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN
            RAISE EXCEPTION 'runtime session identity or tombstone changed' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
""")
    op.execute("""
-- Stored generated claim digests are available only after BEFORE triggers.
CREATE TRIGGER runtime_session_identity AFTER INSERT OR UPDATE OR DELETE
 ON public.runtime_sessions FOR EACH ROW EXECUTE FUNCTION public.guard_runtime_session_identity();
""")
    op.execute("""
REVOKE ALL ON FUNCTION public.guard_runtime_session_identity() FROM PUBLIC;
""")
    op.execute("""
CREATE FUNCTION public.guard_runtime_live_link_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM public.workflow_execution_attempts WHERE id=OLD.workflow_attempt_id) THEN
            RAISE EXCEPTION 'live native attempt still exists' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'INSERT' THEN
        PERFORM 1 FROM public.runtime_sessions s JOIN public.runtime_execution_owners o
          ON o.execution_id=s.execution_id AND o.owner_incarnation_id=s.owner_incarnation_id
          JOIN public.workflow_execution_attempts a ON a.id=s.workflow_attempt_id
          WHERE s.id=NEW.session_id AND s.closed_at IS NULL AND o.live_execution_id=o.execution_id
            AND a.execution_id=s.execution_id AND a.claim_token=s.claim_token
            AND a.worker_incarnation_id=s.worker_incarnation_id AND a.completed_at IS NULL
            AND a.status IN ('claimed','running')
          FOR UPDATE OF a,o,s NOWAIT;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'live link requires current open native fence' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
        RAISE EXCEPTION 'runtime live link cannot reparent' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
""")
    op.execute("""
CREATE TRIGGER runtime_live_link_identity BEFORE INSERT OR UPDATE OR DELETE
 ON public.runtime_workflow_live_links FOR EACH ROW EXECUTE FUNCTION public.guard_runtime_live_link_identity();
""")
    op.execute("""
REVOKE ALL ON FUNCTION public.guard_runtime_live_link_identity() FROM PUBLIC;
""")
    op.execute("""
CREATE FUNCTION public.require_runtime_session_live_link() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.runtime_workflow_live_links WHERE session_id=NEW.id) THEN
        RAISE EXCEPTION 'new runtime session requires atomic native live link' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
""")
    op.execute("""
CREATE CONSTRAINT TRIGGER runtime_session_requires_live_link AFTER INSERT
 ON public.runtime_sessions DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION public.require_runtime_session_live_link();
""")
    op.execute("""
REVOKE ALL ON FUNCTION public.require_runtime_session_live_link() FROM PUBLIC;
""")


def downgrade() -> None:
    # Removing identity history is not a traffic rollback. Refuse populated stores.
    bind = op.get_bind()
    occupied = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.runtime_deployment_artifacts) OR "
            "EXISTS (SELECT 1 FROM public.runtime_execution_owners) OR "
            "EXISTS (SELECT 1 FROM public.runtime_sessions) OR "
            "EXISTS (SELECT 1 FROM public.runtime_workflow_live_links)"
        )
    ).scalar_one()
    if occupied:
        raise RuntimeError("Retain common runtime identity before downgrade")
    for table in (
        "runtime_workflow_live_links",
        "runtime_sessions",
        "runtime_execution_owners",
        "runtime_deployment_artifacts",
    ):
        op.drop_table(table, schema="public")
    for function in (
        "require_runtime_session_live_link",
        "guard_runtime_live_link_identity",
        "guard_runtime_session_identity",
        "guard_runtime_owner_identity",
        "guard_runtime_artifact_identity",
    ):
        op.execute(f"DROP FUNCTION public.{function}()")
    op.drop_constraint(
        "uq_workflow_attempt_runtime_fence",
        "workflow_execution_attempts",
        type_="unique",
        schema="public",
    )
