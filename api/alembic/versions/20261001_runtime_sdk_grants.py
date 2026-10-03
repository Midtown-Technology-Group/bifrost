"""Private closed workflow SDK grants, without source permissions.

Revision ID: 20261001_runtime_sdk_grants
Revises: 20261001_solution_src_account
"""

import sqlalchemy as sa
from alembic import op

revision = "20261001_runtime_sdk_grants"
down_revision = "20261001_solution_src_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_runtime_sdk_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column(
            "workflow_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("workflow_execution_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("solution_deployments.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
            for column in (
                "source_manifest_digest",
                "source_resolution_digest",
            )
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
    )
    op.create_table(
        "workflow_runtime_sdk_grant_operations",
        sa.Column(
            "grant_id",
            sa.Uuid(),
            sa.ForeignKey("workflow_runtime_sdk_grants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("integration_name", sa.String(255), nullable=False),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("scope_organization_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_organization_id", sa.Uuid(), nullable=True),
        sa.Column("solution_install_id", sa.Uuid(), nullable=True),
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


def downgrade() -> None:
    op.drop_table("workflow_runtime_sdk_grant_operations")
    op.drop_table("workflow_runtime_sdk_grants")
