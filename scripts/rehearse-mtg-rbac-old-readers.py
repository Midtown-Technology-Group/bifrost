#!/usr/bin/env python3
"""Run actual frozen-fork readers against an owned, restored synthetic DB.

Run only through rehearse-mtg-rbac-old-readers.sh in the Linux Docker lane.
Application imports come from the read-only git archive, never this checkout.
This is an imported-reader recovery proof, not an HTTP/startup/deploy proof.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path
from uuid import UUID

OLD_SHA = "5085cc467ffb1ed35de9c9ca48890ab0d929e1d0"
OLD_HEAD = "20261012_work_delivery_started"
DATABASE_RE = re.compile(r"^bifrost_r2b_rehearsal_[0-9a-f]{12}$")
VARIANTS = ("clean", "delivery_started_historical_stamp", "withdrawn_builder_residue")
CURRENT_PHASE = "startup"


def verify_imports(manifest: dict) -> int:
    """Every loaded application module must be an unchanged archived source."""
    files = manifest["api_python_sha256"]
    checked = 0
    for name, module in tuple(sys.modules.items()):
        if name.split(".")[0] not in {"src", "shared", "bifrost"}:
            continue
        origin = getattr(module, "__file__", None)
        if origin is None:
            # Namespace packages have no code; require their roots to be frozen.
            roots = list(getattr(module, "__path__", ()))
            assert roots and all(Path(root).resolve().is_relative_to("/app") for root in roots)
            continue
        path = Path(origin).resolve()
        assert path.is_relative_to("/app"), "Application import escaped frozen archive"
        relative = path.relative_to("/app").as_posix()
        assert relative in files, "Application import was not an archived Python source"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == files[relative], "Archived source changed"
        checked += 1
    assert checked > 0, "No old application modules were exercised"
    return checked


async def exercise(fixture: dict, manifest: dict) -> dict:
    global CURRENT_PHASE
    CURRENT_PHASE = "frozen_application_imports"
    from sqlalchemy import select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # These are actual 5085 application implementations, not copied expressions.
    from src.core.principal import UserPrincipal
    from src.models.orm.users import Role, User
    from src.repositories.knowledge import KnowledgeRepository
    from src.routers.knowledge_sources import list_namespace_roles
    from src.services.devices import (
        DeviceOperationError,
        require_device_control_key_issue_permission,
        user_can_execute_devices,
        user_can_manage_devices,
    )
    from shared.sdk_roles import get_consumer_counts

    assert manifest["source_sha"] == OLD_SHA
    assert re.fullmatch(r"[0-9a-f]{32}", fixture["run_id"])
    assert fixture["variant"] in VARIANTS
    database = fixture["restored_database"]
    assert DATABASE_RE.fullmatch(database)
    url = make_url(os.environ["BIFROST_OLD_DATABASE_URL"])
    assert url.database == database and url.host == "postgres" and url.port == 5432
    assert url.drivername == "postgresql+asyncpg"
    engine = create_async_engine(
        url, echo=False,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    try:
        CURRENT_PHASE = "restored_database_identity_and_read_only"
        async with factory() as db:
            async with db.begin():
                assert await db.scalar(text("SHOW transaction_read_only")) == "on"
                assert await db.scalar(text("SELECT current_database()")) == database
                version = int(await db.scalar(text("SHOW server_version_num")))
                assert version // 10000 == 16
                marker = await db.scalar(text(
                    "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"
                ))
                assert marker == f"mtg-rbac-backup:{fixture['run_id']}:{fixture['variant']}"
                assert (await db.scalars(text("SELECT version_num FROM alembic_version"))).all() == [OLD_HEAD]

                CURRENT_PHASE = "old_role_json_reader"
                expected_roles = fixture["role_json"]
                role_ids = [UUID(value) for value in expected_roles]
                roles = (await db.scalars(select(Role).where(Role.id.in_(role_ids)))).all()
                assert {str(role.id): role.permissions for role in roles} == expected_roles

                CURRENT_PHASE = "old_device_readers"
                required_users = {UUID(case["user_id"]) for case in fixture["device_checks"]}
                required_users.add(UUID(fixture["admin_user_id"]))
                users = (await db.scalars(select(User).where(User.id.in_(required_users)))).all()
                principals = {
                    str(user.id): UserPrincipal(
                        user_id=user.id, email=user.email, name=user.name or "",
                        organization_id=user.organization_id, is_superuser=user.is_superuser,
                        is_active=user.is_active,
                    )
                    for user in users
                }
                assert len(principals) == len(required_users)
                for case in fixture["device_checks"]:
                    principal = principals[case["user_id"]]
                    assert await user_can_manage_devices(db, principal) is case["manage"]
                    assert await user_can_execute_devices(db, principal) is case["execute"]
                    try:
                        await require_device_control_key_issue_permission(db, principal)
                    except DeviceOperationError as exc:
                        assert exc.status_code == 403 and not (case["manage"] and case["execute"])
                    else:
                        assert case["manage"] and case["execute"]

                CURRENT_PHASE = "old_knowledge_assignment_and_count_readers"
                admin = principals[fixture["admin_user_id"]]
                assert admin.is_superuser
                assignments = await list_namespace_roles(db=db, user=admin)
                actual_assignments = {
                    item.id: {
                        "id": item.id, "namespace": item.namespace,
                        "organization_id": item.organization_id, "role_id": item.role_id,
                    }
                    for item in assignments
                }
                assert actual_assignments == {
                    expected["id"]: expected for expected in fixture["namespace_assignments"]
                }
                counts = await get_consumer_counts(db, [UUID(value) for value in fixture["knowledge_counts"]])
                assert {str(key): value.knowledge for key, value in counts.items()} == fixture["knowledge_counts"]

                CURRENT_PHASE = "old_knowledge_content_readers"
                knowledge = fixture["knowledge"]
                repo = KnowledgeRepository(
                    session=db, org_id=UUID(knowledge["organization_id"]),
                    user_id=UUID(knowledge["user_id"]),
                )
                home = await repo.get_by_id(UUID(knowledge["home_document_id"]))
                global_doc = await repo.get_by_id(UUID(knowledge["global_document_id"]))
                foreign = await repo.get_by_id(UUID(knowledge["foreign_document_id"]))
                assert home is not None and home.content == knowledge["expected_home_content"]
                assert global_doc is not None and global_doc.content == knowledge["expected_global_content"]
                assert home.id == knowledge["home_document_id"] and global_doc.id == knowledge["global_document_id"]
                assert home.organization_id == knowledge["organization_id"] and global_doc.organization_id is None
                assert foreign is None
                namespaces = {item.namespace: item.scopes for item in await repo.list_namespaces()}
                scope_counts = namespaces[knowledge["namespace"]]
                assert scope_counts["org"] == knowledge["expected_namespace_home_count"]
                assert scope_counts["global"] == knowledge["expected_namespace_global_count"]
                assert scope_counts["total"] == scope_counts["org"] + scope_counts["global"]
                assert not db.new and not db.dirty and not db.deleted
                assert await db.scalar(text("SHOW transaction_read_only")) == "on"
                CURRENT_PHASE = "frozen_import_source_integrity"
                imported = verify_imports(manifest)
        return {
            "status": "passed", "source_commit": OLD_SHA, "restored_head": OLD_HEAD,
            "postgres_major": version // 10000, "transaction_read_only": True,
            "read_only_mechanism": "asyncpg_default_transaction_read_only_on",
            "old_application_modules_hash_verified": imported, "role_json_rows_verified": len(roles),
            "device_personas_verified": len(fixture["device_checks"]),
            "namespace_assignment_rows_verified": len(fixture["namespace_assignments"]),
            "knowledge_role_counts_verified": len(counts), "knowledge_documents_read": 2,
            "foreign_knowledge_document_denied": True,
            "proof_scope": "actual_old_imported_readers_after_backup_restore",
            "http_auth_startup_ui_and_deployment_proven": False,
            "old_knowledge_junction_enforces_content_acl": False,
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    try:
        assert os.environ.get("BIFROST_OLD_READER_CONTAINER") == "1"
        assert Path("/app/src").is_dir()
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
        # Ensure package roots are loaded from the archive before DB work starts.
        for package in ("src", "shared", "bifrost"):
            importlib.import_module(package)
        result = asyncio.run(exercise(fixture, manifest))
    except Exception as exc:
        # Driver exceptions may include a DSN or fixture data; never render them.
        failure = {
            "status": "failed", "source_commit": OLD_SHA,
            "phase": CURRENT_PHASE, "error_type": type(exc).__name__,
        }
        missing = getattr(exc, "name", None)
        if isinstance(exc, ModuleNotFoundError) and isinstance(missing, str) and re.fullmatch(r"[A-Za-z0-9_.]+", missing):
            failure["missing_dependency"] = missing
        print(json.dumps(failure))
        return 1
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
