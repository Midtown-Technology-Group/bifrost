"""Join the fork schema with upstream worker SDK audit attribution.

This merge adds no DDL; both parent migrations retain their original identity.
"""

revision: str = "20261006_mtg_upstream_sdk"
down_revision: tuple[str, str] = (
    "20261006_mtg_upstream_920_app",
    "20260926_audit_execution_id",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
