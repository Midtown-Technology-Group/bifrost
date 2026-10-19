"""Index Teams action receipt candidates without changing receipt semantics.

The protected native inspection's all-history receipt check timed out while
sequentially scanning executions (run 38993549195). Index every row carrying
the receipt key, including null/malformed values and nonterminal executions.
The consumer retains its existing status and emitted-marker checks.

Concurrent creation preserves ordinary writes. A failed concurrent build can
leave an invalid index; inspect its definition/state before a reviewed
DROP INDEX CONCURRENTLY and retry. Never treat an existing invalid index as a
successful migration or mark a receipt emitted to clear readiness.
"""

from alembic import op

revision = "20261017_teams_receipt_index"
down_revision = "20261012_work_delivery_started"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        try:
            op.execute("SET lock_timeout = '5s'")
            op.execute("SET statement_timeout = '30min'")
            op.execute(
                "CREATE INDEX CONCURRENTLY ix_executions_teams_action_receipt "
                "ON executions (id) "
                "WHERE execution_context ? 'teams_action_completion'"
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
                "DROP INDEX CONCURRENTLY IF EXISTS ix_executions_teams_action_receipt"
            )
        finally:
            op.execute("RESET statement_timeout")
            op.execute("RESET lock_timeout")
