"""Offline C3 storage checks against a pinned CRED authority, not Rust expectations."""

from __future__ import annotations

import io
import json
import runpy
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import op as alembic_op
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql

API = Path(__file__).resolve().parents[2]
CANDIDATES = API / "alembic/candidates"
CANDIDATE = CANDIDATES / "20261016_runtime_sdk_grants.py"
AUTHORITY = json.loads(
    (Path(__file__).parent / "fixtures/cred_p1_grant_storage_v1.json").read_text()
)
GRANTS = "workflow_runtime_sdk_grants"
OPERATIONS = "workflow_runtime_sdk_grant_operations"


def _scope() -> dict:
    return runpy.run_path(str(CANDIDATE))


def _render(*, include_parents: bool = False) -> tuple[str, dict[str, sa.Table]]:
    tables = {}

    class Recorder:
        def __getattr__(self, name):
            return getattr(alembic_op, name)

        def create_table(self, name, *args, **kwargs):
            table = alembic_op.create_table(name, *args, **kwargs)
            tables[name] = table
            return table

    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        if include_parents:
            for name in (
                "20261016_runtime_owner_links.py",
                "20261016_runtime_starts_receipts.py",
            ):
                runpy.run_path(str(CANDIDATES / name))["upgrade"]()
        scope = _scope()
        scope["upgrade"].__globals__["op"] = Recorder()
        scope["upgrade"]()
    return " ".join(output.getvalue().split()), tables


def test_candidate_has_one_proposed_parent_and_is_outside_active_history() -> None:
    scope = _scope()
    assert scope["revision"] == "20261016_runtime_grants"
    assert len(scope["revision"]) <= 32
    assert scope["down_revision"] == "20261016_runtime_receipts"
    assert not (API / "alembic/versions" / CANDIDATE.name).exists()
    assert (
        AUTHORITY["authority"]["commit"] == "3918d608a2d497d1ca8424fd4229d191323e2d7a"
    )


@pytest.mark.parametrize("name", [GRANTS, OPERATIONS])
def test_all_original_cred_columns_keep_types_nullability_and_keys(name: str) -> None:
    _, tables = _render()
    table = tables[name]
    expected = AUTHORITY["tables"][name]["columns"]
    additions = (
        ["owner_incarnation_id", "committed_start_id", "start_message_id"]
        if name == GRANTS
        else []
    )
    assert list(table.columns.keys()) == list(expected) + additions
    assert len(table.columns) == (38 if name == GRANTS else 8)
    for column in table.columns:
        actual = {
            "type": column.type.compile(dialect=postgresql.dialect()),
            "nullable": column.nullable,
            "primary_key": column.primary_key,
        }
        assert actual == expected.get(
            column.name, {"type": "UUID", "nullable": False, "primary_key": False}
        )


@pytest.mark.parametrize("name", [GRANTS, OPERATIONS])
def test_existing_cred_checks_are_preserved_exactly(name: str) -> None:
    _, tables = _render()
    actual = {
        item.name: " ".join(str(item.sqltext).split())
        for item in tables[name].constraints
        if isinstance(item, sa.CheckConstraint)
    }
    expected = AUTHORITY["tables"][name]["checks"]
    assert {key: actual[key] for key in expected} == expected
    assert set(actual) - set(expected) == (
        {"ck_runtime_sdk_finite_access"} if name == GRANTS else set()
    )
    if name == GRANTS:
        assert actual["ck_runtime_sdk_finite_access"] == (
            "issued_at >= started_at AND initial_access_expires_at > issued_at "
            "AND isfinite(initial_access_expires_at)"
        )


def test_historical_common_fks_replace_permanent_native_links() -> None:
    sql, tables = _render(include_parents=True)
    assert sql.count("CREATE TABLE ") == 8
    assert (
        sql.index("CREATE TABLE public.runtime_sessions")
        < sql.index("CREATE TABLE public.runtime_starts")
        < sql.index("CREATE TABLE public.workflow_runtime_sdk_grants")
    )
    expected = {
        "fk_runtime_sdk_session_fence": (
            [
                "runtime_session_id",
                "claim_token_digest",
                "worker_incarnation_id",
                "supervisor_incarnation_id",
            ],
            [
                "runtime_sessions.id",
                "runtime_sessions.claim_token_digest",
                "runtime_sessions.worker_incarnation_id",
                "runtime_sessions.supervisor_incarnation_id",
            ],
        ),
        "fk_runtime_sdk_session_identity": (
            [
                "runtime_session_id",
                "execution_id",
                "owner_incarnation_id",
                "workflow_attempt_id",
            ],
            [
                "runtime_sessions.id",
                "runtime_sessions.execution_id",
                "runtime_sessions.owner_incarnation_id",
                "runtime_sessions.workflow_attempt_id",
            ],
        ),
        "fk_runtime_sdk_committed_start": (
            ["committed_start_id", "runtime_session_id", "start_message_id"],
            [
                "runtime_starts.id",
                "runtime_starts.session_id",
                "runtime_starts.start_message_id",
            ],
        ),
        "fk_runtime_sdk_owner_caller": (
            ["execution_id", "owner_incarnation_id", "caller_snapshot_digest"],
            [
                "runtime_execution_owners.execution_id",
                "runtime_execution_owners.owner_incarnation_id",
                "runtime_execution_owners.caller_sha256",
            ],
        ),
        "fk_runtime_sdk_start_clock": (
            [
                "committed_start_id",
                "runtime_session_id",
                "start_message_id",
                "started_at",
            ],
            [
                "runtime_starts.id",
                "runtime_starts.session_id",
                "runtime_starts.start_message_id",
                "runtime_starts.started_at",
            ],
        ),
        "fk_runtime_sdk_execution_source": (
            ["execution_id", "owner_incarnation_id", "workflow_id", "source_id"],
            [
                "runtime_execution_owners.execution_id",
                "runtime_execution_owners.owner_incarnation_id",
                "runtime_execution_owners.workflow_id",
                "runtime_execution_owners.deployment_id",
            ],
        ),
        "fk_runtime_sdk_source_install": (
            ["source_id", "workflow_id", "solution_install_id"],
            [
                "runtime_deployment_artifacts.deployment_id",
                "runtime_deployment_artifacts.workflow_id",
                "runtime_deployment_artifacts.solution_id",
            ],
        ),
    }
    actual = {
        fk.name: (
            list(fk.column_keys),
            [item.target_fullname.removeprefix("public.") for item in fk.elements],
        )
        for fk in tables[GRANTS].foreign_key_constraints
    }
    assert actual == expected
    for table in tables.values():
        for fk in table.foreign_key_constraints:
            assert fk.ondelete == "RESTRICT"
            assert all(
                item.target_fullname.startswith("public.") for item in fk.elements
            )
    assert (
        "UNIQUE (execution_id, owner_incarnation_id, workflow_id, deployment_id)" in sql
    )
    assert "UNIQUE (deployment_id, workflow_id, solution_id)" in sql
    assert (
        "UNIQUE (workflow_attempt_id)" in sql and "UNIQUE (runtime_session_id)" in sql
    )


def test_c3_does_not_rebuild_c1_fences_or_enable_authority() -> None:
    sql, _ = _render()
    assert "CREATE ROLE" not in sql and "GRANT " not in sql
    assert "CREATE TABLE IF NOT EXISTS" not in sql
    assert "ADD COLUMN claim_token_digest" not in sql
    assert "DROP TRIGGER runtime_session" not in sql
    assert "TG_OP = 'DELETE'" in sql
    assert "OLD.revoked_at IS NOT NULL" in sql
    for table in (GRANTS, OPERATIONS):
        assert f"REVOKE ALL ON public.{table} FROM PUBLIC" in sql
        assert (
            f"ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.enforce_runtime_sdk_evidence()"
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
        return (
            self.isolation
            if self.statements[-1] == "SHOW transaction_isolation"
            else self.occupied
        )

    def drop_table(self, table, *, schema):
        assert schema == "public"
        self.dropped.append(table)

    def drop_constraint(self, _name, _table, *, type_, schema):
        assert type_ == "unique" and schema == "public"


def test_populated_downgrade_preserves_grants_and_operations() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=True)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(
        RuntimeError, match="Retain runtime SDK grant and operation evidence"
    ):
        scope["downgrade"]()
    assert not op.dropped


def test_empty_downgrade_locks_all_affected_tables_before_snapshot() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False)
    scope["downgrade"].__globals__["op"] = op
    scope["downgrade"]()
    assert op.statements[0] == "SHOW transaction_isolation"
    assert op.statements[1] == (
        "LOCK TABLE public.runtime_deployment_artifacts, public.runtime_execution_owners, "
        "public.workflow_runtime_sdk_grants, public.workflow_runtime_sdk_grant_operations "
        "IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    assert op.statements[2].startswith("SELECT EXISTS ")
    assert op.dropped == [OPERATIONS, GRANTS]


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable", "unknown"])
def test_downgrade_rejects_stale_snapshot(isolation: str) -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False, isolation=isolation)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="requires read committed isolation"):
        scope["downgrade"]()
    assert op.statements == ["SHOW transaction_isolation"]
    assert not op.dropped
