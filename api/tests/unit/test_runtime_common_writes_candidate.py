"""Offline C5-B2 DDL boundaries; not PostgreSQL lifecycle/race evidence."""

from __future__ import annotations

import io
import runpy
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

API = Path(__file__).resolve().parents[2]
CANDIDATE = API / "alembic/candidates/20261016_runtime_common_writes.py"


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


def test_inactive_continuation_keeps_existing_schema_policy_and_privileges() -> None:
    scope = _scope()
    assert scope["revision"] == "20261016_runtime_common_writes"
    assert len(scope["revision"]) <= 32
    assert scope["down_revision"] == "20261016_runtime_close_grants"
    assert not (API / "alembic/versions" / CANDIDATE.name).exists()
    sql = _sql()
    assert "common writer upgrade requires empty stores" in sql
    for forbidden in ("CREATE TABLE", "ADD COLUMN", "CREATE ROLE", "GRANT "):
        assert forbidden not in sql
    assert sql.count("CREATE TRIGGER runtime_00_common_writer BEFORE ") == 10
    for table in scope["TABLES"]:
        assert f"ON public.{table} FOR EACH ROW" in sql
    assert "OWNER TO runtime_lifecycle_guard" in sql
    assert (
        "REVOKE ALL ON FUNCTION public.guard_runtime_common_writer() FROM PUBLIC" in sql
    )


def test_source_fence_precedes_execution_fence_and_uses_no_waiting_locks() -> None:
    sql = _sql()
    assert sql.index("pg_try_advisory_xact_lock_shared(") < sql.index(
        "pg_try_advisory_xact_lock("
    )
    assert "hashtext('bifrost:workspace-release')" in sql
    assert "hashtext('bifrost:workflow-execution:' || target_execution::text)" in sql
    assert "pg_advisory_xact_lock(" not in sql
    assert "FOR UPDATE" not in sql and "FOR SHARE" not in sql
    assert "VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp" in sql


def test_incumbent_exception_is_only_live_link_delta_not_common_authority() -> None:
    sql = _sql()
    assert "incumbent cannot create common authority" in sql
    assert "(proposed-detach_fields) IS DISTINCT FROM (previous-detach_fields)" in sql
    assert (
        "ARRAY['live_deployment_id','live_workflow_id','live_solution_id','live_reviewed_by']"
        in sql
    )
    assert "WHEN 'runtime_execution_owners' THEN ARRAY['live_execution_id']" in sql
    assert "WHEN 'runtime_workflow_live_links' THEN ARRAY['native_attempt_id']" in sql
    assert "retain common runtime evidence" in sql


def test_start_and_admission_require_current_open_live_fence() -> None:
    sql = _sql()
    for binding in (
        "s.closed_at IS NULL",
        "o.live_execution_id=o.execution_id",
        "e.control_owner='rust'",
        "e.status::text='Running'",
        "e.workflow_id=o.workflow_id",
        "e.solution_deployment_id=o.deployment_id",
        "l.native_attempt_id=s.workflow_attempt_id",
        "a.claim_token=s.claim_token",
        "a.worker_incarnation_id=s.worker_incarnation_id",
        "a.status='running' AND a.phase='execution' AND a.completed_at IS NULL",
        "a.id=NEW.workflow_attempt_id AND a.started_at=NEW.started_at",
        "g.runtime_session_id=target_session AND g.revoked_at IS NULL",
    ):
        assert binding in sql
    # No claim that a row's presence proves process teardown or source release.
    assert "UPDATE public." not in sql and "INSERT INTO public." not in sql
    assert "e.status::text IN ('Pending','Running')" in sql
    assert "new session cannot revive terminal/cancelling work" in sql


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


def test_populated_downgrade_retains_guards() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=True)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="Retain common writer guards"):
        scope["downgrade"]()
    assert len(op.statements) == 3
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in op.statements[1]
    assert not any("DROP " in sql for sql in op.statements)


def test_empty_downgrade_removes_only_this_guard_after_locked_snapshot() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False)
    scope["downgrade"].__globals__["op"] = op
    scope["downgrade"]()
    assert op.statements[0] == "SHOW transaction_isolation"
    assert op.statements[1].startswith(
        "LOCK TABLE public.runtime_deployment_artifacts,"
    )
    assert op.statements[2].startswith("SELECT EXISTS ")
    assert op.statements[3:-1] == [
        f"DROP TRIGGER runtime_00_common_writer ON public.{table}"
        for table in scope["TABLES"]
    ]
    assert op.statements[-1] == "DROP FUNCTION public.guard_runtime_common_writer()"


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable", "unknown"])
def test_downgrade_rejects_stale_snapshot(isolation: str) -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False, isolation=isolation)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="requires read committed isolation"):
        scope["downgrade"]()
    assert op.statements == ["SHOW transaction_isolation"]
