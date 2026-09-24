"""Upgrade/downgrade coverage for the device_jobs migration (#833)."""

from __future__ import annotations

import runpy
from pathlib import Path

from sqlalchemy import CheckConstraint, Column, ForeignKeyConstraint

from src.models.orm.device_jobs import DeviceJob

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260924_device_jobs.py"
)

EXPECTED_INDEXES = {
    "ix_device_jobs_device_id",
    "uq_device_jobs_one_active",
    "ix_device_jobs_org_created",
    "ix_device_jobs_device_status",
    "ix_device_jobs_status_activity",
}
EXPECTED_CHECKS = {
    "ck_device_jobs_timeout_seconds",
    "ck_device_jobs_max_output_bytes",
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


def _migration_globals() -> dict:
    assert MIGRATION_PATH.exists(), (
        "expected migration api/alembic/versions/20260924_device_jobs.py"
    )
    return runpy.run_path(str(MIGRATION_PATH))


def _run_upgrade():
    scope = _migration_globals()
    recorder = _RecordingOp()
    upgrade = scope["upgrade"]
    upgrade.__globals__["op"] = recorder
    upgrade()
    tables = recorder.named_calls("create_table")
    assert len(tables) == 1
    name, args = tables[0][1]
    columns = [a for a in args if isinstance(a, Column)]
    constraints = [a for a in args if not isinstance(a, Column)]
    return scope, recorder, name, columns, constraints


def _server_default_text(column: Column) -> str:
    server_default = column.server_default
    if server_default is None:
        return ""
    arg = getattr(server_default, "arg", server_default)
    return str(getattr(arg, "text", arg)).lower()


def test_migration_revision_chain():
    scope = _migration_globals()
    assert scope["revision"] == "20260924_device_jobs"
    assert scope["down_revision"] == "20260923_dev_ctl_keys"
    assert len(scope["revision"]) <= 32


def test_upgrade_matches_orm_columns_and_guards():
    _scope, _recorder, name, columns, constraints = _run_upgrade()
    assert name == "device_jobs"
    assert {c.name for c in columns} == set(DeviceJob.__table__.columns.keys())
    for col in columns:
        orm_col = DeviceJob.__table__.columns[col.name]
        assert col.nullable == orm_col.nullable, col.name

    status_col = next(c for c in columns if c.name == "status")
    assert "pending" in _server_default_text(status_col)
    log_seq = next(c for c in columns if c.name == "log_sequence")
    assert log_seq.nullable is False

    checks = {c.name for c in constraints if isinstance(c, CheckConstraint)}
    assert checks == EXPECTED_CHECKS

    # ORM must declare the same foreign keys as the migration — otherwise a
    # future autogenerate would propose dropping them (review guard).
    orm_cols = DeviceJob.__table__.columns
    for col_name, target in (
        ("device_id", "devices.id"),
        ("organization_id", "organizations.id"),
    ):
        fks = list(orm_cols[col_name].foreign_keys)
        assert len(fks) == 1, f"{col_name} missing ORM FK"
        assert fks[0].target_fullname == target
        assert fks[0].ondelete == "CASCADE"

    fk_map: dict[tuple, str | None] = {}
    for c in constraints:
        if isinstance(c, ForeignKeyConstraint):
            cols = tuple(dict(c._elements).keys())
            fk_map[cols] = c.ondelete
    assert set(fk_map) == {("device_id",), ("organization_id",)}
    assert all(ondelete == "CASCADE" for ondelete in fk_map.values())


def test_upgrade_index_set_and_partial_unique():
    _scope, recorder, *_rest = _run_upgrade()
    created = {call[1][0] for call in recorder.named_calls("create_index")}
    assert created == EXPECTED_INDEXES

    partial = [
        kwargs
        for call in recorder.named_calls("create_index")
        if call[1][0] == "uq_device_jobs_one_active"
        for kwargs in [call[2]]
    ]
    assert len(partial) == 1
    assert partial[0].get("unique") is True
    where = str(partial[0].get("postgresql_where", ""))
    assert "status IN ('pending', 'claimed', 'running')" in where


def test_downgrade_drops_everything_in_order():
    scope = _migration_globals()
    recorder = _RecordingOp()
    scope["downgrade"].__globals__["op"] = recorder
    scope["downgrade"]()

    kinds = [call[0] for call in recorder.calls]
    assert kinds == ["drop_index"] * 5 + ["drop_table"]
    dropped = {call[1][0] for call in recorder.calls if call[0] == "drop_index"}
    assert dropped == EXPECTED_INDEXES
    assert recorder.calls[-1][1][0] == "device_jobs"
