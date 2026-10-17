"""Offline C5-A DDL checks, not PostgreSQL writer-isolation acceptance."""

from __future__ import annotations

import io
import runpy
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

API = Path(__file__).resolve().parents[2]
CANDIDATE = API / "alembic/candidates/20261016_runtime_native_writers.py"


def _scope() -> dict:
    return runpy.run_path(str(CANDIDATE))


def _sql() -> str:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        _scope()["upgrade"]()
    return " ".join(output.getvalue().split())


def test_candidate_stays_inactive_and_requires_complete_guard_profile() -> None:
    scope = _scope()
    assert scope["revision"] == "20261016_runtime_writers"
    assert len(scope["revision"]) <= 32
    assert scope["down_revision"] == "20261016_runtime_resources"
    assert not (API / "alembic/versions" / CANDIDATE.name).exists()
    sql = _sql()
    assert "CREATE ROLE" not in sql and "GRANT " not in sql
    assert "reviewed NOLOGIN guard custodian is required" in sql
    assert "native writer upgrade requires empty common stores" in sql


def test_only_native_root_gains_metadata_and_no_existing_fk_is_replaced() -> None:
    sql = _sql()
    assert "ADD COLUMN control_owner VARCHAR(16) DEFAULT 'python' NOT NULL" in sql
    assert "ADD COLUMN runtime_owner_id UUID GENERATED ALWAYS AS" in sql
    assert (
        "FOREIGN KEY(runtime_owner_id) REFERENCES public.runtime_execution_owners "
        "(execution_id) DEFERRABLE INITIALLY DEFERRED"
    ) in sql
    assert sql.count("ADD COLUMN ") == 2
    assert sql.count("ADD CONSTRAINT ") == 2
    assert "DROP CONSTRAINT" not in sql
    assert "ON DELETE CASCADE" not in sql
    assert "control_owner IN ('python','rust')" in sql
    # Initial pinning is an INSERT guard, not a permanent non-null constraint
    # that would silently block a later permitted workflow ancestor unlink.
    assert "runtime birth requires pinned source" in sql
    for table in _scope()["NATIVE_TABLES"]:
        if table != "executions":
            assert f"ALTER TABLE public.{table}" not in sql


def test_guard_functions_have_closed_owner_and_no_public_execution() -> None:
    sql = _sql()
    for function in _scope()["FUNCTIONS"]:
        assert (
            f"ALTER FUNCTION public.{function}() OWNER TO runtime_lifecycle_guard"
            in sql
        )
        assert f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC" in sql
    assert (
        sql.count(
            "LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp"
        )
        == 3
    )
    assert "unqualified lifecycle snapshot isolation" in sql
    assert "FOR UPDATE" not in sql and "FOR SHARE" not in sql
    assert sql.count("CREATE TRIGGER runtime_native_writer ") == 7
    assert "CREATE TRIGGER runtime_owner_birth_writer BEFORE INSERT" in sql
    assert "WHEN 'bifrost'" not in sql
    assert "current_setting('bifrost" not in sql


class _DowngradeOp:
    def __init__(self, occupied: bool, isolation: str = "read committed"):
        self.occupied = occupied
        self.isolation = isolation
        self.statements: list[str] = []
        self.dropped: list[str] = []

    def get_bind(self):
        return self

    def execute(self, statement):
        self.statements.append(str(statement))
        return self

    def scalar_one(self):
        if self.statements[-1] == "SHOW transaction_isolation":
            return self.isolation
        return self.occupied

    def drop_constraint(self, name, table, *, type_, schema):
        assert table == "executions" and schema == "public"
        assert type_ in {"foreignkey", "check"}
        self.dropped.append(name)

    def drop_column(self, table, name, *, schema):
        assert table == "executions" and schema == "public"
        self.dropped.append(name)


def test_populated_downgrade_preserves_writer_enforcement() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=True)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="Retain common ownership enforcement"):
        scope["downgrade"]()
    assert len(op.statements) == 3
    assert not op.dropped


def test_empty_downgrade_locks_all_affected_rows_before_fresh_snapshot() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False)
    scope["downgrade"].__globals__["op"] = op
    scope["downgrade"]()
    assert op.statements[0] == "SHOW transaction_isolation"
    assert op.statements[1] == (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table}"
            for table in (*scope["NATIVE_TABLES"], *scope["COMMON_TABLES"])
        )
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    assert op.statements[2] == "SELECT " + " OR ".join(
        f"EXISTS (SELECT 1 FROM public.{table})" for table in scope["COMMON_TABLES"]
    )
    assert op.statements[3].startswith("DROP TRIGGER ")
    assert op.dropped == [
        "fk_execution_retained_runtime_owner",
        "runtime_owner_id",
        "ck_execution_control_owner",
        "control_owner",
    ]


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable", "unknown"])
def test_downgrade_rejects_stale_or_unknown_snapshot(isolation: str) -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False, isolation=isolation)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="requires read committed isolation"):
        scope["downgrade"]()
    assert op.statements == ["SHOW transaction_isolation"]
    assert not op.dropped
