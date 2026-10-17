"""Concurrent DDL must stay bounded and restore its session settings."""

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest


def load_migration():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/20261017_teams_receipt_index.py"
    spec = importlib.util.spec_from_file_location("teams_receipt_index_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
@pytest.mark.parametrize("fail_ddl", [False, True])
def test_concurrent_index_bounds_and_resets_even_after_failure(monkeypatch, direction, fail_ddl):
    module = load_migration()
    calls = []

    class Recorder:
        @contextmanager
        def autocommit_block(self):
            calls.append("enter")
            try:
                yield
            finally:
                calls.append("exit")

        def get_context(self):
            return self

        def execute(self, sql):
            calls.append(sql)
            if fail_ddl and sql.startswith(("CREATE INDEX", "DROP INDEX")):
                raise RuntimeError("simulated DDL failure")

    monkeypatch.setattr(module, "op", Recorder())
    if fail_ddl:
        with pytest.raises(RuntimeError, match="simulated DDL failure"):
            getattr(module, direction)()
    else:
        getattr(module, direction)()

    assert calls[:3] == ["enter", "SET lock_timeout = '5s'", "SET statement_timeout = '30min'"]
    assert calls[-3:] == ["RESET statement_timeout", "RESET lock_timeout", "exit"]
    assert "CONCURRENTLY" in calls[3]
    if direction == "upgrade":
        assert calls[3] == (
            "CREATE INDEX CONCURRENTLY ix_executions_teams_action_receipt "
            "ON executions (id) WHERE execution_context ? 'teams_action_completion'"
        )
    assert module.down_revision == "20261012_work_delivery_started"
    assert len(module.revision) <= 32
