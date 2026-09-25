"""Join Midtown supervised-service and upstream workspace-config migration heads."""

from collections.abc import Sequence

revision: str = "20260925_mtg_workspace_import"
down_revision: tuple[str, str] = (
    "20260925_mtg_services",
    "20260923_cfg_metadata",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""


def downgrade() -> None:
    """Both parent chains own their downgrades."""
