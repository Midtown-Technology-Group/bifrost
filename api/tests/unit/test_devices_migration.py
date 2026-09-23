"""Upgrade/downgrade coverage for the devices table migration (#829)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import Column, ForeignKeyConstraint

from src.models.orm.devices import Device

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260923_devices.py"
)

EXPECTED_INDEXES = {
    "ix_devices_api_key_hash",
    "ix_devices_org_status",
    "ix_devices_external_ref",
}


class _RecordingOp:
    """Record alembic op calls made by the migration module."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def create_table(self, name, *args, **kwargs):
        self.calls.append(("create_table", (name, args), kwargs))

    def create_index(self, name, table_name, columns, **kwargs):
        self.calls.append(
            ("create_index", (name, table_name, tuple(columns)), kwargs)
        )

    def drop_index(self, name, table_name=None, **kwargs):
        self.calls.append(("drop_index", (name, table_name), kwargs))

    def drop_table(self, name, **kwargs):
        self.calls.append(("drop_table", (name,), kwargs))

    def named_calls(self, op_name: str):
        return [call for call in self.calls if call[0] == op_name]


def _load_migration_module():
    assert MIGRATION_PATH.exists(), (
        "expected migration api/alembic/versions/20260923_devices.py"
    )
    spec = importlib.util.spec_from_file_location(
        "devices_migration",
        MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade_create_table():
    module = _load_migration_module()
    recorder = _RecordingOp()
    module.op = recorder
    module.upgrade()
    tables = recorder.named_calls("create_table")
    assert len(tables) == 1
    name, args = tables[0][1]
    columns = [a for a in args if isinstance(a, Column)]
    constraints = [a for a in args if not isinstance(a, Column)]
    return module, recorder, name, columns, constraints


def test_migration_revision_chain() -> None:
    module = _load_migration_module()
    assert module.revision == "20260923_devices"
    assert module.down_revision == "20260920_ai_delivery_fences"
    assert len(module.revision) <= 32


def _server_default_text(column: Column) -> str:
    server_default = column.server_default
    if server_default is None:
        return ""
    arg = getattr(server_default, "arg", server_default)
    return str(getattr(arg, "text", arg)).lower()


def test_upgrade_creates_devices_table_matching_orm() -> None:
    _module, _recorder, name, columns, constraints = _upgrade_create_table()

    assert name == "devices"
    col_names = {col.name for col in columns}
    assert col_names == set(Device.__table__.columns.keys())

    for col in columns:
        orm_col = Device.__table__.columns[col.name]
        assert col.nullable == orm_col.nullable, col.name

    status_col = next(c for c in columns if c.name == "status")
    assert "pending_enrolled" in _server_default_text(status_col)

    api_enabled = next(c for c in columns if c.name == "api_key_enabled")
    assert "true" in _server_default_text(api_enabled)

    fks = [c for c in constraints if isinstance(c, ForeignKeyConstraint)]
    assert len(fks) == 1
    elements = dict(fks[0]._elements)  # unattached constraint: names live here
    assert "organization_id" in elements
    assert str(elements["organization_id"]) == "ForeignKey('organizations.id')"
    assert fks[0].ondelete == "CASCADE"


def test_upgrade_creates_expected_indexes() -> None:
    _module, recorder, *_rest = _upgrade_create_table()
    created = {call[1][0] for call in recorder.named_calls("create_index")}
    assert created == EXPECTED_INDEXES


def test_upgrade_hash_index_is_partial() -> None:
    _module, recorder, *_rest = _upgrade_create_table()
    partial = [
        kwargs
        for call in recorder.named_calls("create_index")
        if call[1][0] == "ix_devices_api_key_hash"
        for kwargs in [call[2]]
    ]
    assert len(partial) == 1
    assert "api_key_hash IS NOT NULL" in str(partial[0].get("postgresql_where", ""))


def test_downgrade_drops_indexes_then_table() -> None:
    module = _load_migration_module()
    recorder = _RecordingOp()
    module.op = recorder

    module.downgrade()

    drops = recorder.calls
    assert [call[0] for call in drops] == [
        "drop_index",
        "drop_index",
        "drop_index",
        "drop_table",
    ]
    dropped_index_names = {call[1][0] for call in drops if call[0] == "drop_index"}
    assert dropped_index_names == EXPECTED_INDEXES
    assert drops[-1][1][0] == "devices"
