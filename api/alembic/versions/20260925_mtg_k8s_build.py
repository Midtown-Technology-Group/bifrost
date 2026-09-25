"""Join Midtown and upstream Kubernetes build migration heads."""

from collections.abc import Sequence

revision: str = "20260925_mtg_k8s_build"
down_revision: tuple[str, str] = (
    "20260925_mtg_solution_inbound",
    "20260915_platform_job_k8s_ns",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both parent chains supply the schema changes."""


def downgrade() -> None:
    """Both parent chains own their downgrades."""
