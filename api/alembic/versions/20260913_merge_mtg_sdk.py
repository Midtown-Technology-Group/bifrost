"""Join Midtown migrations with upstream App SDK provenance."""

from collections.abc import Sequence

revision: str = "20260913_merge_mtg_sdk"
down_revision: tuple[str, str] = (
    "20260912_merge_mtg_prefix",
    "20260912_app_sdk_provenance",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""
    pass


def downgrade() -> None:
    """Both parent chains reverse their schema changes."""
    pass
