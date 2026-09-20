"""Add inert PostgreSQL work-delivery storage; Rabbit remains the default."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260919_postgres_delivery"
down_revision = "20260918_ws_release_retirement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("queue_name", sa.String(100), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("encrypted_envelope", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(255)),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("claim_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('queued', 'claimed', 'completed', 'poison', 'interrupted')",
            name="ck_work_deliveries_status",
        ),
        sa.CheckConstraint(
            "(status = 'claimed' AND lease_token IS NOT NULL AND "
            "lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'claimed' AND lease_token IS NULL AND "
            "lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_work_deliveries_lease",
        ),
    )
    op.create_index(
        "ix_work_deliveries_completed",
        "work_deliveries",
        ["settled_at", "id"],
        postgresql_where=sa.text("status = 'completed'"),
    )
    op.create_index(
        "ix_work_deliveries_claim",
        "work_deliveries",
        ["queue_name", "available_at", "id"],
        postgresql_where=sa.text("status IN ('queued', 'interrupted')"),
    )
    op.create_index(
        "ix_work_deliveries_expired",
        "work_deliveries",
        ["lease_expires_at", "id"],
        postgresql_where=sa.text("status = 'claimed'"),
    )
    op.create_index(
        "uq_work_deliveries_active",
        "work_deliveries",
        ["queue_name", "message_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'claimed', 'interrupted')"),
    )


def downgrade() -> None:
    # Rolling back application images does not require removing this additive
    # table. Refuse a schema downgrade that would discard retained work/evidence.
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM work_deliveries)")
    ).scalar():
        raise RuntimeError(
            "work_deliveries is not empty; retain it during image rollback"
        )
    op.drop_table("work_deliveries")
