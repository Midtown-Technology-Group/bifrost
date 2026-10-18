"""Repair started_at for databases stamped before the delivery schema edit."""

from alembic import op

revision = "20261012_work_delivery_started"
down_revision = "20261006_mtg_upstream_sdk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The released 20260919 revision was later edited to include this column.
    # Fresh databases therefore already have it, while databases stamped before
    # that edit do not. Keep this repair safe for both histories.
    op.execute(
        "ALTER TABLE work_deliveries "
        "ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    # The parent revision owns started_at for fresh databases. Dropping it here
    # would produce a schema that cannot run the parent revision's application.
    pass
