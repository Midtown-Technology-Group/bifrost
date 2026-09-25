"""Join Midtown profile token and upstream model failover migration heads."""

from collections.abc import Sequence

revision: str = "20260925_mtg_agent_failover"
down_revision: tuple[str, str] = (
    "20260925_mtg_profile_max_tokens",
    "20260918_profile_failover",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""


def downgrade() -> None:
    """Both parent chains own their downgrades."""
