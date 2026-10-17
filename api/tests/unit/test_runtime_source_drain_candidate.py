"""Offline drain DDL checks; actual source/physical races remain unproved."""

from __future__ import annotations

import io
import runpy
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

API = Path(__file__).resolve().parents[2]
CANDIDATE = API / "alembic/candidates/20261016_runtime_source_drain.py"


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


def test_inactive_candidate_retains_one_schema_history_and_closed_authority() -> None:
    scope = _scope()
    assert scope["revision"] == "20261016_runtime_source_drain"
    assert len(scope["revision"]) <= 32
    assert scope["down_revision"] == "20261016_runtime_common_writes"
    assert not (API / "alembic/versions" / CANDIDATE.name).exists()
    sql = _sql()
    assert "source drain upgrade requires empty common stores" in sql
    for forbidden in ("CREATE TABLE", "ADD COLUMN", "CREATE ROLE", "GRANT "):
        assert forbidden not in sql
    for signature in scope["FUNCTIONS"]:
        assert (
            f"ALTER FUNCTION public.{signature} OWNER TO runtime_lifecycle_guard" in sql
        )
        assert f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC" in sql


def test_pin_predicate_retains_no_session_native_and_unsettled_history() -> None:
    sql = _sql()
    assert (
        "NOT EXISTS (SELECT 1 FROM public.runtime_sessions s WHERE s.execution_id=target_execution)"
        in sql
    )
    assert "a.logical_job_type IN ('workflow','workflow_execution')" in sql
    assert "a.completed_at IS NULL" in sql
    assert "s.closed_at IS NULL OR NOT EXISTS" in sql
    assert "public.runtime_resource_settlements r WHERE r.session_id=s.id" in sql
    assert (
        "RETURN QUERY SELECT o.execution_id,o.deployment_id FROM public.runtime_execution_owners o"
        in sql
    )
    collector = sql.split("CREATE FUNCTION public.runtime_undrained_sources()", 1)[
        1
    ].split("END $$", 1)[0]
    assert "JOIN public.executions" not in collector
    assert "live_" not in collector


def test_source_mutation_guard_preserves_status_updates_and_native_policy() -> None:
    sql = _sql()
    assert sql.count("CREATE TRIGGER runtime_00_source_drain BEFORE ") == 4
    assert "coordinator cannot mutate native source" in sql
    update_branch = sql.split("IF TG_OP='UPDATE' THEN", 1)[1].split("END IF;", 1)[0]
    assert "RETURN NEW;" in update_branch
    assert "pg_try_advisory" not in update_branch
    assert "runtime_owner_undrained" not in update_branch
    assert "runtime source has undrained obligations" in sql
    assert "retain native history until runtime drain" in sql
    assert "pg_try_advisory_xact_lock(hashtext('bifrost:workspace-release'))" in sql
    assert "FOR UPDATE" not in sql and "FOR SHARE" not in sql
    assert "DELETE FROM" not in sql and "UPDATE public." not in sql


class _DowngradeOp:
    def __init__(self, occupied: bool, isolation: str = "read committed"):
        self.occupied = occupied
        self.isolation = isolation
        self.statements: list[str] = []

    def get_bind(self):
        return self

    def execute(self, statement):
        self.statements.append(str(statement))
        return self

    def scalar_one(self):
        if self.statements[-1] == "SHOW transaction_isolation":
            return self.isolation
        return self.occupied


def test_populated_downgrade_preserves_source_pins() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=True)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="Retain source drain guards"):
        scope["downgrade"]()
    assert len(op.statements) == 3
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in op.statements[1]


def test_empty_downgrade_removes_just_this_package() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False)
    scope["downgrade"].__globals__["op"] = op
    scope["downgrade"]()
    assert op.statements[0] == "SHOW transaction_isolation"
    assert op.statements[2].startswith("SELECT EXISTS ")
    assert op.statements[3:7] == [
        f"DROP TRIGGER runtime_00_source_drain ON public.{table}"
        for table in scope["TABLES"]
    ]
    assert op.statements[7:] == [
        f"DROP FUNCTION public.{signature}"
        for signature in reversed(scope["FUNCTIONS"])
    ]


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable", "unknown"])
def test_downgrade_rejects_stale_snapshot(isolation: str) -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False, isolation=isolation)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="requires read committed isolation"):
        scope["downgrade"]()
    assert op.statements == ["SHOW transaction_isolation"]
