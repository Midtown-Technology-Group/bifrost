"""Retain common Start identity and exact Result payload/receipt evidence.

Storage candidate only. A receipt row does not grant finalization authority:
the future Rust owner must validate wire data and commit it with the existing
domain projection under the reviewed admission, role and session locks.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261010_runtime_receipts"
down_revision = "20261010_runtime_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_starts",
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
    op.create_table(
        "runtime_report_receipts",
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
            "octet_length(raw_result_payload) BETWEEN 1 AND 16777216 AND "
            "result_sha256 = encode(sha256(raw_result_payload), 'hex')",
            name="ck_runtime_receipt_exact_bytes",
        ),
        sa.CheckConstraint(
            "(disposition = 'accepted' AND winner = 'result') OR "
            "(disposition = 'retained' AND winner IN ('cancel','failure'))",
            name="ck_runtime_receipt_disposition",
        ),
    )
    op.execute("""
        CREATE FUNCTION enforce_runtime_report_immutability() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
                RAISE EXCEPTION 'runtime Start and receipt evidence is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in ("runtime_starts", "runtime_report_receipts"):
        op.execute(f"""
            CREATE TRIGGER runtime_report_immutable BEFORE UPDATE OR DELETE
            ON {table} FOR EACH ROW EXECUTE FUNCTION enforce_runtime_report_immutability()
        """)
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION enforce_runtime_report_immutability() FROM PUBLIC"
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM runtime_starts) OR "
                "EXISTS (SELECT 1 FROM runtime_report_receipts)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Retain runtime Start/receipt evidence before downgrade")
    op.drop_table("runtime_report_receipts")
    op.drop_table("runtime_starts")
    op.execute("DROP FUNCTION enforce_runtime_report_immutability()")
