"""Offline DDL contract; not a PostgreSQL race or lifecycle acceptance test."""

from __future__ import annotations

import io
import runpy
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

API = Path(__file__).resolve().parents[2]
CANDIDATE = API / "alembic/candidates/20261016_runtime_close_grants.py"


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


def test_candidate_is_inactive_and_adds_no_policy_schema_or_privileges() -> None:
    scope = _scope()
    assert scope["revision"] == "20261016_runtime_close_grants"
    assert len(scope["revision"]) <= 32
    assert scope["down_revision"] == "20261016_runtime_writers"
    assert not (API / "alembic/versions" / CANDIDATE.name).exists()
    sql = _sql()
    for forbidden in ("CREATE TABLE", "ADD COLUMN", "CREATE ROLE", "GRANT "):
        assert forbidden not in sql
    assert "close/grant upgrade requires empty common authority" in sql
    assert "close/grant upgrade requires read committed" in sql


def test_all_three_writers_share_native_execution_try_fence() -> None:
    sql = _sql()
    assert sql.count("CREATE TRIGGER runtime_close_grant_writer BEFORE") == 3
    assert "public.runtime_workflow_actor() <> 'rust'" in sql
    assert (
        "pg_try_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || target_execution::text))"
        in sql
    )
    assert "pg_advisory_xact_lock(" not in sql
    assert "FOR UPDATE" not in sql and "FOR SHARE" not in sql
    for function in _scope()["FUNCTIONS"]:
        assert (
            f"ALTER FUNCTION public.{function}() OWNER TO runtime_lifecycle_guard"
            in sql
        )
        assert f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC" in sql


def test_close_constraint_reads_final_rows_and_preserves_caller_atomicity() -> None:
    sql = _sql()
    assert sql.count("CREATE CONSTRAINT TRIGGER runtime_closed_grants ") == 2
    assert sql.count("DEFERRABLE INITIALLY DEFERRED") == 2
    assert "closed session cannot issue a grant" in sql
    assert "closed/revoked grant cannot accept operations" in sql
    assert (
        "WHERE s.id=target_session AND s.closed_at IS NOT NULL AND g.revoked_at IS NULL"
        in sql
    )
    # The guard rejects partial authority; it must not silently perform the
    # caller's revocation or issue credentials as a trigger side effect.
    assert "UPDATE public." not in sql and "INSERT INTO public." not in sql
    assert "closed session retains active SDK grant" in sql


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


def test_populated_downgrade_cannot_remove_close_enforcement() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=True)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="Retain common close/grant enforcement"):
        scope["downgrade"]()
    assert len(op.statements) == 3
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in op.statements[1]
    assert all("DROP " not in statement for statement in op.statements)


def test_empty_downgrade_excludes_writers_and_keeps_parent_guards() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False)
    scope["downgrade"].__globals__["op"] = op
    scope["downgrade"]()
    assert op.statements[0] == "SHOW transaction_isolation"
    assert op.statements[1].startswith("LOCK TABLE public.executions, ")
    assert op.statements[2].startswith("SELECT EXISTS ")
    assert len([sql for sql in op.statements if sql.startswith("DROP TRIGGER")]) == 5
    assert len([sql for sql in op.statements if sql.startswith("DROP FUNCTION")]) == 2
    assert not any(
        "DROP TABLE" in sql or "runtime_workflow_actor" in sql for sql in op.statements
    )


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable", "unknown"])
def test_downgrade_rejects_stale_snapshot(isolation: str) -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False, isolation=isolation)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="requires read committed isolation"):
        scope["downgrade"]()
    assert op.statements == ["SHOW transaction_isolation"]
