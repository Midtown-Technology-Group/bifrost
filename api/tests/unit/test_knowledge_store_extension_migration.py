"""Managed Postgres must not need extension privileges after admin setup."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize(
    "offline,installed,creates_extension",
    [(False, True, False), (False, False, True), (True, False, True)],
)
def test_extension_creation_only_when_needed(monkeypatch, offline, installed, creates_extension):
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "20251225_100000_add_knowledge_store_table.py"
    spec = importlib.util.spec_from_file_location("knowledge_store_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    operations = MagicMock()
    operations.get_bind.return_value.scalar.return_value = installed
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: offline)

    migration.upgrade()

    statements = [str(call.args[0]) for call in operations.execute.call_args_list]
    assert ("CREATE EXTENSION IF NOT EXISTS vector" in statements) is creates_extension
    if offline:
        operations.get_bind.assert_not_called()
    else:
        operations.get_bind.return_value.scalar.assert_called_once()
        query = operations.get_bind.return_value.scalar.call_args.args[0]
        assert str(query) == "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector')"
    operations.create_table.assert_called_once()
    assert "ALTER TABLE knowledge_store ADD COLUMN embedding vector(1536) NOT NULL" in statements
