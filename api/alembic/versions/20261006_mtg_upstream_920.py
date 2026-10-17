"""Join current Midtown source accounting and upstream telemetry/provider heads."""

from collections.abc import Sequence

revision: str = "20261006_mtg_upstream_920"
down_revision: tuple[str, str] = (
    "20261003_native_src_account",
    "20260925_mtg_opencode_go",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply their schema changes."""


def downgrade() -> None:
    """Both parent chains own their downgrades."""
