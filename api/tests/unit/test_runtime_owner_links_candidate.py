"""Offline DDL contract checks; these do not install or execute candidate SQL."""

from __future__ import annotations

import io
import runpy
from pathlib import Path

import pytest
from alembic import op as alembic_op
from alembic.migration import MigrationContext
from alembic.operations import Operations

API = Path(__file__).resolve().parents[2]
CANDIDATE = API / "alembic/candidates/20261016_runtime_owner_links.py"


def _scope() -> dict:
    return runpy.run_path(str(CANDIDATE))


def _upgrade_sql() -> str:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        _scope()["upgrade"]()
    return " ".join(output.getvalue().split())


def test_candidate_extends_main_but_is_not_an_active_revision() -> None:
    scope = _scope()
    assert scope["revision"] == "20261016_runtime_owner_links"
    assert len(scope["revision"]) <= 32
    assert scope["down_revision"] == "20261012_work_delivery_started"
    assert CANDIDATE.parent == API / "alembic/candidates"
    assert not (API / "alembic/versions" / CANDIDATE.name).exists()


def test_offline_ddl_creates_one_common_family_and_no_writer_grants() -> None:
    sql = _upgrade_sql()
    for table in (
        "runtime_deployment_artifacts",
        "runtime_execution_owners",
        "runtime_sessions",
        "runtime_workflow_live_links",
    ):
        assert f"CREATE TABLE public.{table}" in sql
    assert sql.count("CREATE TABLE ") == 4
    assert "CREATE ROLE" not in sql
    assert "GRANT " not in sql
    assert "CREATE TABLE IF NOT EXISTS" not in sql
    assert "workflow_runtime_sdk_grants" not in sql


def test_native_cascade_can_remove_only_live_association() -> None:
    sql = _upgrade_sql()
    assert (
        "FOREIGN KEY(workflow_attempt_id, execution_id, claim_token, worker_incarnation_id) "
        "REFERENCES public.workflow_execution_attempts "
        "(id, execution_id, claim_token, worker_incarnation_id) ON DELETE CASCADE"
    ) in sql
    assert (
        "FOREIGN KEY(session_id, execution_id, owner_incarnation_id, workflow_attempt_id, "
        "claim_token, worker_incarnation_id) REFERENCES public.runtime_sessions "
        "(id, execution_id, owner_incarnation_id, workflow_attempt_id, claim_token, "
        "worker_incarnation_id) ON DELETE RESTRICT"
    ) in sql
    sessions = sql.split("CREATE TABLE public.runtime_sessions", 1)[1].split(
        "CREATE TABLE public.runtime_workflow_live_links", 1
    )[0]
    assert "REFERENCES public.workflow_execution_attempts" not in sessions
    assert "UNIQUE (workflow_attempt_id)" in sessions
    assert "GENERATED ALWAYS AS" in sessions


@pytest.mark.parametrize(
    "column,target",
    [
        ("live_execution_id", "executions"),
        ("live_deployment_id", "solution_deployments"),
        ("live_workflow_id", "workflows"),
        ("live_solution_id", "solutions"),
        ("live_reviewed_by", "users"),
    ],
)
def test_live_links_clear_without_nulling_historical_ids(
    column: str, target: str
) -> None:
    sql = _upgrade_sql()
    assert (
        f"FOREIGN KEY({column}) REFERENCES public.{target} (id) ON DELETE SET NULL"
        in sql
    )
    assert f"{column} IS NULL OR {column} = " in sql
    for historical in (
        "execution_id",
        "deployment_id",
        "workflow_id",
        "solution_id",
        "reviewed_by",
    ):
        assert (
            f"FOREIGN KEY({historical}) REFERENCES public.{target} (id) ON DELETE SET NULL"
            not in sql
        )


def test_session_and_live_link_are_one_commit_not_just_independent_valid_ids() -> None:
    sql = _upgrade_sql()
    assert (
        "CREATE CONSTRAINT TRIGGER runtime_session_requires_live_link AFTER INSERT"
        in sql
    )
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "new runtime session requires atomic native live link" in sql
    assert "runtime_live_link_identity BEFORE INSERT OR UPDATE OR DELETE" in sql
    assert "live link requires current open native fence" in sql
    assert "new runtime session must be open" in sql
    assert "runtime_session_identity AFTER INSERT OR UPDATE OR DELETE" in sql


def test_interpreted_descriptor_does_not_require_native_only_json_field() -> None:
    sql = _upgrade_sql()
    assert "artifact->>'kind' = 'native-executable/v1'" in sql
    assert "artifact->>'kind' = 'interpreted-runtime/v1'" in sql
    assert "NOT (artifact ? 'build_evidence_sha256')" in sql
    assert "artifact->>'build_evidence_sha256' = build_evidence_sha256" in sql
    assert "COALESCE(jsonb_typeof(artifact)" in sql


class _DowngradeOp:
    def __init__(self, occupied: bool):
        self.occupied = occupied
        self.dropped: list[str] = []

    def get_bind(self):
        return self

    def execute(self, _statement):
        return self

    def scalar_one(self):
        return self.occupied

    def drop_table(self, table, *, schema):
        assert schema == "public"
        self.dropped.append(table)

    def drop_constraint(self, _name, _table, *, type_, schema):
        assert type_ == "unique" and schema == "public"


def test_populated_downgrade_refuses_before_removing_identity() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=True)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="Retain common runtime identity"):
        scope["downgrade"]()
    assert not op.dropped


def test_empty_downgrade_uses_reverse_dependency_order() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False)
    scope["downgrade"].__globals__["op"] = op
    scope["downgrade"]()
    assert op.dropped == [
        "runtime_workflow_live_links",
        "runtime_sessions",
        "runtime_execution_owners",
        "runtime_deployment_artifacts",
    ]


def test_guard_functions_do_not_bundle_multiple_prepared_ddl_commands() -> None:
    statements = []

    class Recorder:
        def __getattr__(self, name):
            return getattr(alembic_op, name)

        def execute(self, statement):
            statements.append(str(statement))
            alembic_op.execute(statement)

    scope = _scope()
    scope["upgrade"].__globals__["op"] = Recorder()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        scope["upgrade"]()
    functions = [
        sql for sql in statements if sql.lstrip().startswith("CREATE FUNCTION")
    ]
    assert len(functions) == 5
    for sql in functions:
        assert sql.count("CREATE FUNCTION") == 1
        assert "CREATE TRIGGER" not in sql and "REVOKE ALL" not in sql
