"""Internal common runtime records; no workload lifecycle API or dispatch.

Keep Alembic metadata aligned with the isolated schema candidates. Application
handlers do not write these records; future owner transactions require the
separately verified role, admission, custody and lock-order gates.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from src.models.orm.base import Base


class RuntimeDeploymentArtifact(Base):
    __table__ = sa.Table(
        "runtime_deployment_artifacts",
        Base.metadata,
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
            "COALESCE(jsonb_typeof(artifact) = 'object' AND artifact->>'kind' = 'native-executable/v1' AND artifact->>'artifact_id' = artifact_id AND artifact->>'runtime_protocol' = runtime_protocol AND artifact->>'build_evidence_sha256' = build_evidence_sha256, FALSE)",
            name="ck_runtime_artifact_descriptor_binding",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_schema) = 'object' AND jsonb_typeof(output_schema) = 'object'",
            name="ck_runtime_artifact_schema_objects",
        ),
    )


class RuntimeExecutionOwner(Base):
    __table__ = sa.Table(
        "runtime_execution_owners",
        Base.metadata,
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
            "jsonb_typeof(caller_snapshot) = 'object' AND caller_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_owner_caller_snapshot",
        ),
    )


class RuntimeSession(Base):
    __table__ = sa.Table(
        "runtime_sessions",
        Base.metadata,
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
            "channel_custody_sha256 ~ '^[0-9a-f]{64}$' AND binding_sha256 ~ '^[0-9a-f]{64}$' AND prepare_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_session_digests",
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL AND close_reason IS NULL) OR (closed_at IS NOT NULL AND closed_at >= created_at AND close_reason IS NOT NULL AND close_reason ~ '^[a-z][a-z0-9_-]{0,63}$')",
            name="ck_runtime_session_close_shape",
        ),
    )


class RuntimeStart(Base):
    __table__ = sa.Table(
        "runtime_starts",
        Base.metadata,
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("session_id", postgresql.UUID(), nullable=False),
        sa.Column("execution_id", postgresql.UUID(), nullable=False),
        sa.Column("owner_incarnation_id", postgresql.UUID(), nullable=False),
        sa.Column("workflow_attempt_id", postgresql.UUID(), nullable=False),
        sa.Column("start_message_id", postgresql.UUID(), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("context_sha256", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_utc", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("session_id", name="uq_runtime_start_session"),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "start_message_id",
            name="uq_runtime_start_message_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "session_id",
                "execution_id",
                "owner_incarnation_id",
                "workflow_attempt_id",
            ],
            [
                "runtime_sessions.id",
                "runtime_sessions.execution_id",
                "runtime_sessions.owner_incarnation_id",
                "runtime_sessions.workflow_attempt_id",
            ],
            name="fk_runtime_start_session_identity",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "input_sha256 ~ '^[0-9a-f]{64}$' AND context_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_start_input_context_digests",
        ),
        sa.CheckConstraint(
            "deadline_utc IS NULL OR deadline_utc > started_at",
            name="ck_runtime_start_deadline",
        ),
    )


class RuntimeReportReceipt(Base):
    __table__ = sa.Table(
        "runtime_report_receipts",
        Base.metadata,
        sa.Column("session_id", postgresql.UUID(), primary_key=True),
        sa.Column("result_message_id", postgresql.UUID(), primary_key=True),
        sa.Column("committed_start_id", postgresql.UUID(), nullable=False),
        sa.Column("start_message_id", postgresql.UUID(), nullable=False),
        sa.Column("raw_result_payload", sa.LargeBinary(), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("decision_id", postgresql.UUID(), nullable=False),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("winner", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["committed_start_id", "session_id", "start_message_id"],
            [
                "runtime_starts.id",
                "runtime_starts.session_id",
                "runtime_starts.start_message_id",
            ],
            name="fk_runtime_receipt_start_message",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "octet_length(raw_result_payload) BETWEEN 1 AND 16777216 AND result_sha256 = encode(sha256(raw_result_payload), 'hex')",
            name="ck_runtime_receipt_exact_bytes",
        ),
        sa.CheckConstraint(
            "(disposition = 'accepted' AND winner = 'result') OR (disposition = 'retained' AND winner IN ('cancel','failure'))",
            name="ck_runtime_receipt_disposition",
        ),
    )
