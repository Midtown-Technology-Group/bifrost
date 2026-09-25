"""Join Midtown resource telemetry and upstream OpenCode Go schema heads."""

from collections.abc import Sequence

revision: str = "20260925_mtg_opencode_go"
down_revision: tuple[str, str] = (
    "20260925_mtg_resource_report",
    "20260925_opencode_go_wire_api",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""


def downgrade() -> None:
    """Both parent chains own their downgrades."""
