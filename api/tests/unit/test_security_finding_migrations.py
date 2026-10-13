"""Regression tests for security-sensitive legacy migration behavior."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


MIGRATIONS = Path(__file__).parents[2] / "alembic" / "versions"


def _load_migration(filename: str) -> ModuleType:
    path = MIGRATIONS / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_timezone_migration_includes_form_field_timestamps():
    migration = _load_migration("20260209_migrate_to_timezone_aware_utc.py")

    assert ("form_fields", "created_at") in migration.COLUMNS_TO_MIGRATE
    assert ("form_fields", "updated_at") in migration.COLUMNS_TO_MIGRATE


def test_forward_timezone_repair_only_alters_naive_columns():
    migration = _load_migration("20261013_repair_form_field_timezones.py")
    assert migration.down_revision == "20261012_work_delivery_started"
    altered: list[str] = []

    class Result:
        @staticmethod
        def scalar_one_or_none() -> str:
            return "timestamp without time zone"

    class Bind:
        @staticmethod
        def execute(_statement, _parameters) -> Result:
            return Result()

    class FakeOp:
        @staticmethod
        def get_bind() -> Bind:
            return Bind()

        @staticmethod
        def alter_column(_table: str, column: str, **_kwargs) -> None:
            altered.append(column)

    migration.op = FakeOp()
    migration.upgrade()

    assert altered == ["created_at", "updated_at"]


def test_published_snapshot_uses_jsonb_for_fresh_and_upgraded_databases():
    original = _load_migration("20260212_add_published_snapshot.py")
    repair = _load_migration("20261013_published_snapshot_jsonb.py")
    added_types: list[str] = []
    altered: list[tuple[str, str]] = []

    class OriginalOp:
        @staticmethod
        def add_column(_table: str, column) -> None:
            added_types.append(str(column.type))

    class Result:
        @staticmethod
        def scalar_one_or_none() -> str:
            return "json"

    class Bind:
        @staticmethod
        def execute(_statement) -> Result:
            return Result()

    class RepairOp:
        @staticmethod
        def get_bind() -> Bind:
            return Bind()

        @staticmethod
        def alter_column(table: str, column: str, **kwargs) -> None:
            altered.append((f"{table}.{column}", kwargs["postgresql_using"]))

    original.op = OriginalOp()
    original.upgrade()
    repair.op = RepairOp()
    repair.upgrade()

    assert added_types == ["JSONB"]
    assert altered == [
        ("applications.published_snapshot", "published_snapshot::jsonb")
    ]


def test_workflow_source_is_backfilled_before_legacy_columns_are_dropped():
    migration = _load_migration("20260210_drop_workflow_code_columns.py")
    calls: list[tuple[str, str]] = []

    class FakeOp:
        @staticmethod
        def execute(statement) -> None:
            calls.append(("execute", str(statement)))

        @staticmethod
        def drop_column(table: str, column: str) -> None:
            calls.append(("drop", f"{table}.{column}"))

    migration.op = FakeOp()
    migration.upgrade()

    assert calls[0][0] == "execute"
    assert "INSERT INTO file_index" in calls[0][1]
    assert "FROM workflows" in calls[0][1]
    assert calls[1:] == [
        ("drop", "workflows.code"),
        ("drop", "workflows.code_hash"),
    ]
