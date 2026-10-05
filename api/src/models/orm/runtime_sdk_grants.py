"""Private, attempt-bound SDK capabilities; no runtime source permission."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkflowRuntimeSDKGrant(Base):
    __tablename__ = "workflow_runtime_sdk_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_execution_attempts.id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[UUID] = mapped_column(nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_incarnation_id: Mapped[UUID] = mapped_column(nullable=False)
    supervisor_incarnation_id: Mapped[UUID] = mapped_column(nullable=False)
    runtime_session_id: Mapped[UUID] = mapped_column(nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    credential_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    initial_access_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    caller_user_id: Mapped[UUID] = mapped_column(nullable=False)
    caller_organization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    effective_organization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    caller_email: Mapped[str] = mapped_column(String(320), nullable=False)
    caller_name: Mapped[str] = mapped_column(String(255), nullable=False)
    caller_admin: Mapped[int] = mapped_column(Integer, nullable=False)
    caller_provider: Mapped[int] = mapped_column(Integer, nullable=False)
    caller_external: Mapped[int] = mapped_column(Integer, nullable=False)
    caller_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(nullable=False)
    solution_install_id: Mapped[UUID] = mapped_column(nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("solution_deployments.id", ondelete="CASCADE"), nullable=False
    )
    source_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    source_resolution_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    source_global_permission: Mapped[int] = mapped_column(Integer, nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    operations_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    grant_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revocation_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("workflow_attempt_id", name="uq_runtime_sdk_grant_attempt"),
        UniqueConstraint("runtime_session_id", name="uq_runtime_sdk_grant_session"),
        CheckConstraint(
            "schema_version = 'cred-p1/v1' AND source_kind = 'solution-deployment'",
            name="ck_runtime_sdk_grant_schema",
        ),
        CheckConstraint(
            "attempt_number >= 1 AND timeout_seconds >= 0",
            name="ck_runtime_sdk_grant_numbers",
        ),
        CheckConstraint(
            "caller_admin IN (0,1) AND caller_provider IN (0,1) AND caller_external IN (0,1) AND source_global_permission IN (0,1)",
            name="ck_runtime_sdk_grant_flags",
        ),
        CheckConstraint(
            "((timeout_seconds = 0 AND credential_deadline IS NULL) OR (timeout_seconds > 0 AND credential_deadline IS NOT NULL AND initial_access_expires_at = credential_deadline))",
            name="ck_runtime_sdk_grant_deadline",
        ),
        CheckConstraint(
            "((revoked_at IS NULL AND revocation_reason IS NULL) OR (revoked_at IS NOT NULL AND revocation_reason IS NOT NULL AND revocation_reason IN ('session_closed','supervisor_replaced','explicit_revoke')))",
            name="ck_runtime_sdk_grant_revocation",
        ),
        CheckConstraint(
            "octet_length(caller_email) <= 320 AND octet_length(caller_name) <= 255",
            name="ck_runtime_sdk_grant_text_bytes",
        ),
        *tuple(
            CheckConstraint(
                f"{column} ~ '^[0-9a-f]{{64}}$'", name=f"ck_runtime_sdk_{column}"
            )
            for column in (
                "claim_token_digest",
                "caller_snapshot_digest",
                "source_digest",
                "operations_digest",
                "grant_digest",
            )
        ),
        *tuple(
            CheckConstraint(
                f"{column} ~ '^sha256:[0-9a-f]{{64}}$'", name=f"ck_runtime_sdk_{column}"
            )
            for column in (
                "source_manifest_digest",
                "source_resolution_digest",
            )
        ),
        *tuple(
            CheckConstraint(
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
        ),
    )


class WorkflowRuntimeSDKGrantOperation(Base):
    __tablename__ = "workflow_runtime_sdk_grant_operations"

    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_runtime_sdk_grants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    integration_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_organization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    resolved_organization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    solution_install_id: Mapped[UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "grant_id", "operation", name="uq_runtime_sdk_grant_operation"
        ),
        CheckConstraint(
            "ordinal >= 0 AND ordinal < 2", name="ck_runtime_sdk_operation_ordinal"
        ),
        CheckConstraint(
            "operation IN ('integration-get','mapping-get') AND scope_kind IN ('default','global','organization')",
            name="ck_runtime_sdk_operation_kind",
        ),
        CheckConstraint(
            "((scope_kind = 'organization' AND scope_organization_id IS NOT NULL AND resolved_organization_id IS NOT NULL AND scope_organization_id = resolved_organization_id) OR (scope_kind != 'organization' AND scope_organization_id IS NULL)) AND (scope_kind != 'global' OR resolved_organization_id IS NULL)",
            name="ck_runtime_sdk_operation_scope",
        ),
        CheckConstraint(
            "operation != 'mapping-get' OR solution_install_id IS NULL",
            name="ck_runtime_sdk_operation_solution",
        ),
        CheckConstraint(
            "octet_length(integration_name) BETWEEN 1 AND 255",
            name="ck_runtime_sdk_operation_name_bytes",
        ),
    )
