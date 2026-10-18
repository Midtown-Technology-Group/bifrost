"""Preserve canonical sentinel memberships when the incoming graph refuses them.

Run through the Linux Docker test lane only. The snapshot variant is a
PostgreSQL template clone of a fully migrated, synthetically populated fork
head; actual pg_dump/restore coverage lives in rbac-backup-rehearsal.
"""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from tests.e2e.platform.test_mtg_rbac_bridge_migration_rehearsal import (
    _legacy_snapshot,
    _name,
    _seed,
    _sql,
)
from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _assert_safe_database_name,
    _create_database,
    _current_revision,
    _direct_database_url,
    _drop_database,
    _upgrade,
    _with_admin_connection,
)

pytestmark = pytest.mark.e2e
FORK_HEAD = "20261012_work_delivery_started"
SYSTEM_ID = "00000000-0000-0000-0000-000000000001"
GUARD = "20260927_no_sys_role"
CONSTRAINT = "ck_user_roles_not_system_user"


@pytest.fixture(params=["fresh_fork_head", "schema_faithful_snapshot"])
def fork_database(request):
    source = _name()
    snapshot = _name()
    try:
        asyncio.run(_create_database(source))
        source_url = _direct_database_url(source)
        _upgrade(source_url, FORK_HEAD)
        ids = _seed(source_url)
        _sql(
            source_url,
            """
            INSERT INTO users (id, email, name, is_active, is_superuser,
                               is_verified, is_registered, is_system, is_external, organization_id)
            VALUES (CAST(:id AS uuid), 'canonical-sentinel@example.invalid',
                    'Synthetic canonical sentinel', true, false, true, true, true, false,
                    CAST(:home AS uuid))
            ON CONFLICT (id) DO UPDATE
            SET email = EXCLUDED.email, name = EXCLUDED.name,
                is_active = true, is_superuser = false, is_verified = true,
                is_registered = true, is_system = true, is_external = false,
                organization_id = EXCLUDED.organization_id
        """,
            {"id": SYSTEM_ID, "home": ids["org_a"]},
        )
        _sql(
            source_url,
            """
            INSERT INTO user_roles (user_id, role_id, assigned_by)
            VALUES (CAST(:user_id AS uuid), CAST(:role_id AS uuid), 'rehearsal')
        """,
            {"user_id": SYSTEM_ID, "role_id": ids["both"]},
        )
        if request.param == "fresh_fork_head":
            yield source_url
        else:
            _assert_safe_database_name(source)
            _assert_safe_database_name(snapshot)

            async def clone(connection):
                await connection.execute(
                    sa.text(f"CREATE DATABASE {snapshot} TEMPLATE {source}")
                )

            asyncio.run(_with_admin_connection(clone))
            snapshot_url = _direct_database_url(snapshot)
            assert _current_revision(snapshot_url) == FORK_HEAD
            assert _legacy_snapshot(snapshot_url) == _legacy_snapshot(source_url)
            yield snapshot_url
    finally:
        asyncio.run(_drop_database(snapshot))
        asyncio.run(_drop_database(source))


def _assert_refusal_preserved(fork_database, before):
    # No autocommit revision lies between the original fork head and this
    # guard: Alembic must roll this incoming segment back to the original head.
    assert _current_revision(fork_database) == FORK_HEAD
    assert _legacy_snapshot(fork_database) == before
    row = _sql(
        fork_database,
        """
        SELECT NOT EXISTS (
                 SELECT 1 FROM pg_constraint
                 WHERE conrelid = 'public.user_roles'::regclass AND conname = :name
               ) AS no_guard,
               to_regclass('public.role_permissions') IS NULL AS no_permissions,
               to_regclass('public.user_role_boundaries') IS NULL AS no_boundaries,
               NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'mtg_rbac_bridge')
                 AS no_archive
    """,
        {"name": CONSTRAINT},
    )[0]
    assert all(row.values())


def test_full_graph_refuses_canonical_holder_without_deleting_state(fork_database):
    before = _legacy_snapshot(fork_database)
    assert _current_revision(fork_database) == FORK_HEAD
    canonical = [row for row in before["memberships"] if row["user_id"] == SYSTEM_ID]
    assert len(canonical) == 1
    with pytest.raises(
        RuntimeError, match="canonical system account has 1 legacy role assignments"
    ):
        _upgrade(fork_database, "head")
    _assert_refusal_preserved(fork_database, before)


def test_full_graph_refuses_canonical_admin_without_changing_authority(fork_database):
    _sql(
        fork_database,
        "DELETE FROM user_roles WHERE user_id = CAST(:id AS uuid)",
        {"id": SYSTEM_ID},
    )
    _sql(
        fork_database,
        "UPDATE users SET is_superuser = true WHERE id = CAST(:id AS uuid)",
        {"id": SYSTEM_ID},
    )
    before = _legacy_snapshot(fork_database)
    with pytest.raises(
        RuntimeError, match="canonical system account has 1 legacy Admin flags"
    ):
        _upgrade(fork_database, "head")
    _assert_refusal_preserved(fork_database, before)


def test_full_graph_zero_holder_nonadmin_preserves_actual_canonical_shape(
    fork_database,
):
    # Controlled fixture preparation only; the migration never deletes rows.
    _sql(
        fork_database,
        "DELETE FROM user_roles WHERE user_id = CAST(:id AS uuid)",
        {"id": SYSTEM_ID},
    )
    before = _legacy_snapshot(fork_database)
    _upgrade(fork_database, GUARD)
    assert _current_revision(fork_database) == GUARD
    assert _legacy_snapshot(fork_database) == before
    canonical_before = next(row for row in before["users"] if row["id"] == SYSTEM_ID)
    assert canonical_before["is_superuser"] is False
    assert canonical_before["organization_id"] is not None
    assert (
        _sql(
            fork_database,
            """
        SELECT count(*) AS guards FROM pg_constraint
        WHERE conrelid = 'public.user_roles'::regclass AND conname = :name AND convalidated
    """,
            {"name": CONSTRAINT},
        )[0]["guards"]
        == 1
    )
    with pytest.raises(sa.exc.IntegrityError, match=CONSTRAINT):
        _sql(
            fork_database,
            """
            INSERT INTO user_roles (user_id, role_id, assigned_by)
            SELECT CAST(:id AS uuid), id, 'rehearsal' FROM roles ORDER BY id LIMIT 1
        """,
            {"id": SYSTEM_ID},
        )
    assert _legacy_snapshot(fork_database) == before
    _upgrade(fork_database, "head")
    assert _current_revision(fork_database) == "20261012_oauth_ownership"
    canonical_after = _sql(
        fork_database,
        """
        SELECT u.is_superuser, u.is_system, u.organization_id::text AS home,
               u.base_role_id::text AS base_role,
               NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id) AS no_memberships,
               NOT EXISTS (SELECT 1 FROM user_role_boundaries b WHERE b.user_id = u.id) AS no_boundaries
        FROM users u WHERE u.id = CAST(:id AS uuid)
    """,
        {"id": SYSTEM_ID},
    )[0]
    assert canonical_after == {
        "is_superuser": False,
        "is_system": True,
        "home": canonical_before["organization_id"],
        "base_role": "00000000-0000-0000-0000-000000000006",
        "no_memberships": True,
        "no_boundaries": True,
    }
