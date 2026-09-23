"""Upgrade/downgrade coverage for device control-plane migrations (#829, #830).

Covers ``20260923_devices`` and ``20260923_device_control_keys``: revision
chain, ORM parity, scope guards, indexes, and paired downgrade order. Real
up/down against Postgres is exercised by the test-stack boot (``alembic
upgrade head``) plus CI.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import CheckConstraint, Column, ForeignKeyConstraint

from src.models.orm.device_control_keys import DeviceControlKey
from src.models.orm.devices import Device

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"

DEVICES_FILE = VERSIONS_DIR / "20260923_devices.py"
CONTROL_KEYS_FILE = VERSIONS_DIR / "20260923_device_control_keys.py"

DEVICE_INDEXES = {
    "ix_devices_api_key_hash",
    "ix_devices_org_status",
    "ix_devices_external_ref",
}
CONTROL_KEY_INDEXES = {"ix_device_control_keys_org"}
CONTROL_KEY_CHECK = "ck_device_control_keys_device_ids_nonempty"


class _RecordingOp:
    """Record alembic op calls made by a migration module."""

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


def _load_migration(path: Path):
    assert path.exists(), f"expected migration {path}"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(path: Path):
    module = _load_migration(path)
    recorder = _RecordingOp()
    module.op = recorder
    module.upgrade()
    tables = recorder.named_calls("create_table")
    assert len(tables) == 1
    name, args = tables[0][1]
    columns = [a for a in args if isinstance(a, Column)]
    constraints = [a for a in args if not isinstance(a, Column)]
    return module, recorder, name, columns, constraints


def _server_default_text(column: Column) -> str:
    server_default = column.server_default
    if server_default is None:
        return ""
    arg = getattr(server_default, "arg", server_default)
    return str(getattr(arg, "text", arg)).lower()


class TestDevicesMigration:
    def test_revision_chain(self):
        module = _load_migration(DEVICES_FILE)
        assert module.revision == "20260923_devices"
        assert module.down_revision == "20260920_ai_delivery_fences"
        assert len(module.revision) <= 32

    def test_upgrade_matches_orm_columns(self):
        _m, _r, name, columns, constraints = _run_upgrade(DEVICES_FILE)
        assert name == "devices"
        assert {c.name for c in columns} == set(Device.__table__.columns.keys())
        for col in columns:
            orm_col = Device.__table__.columns[col.name]
            assert col.nullable == orm_col.nullable, col.name

        status_col = next(c for c in columns if c.name == "status")
        assert "pending_enrolled" in _server_default_text(status_col)

        api_enabled = next(c for c in columns if c.name == "api_key_enabled")
        assert "true" in _server_default_text(api_enabled)

        fks = [c for c in constraints if isinstance(c, ForeignKeyConstraint)]
        assert len(fks) == 1
        elements = dict(fks[0]._elements)  # unattached: names live here
        assert "organization_id" in elements
        assert str(elements["organization_id"]) == "ForeignKey('organizations.id')"
        assert fks[0].ondelete == "CASCADE"

    def test_upgrade_index_set(self):
        _m, recorder, *_ = _run_upgrade(DEVICES_FILE)
        created = {call[1][0] for call in recorder.named_calls("create_index")}
        assert created == DEVICE_INDEXES

    def test_hash_index_is_partial(self):
        _m, recorder, *_ = _run_upgrade(DEVICES_FILE)
        partial = [
            call[2]
            for call in recorder.named_calls("create_index")
            if call[1][0] == "ix_devices_api_key_hash"
        ]
        assert len(partial) == 1
        assert "api_key_hash IS NOT NULL" in str(
            partial[0].get("postgresql_where", "")
        )

    def test_downgrade_order(self):
        module = _load_migration(DEVICES_FILE)
        recorder = _RecordingOp()
        module.op = recorder
        module.downgrade()
        kinds = [call[0] for call in recorder.calls]
        assert kinds == ["drop_index", "drop_index", "drop_index", "drop_table"]
        dropped = {call[1][0] for call in recorder.calls if call[0] == "drop_index"}
        assert dropped == DEVICE_INDEXES
        assert recorder.calls[-1][1][0] == "devices"


class TestDeviceControlKeysMigration:
    def test_revision_chain(self):
        module = _load_migration(CONTROL_KEYS_FILE)
        assert module.revision == "20260923_dev_ctl_keys"
        assert module.down_revision == "20260923_devices"
        assert len(module.revision) <= 32

    def test_upgrade_matches_orm_and_scope_guard(self):
        _m, _r, name, columns, constraints = _run_upgrade(CONTROL_KEYS_FILE)
        assert name == "device_control_keys"
        assert {c.name for c in columns} == set(
            DeviceControlKey.__table__.columns.keys()
        )
        for col in columns:
            orm_col = DeviceControlKey.__table__.columns[col.name]
            assert col.nullable == orm_col.nullable, col.name

        checks = [c for c in constraints if isinstance(c, CheckConstraint)]
        assert len(checks) == 1
        assert checks[0].name == CONTROL_KEY_CHECK
        assert "cardinality(device_ids) >= 1" in str(checks[0].sqltext)

        fks = [c for c in constraints if isinstance(c, ForeignKeyConstraint)]
        assert len(fks) == 1
        assert fks[0].ondelete == "CASCADE"

        device_ids_col = next(c for c in columns if c.name == "device_ids")
        assert "ARRAY" in str(device_ids_col.type)

    def test_upgrade_index_set(self):
        _m, recorder, *_ = _run_upgrade(CONTROL_KEYS_FILE)
        created = {call[1][0] for call in recorder.named_calls("create_index")}
        assert created == CONTROL_KEY_INDEXES

    def test_downgrade_order(self):
        module = _load_migration(CONTROL_KEYS_FILE)
        recorder = _RecordingOp()
        module.op = recorder
        module.downgrade()
        kinds = [call[0] for call in recorder.calls]
        assert kinds == ["drop_index", "drop_table"]
        assert recorder.calls[0][1][0] == "ix_device_control_keys_org"
        assert recorder.calls[1][1][0] == "device_control_keys"
