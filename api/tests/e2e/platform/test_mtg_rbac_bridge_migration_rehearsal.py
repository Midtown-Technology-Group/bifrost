"""Real PostgreSQL rehearsal of MTG's preflight/R2b/bridge transaction.

Run only through the repository's Linux Docker test-VM/CI lane. Every test
uses a disposable, name-checked database cloned from a pristine full-graph
upgrade to the legacy cut point. This is not a claim to rehearse a production
backup; the release evidence must separately exercise that actual backup.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import create_async_engine

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _alembic_config,
    _assert_safe_database_name,
    _create_database,
    _current_revision,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _temporary_migration_database_url,
    _upgrade,
    _with_admin_connection,
)

pytestmark = pytest.mark.e2e
LEGACY = "20260928_audit_op_surface"
BRIDGE = "20261017_mtg_device_grant_bridge"
GRANTS = {
    "can_manage_config": "devices.readwrite",
    "can_execute_devices": "devices.execute",
    "can_promote_agent": "agents.promote",
}


def _sql(database_url, statement, parameters=None):
    async def execute(connection):
        result = await connection.execute(sa.text(statement), parameters or {})
        return result.mappings().all() if result.returns_rows else None

    return asyncio.run(_run_in_database(database_url, execute))


def _name():
    return f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def legacy_template():
    name = _name()
    try:
        asyncio.run(_create_database(name))
        _upgrade(_direct_database_url(name), LEGACY)
        yield name
    finally:
        asyncio.run(_drop_database(name))


@pytest.fixture
def legacy_db(legacy_template):
    name = _name()
    _assert_safe_database_name(name)
    _assert_safe_database_name(legacy_template)

    async def clone(connection):
        await connection.execute(sa.text(f"CREATE DATABASE {name} TEMPLATE {legacy_template}"))

    try:
        asyncio.run(_with_admin_connection(clone))
        yield _direct_database_url(name)
    finally:
        asyncio.run(_drop_database(name))


def _role(database_url, permissions, role_id=None):
    role_id = role_id or str(uuid4())
    _sql(database_url, """
        INSERT INTO roles (id, name, permissions, created_by)
        VALUES (CAST(:id AS uuid), 'Synthetic migration role', CAST(:permissions AS jsonb), 'rehearsal')
    """, {"id": role_id, "permissions": json.dumps(permissions)})
    return role_id


def _seed(database_url):
    ids = {key: str(uuid4()) for key in (
        "org_a", "org_b", "user_a", "user_b", "admin", "knowledge_global", "knowledge_home"
    )}
    for key in ("org_a", "org_b"):
        _sql(database_url, """
            INSERT INTO organizations (id, name, domain, is_active, is_provider, settings, created_by)
            VALUES (CAST(:id AS uuid), 'Synthetic org', :domain, true, false, '{}'::jsonb, 'rehearsal')
        """, {"id": ids[key], "domain": f"{ids[key]}.invalid"})
    for key, home, admin in (
        ("user_a", ids["org_a"], False), ("user_b", ids["org_b"], False), ("admin", None, True)
    ):
        _sql(database_url, """
            INSERT INTO users (id, email, name, is_active, is_superuser, is_verified,
                               is_registered, is_system, is_external, organization_id)
            VALUES (CAST(:id AS uuid), :email, 'Synthetic user', true, :admin, true,
                    true, false, false, CAST(:home AS uuid))
        """, {"id": ids[key], "email": f"{ids[key]}@example.invalid", "admin": admin, "home": home})
    ids["both"] = _role(database_url, {key: True for key in GRANTS})
    ids["manage"] = _role(database_url, {"can_manage_config": True})
    ids["execute"] = _role(database_url, {"can_execute_devices": True})
    ids["false"] = _role(database_url, {key: False for key in GRANTS})
    ids["absent"] = _role(database_url, {})
    for user, role in (
        ("user_a", "both"), ("user_b", "both"),
        ("user_a", "manage"), ("user_a", "execute"), ("admin", "both")
    ):
        _sql(database_url, """
            INSERT INTO user_roles (user_id, role_id, assigned_by)
            VALUES (CAST(:user AS uuid), CAST(:role AS uuid), 'rehearsal')
        """, {"user": ids[user], "role": ids[role]})
    for row, org in (("knowledge_global", None), ("knowledge_home", ids["org_a"])):
        _sql(database_url, """
            INSERT INTO knowledge_namespace_roles (id, namespace, organization_id, role_id, assigned_by)
            VALUES (CAST(:id AS uuid), 'synthetic-namespace', CAST(:org AS uuid),
                    CAST(:role AS uuid), 'rehearsal')
        """, {"id": ids[row], "org": org, "role": ids["both"]})
    return ids


def _legacy_snapshot(database_url):
    return _sql(database_url, """
        SELECT
          (SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY id), '[]'::jsonb) FROM roles r) AS roles,
          (SELECT coalesce(jsonb_agg(to_jsonb(u) ORDER BY id), '[]'::jsonb) FROM users u) AS users,
          (SELECT coalesce(jsonb_agg(to_jsonb(ur) ORDER BY user_id, role_id), '[]'::jsonb)
             FROM user_roles ur) AS memberships,
          (SELECT coalesce(jsonb_agg(to_jsonb(k) ORDER BY id), '[]'::jsonb)
             FROM knowledge_namespace_roles k) AS knowledge,
          (SELECT jsonb_agg(pg_get_indexdef(indexrelid) ORDER BY indexrelid::regclass::text)
             FROM pg_index WHERE indrelid = 'knowledge_namespace_roles'::regclass) AS knowledge_indexes
    """)[0]


def _assert_failed_segment_left_legacy(database_url, before):
    assert _current_revision(database_url) == LEGACY
    assert _legacy_snapshot(database_url) == before
    row = _sql(database_url, """
        SELECT to_regclass('public.role_permissions') IS NULL AS no_permissions,
               to_regclass('public.user_role_boundaries') IS NULL AS no_boundaries,
               NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'mtg_rbac_bridge') AS no_archive
    """)[0]
    assert all(row.values())


INVALID_JSON = [
    None, [], ["can_manage_config"], "true", 1, True,
    {"unknown": False}, {"workflows.read": True},
    {"can_manage_config": "true"}, {"can_execute_devices": 1},
    {"can_promote_agent": None}, {"can_manage_config": []},
    {"can_execute_devices": {}},
]


@pytest.mark.parametrize("permissions", INVALID_JSON)
def test_malformed_json_refuses_before_r2b_ddl(legacy_db, permissions):
    _role(legacy_db, permissions)
    before = _legacy_snapshot(legacy_db)
    with pytest.raises(RuntimeError, match="MTG RBAC preflight"):
        _upgrade(legacy_db, BRIDGE)
    _assert_failed_segment_left_legacy(legacy_db, before)


def test_sql_null_json_refuses_before_r2b_ddl(legacy_db):
    role_id = _role(legacy_db, {})
    _sql(legacy_db, "ALTER TABLE roles ALTER COLUMN permissions DROP NOT NULL")
    _sql(legacy_db, "UPDATE roles SET permissions = NULL WHERE id = CAST(:id AS uuid)", {"id": role_id})
    before = _legacy_snapshot(legacy_db)
    with pytest.raises(RuntimeError, match="JSON objects"):
        _upgrade(legacy_db, BRIDGE)
    _assert_failed_segment_left_legacy(legacy_db, before)


@pytest.mark.parametrize("table", ["role_permissions", "user_role_boundaries"])
def test_existing_r2b_objects_refuse_adaptation(legacy_db, table):
    _sql(legacy_db, f"CREATE TABLE public.{table} (synthetic_marker integer)")
    before = _legacy_snapshot(legacy_db)
    with pytest.raises(RuntimeError, match="R2b schema or unexpected"):
        _upgrade(legacy_db, BRIDGE)
    assert _current_revision(legacy_db) == LEGACY
    assert _legacy_snapshot(legacy_db) == before
    assert _sql(legacy_db, "SELECT to_regclass(:table) IS NOT NULL AS remains", {"table": f"public.{table}"})[0]["remains"]


@pytest.mark.parametrize("suffix", [5, 6, 7, 8])
def test_builtin_uuid_collision_refuses_without_archiving(legacy_db, suffix):
    _role(legacy_db, {}, f"00000000-0000-0000-0000-{suffix:012d}")
    before = _legacy_snapshot(legacy_db)
    with pytest.raises(RuntimeError, match="builtin UUID collision"):
        _upgrade(legacy_db, BRIDGE)
    _assert_failed_segment_left_legacy(legacy_db, before)


def test_null_home_ordinary_device_holder_refuses(legacy_db):
    ids = _seed(legacy_db)
    # Rehearse historical/bad data even though the normal CHECK prevents it.
    _sql(legacy_db, "ALTER TABLE users DROP CONSTRAINT ck_users_org_requires_superuser")
    _sql(legacy_db, "UPDATE users SET organization_id = NULL WHERE id = CAST(:id AS uuid)",
         {"id": ids["user_a"]})
    before = _legacy_snapshot(legacy_db)
    with pytest.raises(RuntimeError, match="no home organization"):
        _upgrade(legacy_db, BRIDGE)
    _assert_failed_segment_left_legacy(legacy_db, before)


def test_orphan_membership_refuses(legacy_db):
    role_id = _role(legacy_db, {"can_execute_devices": True})

    async def corrupt(connection):
        await connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
        await connection.execute(sa.text("""
            INSERT INTO user_roles (user_id, role_id, assigned_by)
            VALUES (gen_random_uuid(), CAST(:role AS uuid), 'synthetic orphan')
        """), {"role": role_id})

    asyncio.run(_run_in_database(legacy_db, corrupt))
    before = _legacy_snapshot(legacy_db)
    with pytest.raises(RuntimeError, match="orphan"):
        _upgrade(legacy_db, BRIDGE)
    _assert_failed_segment_left_legacy(legacy_db, before)


def test_true_false_absent_shared_roles_and_knowledge_identity(legacy_db):
    ids = _seed(legacy_db)
    before = _legacy_snapshot(legacy_db)
    _upgrade(legacy_db, BRIDGE)
    permissions = {
        (row["role_id"], row["permission"]) for row in _sql(legacy_db,
            "SELECT role_id::text, permission FROM role_permissions WHERE role_id = ANY(CAST(:ids AS uuid[]))",
            {"ids": [ids[key] for key in ("both", "manage", "execute", "false", "absent")]})
    }
    assert permissions == {
        *((ids["both"], permission) for permission in GRANTS.values()),
        (ids["manage"], "devices.readwrite"), (ids["execute"], "devices.execute"),
    }
    boundaries = _sql(legacy_db, """
        SELECT user_id::text, role_id::text, kind, organization_id::text
        FROM user_role_boundaries
    """)
    assert {(r["user_id"], r["role_id"], r["kind"], r["organization_id"]) for r in boundaries} == {
        (ids["user_a"], ids["both"], "organization", ids["org_a"]),
        (ids["user_b"], ids["both"], "organization", ids["org_b"]),
        (ids["user_a"], ids["manage"], "organization", ids["org_a"]),
        (ids["user_a"], ids["execute"], "organization", ids["org_a"]),
    }
    after = _legacy_snapshot(legacy_db)
    assert after["knowledge"] == before["knowledge"]
    assert after["knowledge_indexes"] == before["knowledge_indexes"]
    assert after["memberships"] == before["memberships"]
    archived = _sql(legacy_db, "SELECT original_state FROM mtg_rbac_bridge.rollback_record")[0]["original_state"]
    assert archived["roles"] == {r["id"]: r["permissions"] for r in before["roles"]}
    assert not _sql(legacy_db, """
        SELECT 1 FROM role_permissions p JOIN roles r ON r.id = p.role_id
        WHERE r.is_builtin AND p.permission = ANY(CAST(:grants AS text[]))
    """, {"grants": list(GRANTS.values())})
    # NULL/global uniqueness is still enforced by the original NND index.
    with pytest.raises(sa.exc.IntegrityError):
        _sql(legacy_db, """
            INSERT INTO knowledge_namespace_roles (id, namespace, organization_id, role_id)
            VALUES (gen_random_uuid(), 'synthetic-namespace', NULL, CAST(:role AS uuid))
        """, {"role": ids["both"]})


def test_runtime_mapper_does_not_map_retained_legacy_json():
    from src.models.orm.users import Role

    assert "permissions" not in sa.inspect(Role).attrs


@pytest.mark.parametrize("mutation", [
    "UPDATE roles SET permissions = '{\"can_execute_devices\": true}'::jsonb",
    "INSERT INTO roles (id, name, permissions, created_by) VALUES "
    "(gen_random_uuid(), 'Forbidden legacy writer', '{\"can_manage_config\": true}'::jsonb, 'rehearsal')",
    "UPDATE mtg_rbac_bridge.rollback_record SET original_state = '{}'::jsonb",
    "DELETE FROM mtg_rbac_bridge.rollback_record",
    "TRUNCATE mtg_rbac_bridge.rollback_record",
])
def test_normal_dml_cannot_reactivate_json_or_modify_archive(legacy_db, mutation):
    _seed(legacy_db)
    _upgrade(legacy_db, BRIDGE)
    with pytest.raises(sa.exc.DBAPIError, match="frozen|inert|immutable"):
        _sql(legacy_db, mutation)
    acl = _sql(legacy_db, """
        SELECT NOT EXISTS (
          SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
          WHERE n.nspname = 'mtg_rbac_bridge' AND a.grantee = 0
        ) AS no_public_schema_privileges
    """)[0]
    assert acl["no_public_schema_privileges"]


def test_immediate_segment_downgrade_preserves_original_state(legacy_db):
    _seed(legacy_db)
    before = _legacy_snapshot(legacy_db)
    _upgrade(legacy_db, BRIDGE)
    with _temporary_migration_database_url(legacy_db):
        command.downgrade(_alembic_config(), LEGACY)
    _assert_failed_segment_left_legacy(legacy_db, before)


R2B_ROWS_SQL = """
    SELECT
      (SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM roles r) AS roles,
      (SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM users u) AS users,
      (SELECT jsonb_agg(to_jsonb(m) ORDER BY user_id, role_id) FROM user_roles m) AS memberships,
      (SELECT jsonb_agg(to_jsonb(p) ORDER BY role_id, permission) FROM role_permissions p) AS permissions,
      (SELECT jsonb_agg(to_jsonb(b) ORDER BY id) FROM user_role_boundaries b) AS boundaries,
      (SELECT jsonb_agg(to_jsonb(k) ORDER BY id) FROM knowledge_namespace_roles k) AS knowledge,
      (SELECT jsonb_agg(pg_get_indexdef(indexrelid) ORDER BY indexrelid::regclass::text)
         FROM pg_index WHERE indrelid = 'knowledge_namespace_roles'::regclass) AS knowledge_indexes
"""


def test_bridge_only_downgrade_removes_exact_created_rows(legacy_db, record_testsuite_property):
    ids = _seed(legacy_db)
    database = sa.engine.make_url(legacy_db).database
    r2b_rows = {}

    def capture_r2b(connection, cursor, statement, parameters, context, executemany):
        if (connection.engine.url.database != database or r2b_rows
                or not statement.strip().startswith("LOCK TABLE public.roles")
                or "public.role_permissions" not in statement):
            return
        # The real Alembic R2b body and version update have completed. Keep
        # every legitimate R2b seed row; arbitrary extra grants are forbidden
        # by the bridge's exact-set precondition. Create one proven repair gap.
        removed = connection.execute(sa.text("""
            DELETE FROM user_role_boundaries
            WHERE user_id = CAST(:user AS uuid) AND role_id = CAST(:role AS uuid)
            RETURNING id::text
        """), {"user": ids["user_a"], "role": ids["both"]}).scalars().all()
        assert len(removed) == 1
        r2b_rows.update(connection.execute(sa.text(R2B_ROWS_SQL)).mappings().one())

    sa.event.listen(Engine, "before_cursor_execute", capture_r2b)
    try:
        _upgrade(legacy_db, BRIDGE)
    finally:
        sa.event.remove(Engine, "before_cursor_execute", capture_r2b)
    assert _current_revision(legacy_db) == BRIDGE
    assert r2b_rows["permissions"] and r2b_rows["boundaries"]
    archive = _sql(legacy_db, """
        SELECT original_state, created_permission_rows, created_boundary_rows,
               NOT (SELECT is_called FROM mtg_rbac_bridge.authority_change_epoch) AS rollback_window_open
        FROM mtg_rbac_bridge.rollback_record WHERE singleton
    """)[0]
    assert archive["rollback_window_open"]
    created_permissions = {tuple(row) for row in archive["created_permission_rows"]}
    assert created_permissions == {
        *((ids["both"], permission) for permission in GRANTS.values()),
        (ids["manage"], "devices.readwrite"), (ids["execute"], "devices.execute"),
    }
    original_permissions = {(row["role_id"], row["permission"]) for row in r2b_rows["permissions"]}
    assert created_permissions and not created_permissions & original_permissions
    assert len(archive["created_boundary_rows"]) == 1
    before_down = _sql(legacy_db, R2B_ROWS_SQL)[0]
    assert {(row["role_id"], row["permission"]) for row in before_down["permissions"]} == (
        original_permissions | created_permissions
    )
    assert {row["id"] for row in before_down["boundaries"]} == (
        {row["id"] for row in r2b_rows["boundaries"]} | set(archive["created_boundary_rows"])
    )
    with _temporary_migration_database_url(legacy_db):
        command.downgrade(_alembic_config(), "20260929_r2b_roles")
    assert _current_revision(legacy_db) == "20260929_r2b_roles"
    assert _sql(legacy_db, R2B_ROWS_SQL)[0] == r2b_rows
    retained = _sql(legacy_db, """
        SELECT original_state, bridged_state, created_permission_rows, created_boundary_rows
        FROM mtg_rbac_bridge.rollback_record WHERE singleton
    """)[0]
    assert retained == {"original_state": archive["original_state"], "bridged_state": None,
                        "created_permission_rows": None, "created_boundary_rows": None}
    record_testsuite_property("mtg_rbac_bridge_only_rollback", json.dumps({
        "head": "20260929_r2b_roles", "archive_retained": True, "rollback_window_open_before": True,
        "only_recorded_rows_removed": True, "r2b_rows_and_ids_unchanged": True,
        "unrelated_permission_rows_preserved": len(original_permissions),
        "unrelated_boundary_ids_preserved": len(r2b_rows["boundaries"]),
        "created_permission_rows_removed": len(created_permissions), "created_boundary_rows_removed": 1,
    }, sort_keys=True))


@pytest.mark.parametrize("mutation", [
    "DELETE FROM role_permissions WHERE permission = 'devices.execute'",
    "DELETE FROM user_roles",
    "DELETE FROM user_role_boundaries",
    "UPDATE users SET organization_id = :org WHERE id = :user",
    "UPDATE users SET base_role_id = :role WHERE id = :user",
    "DELETE FROM knowledge_namespace_roles",
])
def test_changed_authority_refuses_snapshot_rollback(legacy_db, mutation):
    ids = _seed(legacy_db)
    _upgrade(legacy_db, BRIDGE)
    _sql(legacy_db, mutation, {"org": ids["org_b"], "user": ids["user_a"], "role": ids["manage"]})
    with pytest.raises(RuntimeError, match="rollback refused"):
        with _temporary_migration_database_url(legacy_db):
            command.downgrade(_alembic_config(), LEGACY)
    assert _current_revision(legacy_db) == BRIDGE
    # Revocations remain revoked; no stale legacy JSON grant is replayed.
    if mutation.startswith("DELETE FROM role_permissions"):
        assert not _sql(legacy_db, "SELECT 1 FROM role_permissions WHERE permission = 'devices.execute'")


def test_revoke_then_regrant_still_refuses_snapshot_rollback(legacy_db):
    ids = _seed(legacy_db)
    _upgrade(legacy_db, BRIDGE)
    _sql(legacy_db, "DELETE FROM role_permissions WHERE role_id = CAST(:id AS uuid) AND permission = 'devices.execute'",
         {"id": ids["both"]})
    _sql(legacy_db, "INSERT INTO role_permissions (role_id, permission) VALUES (CAST(:id AS uuid), 'devices.execute')",
         {"id": ids["both"]})
    with pytest.raises(RuntimeError, match="rollback refused"):
        with _temporary_migration_database_url(legacy_db):
            command.downgrade(_alembic_config(), LEGACY)


def _revision_module(name):
    path = Path("/app/alembic/versions") / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"mtg_rehearsal_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fence_identity(connection, stage):
    identity = dict(connection.execute(sa.text(
        "SELECT pg_backend_pid() AS backend, txid_current() AS transaction"
    )).mappings().one())
    locks = connection.execute(sa.text("""
        SELECT c.relname AS table_name, l.mode
        FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE l.pid = pg_backend_pid() AND l.granted AND n.nspname = 'public'
          AND c.relname IN ('roles', 'user_roles', 'users') AND l.mode = 'ShareRowExclusiveLock'
        ORDER BY c.relname
    """)).mappings().all()
    assert [row["table_name"] for row in locks] == ["roles", "user_roles", "users"]
    return {**identity, "stage": stage, "locks": [dict(row) for row in locks]}


def _prove_blocked_authority_write(connection, database_url, table, statement, parameters, receipts):
    """One real write attempt; observe its wait, cancel, and verify rollback."""
    ready = threading.Event()
    worker = {}
    receipt = {"table": table, "write_attempts": 1, "observer_samples": 0}
    receipts.append(receipt)
    migrator = connection.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()

    async def attempt():
        engine = create_async_engine(database_url, poolclass=sa.pool.NullPool,
                                     connect_args={"timeout": 2, "command_timeout": 3})
        try:
            async with engine.begin() as contender:
                await contender.execute(sa.text("SET LOCAL statement_timeout = '3s'"))
                # Fail on the server before the existing 3s client command
                # deadline, including a lock wait during SQL Parse/prepare.
                await contender.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
                worker["backend"] = (await contender.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one()
                ready.set()
                await contender.execute(sa.text(statement), parameters)
            return {"write_succeeded": True}
        except sa.exc.DBAPIError as exc:
            return {"write_succeeded": False, "sqlstate": getattr(exc.orig, "sqlstate", None),
                    "transaction_rolled_back": True}
        finally:
            ready.set()
            await engine.dispose()

    def run():
        return asyncio.run(asyncio.wait_for(attempt(), timeout=5))

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run)
        observed = None
        cancel_requested = False
        try:
            assert ready.wait(2.5), "Single contender did not establish its synthetic session"
            assert "backend" in worker, "Single contender failed before its authorization write"
            # Poll only the lock observer, never retry the authorization write.
            # The contender also has a short server-side statement deadline.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                target_locks = connection.execute(sa.text("""
                    SELECT l.mode, l.granted,
                           CAST(:migrator AS integer) = ANY(pg_blocking_pids(l.pid)) AS blocked_by_migrator
                    FROM pg_locks l
                    WHERE l.pid = :pid AND l.locktype = 'relation'
                      AND l.relation = CAST(:relation AS regclass)
                """), {"pid": worker["backend"], "migrator": migrator,
                          "relation": f"public.{table}"}).mappings().all()
                receipt["observer_samples"] += 1
                receipt["target_relation_locks"] = [dict(row) for row in target_locks]
                observed = next((row for row in target_locks if not row["granted"]), None)
                if observed is not None:
                    break
                if future.done():
                    break
                time.sleep(0.01)
            assert observed is not None, "Single authority write was not observed waiting behind the migration fence"
            assert dict(observed) == {"mode": "RowExclusiveLock", "granted": False,
                                      "blocked_by_migrator": True}
        finally:
            observer_error = sys.exc_info()[1]
            cancellation_error = None
            if observer_error is not None:
                receipt["observer_failure_type"] = type(observer_error).__name__
            if "backend" in worker and not future.done():
                try:
                    connection.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                    cancelled = connection.execute(sa.text("""
                        SELECT pg_cancel_backend(pid) FROM pg_stat_activity
                        WHERE pid = :pid AND datname = current_database() AND usename = current_user
                    """), {"pid": worker["backend"]}).scalars().all()
                    # The short server deadline may have already closed the
                    # session between observation and bounded cancellation.
                    assert all(cancelled), "Could not cancel the owned synthetic contender"
                    cancel_requested = bool(cancelled)
                    receipt["cancel_target_rows"] = len(cancelled)
                except Exception as exc:
                    cancellation_error = exc
                    receipt["cancellation_failure_type"] = type(exc).__name__
            try:
                result = future.result(timeout=6)
                receipt["worker_result"] = result
            except Exception as worker_error:
                receipt["worker_failure_type"] = type(worker_error).__name__
                if observer_error is not None:
                    raise observer_error from None
                raise
            if observer_error is None and cancellation_error is not None:
                raise cancellation_error
        assert result["write_succeeded"] is False and result["transaction_rolled_back"] is True
        assert result["sqlstate"] in {"57014", "55P03"}
        connection.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
        assert connection.execute(sa.text("""
            SELECT count(*) FROM pg_stat_activity WHERE pid = :pid AND datname = current_database()
        """), {"pid": worker["backend"]}).scalar_one() == 0
    receipt.update({"waiting_mode": observed["mode"], "blocked_by_migrator": True,
                    "cancel_sqlstate": result["sqlstate"], "transaction_rolled_back": True,
                    "contender_session_closed": True, "abort_method": "server_lock_deadline" if result["sqlstate"] == "55P03"
                    else "observer_cancel" if cancel_requested else "server_statement_deadline"})


@pytest.mark.parametrize("outcome", ["commit", "rollback"])
def test_real_alembic_fence_blocks_writes_and_spans_transaction(legacy_db, outcome, record_testsuite_property):
    ids = _seed(legacy_db)
    before = _legacy_snapshot(legacy_db)
    database = sa.engine.make_url(legacy_db).database
    stages, contenders = [], []

    def trace(connection, cursor, statement, parameters, context, executemany):
        if connection.engine.url.database != database:
            return
        normalized = " ".join(statement.split())
        stage = None
        if normalized.startswith("LOCK TABLE public.roles"):
            stage = "bridge" if "public.role_permissions" in normalized else "preflight"
        elif normalized == "DROP INDEX IF EXISTS uq_roles_key":
            stage = "r2b"
        elif normalized.startswith("UPDATE alembic_version SET") and BRIDGE in normalized:
            stage = "cutover"
        if stage is None:
            return
        stages.append(_fence_identity(connection, stage))
        if stage == "preflight":
            for table, write, arguments in (
                ("roles", "UPDATE public.roles SET permissions = '{}'::jsonb WHERE id = CAST(:role AS uuid)",
                 {"role": ids["both"]}),
                ("user_roles", "DELETE FROM public.user_roles WHERE user_id = CAST(:user AS uuid) "
                 "AND role_id = CAST(:role AS uuid)", {"user": ids["user_a"], "role": ids["both"]}),
                ("users", "UPDATE public.users SET organization_id = CAST(:foreign AS uuid) WHERE id = CAST(:user AS uuid)",
                 {"foreign": ids["org_b"], "user": ids["user_a"]}),
            ):
                _prove_blocked_authority_write(connection, legacy_db, table, write, arguments, contenders)
        else:
            archived_txid = connection.execute(sa.text(
                "SELECT transaction_id FROM mtg_rbac_bridge.rollback_record WHERE singleton"
            )).scalar_one()
            assert archived_txid == stages[-1]["transaction"]
        if stage == "cutover" and outcome == "rollback":
            raise RuntimeError("injected fence cutover failure")

    # Hook actual SQL emitted by unmodified Alembic revision bodies, including
    # its real version update. Nothing stamps or substitutes a migration body.
    sa.event.listen(Engine, "after_cursor_execute", trace)
    try:
        if outcome == "rollback":
            with pytest.raises(RuntimeError, match="injected fence cutover failure"):
                _upgrade(legacy_db, BRIDGE)
        else:
            _upgrade(legacy_db, BRIDGE)
    finally:
        sa.event.remove(Engine, "after_cursor_execute", trace)
        # Pytest captures this bounded, static-field diagnostic and includes
        # it on failures. It is deliberately outside the closed proof schema.
        print("MTG RBAC fence observations: " + json.dumps({
            "stages_observed": [{"stage": stage["stage"], "locks": stage["locks"]} for stage in stages],
            "contender_observations": contenders,
        }, sort_keys=True))
    assert [stage["stage"] for stage in stages] == ["preflight", "r2b", "bridge", "cutover"]
    assert len({(stage["backend"], stage["transaction"]) for stage in stages}) == 1
    assert [row["table"] for row in contenders] == ["roles", "user_roles", "users"]
    if outcome == "rollback":
        _assert_failed_segment_left_legacy(legacy_db, before)
    else:
        assert _current_revision(legacy_db) == BRIDGE
        after = _legacy_snapshot(legacy_db)
        assert after["memberships"] == before["memberships"]
        assert after["knowledge"] == before["knowledge"]
        assert after["knowledge_indexes"] == before["knowledge_indexes"]
        assert {r["id"]: r["permissions"] for r in after["roles"] if r["id"] == ids["both"]} == {
            ids["both"]: {key: True for key in GRANTS}
        }
        assert next(u for u in after["users"] if u["id"] == ids["user_a"])["organization_id"] == ids["org_a"]
    assert _sql(legacy_db, """
        SELECT count(*) AS held FROM pg_locks WHERE pid = :pid
    """, {"pid": stages[0]["backend"]})[0]["held"] == 0
    record_testsuite_property(f"mtg_rbac_maintenance_fence_{outcome}", json.dumps({
        "outcome": outcome, "same_backend_and_transaction": True, "transaction_locks_released": True,
        "stages": [{"stage": stage["stage"], "locks": stage["locks"]} for stage in stages],
        "contenders": [{key: item[key] for key in (
            "table", "waiting_mode", "blocked_by_migrator", "write_attempts", "cancel_sqlstate",
            "transaction_rolled_back", "contender_session_closed", "abort_method",
        )} for item in contenders], "original_authority_preserved": True,
        "external_full_graph_release_fence_proven": False,
    }, sort_keys=True))


def _direct_segment(database_url, *, after_r2b=None):
    """Exercise real revision bodies in one transaction; no altered code."""
    async def apply(connection):
        def sync_apply(bind):
            with Operations.context(MigrationContext.configure(bind)):
                _revision_module("20261017_mtg_rbac_preflight").upgrade()
                _revision_module("20260929_r2b_roles").upgrade()
                if after_r2b is not None:
                    after_r2b(bind)
                _revision_module(BRIDGE).upgrade()
        await connection.run_sync(sync_apply)
    asyncio.run(_run_in_database(database_url, apply))


def test_mid_upgrade_failure_rolls_back_r2b_and_preflight(legacy_db):
    _seed(legacy_db)
    before = _legacy_snapshot(legacy_db)

    def fail(_bind):
        raise RuntimeError("injected mid-upgrade failure")

    with pytest.raises(RuntimeError, match="injected mid-upgrade"):
        _direct_segment(legacy_db, after_r2b=fail)
    _assert_failed_segment_left_legacy(legacy_db, before)


@pytest.mark.parametrize("kind", ["platform", "managed_organizations", "foreign_organization"])
def test_unexpected_boundary_refuses_without_partial_acceptance(legacy_db, kind):
    ids = _seed(legacy_db)
    before = _legacy_snapshot(legacy_db)

    def inject(bind):
        bind.execute(sa.text("""
            INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id)
            VALUES (gen_random_uuid(), CAST(:user AS uuid), CAST(:role AS uuid), :kind, CAST(:org AS uuid))
        """), {"user": ids["user_a"], "role": ids["both"],
                 "kind": "organization" if kind == "foreign_organization" else kind,
                 "org": ids["org_b"] if kind == "foreign_organization" else None})

    with pytest.raises(RuntimeError, match="unexpected or foreign"):
        _direct_segment(legacy_db, after_r2b=inject)
    _assert_failed_segment_left_legacy(legacy_db, before)


def test_only_proven_missing_home_boundary_is_repaired(legacy_db):
    ids = _seed(legacy_db)

    def remove(bind):
        bind.execute(sa.text("DELETE FROM user_role_boundaries WHERE user_id = CAST(:user AS uuid) "
                             "AND role_id = CAST(:role AS uuid)"),
                     {"user": ids["user_a"], "role": ids["both"]})

    _direct_segment(legacy_db, after_r2b=remove)
    repaired = _sql(legacy_db, "SELECT created_boundary_rows FROM mtg_rbac_bridge.rollback_record")[0]
    assert len(repaired["created_boundary_rows"]) == 1
    row = _sql(legacy_db, """
        SELECT organization_id::text, kind FROM user_role_boundaries WHERE id = CAST(:id AS uuid)
    """, {"id": repaired["created_boundary_rows"][0]})[0]
    assert row == {"organization_id": ids["org_a"], "kind": "organization"}


def test_full_incoming_graph_from_seeded_legacy_schema(legacy_db):
    _seed(legacy_db)
    _upgrade(legacy_db, "head")
    assert _current_revision(legacy_db) == "20261012_oauth_ownership"
    assert _sql(legacy_db, "SELECT count(*) AS count FROM knowledge_namespace_roles")[0]["count"] == 2
    assert _sql(legacy_db, "SELECT count(*) AS count FROM role_permissions WHERE permission = 'agents.promote'")[0]["count"] == 1
