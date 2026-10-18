#!/usr/bin/env python3
"""Container phases for test.sh's synthetic fork-head dump/restore rehearsal.

The host wrapper runs matching pg_dump/pg_restore in the existing PostgreSQL
test service. This file runs only in the normal Linux test-runner container;
it never accepts a connection URL or touches an existing application DB.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

FORK_PARENT = "20261006_mtg_upstream_sdk"
FORK_HEAD = "20261012_work_delivery_started"
CANDIDATE_HEAD = "20261012_oauth_ownership"
OLD_COMMIT = "5085cc467ffb1ed35de9c9ca48890ab0d929e1d0"
FORWARD_ONLY_CUT = "20260929_drop_builder_residue"
VARIANTS = ("clean", "delivery_started_historical_stamp", "withdrawn_builder_residue")
NAME_RE = re.compile(r"^bifrost_r2b_rehearsal_[0-9a-f]{12}$")
RUN_RE = re.compile(r"^[0-9a-f]{32}$")
ADMIN_ROLE = "00000000-0000-0000-0000-000000000005"
USER_ROLE = "00000000-0000-0000-0000-000000000006"
DECRYPTION_ROLE = "00000000-0000-0000-0000-000000000008"
GRANTS = {
    "can_manage_config": "devices.readwrite",
    "can_execute_devices": "devices.execute",
    "can_promote_agent": "agents.promote",
}


def _seed_backup_documents(database_url: str, ids: dict) -> dict:
    """Synthetic fixed vectors only; no embedder, vendor or network call."""
    bridge, _ = _helpers()
    documents = {}
    for scope, organization_id in (("home", ids["org_a"]), ("global", None), ("foreign", ids["org_b"])):
        document = {"id": str(uuid4()), "content": f"Synthetic RBAC backup {scope} document."}
        bridge._sql(database_url, """
            INSERT INTO knowledge_store
              (id, namespace, organization_id, key, content, metadata, embedding, created_by, chunk_index, chunk_count)
            VALUES (CAST(:id AS uuid), 'synthetic-namespace', CAST(:org AS uuid), :key, :content,
                    '{"synthetic": true}'::jsonb, '[1,0,0]'::vector, CAST(:user AS uuid), 0, 1)
        """, {"id": document["id"], "org": organization_id, "key": f"synthetic-{scope}",
              "content": document["content"], "user": ids["user_a"]})
        documents[scope] = document
    return documents


def _old_reader_fixture(state: dict) -> dict:
    ids, documents, before = state["fixture_ids"], state["fixture_documents"], state["source_legacy"]
    role_json = {row["id"]: row["permissions"] for row in before["roles"]}
    counts = {role_id: sum(row["role_id"] == role_id for row in before["knowledge"]) for role_id in role_json}
    return {
        "run_id": state["run_id"], "variant": state["variant"], "restored_database": state["restored_database"],
        "admin_user_id": ids["admin"], "role_json": role_json,
        "device_checks": [{"user_id": ids[key], "manage": True, "execute": True}
                          for key in ("user_a", "user_b", "admin")],
        "namespace_assignments": [{key: row[key] for key in ("id", "namespace", "organization_id", "role_id")}
                                  for row in before["knowledge"]],
        "knowledge_counts": counts,
        "knowledge": {
            "organization_id": ids["org_a"], "user_id": ids["user_a"], "namespace": "synthetic-namespace",
            "home_document_id": documents["home"]["id"], "global_document_id": documents["global"]["id"],
            "foreign_document_id": documents["foreign"]["id"],
            "expected_home_content": documents["home"]["content"],
            "expected_global_content": documents["global"]["content"],
            "expected_namespace_home_count": 1, "expected_namespace_global_count": 1,
        },
    }


def validate_database_name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise ValueError("Refusing a database outside the synthetic rehearsal namespace")
    return name


def _helpers():
    from tests.e2e.platform import test_mtg_rbac_bridge_migration_rehearsal as bridge
    from tests.e2e.platform import test_r2b_roles_migration_rehearsal as legacy

    return bridge, legacy


def _digest(value) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(serialized).hexdigest()


def _save(path: Path, state: dict, mode: int = 0o600) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _owned_create(name: str, path: Path, state: dict) -> None:
    _, legacy = _helpers()
    validate_database_name(name)

    async def create(connection):
        exists = await connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :name)"),
                                         {"name": name})
        if exists:
            raise RuntimeError("Refusing to reuse an existing rehearsal database")
        await connection.execute(sa.text(f"CREATE DATABASE {_quote(name)}"))
        if name not in state["owned_databases"]:
            state["owned_databases"].append(name)
        _save(path, state)
        # Marker and names are validated literals, never operator SQL input.
        marker = f"mtg-rbac-backup:{state['run_id']}:{state['variant']}"
        await connection.execute(sa.text(f"COMMENT ON DATABASE {_quote(name)} IS '{marker}'"))

    asyncio.run(legacy._with_admin_connection(create))


def _catalog(database_url: str) -> dict:
    bridge, _ = _helpers()
    queries = {
        "schemas": """
          SELECT n.nspname, pg_get_userbyid(n.nspowner) AS owner,
                 (SELECT jsonb_agg(jsonb_build_array(a.grantor::regrole::text, a.grantee::regrole::text,
                             a.privilege_type, a.is_grantable)
                          ORDER BY a.grantor, a.grantee, a.privilege_type, a.is_grantable)
                  FROM aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a) AS acl
          FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
          ORDER BY n.nspname
        """,
        "columns": """
          SELECT c.relname AS relation, a.attname AS column_name,
                 format_type(a.atttypid, a.atttypmod) AS type,
                 a.attnotnull, a.attidentity, a.attgenerated,
                 pg_get_expr(d.adbin, d.adrelid) AS default_expression
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = c.oid
          LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
          WHERE n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped
            AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
          ORDER BY c.relname, a.attnum
        """,
        "constraints": """
          SELECT c.relname AS relation, k.conname, k.contype, k.convalidated,
                 pg_get_constraintdef(k.oid) AS definition
          FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'public' ORDER BY c.relname, k.conname
        """,
        "indexes": """
          SELECT c.relname AS relation, i.relname AS index_name,
                 x.indisvalid, x.indisunique, x.indnullsnotdistinct,
                 pg_get_indexdef(x.indexrelid) AS definition
          FROM pg_index x JOIN pg_class c ON c.oid = x.indrelid
          JOIN pg_class i ON i.oid = x.indexrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'public' ORDER BY c.relname, i.relname
        """,
        "functions": """
          SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS arguments,
                 pg_get_functiondef(p.oid) AS definition, pg_get_userbyid(p.proowner) AS owner,
                 (SELECT jsonb_agg(jsonb_build_array(a.grantor::regrole::text, a.grantee::regrole::text,
                             a.privilege_type, a.is_grantable)
                          ORDER BY a.grantor, a.grantee, a.privilege_type, a.is_grantable)
                  FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a) AS acl
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
          WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
          ORDER BY p.proname, pg_get_function_identity_arguments(p.oid)
        """,
        "triggers": """
          SELECT c.relname AS relation, t.tgname, t.tgenabled, pg_get_triggerdef(t.oid) AS definition
          FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'public' AND NOT t.tgisinternal ORDER BY c.relname, t.tgname
        """,
        "views": """
          SELECT c.relname, pg_get_viewdef(c.oid) AS definition
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm') ORDER BY c.relname
        """,
        "enums": """
          SELECT t.typname, e.enumsortorder, e.enumlabel
          FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
          JOIN pg_enum e ON e.enumtypid = t.oid
          WHERE n.nspname = 'public' ORDER BY t.typname, e.enumsortorder
        """,
        "extensions": "SELECT extname, extversion FROM pg_extension ORDER BY extname",
        "relation_privileges": """
          SELECT c.relname, c.relkind, pg_get_userbyid(c.relowner) AS owner,
                 (SELECT jsonb_agg(jsonb_build_array(a.grantor::regrole::text, a.grantee::regrole::text,
                             a.privilege_type, a.is_grantable)
                          ORDER BY a.grantor, a.grantee, a.privilege_type, a.is_grantable)
                  FROM aclexplode(coalesce(c.relacl, acldefault(
                       CASE WHEN c.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END, c.relowner))) a) AS acl
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') ORDER BY c.relname
        """,
    }
    result = {key: [dict(row) for row in bridge._sql(database_url, query)] for key, query in queries.items()}
    # Normalize driver-specific scalar wrappers before saving/comparing.
    return json.loads(json.dumps(result, sort_keys=True, default=str))


def _table_data(database_url: str) -> dict:
    bridge, _ = _helpers()
    tables = bridge._sql(database_url, """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname
    """)
    result = {}
    for table in tables:
        name = table["relname"]
        row = bridge._sql(database_url, f"""
          SELECT count(*) AS rows, encode(sha256(convert_to(coalesce(
            jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb)::text, 'UTF8')), 'hex') AS sha256
          FROM public.{_quote(name)} t
        """)[0]
        result[name] = dict(row)
    return result


def assert_restore_parity(expected_catalog: dict, actual_catalog: dict,
                          expected_data: dict, actual_data: dict) -> None:
    if expected_catalog != actual_catalog:
        differing = sorted(key for key in expected_catalog if expected_catalog.get(key) != actual_catalog.get(key))
        raise AssertionError(f"Restored schema differs in catalog classes: {', '.join(differing)}")
    if expected_data != actual_data:
        differing = sorted(key for key in set(expected_data) | set(actual_data)
                           if expected_data.get(key) != actual_data.get(key))
        raise AssertionError(f"Restored rows differ in {len(differing)} public tables")


def _migration_sources() -> dict:
    _, legacy = _helpers()
    script = ScriptDirectory.from_config(legacy._alembic_config())
    baseline = list(script.walk_revisions(base="base", head=FORK_HEAD))
    if len(baseline) != 330 or script.get_current_head() != CANDIDATE_HEAD:
        raise AssertionError("Expected 330 preserved fork revisions and the approved single candidate head")
    fork_files = {Path(revision.path).name: hashlib.sha256(Path(revision.path).read_bytes()).hexdigest()
                  for revision in baseline}
    candidate_files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in Path("/app/alembic/versions").glob("*.py")}
    return {"fork_revision_count": len(baseline), "fork_sources_sha256": _digest(fork_files),
            "candidate_revision_files": len(candidate_files), "candidate_sources_sha256": _digest(candidate_files)}


def prepare(args, path: Path, state: dict) -> None:
    bridge, legacy = _helpers()
    if path.exists():
        raise RuntimeError("Refusing to overwrite existing rehearsal state")
    state.update({"contract_version": 1, "run_id": args.run_id, "variant": args.variant,
                  "source_database": validate_database_name(args.source),
                  "restored_database": validate_database_name(args.restored),
                  "owned_databases": [], "status": "preparing", "synthetic_data_only": True,
                  "timings_seconds": {}, "migration_sources": _migration_sources()})
    if args.source == args.restored:
        raise ValueError("Source and restoration databases must differ")
    _save(path, state)
    source_url = legacy._direct_database_url(args.source)
    _owned_create(args.source, path, state)
    _owned_create(args.restored, path, state)
    legacy._upgrade(source_url, FORK_PARENT)
    assert legacy._current_revision(source_url) == FORK_PARENT
    if args.variant == "delivery_started_historical_stamp":
        # Known historical shape is documented by the preserved repair itself:
        # an already-stamped SDK head lacks the later-edited parent's column.
        bridge._sql(source_url, "ALTER TABLE public.work_deliveries DROP COLUMN started_at")
        state["historical_variant"] = {
            "stamp": FORK_PARENT, "missing_column": "work_deliveries.started_at",
            "source": "api/alembic/versions/20261012_work_delivery_started.py",
        }
    legacy._upgrade(source_url, FORK_HEAD)
    assert legacy._current_revision(source_url) == FORK_HEAD
    assert bridge._sql(source_url, """
        SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
          AND table_name = 'work_deliveries' AND column_name = 'started_at'
    """)
    state["fixture_ids"] = bridge._seed(source_url)
    state["fixture_documents"] = _seed_backup_documents(source_url, state["fixture_ids"])
    if args.variant == "withdrawn_builder_residue":
        from tests.e2e.platform.test_builder_residue_migration_rehearsal import _apply_residue
        asyncio.run(_apply_residue(source_url, str(uuid4())))
        state["historical_variant"] = {
            "stamp": FORK_HEAD, "source": "api/tests/fixtures/builder_residue.sql",
            "meaning": "synthetic reproduction of the documented withdrawn Builder residue",
        }
    state["source_head"] = legacy._current_revision(source_url)
    state["source_catalog"] = _catalog(source_url)
    state["source_data"] = _table_data(source_url)
    state["source_legacy"] = dict(bridge._legacy_snapshot(source_url))
    _save(path.parent / "old-reader-fixture.json", _old_reader_fixture(state), mode=0o644)
    empty_tables = bridge._sql(legacy._direct_database_url(args.restored), """
        SELECT count(*) AS count FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    """)[0]["count"]
    assert empty_tables == 0, "Restoration target must be a newly created empty database"
    state["target_initial_public_tables"] = empty_tables
    state["status"] = "prepared_for_dump"


def restored(args, path: Path, state: dict) -> None:
    _, legacy = _helpers()
    archive = path.parent / "fork-head.dump"
    contents = archive.read_bytes()
    if contents[:5] != b"PGDMP":
        raise AssertionError("Expected an actual PostgreSQL custom-format archive")
    digest = hashlib.sha256(contents).hexdigest()
    if digest != args.dump_sha256:
        raise AssertionError("Restored archive digest does not match the host tool receipt")
    restored_url = legacy._direct_database_url(state["restored_database"])
    assert legacy._current_revision(restored_url) == FORK_HEAD
    actual_catalog, actual_data = _catalog(restored_url), _table_data(restored_url)
    assert_restore_parity(state["source_catalog"], actual_catalog, state["source_data"], actual_data)
    state.update({"dump_sha256": digest, "dump_bytes": len(contents), "archive_magic": "PGDMP",
                  "restored_head_before_upgrade": legacy._current_revision(restored_url),
                  "schema_restore_sha256": _digest(actual_catalog), "data_restore_sha256": _digest(actual_data),
                  "restored_schema_and_all_public_rows_equal": True, "status": "restored"})
    state["timings_seconds"].update({"pg_dump": args.dump_elapsed_ns / 1_000_000_000,
                                     "pg_restore": args.restore_elapsed_ns / 1_000_000_000})
    state["postgres_tool_commands"] = [
        "pg_dump --no-password --format=custom",
        'pg_restore --no-password --dbname "$PGDATABASE" --exit-on-error --single-transaction --clean --if-exists',
    ]
    state["initial_restoration_verified"] = True


class InjectedMigrationInterruption(RuntimeError):
    """A test-owned failure after the forward-only cut point was committed."""


def interrupt(args, path: Path, state: dict) -> None:
    _, legacy = _helpers()
    if not state.get("initial_restoration_verified"):
        raise AssertionError("Verify the original dump restoration before the interruption rehearsal")
    url = legacy._direct_database_url(state["restored_database"])
    legacy._upgrade(url, FORWARD_ONLY_CUT)
    assert legacy._current_revision(url) == FORWARD_ONLY_CUT
    assert _catalog(url) != state["source_catalog"], "Forward-only upgrade did not change the fork schema"
    injected = False

    def fail_next_mutation(connection, cursor, statement, parameters, context, executemany):
        nonlocal injected
        if connection.engine.url.database == state["restored_database"] and re.match(
            r"\s*(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|TRUNCATE)\b", statement, re.IGNORECASE
        ):
            injected = True
            raise InjectedMigrationInterruption("Injected interruption after committed forward-only migration")

    sa.event.listen(Engine, "before_cursor_execute", fail_next_mutation)
    try:
        try:
            legacy._upgrade(url, "head")
        except InjectedMigrationInterruption:
            pass
        else:
            raise AssertionError("Expected the deliberate interrupted upgrade to fail")
    finally:
        sa.event.remove(Engine, "before_cursor_execute", fail_next_mutation)
    assert injected and legacy._current_revision(url) == FORWARD_ONLY_CUT
    state.update({"committed_forward_only_head": FORWARD_ONLY_CUT,
                  "interrupted_upgrade_head": legacy._current_revision(url),
                  "post_forward_only_interruption_observed": True,
                  "whole_chain_downgrade_claimed": False,
                  "matching_5085_runtime_restoration": "pending separate old-code debug lane",
                  "status": "interruption_verified"})


def reset_target(args, path: Path, state: dict) -> None:
    _, legacy = _helpers()
    if not state.get("post_forward_only_interruption_observed"):
        raise AssertionError("Only the deliberately interrupted, owned restore target may be recreated")
    target = validate_database_name(state["restored_database"])
    marker = f"mtg-rbac-backup:{state['run_id']}:{state['variant']}"

    async def ownership(connection):
        return await connection.scalar(sa.text(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = :name"
        ), {"name": target})

    if target not in state["owned_databases"] or asyncio.run(legacy._with_admin_connection(ownership)) != marker:
        raise RuntimeError("Restore target ownership differs; refusing recreation")
    asyncio.run(legacy._drop_database(target))
    _owned_create(target, path, state)
    assert _table_data(legacy._direct_database_url(target)) == {}
    state["restore_target_recreated_empty_after_interruption"] = True
    state["status"] = "ready_for_recovery_restore"


def recovered(args, path: Path, state: dict) -> None:
    if not state.get("restore_target_recreated_empty_after_interruption"):
        raise AssertionError("A fresh owned target is required for consistent backup recovery")
    original_restore_seconds = state["timings_seconds"]["pg_restore"]
    restored(args, path, state)
    state["timings_seconds"]["recovery_pg_restore"] = state["timings_seconds"]["pg_restore"]
    state["timings_seconds"]["pg_restore"] = original_restore_seconds
    state.update({"post_forward_only_backup_restore_parity_verified": True,
                  "recovery_restored_head": FORK_HEAD, "status": "recovery_restored"})


def assert_old_reader_proof(result: dict, provenance: dict, cleanup_receipt: dict) -> None:
    if not (result.get("status") == "passed" and result.get("source_commit") == OLD_COMMIT
            and result.get("restored_head") == FORK_HEAD and result.get("transaction_read_only") is True
            and result.get("old_application_modules_hash_verified", 0) > 0
            and result.get("knowledge_documents_read") == 2
            and result.get("foreign_knowledge_document_denied") is True
            and result.get("http_auth_startup_ui_and_deployment_proven") is False):
        raise AssertionError("Frozen old-application reader proof is missing, wrong-source or incomplete")
    if not (provenance.get("status") == "passed" and provenance.get("source_sha") == OLD_COMMIT
            and re.fullmatch(r"sha256:[0-9a-f]{64}", provenance.get("image_id", ""))
            and re.fullmatch(r"[0-9a-f]{64}", provenance.get("source_archive_sha256", ""))
            and provenance.get("source_bind_read_only") is True
            and provenance.get("synthetic_data_only") is True
            and provenance.get("host_ports_published") is False):
        raise AssertionError("Frozen old-code image/source provenance differs")
    inputs = provenance.get("dependency_inputs_sha256", {})
    if set(inputs) != {"api/Dockerfile.dev", "pyproject.toml", "requirements.lock", "requirements-pyright.lock"} or any(
        not re.fullmatch(r"[0-9a-f]{64}", value) for value in inputs.values()
    ):
        raise AssertionError("Frozen dependency input provenance is incomplete")
    if cleanup_receipt.get("status") != "passed" or any(cleanup_receipt.get(key) is not True for key in (
        "container_removed", "image_removed", "temporary_source_removed", "envfile_removed"
    )):
        raise AssertionError("Frozen old-reader resource/credential cleanup was not verified")


def old_readers(args, path: Path, state: dict) -> None:
    if not state.get("post_forward_only_backup_restore_parity_verified"):
        raise AssertionError("Recover the consistent original backup before exercising old application readers")
    result = json.loads((path.parent / "old-reader-result.json").read_text())
    provenance = json.loads((path.parent / "old-reader-provenance.json").read_text())
    cleanup_receipt = json.loads((path.parent / "old-reader-cleanup.json").read_text())
    assert_old_reader_proof(result, provenance, cleanup_receipt)
    fixture = _old_reader_fixture(state)
    assert result["role_json_rows_verified"] == len(fixture["role_json"])
    assert result["device_personas_verified"] == len(fixture["device_checks"])
    assert result["namespace_assignment_rows_verified"] == len(fixture["namespace_assignments"])
    assert result["knowledge_role_counts_verified"] == len(fixture["knowledge_counts"])
    host = json.loads((path.parent.parent / "host-provenance.json").read_text())
    assert provenance["postgres_container"] == host["postgres_container"]
    assert provenance["network"] == host["compose_project"] + "_default"
    _, legacy = _helpers()
    url = legacy._direct_database_url(state["restored_database"])
    assert legacy._current_revision(url) == FORK_HEAD
    assert_restore_parity(state["source_catalog"], _catalog(url), state["source_data"], _table_data(url))
    state.update({"matching_5085_runtime_restoration": "passed: actual frozen imported readers in a read-only transaction",
                  "matching_old_reader_verification": True, "old_reader_restore_state_unchanged": True,
                  "old_reader_proof": {"result": result, "provenance": provenance, "cleanup": cleanup_receipt},
                  "status": "old_readers_verified"})


def upgrade(args, path: Path, state: dict) -> None:
    bridge, legacy = _helpers()
    if not state.get("post_forward_only_backup_restore_parity_verified") or not state.get("matching_old_reader_verification"):
        raise AssertionError("An actual verified dump restoration must precede candidate upgrade")
    database_url = legacy._direct_database_url(state["restored_database"])
    assert legacy._current_revision(database_url) == FORK_HEAD
    legacy._upgrade(database_url, "head")
    assert legacy._current_revision(database_url) == CANDIDATE_HEAD
    before = state["source_legacy"]
    after = dict(bridge._legacy_snapshot(database_url))
    originals = {row["id"]: row["permissions"] for row in before["roles"]}
    actual_json = {row["id"]: row["permissions"] for row in after["roles"] if row["id"] in originals}
    assert actual_json == originals, "Original role IDs and inert JSON must be retained exactly"
    expected_grants = {(role_id, permission) for role_id, payload in originals.items()
                       for key, permission in GRANTS.items() if payload.get(key) is True}
    actual_grants = {(row["role_id"], row["permission"]) for row in bridge._sql(database_url,
        "SELECT role_id::text, permission FROM role_permissions WHERE role_id = ANY(CAST(:ids AS uuid[]))",
        {"ids": list(originals)})}
    assert actual_grants == expected_grants, "Exact original-role bridge grants differ"
    holders = {row["id"]: row for row in before["users"]}
    expected_boundaries = {(row["user_id"], row["role_id"], "organization", holders[row["user_id"]]["organization_id"])
                           for row in before["memberships"] if holders[row["user_id"]]["organization_id"] is not None}
    actual_boundaries = {(row["user_id"], row["role_id"], row["kind"], row["organization_id"])
                        for row in bridge._sql(database_url, """
          SELECT user_id::text, role_id::text, kind, organization_id::text FROM user_role_boundaries
          WHERE role_id = ANY(CAST(:ids AS uuid[]))
        """, {"ids": list(originals)})}
    assert actual_boundaries == expected_boundaries, "Original assignments must have exact home-org boundaries"
    old_memberships = {(row["user_id"], row["role_id"]): row for row in before["memberships"]}
    preserved = {(row["user_id"], row["role_id"]): row for row in after["memberships"]
                 if row["role_id"] in originals}
    assert preserved == old_memberships, "Original membership rows changed"
    assert all(row["role_id"] in originals or row["role_id"] in (ADMIN_ROLE, DECRYPTION_ROLE)
               for row in after["memberships"]), "Unexpected additional role assignments appeared"
    assert after["knowledge"] == before["knowledge"], "Knowledge row IDs, NULL/global scope or metadata changed"
    assert after["knowledge_indexes"] == before["knowledge_indexes"], "Knowledge index identity/semantics changed"
    assert not bridge._sql(database_url, """
        SELECT 1 FROM role_permissions p JOIN roles r ON r.id = p.role_id
        WHERE r.is_builtin AND p.permission = ANY(CAST(:grants AS text[]))
    """, {"grants": list(GRANTS.values())}), "Builtin seeding activated bridge permissions"
    from shared.builtin_roles import PLATFORM_OPERATOR_PERMISSIONS, USER_BASE_PERMISSIONS

    expected_builtins = {
        ADMIN_ROLE: {"*"}, USER_ROLE: set(USER_BASE_PERMISSIONS),
        "00000000-0000-0000-0000-000000000007": set(PLATFORM_OPERATOR_PERMISSIONS),
        DECRYPTION_ROLE: {"secrets.read"},
    }
    actual_builtins = {role_id: set() for role_id in expected_builtins}
    for row in bridge._sql(database_url, """
        SELECT role_id::text, permission FROM role_permissions WHERE role_id = ANY(CAST(:ids AS uuid[]))
    """, {"ids": list(expected_builtins)}):
        actual_builtins[row["role_id"]].add(row["permission"])
    assert actual_builtins == expected_builtins, "Actual final permission rows differ for a fixed builtin role"
    actual_holders = {row["id"]: row for row in after["users"]}
    for holder_id, original in holders.items():
        final = actual_holders[holder_id]
        assert final["organization_id"] == original["organization_id"]
        assert final["is_superuser"] == original["is_superuser"]
        assert final["base_role_id"] == USER_ROLE
    if state["variant"] == "withdrawn_builder_residue":
        from tests.e2e.platform.test_builder_residue_migration_rehearsal import RESIDUE_TABLES, RESIDUE_COLUMNS

        catalog = _catalog(database_url)
        columns = {(r["relation"], r["column_name"]) for r in catalog["columns"]}
        assert not RESIDUE_COLUMNS & columns
        assert not RESIDUE_TABLES & set(_table_data(database_url))
    assert _migration_sources() == state["migration_sources"], "Migration source changed during rehearsal"
    state.update({"upgraded_head": legacy._current_revision(database_url),
                  "exact_grants_and_home_boundaries_equal": True,
                  "original_memberships_equal": True, "knowledge_rows_ids_indexes_equal": True,
                  "builtin_bridge_grant_count": 0, "bridge_permission_rows": len(actual_grants),
                  "all_four_builtin_final_grants_equal": True,
                  "original_membership_rows": len(old_memberships), "knowledge_rows": len(before["knowledge"]),
                  "status": "upgrade_verified"})


def classify_restore_failure(path: Path, exit_code: int | None) -> dict:
    """Expose only closed categories; raw diagnostics/SQL never enter receipts."""
    result = {"tool": "pg_restore", "exit_code": exit_code,
              "category": "restore_diagnostic_unavailable", "raw_diagnostic_exposed": False}
    try:
        with path.open("rb") as stream:
            size = path.stat().st_size
            prefix = stream.read(32768)
            stream.seek(max(0, size - 32768))
            sample = (prefix + stream.read(32768)).decode("utf-8", errors="replace").lower()
    except OSError:
        return result
    result["diagnostic_bytes"] = size
    categories = (
        ("missing_database_target", r"(?:requires? (?:a )?database|(?:-d/--dbname|--dbname).*must be specified)"),
        ("connection_failure", r"(?:connection .*failed|could not connect|connection refused|could not translate host name)"),
        ("archive_version_unsupported", r"unsupported version .*file header"),
        ("archive_read_failure", r"(?:input file .*does not appear|unexpected end of file|could not read from input file)"),
        ("insufficient_privilege", r"(?:permission denied|must be (?:owner|superuser)|insufficient privilege)"),
        ("schema_conflict", r"(?:already exists|duplicate key value violates unique constraint)"),
        ("restore_sql_failure", r"(?:could not execute query|error:.*(?:sql|syntax error))"),
        ("container_execution_failure", r"(?:no such container|container .*not running|error response from daemon)"),
    )
    result["category"] = next((name for name, pattern in categories if re.search(pattern, sample)),
                              "unclassified_restore_failure")
    return result


def cleanup(args, path: Path, state: dict) -> None:
    if args is not None and args.failed_host_step:
        state["failed_host_step"] = args.failed_host_step
        if args.failed_host_step in ("pg_restore", "recovery_pg_restore"):
            log_name = "pg_restore.log" if args.failed_host_step == "pg_restore" else "recovery-pg_restore.log"
            state["restore_failure"] = classify_restore_failure(
                path.parent / log_name, args.failed_host_exit_code)
    if not state.get("owned_databases"):
        state["database_cleanup_verified"] = True
        state["status"] = "failed"
        return
    _, legacy = _helpers()
    marker = f"mtg-rbac-backup:{state['run_id']}:{state['variant']}"
    failures = []
    for name in state.get("owned_databases", []):
        validate_database_name(name)

        async def inspect(connection):
            return (await connection.execute(sa.text("""
              SELECT shobj_description(oid, 'pg_database') AS marker
              FROM pg_database WHERE datname = :name
            """), {"name": name})).mappings().first()

        try:
            ownership = asyncio.run(legacy._with_admin_connection(inspect))
            if ownership is not None and ownership["marker"] != marker:
                raise RuntimeError("Owned database marker differs; refusing cleanup")
            if ownership is not None:
                asyncio.run(legacy._drop_database(name))
            assert asyncio.run(legacy._with_admin_connection(inspect)) is None
        except Exception as exc:
            failures.append(type(exc).__name__)
    state["database_cleanup_verified"] = not failures
    state["cleanup_failure_types"] = failures
    if failures:
        raise RuntimeError("Synthetic database cleanup could not be verified; inspect the private state file")
    state["status"] = "passed" if state.get("status") == "upgrade_verified" and not state.get("failed_host_step") else "failed"


def _receipt(state: dict) -> dict:
    # Durable public proof contains hashes/counts/heads, never raw dump rows.
    omitted = {"source_catalog", "source_data", "source_legacy", "owned_databases", "fixture_ids", "fixture_documents"}
    return {key: value for key, value in state.items() if key not in omitted}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "restored", "interrupt", "reset_target", "recovered", "old_readers", "upgrade", "cleanup"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--source")
    parser.add_argument("--restored")
    parser.add_argument("--dump-sha256")
    parser.add_argument("--dump-elapsed-ns", type=int)
    parser.add_argument("--restore-elapsed-ns", type=int)
    parser.add_argument("--failed-host-step", choices=(
        "prepare", "pg_dump", "pg_restore", "restored", "interrupt", "reset_target",
        "recovery_pg_restore", "recovered", "old_readers", "upgrade", "cleanup",
    ))
    parser.add_argument("--failed-host-exit-code", type=int, choices=range(256))
    args = parser.parse_args()
    if sys.platform != "linux" or os.environ.get("BIFROST_ENVIRONMENT") != "testing":
        parser.error("This helper only runs in the Linux Docker test-runner environment")
    if not RUN_RE.fullmatch(args.run_id):
        parser.error("Invalid synthetic rehearsal run identity")
    if os.getuid() == 0:
        parser.error("Run phases with the validated non-root host UID to preserve shared log ownership")
    if args.phase == "prepare" and (not args.source or not args.restored):
        parser.error("Prepare requires distinct synthetic source/restoration database names")
    if args.phase in ("restored", "recovered") and (
        not args.dump_sha256 or not re.fullmatch(r"[0-9a-f]{64}", args.dump_sha256)
        or args.dump_elapsed_ns is None or args.dump_elapsed_ns < 0
        or args.restore_elapsed_ns is None or args.restore_elapsed_ns < 0
    ):
        parser.error("Restoration verification requires the actual archive digest and tool timings")
    expected = Path("/tmp/bifrost") / f"rbac-backup-{args.run_id}" / args.variant
    if args.directory.resolve() != expected.resolve():
        parser.error("Evidence directory must be under the fixed per-run test results path")
    args.directory.mkdir(parents=True, exist_ok=True)
    path = args.directory / "private-state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "run_id": args.run_id, "variant": args.variant, "owned_databases": []
    }
    phase_user = {"uid": os.getuid(), "gid": os.getgid()}
    if state.get("phase_user", phase_user) != phase_user:
        parser.error("Rehearsal phase identity differs from the owner of the private artifacts")
    state["phase_user"] = phase_user
    if state and (state.get("run_id") != args.run_id or state.get("variant") != args.variant):
        parser.error("Saved rehearsal identity differs")
    started = time.monotonic()
    try:
        globals()[args.phase](args, path, state)
    except Exception as exc:
        # Exception text from drivers can contain connection URLs/SQL values;
        # preserve the type and phase, not arbitrary driver exception strings.
        state["failed_phase"] = args.phase
        state["failure_type"] = type(exc).__name__
        if isinstance(exc, (AssertionError, RuntimeError, ValueError)):
            state["failure_reason"] = str(exc)
        diagnostic = traceback.format_exc()
        redactions = [value for key, value in os.environ.items()
                      if value and len(value) >= 4 and re.search("PASSWORD|TOKEN|SECRET|KEY", key)]
        for key in ("BIFROST_DATABASE_URL", "BIFROST_DATABASE_URL_SYNC"):
            if os.environ.get(key):
                password = sa.engine.make_url(os.environ[key]).password
                if password:
                    redactions.append(password)
        for value in sorted(redactions, key=len, reverse=True):
            diagnostic = diagnostic.replace(value, "[redacted]")
        (args.directory / "diagnostic.log").write_text(diagnostic, encoding="utf-8")
        state["status"] = "failed"
        print(f"RBAC backup rehearsal {args.phase} failed ({type(exc).__name__}); inspect receipt.json", file=sys.stderr)
        return 1
    finally:
        state.setdefault("timings_seconds", {})[args.phase] = round(time.monotonic() - started, 3)
        _save(path, state)
        _save(args.directory / "receipt.json", _receipt(state), mode=0o644)
        # Phases run as the host's UID. Only count/head/tool logs are public;
        # restore diagnostics can contain SQL, and dump/state remain 0600.
        private_logs = {"pg_dump.log", "pg_restore.log", "recovery-pg_restore.log", "diagnostic.log"}
        for log in args.directory.glob("*.log"):
            log.chmod(0o600 if log.name in private_logs else 0o644)
    print(f"RBAC backup rehearsal {args.variant}: {args.phase} verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
