"""Retain immutable common process/source retirement observations.

Storage candidate only; no operational role or production dispatcher is added.
The original owner must obtain actual opaque custody evidence before insertion.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261011_runtime_resources"
down_revision = "20261010_runtime_fences"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
                "runtime_sessions.id",
                "runtime_sessions.execution_id",
                "runtime_sessions.owner_incarnation_id",
                "runtime_sessions.workflow_attempt_id",
            ],
            name="fk_runtime_resource_session_identity",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "octet_length(raw_observation) BETWEEN 1 AND 65536 AND "
            "observation_sha256 = encode(sha256(raw_observation), 'hex')",
            name="ck_runtime_resource_exact_bytes",
        ),
    )
    op.execute("""
        CREATE FUNCTION enforce_runtime_resource_closed() RETURNS trigger
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
        ON runtime_resource_settlements FOR EACH ROW
        EXECUTE FUNCTION enforce_runtime_resource_closed()
    """)
    op.execute("""
        CREATE TRIGGER runtime_resource_immutable BEFORE UPDATE OR DELETE
        ON runtime_resource_settlements FOR EACH ROW
        EXECUTE FUNCTION enforce_runtime_report_immutability()
    """)
    op.execute("REVOKE ALL ON runtime_resource_settlements FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION enforce_runtime_resource_closed() FROM PUBLIC")


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM runtime_resource_settlements)"))
        .scalar()
    ):
        raise RuntimeError(
            "Retain runtime resource settlement evidence before downgrade"
        )
    op.drop_table("runtime_resource_settlements")
    op.execute("DROP FUNCTION enforce_runtime_resource_closed()")
