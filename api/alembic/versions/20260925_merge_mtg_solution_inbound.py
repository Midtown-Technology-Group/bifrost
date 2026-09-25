"""Join Midtown migrations with upstream Solution inbound access."""

from collections.abc import Sequence

revision: str = "20260925_merge_mtg_solution_inbound"
down_revision: tuple[str, str] = (
    "20260924_action_approval",
    "20260916_solution_inbound",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""
    pass


def downgrade() -> None:
    """Both parent chains own their downgrades."""
    pass
