"""Join Midtown's execution migrations with upstream home and logo metadata.

Revision ID: 20260912_merge_mtg_redesign
Revises: 20260903_merge_mtg_upstream, 20260909_form_logos
"""

from collections.abc import Sequence

revision: str = "20260912_merge_mtg_redesign"
down_revision: tuple[str, str] = (
    "20260903_merge_mtg_upstream",
    "20260909_form_logos",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Schema changes are applied by both parent chains."""
    pass


def downgrade() -> None:
    """Schema changes are reversed by both parent chains."""
    pass
