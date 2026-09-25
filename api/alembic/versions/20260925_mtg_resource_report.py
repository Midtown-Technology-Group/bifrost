"""Join Midtown workspace import and upstream execution resource telemetry."""

from collections.abc import Sequence

revision: str = "20260925_mtg_resource_report"
down_revision: tuple[str, str] = (
    "20260925_mtg_workspace_import",
    "20260924_exec_resource",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""


def downgrade() -> None:
    """Both parent chains own their downgrades."""
