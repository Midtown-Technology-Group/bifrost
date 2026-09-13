"""Join Midtown modernization with the upstream document prefix index."""

from collections.abc import Sequence

revision: str = "20260912_merge_mtg_prefix"
down_revision: tuple[str, str] = (
    "20260912_merge_mtg_redesign",
    "20260912_document_prefix_index",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""
    pass


def downgrade() -> None:
    """Both parent chains reverse their schema changes."""
    pass
