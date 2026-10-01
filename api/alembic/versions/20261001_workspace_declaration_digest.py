"""Retain immutable declaration identity independently of operational status.

Existing declarations stay null rather than guessing their original reason
from a diagnostic changed by the sweep or an operator.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261001_ws_declaration_digest"
down_revision = "20260929_ws_source_supersession"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_source_releases",
        sa.Column("declaration_digest", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_source_releases", "declaration_digest")
