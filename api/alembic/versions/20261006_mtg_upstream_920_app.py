"""Join upstream integration with protected App source accounting."""

from collections.abc import Sequence

revision: str = "20261006_mtg_upstream_920_app"
down_revision: tuple[str, str] = (
    "20261006_mtg_upstream_920",
    "20261005_package_src_account",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply their schema changes."""


def downgrade() -> None:
    """Both parent chains own their downgrades."""
