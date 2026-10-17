"""bound operation receipt admission per hashed caller scope

Revision ID: 20261017_receipt_admission
Revises: 20261012_work_delivery_started
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_receipt_admission"
down_revision: str = "20261012_work_delivery_started"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy rows remain valid permanent tombstones. New bounded callers write
    # a privacy-preserving admission digest used only for exact quota counts.
    op.add_column(
        "operation_receipts",
        sa.Column("admission_key", sa.String(length=64), nullable=True),
    )
    # This table is intentionally append-only. Build without blocking receipt
    # writes even when an existing installation already has many tombstones.
    with op.get_context().autocommit_block():
        try:
            op.execute("SET lock_timeout = '5s'")
            op.execute("SET statement_timeout = '30min'")
            op.execute(
                "CREATE INDEX CONCURRENTLY "
                "ix_operation_receipts_namespace_admission "
                "ON operation_receipts (namespace, admission_key) "
                "WHERE admission_key IS NOT NULL"
            )
        finally:
            op.execute("RESET statement_timeout")
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        try:
            op.execute("SET lock_timeout = '5s'")
            op.execute("SET statement_timeout = '30min'")
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "ix_operation_receipts_namespace_admission"
            )
        finally:
            op.execute("RESET statement_timeout")
            op.execute("RESET lock_timeout")
    op.drop_column("operation_receipts", "admission_key")
