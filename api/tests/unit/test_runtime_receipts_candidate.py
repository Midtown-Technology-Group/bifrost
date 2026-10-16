"""Offline C2 DDL checks; no candidate SQL or runtime authority is applied."""

from __future__ import annotations

import io
import runpy
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

API = Path(__file__).resolve().parents[2]
CANDIDATES = API / "alembic/candidates"
CANDIDATE = CANDIDATES / "20261016_runtime_starts_receipts.py"


def _scope() -> dict:
    return runpy.run_path(str(CANDIDATE))


def _sql(*, include_c1: bool = False) -> str:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        if include_c1:
            runpy.run_path(str(CANDIDATES / "20261016_runtime_owner_links.py"))[
                "upgrade"
            ]()
        _scope()["upgrade"]()
    return " ".join(output.getvalue().split())


def test_candidate_depends_on_c1_and_is_not_active_history() -> None:
    scope = _scope()
    assert scope["revision"] == "20261016_runtime_receipts"
    assert len(scope["revision"]) <= 32
    assert scope["down_revision"] == "20261016_runtime_owner_links"
    assert not (API / "alembic/versions" / CANDIDATE.name).exists()


def test_combined_proposal_creates_c1_before_typed_c2_with_no_authority() -> None:
    sql = _sql(include_c1=True)
    assert sql.count("CREATE TABLE ") == 6
    assert (
        sql.index("CREATE TABLE public.runtime_sessions")
        < sql.index("CREATE TABLE public.runtime_starts")
        < sql.index("CREATE TABLE public.runtime_report_receipts")
    )
    assert "CREATE ROLE" not in sql and "GRANT " not in sql
    assert "CREATE TABLE IF NOT EXISTS" not in sql
    assert "workflow_runtime_sdk_grants" not in sql


def test_start_binds_complete_historical_session_and_one_start_per_session() -> None:
    sql = _sql()
    assert (
        "FOREIGN KEY(session_id, execution_id, owner_incarnation_id, workflow_attempt_id) "
        "REFERENCES public.runtime_sessions "
        "(id, execution_id, owner_incarnation_id, workflow_attempt_id) ON DELETE RESTRICT"
    ) in sql
    assert "UNIQUE (session_id)" in sql
    assert "UNIQUE (id, session_id, start_message_id)" in sql
    assert "UNIQUE (id, session_id, start_message_id, started_at)" in sql
    assert "input_sha256 ~ '^[0-9a-f]{64}$'" in sql
    assert "context_sha256 ~ '^[0-9a-f]{64}$'" in sql
    assert "deadline_utc IS NULL OR deadline_utc > started_at" in sql


def test_receipt_binds_exact_start_message_and_bounded_report_bytes() -> None:
    sql = _sql()
    assert "PRIMARY KEY (session_id, result_message_id)" in sql
    assert (
        "FOREIGN KEY(committed_start_id, session_id, start_message_id) "
        "REFERENCES public.runtime_starts (id, session_id, start_message_id) "
        "ON DELETE RESTRICT"
    ) in sql
    assert "octet_length(raw_result_payload) BETWEEN 1 AND 16777216" in sql
    assert "result_sha256 = encode(sha256(raw_result_payload), 'hex')" in sql
    assert "disposition = 'accepted' AND winner = 'result'" in sql
    assert "disposition = 'retained' AND winner IN ('cancel','failure')" in sql
    assert "ON DELETE CASCADE" not in sql
    assert "REFERENCES public.executions" not in sql
    assert "REFERENCES public.workflow_execution_attempts" not in sql


def test_storage_retains_start_and_receipt_updates_or_deletes() -> None:
    sql = _sql()
    for table in ("runtime_starts", "runtime_report_receipts"):
        assert (
            "CREATE TRIGGER runtime_report_immutable BEFORE UPDATE OR DELETE "
            f"ON public.{table} FOR EACH ROW EXECUTE FUNCTION "
            "public.enforce_runtime_report_immutability()"
        ) in sql
        assert f"REVOKE ALL ON public.{table} FROM PUBLIC" in sql
    assert "TG_OP = 'DELETE' OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)" in sql
    assert "LANGUAGE plpgsql SET search_path = pg_catalog" in sql
    assert (
        "REVOKE ALL ON FUNCTION public.enforce_runtime_report_immutability() FROM PUBLIC"
        in sql
    )


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

    def drop_table(self, table, *, schema):
        assert schema == "public"
        self.dropped.append(table)


def test_populated_downgrade_refuses_without_removing_start_or_receipts() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=True)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="Retain runtime Start/receipt evidence"):
        scope["downgrade"]()
    assert not op.dropped


def test_empty_downgrade_locks_before_fresh_snapshot_and_drops_reverse_order() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False)
    scope["downgrade"].__globals__["op"] = op
    scope["downgrade"]()
    assert op.statements[:2] == [
        "SHOW transaction_isolation",
        "LOCK TABLE public.runtime_starts, public.runtime_report_receipts "
        "IN ACCESS EXCLUSIVE MODE NOWAIT",
    ]
    assert op.statements[2].startswith("SELECT EXISTS ")
    assert op.dropped == ["runtime_report_receipts", "runtime_starts"]
    assert (
        op.statements[-1]
        == "DROP FUNCTION public.enforce_runtime_report_immutability()"
    )


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable", "unknown"])
def test_downgrade_refuses_stale_or_unknown_snapshot(isolation: str) -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False, isolation=isolation)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="requires read committed isolation"):
        scope["downgrade"]()
    assert op.statements == ["SHOW transaction_isolation"]
    assert not op.dropped
