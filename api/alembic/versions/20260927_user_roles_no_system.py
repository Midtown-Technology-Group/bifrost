"""guard: user_roles cannot reference the system account

Revision ID: 20260927_no_sys_role
Revises: 20260927_drop_local_runner
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_no_sys_role"
down_revision: str | None = "20260927_drop_local_runner"
branch_labels = None
depends_on = None

# System (sentinel) user UUID from src.core.constants.SYSTEM_USER_ID
SYSTEM_USER_UUID = "00000000-0000-0000-0000-000000000001"
CONSTRAINT_NAME = "ck_user_roles_not_system_user"


def upgrade() -> None:
    # Defense in depth alongside the application-level guard: the system
    # account is never a valid target for a role assignment, so make it
    # unrepresentable at the schema level too.
    # A legacy holder must be resolved explicitly before this migration. Never
    # delete an assignment before MTG's later preflight can inventory it. Hold
    # the write fence through the count and CHECK creation so a concurrent
    # assignment cannot turn this validation into a race.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE public.roles, public.user_roles, public.users IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    canonical_admins = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.users "
            "WHERE id = CAST(:system_user_id AS uuid) AND is_superuser IS TRUE"
        ),
        {"system_user_id": SYSTEM_USER_UUID},
    ).scalar_one()
    if canonical_admins:
        # R3a later moves every Admin base role into an additional assignment.
        # This sentinel cannot hold such an assignment under this CHECK. Refuse
        # the unrepresentable authority state before R2b instead of silently
        # stripping its authority or adding a forbidden membership.
        raise RuntimeError(
            f"MTG RBAC preflight: canonical system account has {canonical_admins} legacy Admin flags; "
            "refusing an unrepresentable authority state"
        )
    count = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.user_roles "
            "WHERE user_id = CAST(:system_user_id AS uuid)"
        ),
        {"system_user_id": SYSTEM_USER_UUID},
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"MTG RBAC preflight: canonical system account has {count} legacy role assignments; "
            "refusing to delete or migrate them"
        )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "user_roles",
        # DDL can't take bound parameters; SYSTEM_USER_UUID is a fixed constant.
        f"user_id <> '{SYSTEM_USER_UUID}'",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "user_roles", type_="check")
