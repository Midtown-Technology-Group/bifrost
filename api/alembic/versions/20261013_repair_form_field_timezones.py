"""Repair form field timestamps skipped by the original UTC migration.

Revision ID: 20261013_form_field_tz
Revises: 20261012_work_delivery_started
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20261013_form_field_tz"
down_revision = "20261012_work_delivery_started"
branch_labels = None
depends_on = None


FORM_FIELD_TIMESTAMPS = ("created_at", "updated_at")


def upgrade() -> None:
    bind = op.get_bind()
    for column_name in FORM_FIELD_TIMESTAMPS:
        data_type = bind.execute(
            sa.text("""
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'form_fields'
                  AND column_name = :column_name
            """),
            {"column_name": column_name},
        ).scalar_one_or_none()
        if data_type == "timestamp without time zone":
            op.alter_column(
                "form_fields",
                column_name,
                type_=sa.DateTime(timezone=True),
                existing_type=sa.DateTime(),
                postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
            )


def downgrade() -> None:
    # Timestamp normalization is intentionally irreversible. Reverting to a
    # naive type would discard the timezone contract and can shift stored data.
    pass
