"""Join Midtown migrations with upstream Anthropic cache capability."""

from collections.abc import Sequence

revision: str = "20260914_merge_mtg_cache"
down_revision: tuple[str, str] = (
    "20260913_merge_mtg_sdk",
    "20260914_anthropic_cache",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""
    pass


def downgrade() -> None:
    """Both parent chains reverse their schema changes."""
    pass
