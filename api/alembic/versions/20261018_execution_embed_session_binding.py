"""Persist app-embed session ownership at execution admission."""

import sqlalchemy as sa
from alembic import op

revision = "20261018_exec_embed_binding"
down_revision = "20261012_work_delivery_started"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("embed_session_binding", sa.String(length=71), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executions", "embed_session_binding")
