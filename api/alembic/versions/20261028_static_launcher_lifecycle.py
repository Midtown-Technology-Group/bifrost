"""Record when a static launcher asked to drain on a build change (#1105)

Revision ID: 20261028_static_launcher_lifecycle
Revises: 20261025_remote_worker_ownership
Create Date: 2026-10-28

A remote static launcher may opt in, on its own hello, to drain when no
live controller still runs the build it reported. Existing rows stay
opted out. The idle and max-run limits are not stored: the launcher sends
them again on every hello, and the API executor that owns the session
applies them.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261028_static_launcher_lifecycle"
down_revision: Union[str, None] = "20261025_remote_worker_ownership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "execution_workers",
        sa.Column(
            "drain_when_outdated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("execution_workers", "drain_when_outdated")
