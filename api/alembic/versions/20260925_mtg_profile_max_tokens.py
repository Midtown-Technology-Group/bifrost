"""Join Midtown Kubernetes build and upstream profile token migration heads."""

from collections.abc import Sequence

revision: str = "20260925_mtg_profile_max_tokens"
down_revision: tuple[str, str] = (
    "20260925_mtg_k8s_build",
    "20260918_merge_plat_prof_heads",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""


def downgrade() -> None:
    """Both parent chains own their downgrades."""
