from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20261017_operation_receipt_admission.py"
)


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row


class _Bind:
    def __init__(
        self,
        *,
        column: dict[str, Any] | None,
        index: dict[str, Any] | None,
    ) -> None:
        self.column = column
        self.index = index

    def execute(self, statement: Any) -> _Result:
        sql = str(statement)
        if "FROM pg_attribute AS attribute" in sql:
            assert "attribute.attidentity <> '' AS has_identity" in sql
            assert (
                "attribute.attgenerated <> '' AS has_generated_value" in sql
            )
            return _Result(self.column)
        if "FROM pg_class AS index_relation" in sql:
            return _Result(self.index)
        raise AssertionError(f"unexpected catalog query: {sql}")


class _Context:
    def __init__(self, statements: list[str]) -> None:
        self.statements = statements

    @contextmanager
    def autocommit_block(self) -> Iterator[None]:
        self.statements.append("AUTOCOMMIT ENTER")
        try:
            yield
        finally:
            self.statements.append("AUTOCOMMIT EXIT")


class _RecordingOp:
    def __init__(
        self,
        *,
        column: dict[str, Any] | None,
        index: dict[str, Any] | None,
    ) -> None:
        self.statements: list[str] = []
        self.bind = _Bind(column=column, index=index)
        self.context = _Context(self.statements)

    def execute(self, statement: str) -> None:
        self.statements.append(statement)

    def get_bind(self) -> _Bind:
        return self.bind

    def get_context(self) -> _Context:
        return self.context


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "operation_receipt_admission_migration",
        MIGRATION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compatible_column() -> dict[str, Any]:
    return {
        "data_type": "character varying(64)",
        "not_null": False,
        "default_value": None,
        "has_identity": False,
        "has_generated_value": False,
    }


def _complete_index() -> dict[str, Any]:
    return {
        "correct_table": True,
        "valid": True,
        "ready": True,
        "unique_index": False,
        "access_method": "btree",
        "key_count": 2,
        "attribute_count": 2,
        "columns": ["namespace", "admission_key"],
        "predicate": "(admission_key IS NOT NULL)",
    }


@pytest.mark.parametrize(
    "existing_index",
    [None, _complete_index() | {"valid": False, "ready": False}],
    ids=["missing", "partial"],
)
def test_upgrade_repairs_a_missing_or_partial_concurrent_index(
    existing_index: dict[str, Any] | None,
) -> None:
    module = _load_migration()
    recorder = _RecordingOp(
        column=_compatible_column(),
        index=existing_index,
    )
    module.op = recorder

    module.upgrade()

    assert recorder.statements == [
        "SET LOCAL lock_timeout = '5s'",
        (
            "ALTER TABLE operation_receipts "
            "ADD COLUMN IF NOT EXISTS admission_key VARCHAR(64)"
        ),
        "AUTOCOMMIT ENTER",
        "SET lock_timeout = '5s'",
        "SET statement_timeout = '30min'",
        (
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "ix_operation_receipts_namespace_admission"
        ),
        (
            "CREATE INDEX CONCURRENTLY "
            "ix_operation_receipts_namespace_admission "
            "ON operation_receipts (namespace, admission_key) "
            "WHERE admission_key IS NOT NULL"
        ),
        "RESET statement_timeout",
        "RESET lock_timeout",
        "AUTOCOMMIT EXIT",
    ]


def test_upgrade_preserves_an_already_complete_index() -> None:
    module = _load_migration()
    recorder = _RecordingOp(
        column=_compatible_column(),
        index=_complete_index(),
    )
    module.op = recorder

    module.upgrade()

    assert not any("DROP INDEX" in sql for sql in recorder.statements)
    assert not any("CREATE INDEX" in sql for sql in recorder.statements)


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("not_null", True),
        ("default_value", "'unexpected'::character varying"),
        ("has_identity", True),
        ("has_generated_value", True),
    ],
)
def test_upgrade_rejects_an_incompatible_existing_column(
    override: str,
    value: Any,
) -> None:
    module = _load_migration()
    recorder = _RecordingOp(
        column=_compatible_column() | {override: value},
        index=None,
    )
    module.op = recorder

    with pytest.raises(RuntimeError, match="incompatible definition"):
        module.upgrade()

    assert "AUTOCOMMIT ENTER" not in recorder.statements


def test_upgrade_rejects_a_missing_column_after_add() -> None:
    module = _load_migration()
    recorder = _RecordingOp(column=None, index=None)
    module.op = recorder

    with pytest.raises(RuntimeError, match="was not created"):
        module.upgrade()

    assert "AUTOCOMMIT ENTER" not in recorder.statements


def test_downgrade_is_restart_safe() -> None:
    module = _load_migration()
    recorder = _RecordingOp(column=_compatible_column(), index=None)
    module.op = recorder

    module.downgrade()

    assert recorder.statements == [
        "AUTOCOMMIT ENTER",
        "SET lock_timeout = '5s'",
        "SET statement_timeout = '30min'",
        (
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "ix_operation_receipts_namespace_admission"
        ),
        "RESET statement_timeout",
        "RESET lock_timeout",
        "AUTOCOMMIT EXIT",
        "SET LOCAL lock_timeout = '5s'",
        "ALTER TABLE operation_receipts DROP COLUMN IF EXISTS admission_key",
    ]
