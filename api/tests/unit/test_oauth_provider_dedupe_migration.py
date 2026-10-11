"""Regression tests for OAuth-provider deduplication migration safety."""

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20261011_oauth_provider_dedupe.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "oauth_provider_dedupe_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_references_and_enforces_null_aware_key(monkeypatch):
    migration = _load_migration()
    statements: list[str] = []
    created_indexes: list[tuple[tuple, dict]] = []

    monkeypatch.setattr(
        migration.op, "execute", lambda sql: statements.append(str(sql))
    )
    monkeypatch.setattr(migration.op, "drop_index", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda *args, **kwargs: created_indexes.append((args, kwargs)),
    )

    migration.upgrade()

    sql = "\n".join(statements)
    assert "COUNT(DISTINCT integration_id)" in sql
    assert "UPDATE oauth_tokens" in sql
    assert "UPDATE mcp_servers" in sql
    assert "DELETE FROM oauth_tokens" not in sql
    assert created_indexes == [
        (
            (
                "ix_oauth_providers_org_name",
                "oauth_providers",
                ["organization_id", "provider_name"],
            ),
            {"unique": True, "postgresql_nulls_not_distinct": True},
        )
    ]
