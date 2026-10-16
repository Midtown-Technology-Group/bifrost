"""Offline C4 storage checks against the pinned common mechanism source.

DDL rendering does not execute PostgreSQL or establish physical custody.
"""

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
CANDIDATE = CANDIDATES / "20261016_runtime_admissions_resources.py"
AUTHORITY = json.loads(
    (
        Path(__file__).parent / "fixtures/common_admission_resource_storage_v1.json"
    ).read_text()
)
ADMISSIONS = "runtime_admissions"
RESOURCES = "runtime_resource_settlements"


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
                "20261016_runtime_sdk_grants.py",
            ):
                runpy.run_path(str(CANDIDATES / name))["upgrade"]()
        scope = _scope()
        scope["upgrade"].__globals__["op"] = Recorder()
        scope["upgrade"]()
    return " ".join(output.getvalue().split()), tables


def test_candidate_is_inactive_and_follows_closed_grant_storage() -> None:
    scope = _scope()
    assert scope["revision"] == "20261016_runtime_resources"
    assert len(scope["revision"]) <= 32
    assert scope["down_revision"] == "20261016_runtime_grants"
    assert not (API / "alembic/versions" / CANDIDATE.name).exists()
    assert (
        AUTHORITY["authority"]["commit"] == "56d1fb006d220b9dbb0747f1cca4def18677f918"
    )


@pytest.mark.parametrize("name", [ADMISSIONS, RESOURCES])
def test_source_columns_checks_and_keys_are_preserved(name: str) -> None:
    _, tables = _render()
    table = tables[name]
    expected = AUTHORITY["tables"][name]
    actual_columns = {
        column.name: {
            "type": column.type.compile(dialect=postgresql.dialect()),
            "nullable": column.nullable,
            "primary_key": column.primary_key,
            "server_default": (
                str(column.server_default.arg) if column.server_default else None
            ),
        }
        for column in table.columns
    }
    assert list(actual_columns) == list(expected["columns"])
    assert actual_columns == expected["columns"]
    assert {
        item.name: " ".join(str(item.sqltext).split())
        for item in table.constraints
        if isinstance(item, sa.CheckConstraint)
    } == expected["checks"]
    assert {
        item.name: list(item.columns.keys())
        for item in table.constraints
        if isinstance(item, sa.UniqueConstraint)
    } == expected["uniques"]
    assert {
        item.name: {
            "columns": list(item.columns.keys()),
            "references": [
                element.target_fullname.removeprefix("public.")
                for element in item.elements
            ],
            "ondelete": item.ondelete,
        }
        for item in table.foreign_key_constraints
    } == expected["foreign_keys"]
    assert all(item.match in (None, "SIMPLE") for item in table.foreign_key_constraints)


def test_full_storage_chain_has_reference_keys_before_dependents_and_no_roles() -> None:
    sql, _ = _render(include_parents=True)
    assert sql.count("CREATE TABLE ") == 10
    assert (
        sql.index("CREATE TABLE public.runtime_sessions")
        < sql.index("CREATE TABLE public.runtime_starts")
        < sql.index("CREATE TABLE public.workflow_runtime_sdk_grants")
        < sql.index("ADD CONSTRAINT uq_runtime_sdk_grant_admission_identity")
        < sql.index("CREATE TABLE public.runtime_admissions")
        < sql.index("CREATE TABLE public.runtime_resource_settlements")
    )
    assert (
        "UNIQUE (id, runtime_session_id, committed_start_id, start_message_id, "
        "operations_digest, initial_access_expires_at)"
    ) in sql
    assert "CREATE ROLE" not in sql and "GRANT " not in sql
    assert "IF NOT EXISTS" not in sql
    _, tables = _render()
    assert all(
        element.target_fullname.startswith("public.")
        for table in tables.values()
        for fk in table.foreign_key_constraints
        for element in fk.elements
    )
    assert "ON DELETE CASCADE" not in _render()[0]


def test_physical_fact_requires_closed_full_session_and_preserves_immutability() -> (
    None
):
    sql, _ = _render()
    assert (
        "SELECT closed_at INTO closed FROM public.runtime_sessions "
        "WHERE id=NEW.session_id AND execution_id=NEW.execution_id "
        "AND owner_incarnation_id=NEW.owner_incarnation_id "
        "AND workflow_attempt_id=NEW.workflow_attempt_id FOR KEY SHARE NOWAIT"
    ) in sql
    assert "IF closed IS NULL THEN RAISE EXCEPTION" in sql
    assert "LANGUAGE plpgsql SET search_path = pg_catalog" in sql
    for table, trigger in (
        (ADMISSIONS, "runtime_admission_immutable"),
        (RESOURCES, "runtime_resource_immutable"),
    ):
        assert (
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.enforce_runtime_report_immutability()"
        ) in sql
        assert f"REVOKE ALL ON public.{table} FROM PUBLIC" in sql
    assert (
        "CREATE TRIGGER runtime_resource_closed BEFORE INSERT "
        "ON public.runtime_resource_settlements FOR EACH ROW "
        "EXECUTE FUNCTION public.enforce_runtime_resource_closed()"
    ) in sql
    assert (
        "REVOKE ALL ON FUNCTION public.enforce_runtime_resource_closed() FROM PUBLIC"
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

    def drop_constraint(self, name, table, *, type_, schema):
        assert (name, table, type_, schema) == (
            "uq_runtime_sdk_grant_admission_identity",
            "workflow_runtime_sdk_grants",
            "unique",
            "public",
        )
        self.dropped.append(name)


def test_populated_downgrade_retains_provision_and_resource_facts() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=True)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="Retain runtime provision/release"):
        scope["downgrade"]()
    assert op.statements[2] == (
        "SELECT EXISTS (SELECT 1 FROM public.runtime_admissions) OR "
        "EXISTS (SELECT 1 FROM public.runtime_resource_settlements)"
    )
    assert not op.dropped


def test_empty_downgrade_excludes_writers_before_fresh_snapshot() -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False)
    scope["downgrade"].__globals__["op"] = op
    scope["downgrade"]()
    assert op.statements[:2] == [
        "SHOW transaction_isolation",
        "LOCK TABLE public.workflow_runtime_sdk_grants, public.runtime_sessions, "
        "public.runtime_admissions, public.runtime_resource_settlements "
        "IN ACCESS EXCLUSIVE MODE NOWAIT",
    ]
    assert op.statements[2].startswith("SELECT EXISTS ")
    assert op.dropped == [
        RESOURCES,
        ADMISSIONS,
        "uq_runtime_sdk_grant_admission_identity",
    ]
    assert op.statements[3] == "DROP FUNCTION public.enforce_runtime_resource_closed()"
    assert not any("report_immutability" in item for item in op.statements)


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable", "unknown"])
def test_downgrade_refuses_stale_or_unknown_snapshot(isolation: str) -> None:
    scope = _scope()
    op = _DowngradeOp(occupied=False, isolation=isolation)
    scope["downgrade"].__globals__["op"] = op
    with pytest.raises(RuntimeError, match="requires read committed isolation"):
        scope["downgrade"]()
    assert op.statements == ["SHOW transaction_isolation"]
    assert not op.dropped
