"""Bind grant attempt ordinal, caller digest and Start clock to retained parents.

No role grants or owner transactions are introduced. These composite constraints
make independently valid scalar fields insufficient to change admitted identity.
"""

from alembic import op

revision = "20261010_runtime_fences"
down_revision = "20261010_runtime_admissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_runtime_owner_caller",
        "runtime_execution_owners",
        ["execution_id", "owner_incarnation_id", "caller_sha256"],
    )
    op.create_unique_constraint(
        "uq_runtime_start_clock",
        "runtime_starts",
        ["id", "session_id", "start_message_id", "started_at"],
    )
    op.create_unique_constraint(
        "uq_workflow_attempt_runtime_number",
        "workflow_execution_attempts",
        ["id", "execution_id", "attempt_number"],
    )
    op.create_foreign_key(
        "fk_runtime_sdk_owner_caller",
        "workflow_runtime_sdk_grants",
        "runtime_execution_owners",
        ["execution_id", "owner_incarnation_id", "caller_snapshot_digest"],
        ["execution_id", "owner_incarnation_id", "caller_sha256"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_runtime_sdk_start_clock",
        "workflow_runtime_sdk_grants",
        "runtime_starts",
        ["committed_start_id", "runtime_session_id", "start_message_id", "started_at"],
        ["id", "session_id", "start_message_id", "started_at"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_runtime_sdk_attempt_number",
        "workflow_runtime_sdk_grants",
        "workflow_execution_attempts",
        ["workflow_attempt_id", "execution_id", "attempt_number"],
        ["id", "execution_id", "attempt_number"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Removing identity fences from retained grants would weaken accepted facts.
    import sqlalchemy as sa

    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM workflow_runtime_sdk_grants)"))
        .scalar()
    ):
        raise RuntimeError("Retain runtime grant identity fences before downgrade")
    for name in (
        "fk_runtime_sdk_owner_caller",
        "fk_runtime_sdk_start_clock",
        "fk_runtime_sdk_attempt_number",
    ):
        op.drop_constraint(name, "workflow_runtime_sdk_grants", type_="foreignkey")
    op.drop_constraint(
        "uq_workflow_attempt_runtime_number",
        "workflow_execution_attempts",
        type_="unique",
    )
    op.drop_constraint("uq_runtime_start_clock", "runtime_starts", type_="unique")
    op.drop_constraint(
        "uq_runtime_owner_caller", "runtime_execution_owners", type_="unique"
    )
