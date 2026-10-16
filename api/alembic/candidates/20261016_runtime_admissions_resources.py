"""Common provision/release and physical-resource evidence — REVIEW ONLY, C4.

Extracted from 56d1fb006d220b9dbb0747f1cca4def18677f918 runtime_admissions and
runtime_resource_settlements. Outside active history; C1-C5, writer/authority,
physical-custody and sensitive-payload disposition gates remain required.
Release means execution delivery after provisioning, never physical retirement.
No fact here authorizes spawn, replay, source release or owner reassignment.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261016_runtime_resources"
down_revision = "20261016_runtime_grants"
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
        schema="public",
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
                "public.workflow_runtime_sdk_grants.id",
                "public.workflow_runtime_sdk_grants.runtime_session_id",
                "public.workflow_runtime_sdk_grants.committed_start_id",
                "public.workflow_runtime_sdk_grants.start_message_id",
                "public.workflow_runtime_sdk_grants.operations_digest",
                "public.workflow_runtime_sdk_grants.initial_access_expires_at",
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
                "public.runtime_admissions.id",
                "public.runtime_admissions.purpose",
                "public.runtime_admissions.session_id",
                "public.runtime_admissions.grant_id",
                "public.runtime_admissions.delivery_id",
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
        schema="public",
    )
    op.execute("""
        CREATE TRIGGER runtime_admission_immutable BEFORE UPDATE OR DELETE
        ON public.runtime_admissions FOR EACH ROW
        EXECUTE FUNCTION public.enforce_runtime_report_immutability()
    """)
    op.execute("REVOKE ALL ON public.runtime_admissions FROM PUBLIC")
    op.create_table(
        "runtime_resource_settlements",
        sa.Column("session_id", postgresql.UUID(), primary_key=True),
        sa.Column("execution_id", postgresql.UUID(), nullable=False),
        sa.Column("owner_incarnation_id", postgresql.UUID(), nullable=False),
        sa.Column("workflow_attempt_id", postgresql.UUID(), nullable=False),
        sa.Column("raw_observation", sa.LargeBinary(), nullable=False),
        sa.Column("observation_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.ForeignKeyConstraint(
            [
                "session_id",
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
            name="fk_runtime_resource_session_identity",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "octet_length(raw_observation) BETWEEN 1 AND 65536 AND "
            "observation_sha256 = encode(sha256(raw_observation), 'hex')",
            name="ck_runtime_resource_exact_bytes",
        ),
        schema="public",
    )
    # Closed is a necessary retained fact, not proof of process/source custody.
    # C5 and the trusted physical observer must supply authority separately.
    op.execute("""
        CREATE FUNCTION public.enforce_runtime_resource_closed() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE closed timestamptz;
        BEGIN
            SELECT closed_at INTO closed FROM public.runtime_sessions
                WHERE id=NEW.session_id AND execution_id=NEW.execution_id
                AND owner_incarnation_id=NEW.owner_incarnation_id
                AND workflow_attempt_id=NEW.workflow_attempt_id FOR KEY SHARE NOWAIT;
            IF closed IS NULL THEN
                RAISE EXCEPTION 'resource settlement requires retained closed session'
                    USING ERRCODE='check_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER runtime_resource_closed BEFORE INSERT
        ON public.runtime_resource_settlements FOR EACH ROW
        EXECUTE FUNCTION public.enforce_runtime_resource_closed()
    """)
    op.execute("""
        CREATE TRIGGER runtime_resource_immutable BEFORE UPDATE OR DELETE
        ON public.runtime_resource_settlements FOR EACH ROW
        EXECUTE FUNCTION public.enforce_runtime_report_immutability()
    """)
    op.execute("REVOKE ALL ON public.runtime_resource_settlements FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION public.enforce_runtime_resource_closed() FROM PUBLIC"
    )


def downgrade() -> None:
    bind = op.get_bind()
    isolation = bind.execute(sa.text("SHOW transaction_isolation")).scalar_one()
    if isolation != "read committed":
        raise RuntimeError(
            "Runtime admission/resource downgrade requires read committed isolation"
        )
    bind.execute(
        sa.text(
            "LOCK TABLE public.workflow_runtime_sdk_grants, public.runtime_sessions, "
            "public.runtime_admissions, public.runtime_resource_settlements "
            "IN ACCESS EXCLUSIVE MODE NOWAIT"
        )
    )
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.runtime_admissions) OR "
            "EXISTS (SELECT 1 FROM public.runtime_resource_settlements)"
        )
    ).scalar_one():
        raise RuntimeError(
            "Retain runtime provision/release and resource evidence before downgrade"
        )
    op.drop_table("runtime_resource_settlements", schema="public")
    op.execute("DROP FUNCTION public.enforce_runtime_resource_closed()")
    op.drop_table("runtime_admissions", schema="public")
    op.drop_constraint(
        "uq_runtime_sdk_grant_admission_identity",
        "workflow_runtime_sdk_grants",
        type_="unique",
        schema="public",
    )
