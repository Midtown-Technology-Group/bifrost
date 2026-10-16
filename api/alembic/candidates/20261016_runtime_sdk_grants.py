"""Closed SDK grant/common identity storage — REVIEW ONLY, C3.

CRED authority: 3918d608a2d497d1ca8424fd4229d191323e2d7a.
Common extraction: 56d1fb006d220b9dbb0747f1cca4def18677f918.
Retain all 35 CRED grant fields and 8 operation fields, adding only the common
owner/Start identities. Native source and ordinal eligibility must be checked
under the complete C5 admission locks; historical FKs never pin native rows.
No issuer, renewal, ingress, role or runtime authority is enabled. Caller payload
retention and C1-C5 application/activation remain held. Outside active history.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_runtime_grants"
down_revision = "20261016_runtime_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_runtime_owner_source",
        "runtime_execution_owners",
        ["execution_id", "owner_incarnation_id", "workflow_id", "deployment_id"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_runtime_artifact_install",
        "runtime_deployment_artifacts",
        ["deployment_id", "workflow_id", "solution_id"],
        schema="public",
    )
    op.create_table(
        "workflow_runtime_sdk_grants",
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
                "public.runtime_sessions.id",
                "public.runtime_sessions.claim_token_digest",
                "public.runtime_sessions.worker_incarnation_id",
                "public.runtime_sessions.supervisor_incarnation_id",
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
                "public.runtime_sessions.id",
                "public.runtime_sessions.execution_id",
                "public.runtime_sessions.owner_incarnation_id",
                "public.runtime_sessions.workflow_attempt_id",
            ],
            name="fk_runtime_sdk_session_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["committed_start_id", "runtime_session_id", "start_message_id"],
            [
                "public.runtime_starts.id",
                "public.runtime_starts.session_id",
                "public.runtime_starts.start_message_id",
            ],
            name="fk_runtime_sdk_committed_start",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id", "owner_incarnation_id", "caller_snapshot_digest"],
            [
                "public.runtime_execution_owners.execution_id",
                "public.runtime_execution_owners.owner_incarnation_id",
                "public.runtime_execution_owners.caller_sha256",
            ],
            name="fk_runtime_sdk_owner_caller",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "committed_start_id",
                "runtime_session_id",
                "start_message_id",
                "started_at",
            ],
            [
                "public.runtime_starts.id",
                "public.runtime_starts.session_id",
                "public.runtime_starts.start_message_id",
                "public.runtime_starts.started_at",
            ],
            name="fk_runtime_sdk_start_clock",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id", "owner_incarnation_id", "workflow_id", "source_id"],
            [
                "public.runtime_execution_owners.execution_id",
                "public.runtime_execution_owners.owner_incarnation_id",
                "public.runtime_execution_owners.workflow_id",
                "public.runtime_execution_owners.deployment_id",
            ],
            name="fk_runtime_sdk_execution_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "workflow_id", "solution_install_id"],
            [
                "public.runtime_deployment_artifacts.deployment_id",
                "public.runtime_deployment_artifacts.workflow_id",
                "public.runtime_deployment_artifacts.solution_id",
            ],
            name="fk_runtime_sdk_source_install",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "issued_at >= started_at AND initial_access_expires_at > issued_at AND isfinite(initial_access_expires_at)",
            name="ck_runtime_sdk_finite_access",
        ),
        schema="public",
    )
    op.create_table(
        "workflow_runtime_sdk_grant_operations",
        sa.Column(
            "grant_id",
            sa.Uuid(),
            sa.ForeignKey("public.workflow_runtime_sdk_grants.id", ondelete="RESTRICT"),
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
                "public.workflow_runtime_sdk_grants.id",
                "public.workflow_runtime_sdk_grants.solution_install_id",
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
        schema="public",
    )

    op.execute("""
        CREATE FUNCTION public.enforce_runtime_sdk_evidence() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'runtime SDK evidence must be retained'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_TABLE_NAME = 'workflow_runtime_sdk_grants' THEN
                IF (to_jsonb(NEW) - 'revoked_at' - 'revocation_reason') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'revoked_at' - 'revocation_reason') OR
                   (OLD.revoked_at IS NOT NULL AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN
                    RAISE EXCEPTION 'runtime SDK identity and revocation are immutable'
                        USING ERRCODE = 'check_violation';
                END IF;
            ELSIF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
                RAISE EXCEPTION 'runtime SDK operations are immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in (
        "workflow_runtime_sdk_grants",
        "workflow_runtime_sdk_grant_operations",
    ):
        op.execute(f"""
            CREATE TRIGGER runtime_sdk_evidence BEFORE UPDATE OR DELETE
            ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.enforce_runtime_sdk_evidence()
        """)
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION public.enforce_runtime_sdk_evidence() FROM PUBLIC"
    )


def downgrade() -> None:
    bind = op.get_bind()
    isolation = bind.execute(sa.text("SHOW transaction_isolation")).scalar_one()
    if isolation != "read committed":
        raise RuntimeError("Runtime grant downgrade requires read committed isolation")
    bind.execute(
        sa.text(
            "LOCK TABLE public.runtime_deployment_artifacts, public.runtime_execution_owners, "
            "public.workflow_runtime_sdk_grants, public.workflow_runtime_sdk_grant_operations "
            "IN ACCESS EXCLUSIVE MODE NOWAIT"
        )
    )
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.workflow_runtime_sdk_grants) OR "
            "EXISTS (SELECT 1 FROM public.workflow_runtime_sdk_grant_operations)"
        )
    ).scalar_one():
        raise RuntimeError(
            "Retain runtime SDK grant and operation evidence before downgrade"
        )
    op.drop_table("workflow_runtime_sdk_grant_operations", schema="public")
    op.drop_table("workflow_runtime_sdk_grants", schema="public")
    op.execute("DROP FUNCTION public.enforce_runtime_sdk_evidence()")
    op.drop_constraint(
        "uq_runtime_artifact_install",
        "runtime_deployment_artifacts",
        type_="unique",
        schema="public",
    )
    op.drop_constraint(
        "uq_runtime_owner_source",
        "runtime_execution_owners",
        type_="unique",
        schema="public",
    )
