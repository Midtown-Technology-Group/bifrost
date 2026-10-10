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
            server_default=sa.text("NOW()"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("close_reason", sa.String(64)),
        sa.UniqueConstraint("workflow_attempt_id", name="uq_runtime_session_attempt"),
        sa.UniqueConstraint(
            "id",
            "claim_token_digest",
            "worker_incarnation_id",
            "supervisor_incarnation_id",
            name="uq_runtime_session_sdk_fence",
        ),
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


class WorkflowRuntimeSDKGrant(Base):
    __table__ = sa.Table(
        "workflow_runtime_sdk_grants",
        Base.metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("workflow_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("claim_token_digest", sa.String(64), nullable=False),
        sa.Column("worker_incarnation_id", sa.Uuid(), nullable=False),
        sa.Column("supervisor_incarnation_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_session_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("credential_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "initial_access_expires_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("caller_user_id", sa.Uuid(), nullable=False),
        sa.Column("caller_organization_id", sa.Uuid(), nullable=True),
        sa.Column("effective_organization_id", sa.Uuid(), nullable=True),
        sa.Column("caller_email", sa.String(320), nullable=False),
        sa.Column("caller_name", sa.String(255), nullable=False),
        sa.Column("caller_admin", sa.Integer(), nullable=False),
        sa.Column("caller_provider", sa.Integer(), nullable=False),
        sa.Column("caller_external", sa.Integer(), nullable=False),
        sa.Column("caller_snapshot_digest", sa.String(64), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("solution_install_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_manifest_digest", sa.String(71), nullable=False),
        sa.Column("source_resolution_digest", sa.String(71), nullable=False),
        sa.Column("source_global_permission", sa.Integer(), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("operations_digest", sa.String(64), nullable=False),
        sa.Column("grant_digest", sa.String(64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(32), nullable=True),
        sa.UniqueConstraint("workflow_attempt_id", name="uq_runtime_sdk_grant_attempt"),
        sa.UniqueConstraint("runtime_session_id", name="uq_runtime_sdk_grant_session"),
        sa.UniqueConstraint(
            "id", "solution_install_id", name="uq_runtime_sdk_grant_install"
        ),
        sa.CheckConstraint(
            "schema_version = 'cred-p1/v1' AND source_kind = 'solution-deployment'",
            name="ck_runtime_sdk_grant_schema",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1 AND timeout_seconds >= 0",
            name="ck_runtime_sdk_grant_numbers",
        ),
        sa.CheckConstraint(
            "caller_admin IN (0,1) AND caller_provider IN (0,1) AND caller_external IN (0,1) AND source_global_permission IN (0,1)",
            name="ck_runtime_sdk_grant_flags",
        ),
        sa.CheckConstraint(
            "((timeout_seconds = 0 AND credential_deadline IS NULL) OR (timeout_seconds > 0 AND credential_deadline IS NOT NULL AND initial_access_expires_at = credential_deadline))",
            name="ck_runtime_sdk_grant_deadline",
        ),
        sa.CheckConstraint(
            "((revoked_at IS NULL AND revocation_reason IS NULL) OR (revoked_at IS NOT NULL AND revocation_reason IS NOT NULL AND revocation_reason IN ('session_closed','supervisor_replaced','explicit_revoke')))",
            name="ck_runtime_sdk_grant_revocation",
        ),
        sa.CheckConstraint(
            "octet_length(caller_email) <= 320 AND octet_length(caller_name) <= 255",
            name="ck_runtime_sdk_grant_text_bytes",
        ),
        *[
            sa.CheckConstraint(
                f"{column} ~ '^[0-9a-f]{{64}}$'", name=f"ck_runtime_sdk_{column}"
            )
            for column in (
                "claim_token_digest",
                "caller_snapshot_digest",
                "source_digest",
                "operations_digest",
                "grant_digest",
            )
        ],
        *[
            sa.CheckConstraint(
                f"{column} ~ '^sha256:[0-9a-f]{{64}}$'", name=f"ck_runtime_sdk_{column}"
            )
            for column in ("source_manifest_digest", "source_resolution_digest")
        ],
        *[
            sa.CheckConstraint(
                f"{column} IS NULL OR ({column} >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND {column} <= TIMESTAMPTZ '9999-12-31 23:59:59.999999+00')",
                name=f"ck_runtime_sdk_time_{column}",
            )
            for column in (
                "started_at",
                "issued_at",
                "credential_deadline",
                "initial_access_expires_at",
                "revoked_at",
            )
        ],
        sa.Column("owner_incarnation_id", sa.Uuid(), nullable=False),
        sa.Column("committed_start_id", sa.Uuid(), nullable=False),
        sa.Column("start_message_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            [
                "runtime_session_id",
                "claim_token_digest",
                "worker_incarnation_id",
                "supervisor_incarnation_id",
            ],
            [
                "runtime_sessions.id",
                "runtime_sessions.claim_token_digest",
                "runtime_sessions.worker_incarnation_id",
                "runtime_sessions.supervisor_incarnation_id",
            ],
            name="fk_runtime_sdk_session_fence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "runtime_session_id",
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
            name="fk_runtime_sdk_session_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["committed_start_id", "runtime_session_id", "start_message_id"],
            [
                "runtime_starts.id",
                "runtime_starts.session_id",
                "runtime_starts.start_message_id",
            ],
            name="fk_runtime_sdk_committed_start",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id", "workflow_id", "source_id"],
            [
                "executions.id",
                "executions.workflow_id",
                "executions.solution_deployment_id",
            ],
            name="fk_runtime_sdk_execution_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "solution_install_id"],
            ["solution_deployments.id", "solution_deployments.solution_id"],
            name="fk_runtime_sdk_source_install",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "issued_at >= started_at AND initial_access_expires_at > issued_at AND isfinite(initial_access_expires_at)",
            name="ck_runtime_sdk_finite_access",
        ),
    )


class WorkflowRuntimeSDKGrantOperation(Base):
    __table__ = sa.Table(
        "workflow_runtime_sdk_grant_operations",
        Base.metadata,
        sa.Column(
            "grant_id",
            sa.Uuid(),
            sa.ForeignKey("workflow_runtime_sdk_grants.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("integration_name", sa.String(255), nullable=False),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("scope_organization_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_organization_id", sa.Uuid(), nullable=True),
        sa.Column("solution_install_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["grant_id", "solution_install_id"],
            [
                "workflow_runtime_sdk_grants.id",
                "workflow_runtime_sdk_grants.solution_install_id",
            ],
            name="fk_runtime_sdk_operation_install",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "grant_id", "operation", name="uq_runtime_sdk_grant_operation"
        ),
        sa.CheckConstraint(
            "ordinal >= 0 AND ordinal < 2", name="ck_runtime_sdk_operation_ordinal"
        ),
        sa.CheckConstraint(
            "operation IN ('integration-get','mapping-get') AND scope_kind IN ('default','global','organization')",
            name="ck_runtime_sdk_operation_kind",
        ),
        sa.CheckConstraint(
            "((scope_kind = 'organization' AND scope_organization_id IS NOT NULL AND resolved_organization_id IS NOT NULL AND scope_organization_id = resolved_organization_id) OR (scope_kind != 'organization' AND scope_organization_id IS NULL)) AND (scope_kind != 'global' OR resolved_organization_id IS NULL)",
            name="ck_runtime_sdk_operation_scope",
        ),
        sa.CheckConstraint(
            "operation != 'mapping-get' OR solution_install_id IS NULL",
            name="ck_runtime_sdk_operation_solution",
        ),
        sa.CheckConstraint(
            "octet_length(integration_name) BETWEEN 1 AND 255",
            name="ck_runtime_sdk_operation_name_bytes",
        ),
    )
