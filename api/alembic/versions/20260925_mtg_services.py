"""Join Midtown agent and upstream supervised-service migration heads."""

from collections.abc import Sequence

revision: str = "20260925_mtg_services"
down_revision: tuple[str, str] = (
    "20260925_mtg_agent_failover",
    "20260921_service_logs",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""


def downgrade() -> None:
    """Both parent chains own their downgrades."""
