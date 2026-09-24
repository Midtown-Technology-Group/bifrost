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


def _migration_globals(path: Path = MIGRATION_PATH) -> dict:
    assert path.exists(), f"expected migration {path}"
    return runpy.run_path(str(path))


LOGS_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260924_device_job_logs.py"
)


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


class TestDeviceJobLogsMigration:
    def test_revision_chain(self):
        scope = _migration_globals(LOGS_MIGRATION_PATH)
        assert scope["revision"] == "20260924_device_job_logs"
        assert scope["down_revision"] == "20260924_device_jobs"
        assert len(scope["revision"]) <= 32

    def test_upgrade_composite_pk_and_cascade_fk(self):
        scope = _migration_globals(LOGS_MIGRATION_PATH)
        recorder = _RecordingOp()
        scope["upgrade"].__globals__["op"] = recorder
        scope["upgrade"]()

        tables = recorder.named_calls("create_table")
        assert len(tables) == 1
        name, args = tables[0][1]
        assert name == "device_job_logs"
        columns = [a for a in args if isinstance(a, Column)]
        constraints = [a for a in args if not isinstance(a, Column)]
        assert {c.name for c in columns} == {
            "job_id", "seq", "stream", "text", "ts", "received_at",
        }

        pk = [c for c in constraints if type(c).__name__ == "PrimaryKeyConstraint"]
        assert len(pk) == 1
        # Unattached PK constraints don't render their string column args;
        # assert against the migration source (repo precedent for source
        # assertions in migration tests).
        source = LOGS_MIGRATION_PATH.read_text()
        assert 'sa.PrimaryKeyConstraint("job_id", "seq")' in source

        fks = [c for c in constraints if isinstance(c, ForeignKeyConstraint)]
        assert len(fks) == 1
        fk_elements = dict(fks[0]._elements)
        assert "job_id" in fk_elements
        assert "device_jobs.id" in str(fk_elements["job_id"])
        assert fks[0].ondelete == "CASCADE"

    def test_downgrade_drops_table(self):
        scope = _migration_globals(LOGS_MIGRATION_PATH)
        recorder = _RecordingOp()
        scope["downgrade"].__globals__["op"] = recorder
        scope["downgrade"]()
        kinds = [call[0] for call in recorder.calls]
        assert kinds == ["drop_table"]
        assert recorder.calls[0][1][0] == "device_job_logs"
