"""Common immutable provision/release evidence, not permission to spawn.

Fresh eligibility, close serialization and observed commit are owner/guardian
requirements. These storage constraints bind the facts but cannot prove physical
custody or make a database commit atomic with process creation.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261010_runtime_admissions"
down_revision = "20261010_runtime_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_runtime_sdk_grant_admission_identity",
        "workflow_runtime_sdk_grants",
        [
            "id",
            "runtime_session_id",
            "committed_start_id",
            "start_message_id",
            "operations_digest",
            "initial_access_expires_at",
        ],
    )
    op.create_table(
        "runtime_admissions",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("session_id", postgresql.UUID(), nullable=False),
        sa.Column("committed_start_id", postgresql.UUID(), nullable=False),
        sa.Column("start_message_id", postgresql.UUID(), nullable=False),
        sa.Column("grant_id", postgresql.UUID(), nullable=False),
        sa.Column("delivery_id", postgresql.UUID(), nullable=False),
        sa.Column("operations_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provision_admission_id", postgresql.UUID()),
        sa.Column("provision_purpose", sa.String(16)),
        sa.Column("frontier_sha256", sa.String(64), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "session_id", "purpose", name="uq_runtime_admission_purpose"
        ),
        sa.UniqueConstraint(
            "id",
            "purpose",
            "session_id",
            "grant_id",
            "delivery_id",
            name="uq_runtime_admission_delivery_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "grant_id",
                "session_id",
                "committed_start_id",
                "start_message_id",
                "operations_digest",
                "expires_at",
            ],
            [
                "workflow_runtime_sdk_grants.id",
                "workflow_runtime_sdk_grants.runtime_session_id",
                "workflow_runtime_sdk_grants.committed_start_id",
                "workflow_runtime_sdk_grants.start_message_id",
                "workflow_runtime_sdk_grants.operations_digest",
                "workflow_runtime_sdk_grants.initial_access_expires_at",
            ],
            name="fk_runtime_admission_exact_grant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "provision_admission_id",
                "provision_purpose",
                "session_id",
                "grant_id",
                "delivery_id",
            ],
            [
                "runtime_admissions.id",
                "runtime_admissions.purpose",
                "runtime_admissions.session_id",
                "runtime_admissions.grant_id",
                "runtime_admissions.delivery_id",
            ],
            name="fk_runtime_release_exact_provision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(purpose = 'provision' AND provision_admission_id IS NULL AND provision_purpose IS NULL) OR "
            "(purpose = 'release' AND provision_admission_id IS NOT NULL AND provision_purpose IS NOT NULL AND provision_purpose = 'provision')",
            name="ck_runtime_admission_purpose",
        ),
        sa.CheckConstraint(
            "frontier_sha256 ~ '^[0-9a-f]{64}$' AND operations_digest ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_admission_digests",
        ),
        sa.CheckConstraint(
            "isfinite(admitted_at) AND isfinite(expires_at) AND admitted_at < expires_at",
            name="ck_runtime_admission_finite_budget",
        ),
    )
    op.execute("""
        CREATE TRIGGER runtime_admission_immutable BEFORE UPDATE OR DELETE
        ON runtime_admissions FOR EACH ROW EXECUTE FUNCTION enforce_runtime_report_immutability()
    """)
    op.execute("REVOKE ALL ON runtime_admissions FROM PUBLIC")


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM runtime_admissions)"))
        .scalar()
    ):
        raise RuntimeError("Retain runtime provision/release evidence before downgrade")
    op.drop_table("runtime_admissions")
    op.drop_constraint(
        "uq_runtime_sdk_grant_admission_identity",
        "workflow_runtime_sdk_grants",
        type_="unique",
    )
