"""Retain the MTG local runner while preserving the upstream revision identity.

Upstream removed this schema because its CLI session UI was unreachable. MTG
still uses it in routers/cli.py, routers/websocket.py, and execution visibility
filters, with test_cli_sessions_coverage and test_websocket_read_visibility
coverage. Dropping cli_sessions or executions.session_id/is_local_execution
would destroy the retained fork feature. No later upstream migration requires
these objects to be absent. Both directions deliberately preserve the schema;
the original creation revisions continue to own it.

Revision ID: 20260927_drop_local_runner
Revises: 20260927_r1b_mcp_names_b2
"""

revision = "20260927_drop_local_runner"
down_revision = "20260927_r1b_mcp_names_b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """The MTG local runner remains supported."""


def downgrade() -> None:
    """No schema was removed by this revision."""
