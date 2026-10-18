"""R2b: base roles, row permissions and home boundaries, adapted for MTG.

Revision ID: 20260929_r2b_roles
Revises: 20261017_mtg_rbac_preflight
Create Date: 2026-09-29

Adds the data model for the R2/R3 roles catalog and backfills it so it
describes today's access exactly — nothing about who can do what changes in
this migration. See docs/plans (R2b design) for the full model.

Schema changes:
- roles.is_base, roles.is_builtin (booleans)
- role_permissions(role_id, permission) — a role's granted permission strings
- Seeds three builtin roles with fixed UUIDs: Platform Admin (base),
  User (base), Platform Operator (not base, assigned to nobody yet)
- users.base_role_id (NOT NULL FK roles.id) — backfilled from is_superuser
- user_role_boundaries(user_id, role_id, kind, organization_id) — backfilled
  with one 'organization' boundary per existing user_roles row, at the
  user's home org
- Retains roles.permissions physically for the inert rollback window
- Retains knowledge_namespace_roles, including its rows, IDs and indexes

The preceding MTG preflight validates and archives the legacy inventory before
any R2b DDL; the following bridge translates the three approved boolean keys.
All three revisions must execute in the same online transaction.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from uuid import UUID

# Frozen at the time this migration was written. Migrations never import live
# application code: shared/builtin_roles.py may evolve, but this revision must
# keep meaning what it meant. tests/unit/test_builtin_roles.py asserts these
# copies equal the live constants and the access-list derivation today.
PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")
USER_BASE_PERMISSIONS: frozenset[str] = frozenset(
    {
        "agentruns.read",
        "agents.read",
        "apps.read",
        "configs.read",
        "events.read",
        "executions.read",
        "forms.read",
        "integrations.read",
        "knowledge.read",
        "mcp.read",
        "metrics.read",
        "policyrules.read",
        "roles.read",
        "settings.read",
        "tables.read",
        "workflows.read",
    }
)
PLATFORM_OPERATOR_PERMISSIONS: frozenset[str] = frozenset(
    {
        "agentruns.read",
        "agents.read",
        "apps.read",
        "configs.read",
        "executions.read",
        "forms.read",
        "integrations.read",
        "metrics.read",
        "organizations.read",
        "workflows.read",
    }
)

revision: str = "20260929_r2b_roles"
down_revision: Union[str, None] = "20261017_mtg_rbac_preflight"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SYSTEM_ACTOR = "system"


def _assert_mtg_preflight() -> None:
    valid = op.get_bind().execute(sa.text("""
        SELECT contract_version = 1 AND transaction_id = txid_current()
          AND migration_owner = current_user
          AND original_state = mtg_rbac_bridge.legacy_state()
          AND bridged_state IS NULL
        FROM mtg_rbac_bridge.rollback_record WHERE singleton
    """)).scalar_one()
    if not valid:
        raise RuntimeError("MTG R2b requires unchanged preflight state in the same transaction")


def _seed_builtin_roles() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO roles (id, name, description, is_base, is_builtin, created_by, created_at, updated_at)
            VALUES
                (:admin_id, 'Platform Admin', 'Full platform administration.', true, true, :actor, NOW(), NOW()),
                (:user_id, 'User', 'Baseline access every user holds.', true, true, :actor, NOW(), NOW()),
                (:operator_id, 'Platform Operator', 'Read-only visibility into managed organizations. Not yet assignable.', false, true, :actor, NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "admin_id": str(PLATFORM_ADMIN_ROLE_ID),
            "user_id": str(USER_ROLE_ID),
            "operator_id": str(PLATFORM_OPERATOR_ROLE_ID),
            "actor": SYSTEM_ACTOR,
        },
    )

    for permission in sorted(USER_BASE_PERMISSIONS):
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission) VALUES (CAST(:role_id AS uuid), :permission) "
                "ON CONFLICT DO NOTHING"
            ),
            {"role_id": str(USER_ROLE_ID), "permission": permission},
        )
    for permission in sorted(PLATFORM_OPERATOR_PERMISSIONS):
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission) VALUES (CAST(:role_id AS uuid), :permission) "
                "ON CONFLICT DO NOTHING"
            ),
            {"role_id": str(PLATFORM_OPERATOR_ROLE_ID), "permission": permission},
        )


def _backfill_users_base_role_id() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE users
            SET base_role_id = CASE WHEN is_superuser THEN CAST(:admin_id AS uuid) ELSE CAST(:user_id AS uuid) END
            WHERE base_role_id IS NULL
            """
        ),
        {"admin_id": str(PLATFORM_ADMIN_ROLE_ID), "user_id": str(USER_ROLE_ID)},
    )


def _backfill_user_role_boundaries() -> int:
    """One 'organization' boundary per existing user_roles row, at the
    user's home org. Users with no home org get no boundary (there's no
    org to invent) — returns how many such rows were skipped, for the
    migration test to assert against."""
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id)
            SELECT gen_random_uuid(), ur.user_id, ur.role_id, 'organization', u.organization_id
            FROM user_roles ur
            JOIN users u ON u.id = ur.user_id
            WHERE u.organization_id IS NOT NULL
            ON CONFLICT DO NOTHING
            """
        )
    )
    skipped = connection.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM user_roles ur
            JOIN users u ON u.id = ur.user_id
            WHERE u.organization_id IS NULL
            """
        )
    ).scalar_one()
    return int(skipped)


def upgrade() -> None:
    # Enforce preflight BEFORE even the historical Builder-residue cleanup.
    _assert_mtg_preflight()
    # Residue of the withdrawn Builder revision 20260730_role_auth_scopes,
    # which already-upgraded databases may carry.
    op.execute("DROP INDEX IF EXISTS uq_roles_key")
    for column in ("key", "scopes", "is_builtin", "assignable_to_resources"):
        op.execute(f"ALTER TABLE roles DROP COLUMN IF EXISTS {column}")

    op.add_column(
        "roles",
        sa.Column("is_base", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "roles",
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.UUID(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission", sa.String(length=64), primary_key=True),
    )

    _seed_builtin_roles()

    op.add_column("users", sa.Column("base_role_id", sa.UUID(), nullable=True))
    _backfill_users_base_role_id()
    op.alter_column("users", "base_role_id", nullable=False)
    op.create_foreign_key(
        "fk_users_base_role_id",
        "users",
        "roles",
        ["base_role_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "user_role_boundaries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "organization_id",
            sa.UUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "role_id"],
            ["user_roles.user_id", "user_roles.role_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('organization', 'managed_organizations', 'platform')",
            name="ck_user_role_boundaries_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'organization') = (organization_id IS NOT NULL)",
            name="ck_user_role_boundaries_org_kind_match",
        ),
    )
    op.create_index(
        "ux_user_role_boundaries_org",
        "user_role_boundaries",
        ["user_id", "role_id", "kind", "organization_id"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_index(
        "ux_user_role_boundaries_no_org",
        "user_role_boundaries",
        ["user_id", "role_id", "kind"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )

    skipped = _backfill_user_role_boundaries()
    if skipped:
        print(  # noqa: T201 — migration-time visibility, no logger configured here
            f"R2b roles migration: {skipped} user_roles row(s) belong to a user "
            "with no home organization_id; no boundary was created for them "
            "(there's no org to invent). See the migration test for the count."
        )

    # MTG deliberately retains both legacy objects. No runtime reader/writer
    # maps permissions; the next revision freezes normal DML of its JSON.


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "LOCK TABLE public.roles, public.user_roles, public.users, "
        "public.organizations, public.knowledge_namespace_roles, "
        "public.role_permissions, public.user_role_boundaries "
        "IN SHARE ROW EXCLUSIVE MODE"
    ))
    valid = connection.execute(sa.text("""
        SELECT original_state = mtg_rbac_bridge.legacy_state()
          AND bridged_state IS NULL
        FROM mtg_rbac_bridge.rollback_record WHERE singleton
    """)).scalar_one()
    actual_permissions = set(connection.execute(sa.text(
        "SELECT role_id::text, permission FROM public.role_permissions"
    )).tuples())
    expected_permissions = {
        (str(USER_ROLE_ID), permission) for permission in USER_BASE_PERMISSIONS
    } | {
        (str(PLATFORM_OPERATOR_ROLE_ID), permission) for permission in PLATFORM_OPERATOR_PERMISSIONS
    }
    changed_base_roles = connection.execute(sa.text("""
        SELECT count(*) FROM public.users
        WHERE base_role_id IS DISTINCT FROM CASE WHEN is_superuser
            THEN '00000000-0000-0000-0000-000000000005'::uuid
            ELSE '00000000-0000-0000-0000-000000000006'::uuid END
    """)).scalar_one()
    changed_boundaries = connection.execute(sa.text("""
        SELECT count(*) FROM (
          (SELECT user_id, role_id, kind, organization_id FROM public.user_role_boundaries
           EXCEPT SELECT ur.user_id, ur.role_id, 'organization', u.organization_id
             FROM public.user_roles ur JOIN public.users u ON u.id = ur.user_id
             WHERE u.organization_id IS NOT NULL)
          UNION ALL
          (SELECT ur.user_id, ur.role_id, 'organization', u.organization_id
             FROM public.user_roles ur JOIN public.users u ON u.id = ur.user_id
             WHERE u.organization_id IS NOT NULL
           EXCEPT SELECT user_id, role_id, kind, organization_id FROM public.user_role_boundaries)
        ) differences
    """)).scalar_one()
    if not valid or actual_permissions != expected_permissions or changed_base_roles or changed_boundaries:
        raise RuntimeError("MTG R2b rollback refused: grants, memberships or boundaries changed")

    # Retained JSON and knowledge junction are not recreated or overwritten.

    op.drop_index("ux_user_role_boundaries_no_org", table_name="user_role_boundaries")
    op.drop_index("ux_user_role_boundaries_org", table_name="user_role_boundaries")
    op.drop_table("user_role_boundaries")

    op.drop_constraint("fk_users_base_role_id", "users", type_="foreignkey")
    op.drop_column("users", "base_role_id")

    op.drop_table("role_permissions")
    op.drop_column("roles", "is_builtin")
    op.drop_column("roles", "is_base")

    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM roles WHERE id IN (CAST(:a AS uuid), CAST(:u AS uuid), CAST(:o AS uuid))"),
        {
            "a": str(PLATFORM_ADMIN_ROLE_ID),
            "u": str(USER_ROLE_ID),
            "o": str(PLATFORM_OPERATOR_ROLE_ID),
        },
    )
