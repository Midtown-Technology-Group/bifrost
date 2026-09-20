"""Add fenced package fanout fields to worker control commands."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260919_package_worker_controls"
down_revision = "20260919_postgres_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "worker_control_commands",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "worker_control_commands",
        sa.Column(
            "target_incarnation_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column(
        "worker_control_commands",
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "worker_control_commands", sa.Column("payload", sa.JSON(), nullable=True)
    )
    op.create_index(
        "uq_worker_control_operation_worker",
        "worker_control_commands",
        ["operation_id", "worker_id"],
        unique=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM worker_control_commands "
            "WHERE operation_id IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError(
            "package worker control rows exist; retain them during rollback"
        )
    op.drop_index(
        "uq_worker_control_operation_worker", table_name="worker_control_commands"
    )
    op.drop_column("worker_control_commands", "payload")
    op.drop_column("worker_control_commands", "claim_token")
    op.drop_column("worker_control_commands", "target_incarnation_id")
    op.drop_column("worker_control_commands", "operation_id")
