"""Injected material/transaction characterization; no committed DB/oracle proof."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import monotonic_ns
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest

from tests.e2e.platform import agent_reference_lineage as reader

if TYPE_CHECKING:
    from asyncpg import Connection

U = UUID("10000000-0000-4000-8000-000000000001")
ORG = UUID("10000000-0000-4000-8000-000000000002")
A = UUID("10000000-0000-4000-8000-000000000003")
S = UUID("10000000-0000-4000-8000-000000000004")
D1 = UUID("10000000-0000-4000-8000-000000000005")
D2 = UUID("10000000-0000-4000-8000-000000000006")
ROLE = UUID("10000000-0000-4000-8000-000000000007")
PROFILE = UUID("10000000-0000-4000-8000-000000000008")
CONNECTION = UUID("10000000-0000-4000-8000-000000000009")
R = UUID("10000000-0000-4000-8000-000000000010")
E = UUID("abcdef00-0000-4000-8000-000000000011")
STEP = UUID("10000000-0000-4000-8000-000000000012")
PA = UUID("10000000-0000-4000-8000-000000000013")
MARKER = "private-must-not-be-repr-or-exception-text"
IDS = reader.SetupIds(U, ORG, A, S, D1, D2, ROLE, PROFILE, CONNECTION)
RUN_IDS = reader.RunIds(IDS, R)
NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def transaction_row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "backend_pid": 123,
        "postmaster_started_at": NOW,
        "virtual_transaction": "7/11",
        "own_virtual_xid": "7/11",
        "own_lock_mode": "ExclusiveLock",
        "own_lock_granted": True,
    }
    row.update(changes)
    return row


def deadline() -> int:
    return monotonic_ns() + 30_000_000_000


class InjectedConnection:
    """Public API double. SQL custody assertions do not execute PostgreSQL."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, tuple[Any, ...], float | None]] = []
        self.active = False
        self.terminated = False
        self.isolation = "read committed"
        self.read_only = "on"
        self.settings: object | None = None
        self.transaction_rows: object = [transaction_row()]
        self.transaction_rows_after: object | None = None
        self.transaction_reads = 0
        self.metadata_override: dict[str, object] = {}
        self.metadata_reads: dict[str, int] = {}
        self.change_after: dict[str, list[dict[str, Any]]] = {}
        self.fail_sql: str | None = None
        self.stall_sql: str | None = None
        self.cancel_sql: str | None = None
        self.exit_stays_active = False
        self.material_override: dict[str, object] = {}
        self.timeouts: list[float] = []

    def is_in_transaction(self) -> bool:
        return self.active

    def terminate(self) -> None:
        self.terminated = True
        self.active = False

    async def effect(self, sql: str) -> None:
        if self.cancel_sql is not None and self.cancel_sql in sql:
            raise asyncio.CancelledError
        if self.stall_sql is not None and self.stall_sql in sql:
            await asyncio.Event().wait()
        if self.fail_sql is not None and self.fail_sql in sql:
            raise RuntimeError(MARKER)

    async def execute(self, sql: str, *, timeout: float) -> str:
        self.calls.append((sql, (), timeout))
        self.timeouts.append(timeout)
        await self.effect(sql)
        if sql.startswith("BEGIN"):
            self.active = True
            self.isolation = (
                "repeatable read" if "REPEATABLE READ" in sql else "read committed"
            )
        elif sql in ("COMMIT", "ROLLBACK"):
            self.active = self.exit_stays_active
        else:
            raise AssertionError("unexpected control statement")
        return sql

    async def fetch(self, sql: str, *args: Any, timeout: float) -> list[dict[str, Any]]:
        self.calls.append((sql, args, timeout))
        self.timeouts.append(timeout)
        await self.effect(sql)
        if sql == reader._SETTINGS_SQL:
            if self.settings is not None:
                return cast(list[dict[str, Any]], self.settings)
            return [{"isolation": self.isolation, "read_only": self.read_only}]
        if sql == reader._TRANSACTION_IDENTITY_SQL:
            self.transaction_reads += 1
            rows = (
                self.transaction_rows_after
                if self.transaction_reads % 2 == 0
                and self.transaction_rows_after is not None
                else self.transaction_rows
            )
            return cast(list[dict[str, Any]], rows)
        marker = sql.split(" */", 1)[0].split(":")
        name, phase = marker[1], marker[2]
        group = reader._GROUPS[name]
        rows = self.rows.get(name, [])
        if phase == "metadata":
            if name in self.metadata_override:
                return cast(list[dict[str, Any]], self.metadata_override[name])
            count = self.metadata_reads.get(name, 0)
            self.metadata_reads[name] = count + 1
            if count and name in self.change_after:
                rows = self.change_after[name]
            # Only model this fixed source ORDER BY, not general SQL behavior.
            if name in ("workflows_selected", "workflows_solution"):
                rows = sorted(rows, key=lambda row: row["id"])
            result = []
            for row in rows:
                metadata = {key: row[key] for key in group.keys}
                metadata.update({cell.name: row[cell.name] for cell in group.selectors})
                metadata.update(
                    {
                        cell.name + "_bytes": utf8_size(row[cell.name])
                        for cell in group.cells
                        if cell.variable
                    }
                )
                result.append(metadata)
            return result
        if name in self.material_override:
            return cast(list[dict[str, Any]], self.material_override[name])
        assert phase == "material"
        identities, budget = args[:-1], args[-1]
        matching = [
            row for row in rows if tuple(row[k] for k in group.keys) == identities
        ]
        return [material_record(group, row, budget) for row in matching]


def observer(connection: InjectedConnection) -> Connection:
    return cast("Connection", connection)


def utf8_size(value: object) -> int | None:
    return None if value is None else len(cast(str, value).encode("utf-8"))


def material_record(
    group: reader._Group, row: dict[str, Any], remaining: int
) -> dict[str, Any]:
    result = dict(row)
    lengths = {
        cell.name + "_bytes": utf8_size(row[cell.name])
        for cell in group.cells
        if cell.variable
    }
    charge = group.fixed_charge + sum(
        size for size in lengths.values() if size is not None
    )
    oversize = charge > remaining or any(
        (lengths[cell.name + "_bytes"] or 0) > cell.cap
        for cell in group.cells
        if cell.variable
    )
    if oversize:
        for cell in group.cells:
            if cell.variable:
                result[cell.name] = None
    result.update(lengths)
    result.update(row_charge=charge, oversize=oversize)
    return result


def source_row(name: str, **changes: Any) -> dict[str, Any]:
    """Only a unit data constructor; no assertions derive expected source semantics."""
    group = reader._GROUPS[name]
    result: dict[str, Any] = {}
    for cell in group.cells:
        if cell.nullable:
            result[cell.name] = None
        elif cell.kind == "uuid":
            result[cell.name] = U
        elif cell.kind == "int":
            result[cell.name] = 1
        elif cell.kind == "bool":
            result[cell.name] = True
        elif cell.kind == "float":
            result[cell.name] = 0.5
        elif cell.kind == "decimal":
            result[cell.name] = Decimal("1.25")
        elif cell.kind == "datetime":
            result[cell.name] = NOW
        elif cell.kind.startswith("json_"):
            result[cell.name] = (
                "[]" if cell.kind in ("json_array", "json_strings") else "{}"
            )
        else:
            result[cell.name] = "fixture"
    result.update(changes)
    return result


def present_setup(connection: InjectedConnection) -> None:
    connection.rows.update(
        {
            "user": [
                source_row(
                    "user",
                    id=U,
                    organization_id=ORG,
                    email="normal@example.invalid",
                    name=MARKER,
                    is_superuser=False,
                    is_system=False,
                    is_external=False,
                )
            ],
            "organization": [source_row("organization", id=ORG)],
            "user_roles": [
                source_row(
                    "user_roles", user_id=U, role_id=ROLE, name="capacity-reader"
                )
            ],
            "agent": [
                source_row(
                    "agent",
                    id=A,
                    organization_id=ORG,
                    owner_user_id=U,
                    access_level="private",
                    llm_profile_id=PROFILE,
                )
            ],
            "agent_tools": [
                source_row("agent_tools", agent_id=A, workflow_id=reader.CAPACITY_ID),
                source_row("agent_tools", agent_id=A, workflow_id=reader.PREVIEW_ID),
            ],
            "workflows_selected": [
                source_row(
                    "workflows_selected", id=w, solution_id=S, organization_id=ORG
                )
                for w in reader.WORKFLOW_IDS
            ],
            "workflows_solution": [
                source_row(
                    "workflows_solution", id=w, solution_id=S, organization_id=ORG
                )
                for w in reader.WORKFLOW_IDS
            ],
            "workflow_roles": [
                source_row("workflow_roles", workflow_id=w, role_id=ROLE)
                for w in (reader.CAPACITY_ID, reader.PREVIEW_ID)
            ],
            "solution": [
                source_row(
                    "solution", id=S, organization_id=ORG, active_deployment_id=D2
                )
            ],
            "deployments": [
                source_row("deployments", id=d, solution_id=S, organization_id=ORG)
                for d in (D1, D2)
            ],
            "profile": [source_row("profile", id=PROFILE, connection_id=CONNECTION)],
            "connection": [source_row("connection", id=CONNECTION)],
            "assignments": [
                source_row("assignments", assignment_key=key, profile_id=PROFILE)
                for key in (
                    "primary",
                    "summarization",
                    "tuning",
                    "image_generation",
                    "video_generation",
                    "chat_default",
                )
            ],
        }
    )


def present_steps(
    connection: InjectedConnection,
    content: str | None = None,
    step_type: str = "tool_result",
) -> None:
    connection.rows["steps"] = [
        source_row(
            "steps",
            id=STEP,
            run_id=R,
            step_number=4,
            type=step_type,
            content=content
            if content is not None
            else json.dumps({"execution_id": str(E)}),
        )
    ]


def material_calls(
    connection: InjectedConnection, name: str
) -> list[tuple[str, tuple[Any, ...], float | None]]:
    return [call for call in connection.calls if f":{name}:material */" in call[0]]


@pytest.mark.asyncio
async def test_setup_preserves_two_independent_workflow_sets_and_actual_grants() -> (
    None
):
    connection = InjectedConnection()
    present_setup(connection)
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.kind is reader.ReadKind.OBSERVED and result.code is None
    snapshot = cast(reader.SetupSnapshot, result.snapshot)
    assert snapshot.ids is IDS
    assert snapshot.agent_roles == ()
    assert [(row.workflow_id, row.role_id) for row in snapshot.workflow_roles] == [
        (reader.CAPACITY_ID, ROLE),
        (reader.PREVIEW_ID, ROLE),
    ]
    assert tuple(row.id for row in snapshot.workflow_rows) == tuple(
        sorted(reader.WORKFLOW_IDS)
    )
    assert tuple(row.id for row in snapshot.solution_workflow_rows) == tuple(
        sorted(reader.WORKFLOW_IDS)
    )
    assert material_calls(connection, "workflows_selected") and material_calls(
        connection, "workflows_solution"
    )
    assert snapshot.principal_rows.roles[0].name == "capacity-reader"
    assert snapshot.transaction_facts == reader.TransactionFacts(
        "read committed", True, reader.TransactionIdentity(NOW, 123, "7/11")
    )
    assert connection.calls[0][0] == "BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY"
    assert connection.calls[-1][0] == "COMMIT"
    assert not connection.active and not connection.terminated
    assert all(0 < cast(float, call[2]) <= 3 for call in connection.calls)
    assert connection.timeouts == sorted(connection.timeouts, reverse=True)
    assert MARKER not in repr(result) + repr(snapshot) + repr(snapshot.principal_rows)
    assert MARKER not in repr(snapshot.principal_rows.user)
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "agent_row", None)


@pytest.mark.asyncio
async def test_empty_final_retains_all_deferred_child_groups_without_acceptance() -> (
    None
):
    connection = InjectedConnection()
    result = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.FINAL,
        deadline_ns=deadline(),
    )
    assert result.kind is reader.ReadKind.OBSERVED
    snapshot = cast(reader.RunSnapshot, result.snapshot)
    assert snapshot.discovered_execution_id is None
    assert snapshot.run_row is None and snapshot.step_rows == ()
    assert snapshot.deferred_child_groups == tuple(reader.DeferredChildGroup)
    assert not any(
        ":execution:" in call[0]
        or ":generic_known:" in call[0]
        or ":usage_known:" in call[0]
        for call in connection.calls
    )
    assert snapshot.transaction_facts.isolation == "repeatable read"
    assert not hasattr(result, "accepted") and not hasattr(result, "settled")


@pytest.mark.asyncio
async def test_capacity_execution_metadata_before_step4_never_discovers_e() -> None:
    connection = InjectedConnection()
    for name in ("solution_executions", "selected_executions"):
        connection.rows[name] = [source_row(name, id=E, workflow_id=reader.CAPACITY_ID)]
    result = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.POLL,
        deadline_ns=deadline(),
    )
    snapshot = cast(reader.RunSnapshot, result.snapshot)
    assert result.kind is reader.ReadKind.OBSERVED
    assert snapshot.setup_material.solution_execution_metadata[0].id == E
    assert snapshot.discovered_execution_id is None
    assert snapshot.deferred_child_groups == tuple(reader.DeferredChildGroup)
    assert not any(E in call[1] or str(E) in call[1] for call in connection.calls)


@pytest.mark.asyncio
async def test_actual_step4_drives_every_child_read_and_text_delivery_bind() -> None:
    connection = InjectedConnection()
    present_steps(connection)
    connection.rows["execution"] = [
        source_row("execution", id=E, workflow_id=reader.CAPACITY_ID)
    ]
    result = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.FINAL,
        deadline_ns=deadline(),
    )
    snapshot = cast(reader.RunSnapshot, result.snapshot)
    assert result.kind is reader.ReadKind.OBSERVED
    assert (
        snapshot.discovered_execution_id == E and snapshot.deferred_child_groups == ()
    )
    calls = {
        sql.split(" */", 1)[0].split(":")[1]: args
        for sql, args, _ in connection.calls
        if ":metadata */" in sql
    }
    assert calls["execution"] == (E,)
    assert calls["workflow_attempts"] == (E,)
    assert calls["workflow_delivery"] == (str(E),)
    assert calls["generic_known"] == (R, E)
    assert calls["usage_known"] == (R, E)
    assert calls["agent_delivery"] == (str(R),)
    assert calls["summary_delivery"] == (str(R),)
    assert all(type(item) is UUID for item in calls["workflows_selected"][0])


@pytest.mark.parametrize(
    "content,step_type",
    [
        ("{}", "tool_result"),
        ('{"execution_id":null}', "tool_result"),
        ('{"execution_id":12}', "tool_result"),
        ('{"execution_id":"bad"}', "tool_result"),
        (json.dumps({"execution_id": str(E).upper()}), "tool_result"),
        (json.dumps({"execution_id": str(E).replace("-", "")}), "tool_result"),
        ("[]", "tool_result"),
        (json.dumps({"execution_id": str(E)}), "tool_call"),
    ],
)
@pytest.mark.asyncio
async def test_step4_discovery_invalid_has_no_fallback(
    content: str, step_type: str
) -> None:
    connection = InjectedConnection()
    present_steps(connection, content, step_type)
    result = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.POLL,
        deadline_ns=deadline(),
    )
    # Nonobject content is rejected as source-shape material before discovery.
    assert result.kind is reader.ReadKind.FAILED
    assert result.code in (
        reader.ReaderCode.DISCOVERY_INVALID,
        reader.ReaderCode.MATERIAL_INVALID,
    )
    assert result.snapshot is None and result.acquisition is None
    assert not any(":execution:" in call[0] for call in connection.calls)


@pytest.mark.asyncio
async def test_two_actual_step4_rows_are_ambiguous_despite_valid_uuids() -> None:
    connection = InjectedConnection()
    present_steps(connection)
    connection.rows["steps"].append(
        source_row(
            "steps",
            id=PA,
            run_id=R,
            step_number=4,
            type="tool_result",
            content=json.dumps({"execution_id": str(E)}),
        )
    )
    result = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.POLL,
        deadline_ns=deadline(),
    )
    assert result.code is reader.ReaderCode.DISCOVERY_INVALID


@pytest.mark.parametrize("name", tuple(reader._GROUPS))
@pytest.mark.asyncio
async def test_each_cardinality_limit_rejects_before_private_fetch(name: str) -> None:
    group = reader._GROUPS[name]
    connection = InjectedConnection()
    connection.rows[name] = [source_row(name) for _ in range(group.maximum + 1)]
    observation = reader._Read(observer(connection), deadline())
    with pytest.raises(reader._Fault) as failure:
        await observation.group(name, U)
    assert failure.value.code is reader.ReaderCode.CARDINALITY_EXCESS
    assert not material_calls(connection, name)
    assert failure.value.__cause__ is None and failure.value.__context__ is None


@pytest.mark.asyncio
async def test_known_generic_query_keeps_lone_wrong_type_and_rejects_second_history() -> (
    None
):
    connection = InjectedConnection()
    present_steps(connection)
    wrong = source_row(
        "generic_known",
        id=PA,
        logical_job_id=R,
        logical_job_type="workflow",
        lease_absent=True,
    )
    connection.rows["generic_known"] = [wrong]
    first = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.POLL,
        deadline_ns=deadline(),
    )
    assert first.kind is reader.ReadKind.OBSERVED
    assert (
        cast(reader.RunSnapshot, first.snapshot).known_generic_rows[0].logical_job_type
        == "workflow"
    )
    connection.calls.clear()
    connection.rows["generic_known"].append(
        source_row(
            "generic_known", id=STEP, logical_job_id=E, logical_job_type="workflow"
        )
    )
    second = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.POLL,
        deadline_ns=deadline(),
    )
    assert second.code is reader.ReaderCode.CARDINALITY_EXCESS
    assert not material_calls(connection, "generic_known")


@pytest.mark.asyncio
async def test_child_linked_usage_fails_below_total_count_limit() -> None:
    connection = InjectedConnection()
    present_steps(connection)
    connection.rows["usage_known"] = [
        source_row("usage_known", id=17, agent_run_id=R, execution_id=E)
    ]
    result = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.POLL,
        deadline_ns=deadline(),
    )
    assert result.code is reader.ReaderCode.CARDINALITY_EXCESS


@pytest.mark.asyncio
async def test_read_committed_admitted_set_change_is_not_observed_success() -> None:
    connection = InjectedConnection()
    connection.change_after["selected_executions"] = [
        source_row("selected_executions", id=E, workflow_id=reader.CAPACITY_ID)
    ]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert (
        result.kind is reader.ReadKind.CHANGED
        and result.code is reader.ReaderCode.SNAPSHOT_CHANGED
    )
    assert connection.calls[-1][0] == "COMMIT"


@pytest.mark.asyncio
async def test_missing_admitted_material_is_changed_not_newest_row_substitution() -> (
    None
):
    connection = InjectedConnection()
    connection.rows["user"] = [source_row("user", id=U)]
    connection.material_override["user"] = []
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.kind is reader.ReadKind.CHANGED
    assert cast(reader.SetupSnapshot, result.snapshot).principal_rows.user is None


@pytest.mark.parametrize(
    "raw",
    [
        '{"x":1,"x":2}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":1e9999}',
        '{"x":"\\ud800"}',
        '{"x":',
        "[" * 65 + "]" * 65,
    ],
)
def test_strict_bounded_json_rejects_without_body_exception_chain(raw: str) -> None:
    with pytest.raises(reader._Fault) as failure:
        reader._parse_json(raw, "json_container")
    assert failure.value.code is reader.ReaderCode.MATERIAL_INVALID
    assert failure.value.__cause__ is None and failure.value.__context__ is None
    assert raw not in str(failure.value)


def test_json_depth64_finite_controls_and_recursive_immutability() -> None:
    value = reader._parse_json("[" * 64 + "0" + "]" * 64, "json_array")
    assert isinstance(value, tuple)
    parsed = reader._parse_json(
        '{"bounds":{"value":1.25,"enabled":false},"tools":["capacity"]}', "json_object"
    )
    assert isinstance(parsed, Mapping)
    assert parsed["tools"] == ("capacity",)
    assert cast(Mapping, parsed["bounds"])["value"] == 1.25
    with pytest.raises(TypeError):
        cast(Any, parsed)["tools"] = ()


@pytest.mark.parametrize(
    "name,field,bad",
    [
        ("run", "caller_user_id", U),
        ("user", "is_superuser", 1),
        ("steps", "tokens_used", True),
        ("usage_known", "id", U),
        ("usage_known", "cost", 1.5),
        ("workflow_attempts", "process_id", 1000),
        ("parent_attempts", "process_id", "1000"),
        ("steps", "created_at", datetime(2026, 10, 2)),
        ("run", "confidence", float("nan")),
        ("usage_known", "provider_cost", Decimal("NaN")),
    ],
)
def test_actual_native_column_types_are_not_coerced(
    name: str, field: str, bad: object
) -> None:
    group = reader._GROUPS[name]
    row = source_row(name, **{field: bad})
    record = material_record(group, row, reader.MAX_SNAPSHOT_BYTES)
    identity = tuple(row[k] for k in group.keys)
    with pytest.raises(reader._Fault) as failure:
        reader._material(group, [record], identity, reader.MAX_SNAPSHOT_BYTES)
    assert failure.value.code is reader.ReaderCode.MATERIAL_INVALID


@pytest.mark.parametrize("field", ["name", "system_prompt", "channels"])
@pytest.mark.asyncio
async def test_oversized_metadata_denies_private_agent_fetch(field: str) -> None:
    connection = InjectedConnection()
    connection.rows["agent"] = [source_row("agent", id=A, **{field: "x" * 65537})]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.MATERIAL_OVERSIZE
    assert not material_calls(connection, "agent")


@pytest.mark.asyncio
async def test_aggregate_growth_with_legal_cells_withholds_every_variable_cell() -> (
    None
):
    connection = InjectedConnection()
    group = reader._GROUPS["agent"]
    connection.rows["agent"] = [source_row("agent", id=A)]
    grown = source_row("agent", id=A, name="x" * 1000, system_prompt="y" * 1000)
    remaining = group.fixed_charge + 1500
    withheld = material_record(group, grown, remaining)
    assert withheld["oversize"] is True
    assert all(withheld[c.name] is None for c in group.cells if c.variable)
    connection.material_override["agent"] = [withheld]
    observation = reader._Read(observer(connection), deadline())
    observation.remaining_bytes = remaining
    with pytest.raises(reader._Fault) as failure:
        await observation.group("agent", A)
    assert failure.value.code is reader.ReaderCode.MATERIAL_OVERSIZE
    assert material_calls(connection, "agent")[0][1][-1] == remaining


@pytest.mark.asyncio
async def test_insufficient_fixed_charge_stops_before_material_query() -> None:
    connection = InjectedConnection()
    connection.rows["user"] = [source_row("user", id=U)]
    observation = reader._Read(observer(connection), deadline())
    observation.remaining_bytes = reader._GROUPS["user"].fixed_charge - 1
    with pytest.raises(reader._Fault) as failure:
        await observation.group("user", U)
    assert failure.value.code is reader.ReaderCode.MATERIAL_OVERSIZE
    assert not material_calls(connection, "user")


def test_exact_utf8_charge_native_nulls_and_driver_alias_forgery() -> None:
    group = reader._GROUPS["user"]
    row = source_row(
        "user", id=U, name=None, email="é@example.invalid", organization_id=None
    )
    record = material_record(group, row, reader.MAX_SNAPSHOT_BYTES)
    native_count = 8  # id/org plus six explicit flags; source-column classification.
    assert group.fixed_charge == 256 + 128 * (native_count + 4)
    expected = 256 + 128 * (native_count + 4) + len("é@example.invalid".encode())
    assert record["row_charge"] == expected
    value, charged = reader._material(group, [record], (U,), reader.MAX_SNAPSHOT_BYTES)
    assert isinstance(value, reader.UserRow) and charged == expected
    for field, bad in (
        ("row_charge", expected - 1),
        ("email_bytes", 1),
        ("oversize", 0),
    ):
        mutant = dict(record, **{field: bad})
        with pytest.raises(reader._Fault):
            reader._material(group, [mutant], (U,), reader.MAX_SNAPSHOT_BYTES)


@pytest.mark.parametrize(
    "settings",
    [
        [],
        [{"isolation": "serializable", "read_only": "on"}],
        [{"isolation": "read committed", "read_only": "off"}],
        [{"isolation": "read committed", "read_only": True}],
    ],
)
@pytest.mark.asyncio
async def test_actual_isolation_readback_is_required(settings: object) -> None:
    connection = InjectedConnection()
    connection.settings = settings
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.ISOLATION_MISMATCH
    assert result.snapshot is None and result.acquisition is None
    assert connection.calls[-1][0] == "ROLLBACK" and not connection.terminated


@pytest.mark.asyncio
async def test_preexisting_transaction_and_expired_deadline_are_never_mutated() -> None:
    connection = InjectedConnection()
    connection.active = True
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.ACTIVE_TRANSACTION
    assert not connection.calls and connection.active and not connection.terminated
    expired = await reader.read_setup(observer(connection), IDS, deadline_ns=1)
    assert expired.code is reader.ReaderCode.DEADLINE_EXPIRED
    assert not connection.calls


@pytest.mark.parametrize("control", ["BEGIN", "COMMIT"])
@pytest.mark.asyncio
async def test_uncertain_control_terminates_and_drops_all_material(
    control: str,
) -> None:
    connection = InjectedConnection()
    connection.fail_sql = control
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.CONNECTION_TAINTED
    assert (
        result.snapshot is None and result.acquisition is None and connection.terminated
    )
    assert not any(call[0] == "ROLLBACK" for call in connection.calls)
    assert MARKER not in repr(result)


@pytest.mark.parametrize("control", ["BEGIN", "COMMIT", "ROLLBACK"])
@pytest.mark.asyncio
async def test_stalled_control_has_no_cleanup_window_or_transaction_context(
    control: str,
) -> None:
    connection = InjectedConnection()
    connection.stall_sql = control
    if control == "ROLLBACK":
        connection.settings = [{"isolation": "serializable", "read_only": "on"}]
    start = monotonic_ns()
    result = await reader.read_setup(
        observer(connection), IDS, deadline_ns=start + 50_000_000
    )
    assert result.code is reader.ReaderCode.CONNECTION_TAINTED
    assert (
        result.snapshot is None and result.acquisition is None and connection.terminated
    )
    assert monotonic_ns() - start < 500_000_000
    assert all(cast(float, call[2]) <= 0.05 for call in connection.calls)


@pytest.mark.parametrize("point", ["BEGIN", ":user:metadata", "COMMIT", "ROLLBACK"])
@pytest.mark.asyncio
async def test_cancellation_propagates_after_narrow_termination(point: str) -> None:
    connection = InjectedConnection()
    connection.cancel_sql = point
    if point == "ROLLBACK":
        connection.settings = [{"isolation": "serializable", "read_only": "on"}]
    with pytest.raises(asyncio.CancelledError):
        await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert connection.terminated
    if point != "ROLLBACK":
        assert not any(call[0] == "ROLLBACK" for call in connection.calls)


@pytest.mark.asyncio
async def test_failed_exit_readback_terminates_even_after_control_reply() -> None:
    connection = InjectedConnection()
    connection.exit_stays_active = True
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.CONNECTION_TAINTED and connection.terminated
    assert result.snapshot is None and result.acquisition is None


@pytest.mark.asyncio
async def test_non_timeout_failure_rolls_back_only_with_remaining_budget_and_redacts() -> (
    None
):
    connection = InjectedConnection()
    connection.fail_sql = ":user:metadata"
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.CONNECTION_FAILURE
    assert connection.calls[-1][0] == "ROLLBACK" and not connection.terminated
    assert (
        result.snapshot is None
        and result.acquisition is None
        and MARKER not in repr(result)
    )
    assert cast(float, connection.calls[-1][2]) <= cast(float, connection.calls[0][2])


@pytest.mark.parametrize(
    "bad_ids,bad_deadline",
    [
        (replace(IDS, user_id=str(U)), 1),
        (replace(IDS, final_deployment_id=D1), 1),
        (IDS, True),
        (IDS, 0),
        (IDS, 1.2),
    ],
)
@pytest.mark.asyncio
async def test_invalid_inputs_never_begin_or_terminate(
    bad_ids: reader.SetupIds, bad_deadline: Any
) -> None:
    connection = InjectedConnection()
    result = await reader.read_setup(
        observer(connection), bad_ids, deadline_ns=bad_deadline
    )
    assert result.code is reader.ReaderCode.INVALID_INPUT
    assert not connection.calls and not connection.terminated


def test_fixed_sql_sensitive_catalog_and_same_statement_guards() -> None:
    generic = reader._GROUPS["generic_known"].metadata_sql
    assert "logical_job_id IN ($1::uuid,$2::uuid)" in generic
    assert "logical_job_type=" not in generic
    assert "LIMIT 2" in generic
    exclusion = reader._GROUPS["solution_executions"].metadata_sql
    assert "JOIN workflows w ON e.workflow_id=w.id" in exclusion
    assert "w.solution_id=$1::uuid" in exclusion
    assert "execution_id" not in reader.RunIds.__dataclass_fields__
    assert "solution_workflow_rows" in reader.SetupSnapshot.__dataclass_fields__
    assert "caller_user_id" in reader.RunRow.__dataclass_fields__
    assert (
        "metadata" in reader.RunRow.__dataclass_fields__
        and "run_metadata" not in reader.RunRow.__dataclass_fields__
    )
    assert "runtime_evidence_hash" in reader.WorkflowAttemptRow.__dataclass_fields__
    assert "process_id" in reader.ParentAttemptRow.__dataclass_fields__
    assert "solution_id" not in reader.ExecutionRow.__dataclass_fields__
    for group in reader._GROUPS.values():
        assert "SELECT *" not in group.metadata_sql + group.material_sql
        assert not any(
            token in (group.metadata_sql + group.material_sql).upper()
            for token in (
                "FOR UPDATE",
                "FOR SHARE",
                "DELETE ",
                "INSERT ",
                "UPDATE ",
                "PG_ADVISORY",
            )
        )
        assert " LIMIT " in group.metadata_sql
        assert "row_charge<=$" in group.material_sql
        assert "NOT admitted AS oversize" in group.material_sql
        for cell in group.cells:
            if cell.variable:
                assert (
                    f"CASE WHEN admitted THEN {cell.name} ELSE NULL END AS {cell.name}"
                    in group.material_sql
                )
                assert (
                    f"coalesce({cell.name}_bytes,0)<={cell.cap}" in group.material_sql
                )
                assert cell.name not in group.metadata_sql.split("SELECT", 1)[1].split(
                    "FROM", 1
                )[0].split(",")
    for name in ("agent_delivery", "workflow_delivery", "summary_delivery"):
        assert "status=" not in reader._GROUPS[name].metadata_sql
    assert "encrypted_api_key" not in reader._GROUPS["connection"].material_sql


def test_no_runtime_adapters_or_connection_acquisition_source_tripwire() -> None:
    source = Path(reader.__file__).read_text()
    assert "from src." not in source and "import src." not in source
    assert "decrypt_secret" not in source and "SolutionDeploymentStorage" not in source
    assert "create_pool(" not in source and ".connect(" not in source
    assert "os.environ" not in source and "get_settings(" not in source
    assert "connection.transaction(" not in source and "SAVEPOINT" not in source
    assert (
        "accepted" not in reader.ReadKind.__members__
        and "settled" not in reader.ReadKind.__members__
    )


@pytest.mark.parametrize(
    "size,allowed", [(65537, True), (131072, True), (131073, False)]
)
def test_delivery_ciphertext_distinct128k_cap(size: int, allowed: bool) -> None:
    group = reader._GROUPS["agent_delivery"]
    row = source_row(
        "agent_delivery", id=PA, message_id=str(R), encrypted_envelope="x" * size
    )
    raw = material_record(group, row, reader.MAX_SNAPSHOT_BYTES)
    if allowed:
        value, charge = reader._material(group, [raw], (PA,), reader.MAX_SNAPSHOT_BYTES)
        assert isinstance(value, reader.DeliveryRow)
        assert len(value.encrypted_envelope) == size and charge >= size
    else:
        with pytest.raises(reader._Fault) as failure:
            reader._material(group, [raw], (PA,), reader.MAX_SNAPSHOT_BYTES)
        assert failure.value.code is reader.ReaderCode.MATERIAL_OVERSIZE
        assert raw["encrypted_envelope"] is None


@pytest.mark.asyncio
async def test_exhausted_cleanup_budget_does_not_attempt_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100]
    monkeypatch.setattr(reader, "monotonic_ns", lambda: clock[0])

    class ExpiringConnection(InjectedConnection):
        async def effect(self, sql: str) -> None:
            if ":user:metadata" in sql:
                clock[0] = 100 + 3_000_000_000
                raise RuntimeError(MARKER)
            await super().effect(sql)

    connection = ExpiringConnection()
    result = await reader.read_setup(
        observer(connection), IDS, deadline_ns=100 + 10_000_000_000
    )
    assert result.code is reader.ReaderCode.CONNECTION_TAINTED
    assert connection.terminated and result.snapshot is None
    assert not any(call[0] == "ROLLBACK" for call in connection.calls)


@pytest.mark.asyncio
async def test_repeat_calls_use_fresh_transactions_and_preserve_real_values() -> None:
    connection = InjectedConnection()
    connection.rows["steps"] = [
        source_row(
            "steps",
            id=STEP,
            run_id=R,
            step_number=1,
            type="llm_request",
            content='{"model":"fixture"}',
            created_at=NOW,
        )
    ]
    first = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.POLL,
        deadline_ns=deadline(),
    )
    connection.rows["steps"][0]["created_at"] = NOW.replace(microsecond=123456)
    second = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.FINAL,
        deadline_ns=deadline(),
    )
    assert cast(reader.RunSnapshot, first.snapshot).step_rows[0].created_at is NOW
    assert (
        cast(reader.RunSnapshot, second.snapshot).step_rows[0].created_at.microsecond
        == 123456
    )
    begins = [call[0] for call in connection.calls if call[0].startswith("BEGIN")]
    assert begins == [
        "BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY",
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",
    ]
    assert sum(call[0] == "COMMIT" for call in connection.calls) == 2


@pytest.mark.parametrize(
    "name,field",
    [
        ("agent", "channels"),
        ("workflows_selected", "parameters_schema"),
        ("deployments", "compiled_manifest"),
    ],
)
def test_json_schema_shapes_and_nullability_are_not_private_wire_integer_rule(
    name: str, field: str
) -> None:
    group = reader._GROUPS[name]
    row = source_row(name, **{field: "12"})
    record = material_record(group, row, reader.MAX_SNAPSHOT_BYTES)
    with pytest.raises(reader._Fault) as failure:
        reader._material(
            group,
            [record],
            tuple(row[k] for k in group.keys),
            reader.MAX_SNAPSHOT_BYTES,
        )
    assert failure.value.code is reader.ReaderCode.MATERIAL_INVALID


@pytest.mark.asyncio
async def test_integer_usage_pk_and_assignment_string_pk_material_binds() -> None:
    connection = InjectedConnection()
    connection.rows["usage_run"] = [source_row("usage_run", id=17, agent_run_id=R)]
    connection.rows["assignments"] = [
        source_row("assignments", assignment_key="primary", profile_id=PROFILE)
    ]
    result = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.POLL,
        deadline_ns=deadline(),
    )
    assert result.kind is reader.ReadKind.OBSERVED
    assert type(material_calls(connection, "usage_run")[0][1][0]) is int
    assert material_calls(connection, "usage_run")[0][1][0] == 17
    assert material_calls(connection, "assignments")[0][1][0] == "primary"
    assert "id=$1::int" in reader._GROUPS["usage_run"].material_sql
    assert "assignment_key=$1::text" in reader._GROUPS["assignments"].material_sql


@pytest.mark.asyncio
async def test_unexpected_registration_membership_is_retained_separately_for_future_oracle() -> (
    None
):
    connection = InjectedConnection()
    present_setup(connection)
    unexpected = UUID("ffffffff-0000-4000-8000-000000000001")
    connection.rows["workflows_solution"][2] = source_row(
        "workflows_solution", id=unexpected, solution_id=S
    )
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.kind is reader.ReadKind.OBSERVED
    snapshot = cast(reader.SetupSnapshot, result.snapshot)
    assert tuple(row.id for row in snapshot.workflow_rows) == tuple(
        sorted(reader.WORKFLOW_IDS)
    )
    assert snapshot.solution_workflow_rows[2].id == unexpected
    assert not hasattr(result, "verified")


def test_private_nested_json_container_reprs_are_redacted_and_immutable() -> None:
    parsed = reader._parse_json(
        json.dumps({"secret": MARKER, "nested": [MARKER]}), "json_object"
    )
    assert isinstance(parsed, Mapping)
    assert MARKER not in repr(parsed) + str(parsed)
    assert MARKER not in repr(parsed["nested"]) + str(parsed["nested"])
    assert parsed["secret"] == MARKER
    assert cast(tuple, parsed["nested"])[0] == MARKER
    with pytest.raises((AttributeError, FrozenInstanceError)):
        setattr(parsed, "_data", {})
    with pytest.raises(AttributeError):
        setattr(parsed["nested"], "extra", 1)


@pytest.mark.asyncio
async def test_actual_acquisition_binds_same_identity_and_control_order() -> None:
    connection = InjectedConnection()
    before = monotonic_ns()
    limit = deadline()
    result = await reader.read_run(
        observer(connection),
        RUN_IDS,
        stage=reader.RunReadStage.FINAL,
        deadline_ns=limit,
    )
    after = monotonic_ns()
    assert result.kind is reader.ReadKind.OBSERVED
    snapshot = cast(reader.RunSnapshot, result.snapshot)
    acquisition = cast(reader.ReadAcquisition, result.acquisition)
    identity = snapshot.transaction_facts.identity
    assert identity == reader.TransactionIdentity(NOW, 123, "7/11")
    assert acquisition.transaction is identity
    assert snapshot.setup_material.transaction_facts.identity is identity
    assert before <= acquisition.started_ns <= acquisition.completed_ns <= after < limit
    assert type(acquisition.started_ns) is type(acquisition.completed_ns) is int
    assert connection.transaction_reads == 2
    sql = [call[0] for call in connection.calls]
    assert sql[:3] == [
        reader._BEGIN_FINAL,
        reader._SETTINGS_SQL,
        reader._TRANSACTION_IDENTITY_SQL,
    ]
    assert sql[-2:] == [reader._TRANSACTION_IDENTITY_SQL, "COMMIT"]
    assert sql.count(reader._TRANSACTION_IDENTITY_SQL) == 2
    for value, attribute in (
        (identity, "backend_pid"),
        (snapshot.transaction_facts, "read_only"),
        (acquisition, "completed_ns"),
    ):
        assert "7/11" not in repr(value) and "2026" not in repr(value)
        with pytest.raises(FrozenInstanceError):
            setattr(value, attribute, 0)
    assert "acquisition" not in repr(result)


@pytest.mark.parametrize(
    "rows,code",
    [
        ([], reader.ReaderCode.MATERIAL_INVALID),
        ((), reader.ReaderCode.MATERIAL_INVALID),
        ([transaction_row(), transaction_row()], reader.ReaderCode.CARDINALITY_EXCESS),
        ([{}], reader.ReaderCode.SCHEMA_MISMATCH),
        ([dict(transaction_row(), extra=MARKER)], reader.ReaderCode.SCHEMA_MISMATCH),
        ([None], reader.ReaderCode.MATERIAL_INVALID),
    ],
)
@pytest.mark.asyncio
async def test_transaction_metadata_shape_fails_before_domain_material(
    rows: object, code: reader.ReaderCode
) -> None:
    connection = InjectedConnection()
    connection.transaction_rows = rows
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.kind is reader.ReadKind.FAILED and result.code is code
    assert result.snapshot is None and result.acquisition is None
    assert connection.transaction_reads == 1
    assert not material_calls(connection, "user")
    assert connection.calls[-1][0] == "ROLLBACK" and not connection.terminated
    assert MARKER not in repr(result)


@pytest.mark.parametrize(
    "rows,code",
    [
        ([], reader.ReaderCode.MATERIAL_INVALID),
        ([transaction_row(), transaction_row()], reader.ReaderCode.CARDINALITY_EXCESS),
        ([dict(transaction_row(), extra=MARKER)], reader.ReaderCode.SCHEMA_MISMATCH),
    ],
)
@pytest.mark.asyncio
async def test_second_metadata_shape_never_returns_snapshot_or_acquisition(
    rows: object,
    code: reader.ReaderCode,
) -> None:
    connection = InjectedConnection()
    present_setup(connection)
    connection.transaction_rows_after = rows
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.kind is reader.ReadKind.FAILED and result.code is code
    assert result.snapshot is None and result.acquisition is None
    assert connection.transaction_reads == 2 and material_calls(connection, "user")
    assert connection.calls[-1][0] == "ROLLBACK" and not connection.terminated


@pytest.mark.parametrize(
    "field,bad",
    [
        ("backend_pid", True),
        ("backend_pid", "123"),
        ("backend_pid", 0),
        ("backend_pid", -1),
        ("backend_pid", 2147483648),
        ("postmaster_started_at", NOW.isoformat()),
        ("postmaster_started_at", datetime(2026, 10, 2)),
        ("virtual_transaction", None),
        ("virtual_transaction", "01/2"),
        ("virtual_transaction", "1/02"),
        ("virtual_transaction", "0/1"),
        ("virtual_transaction", "1/0"),
        ("virtual_transaction", "١/2"),
        ("virtual_transaction", "1/2\n"),
        ("virtual_transaction", "1/2/3"),
        ("virtual_transaction", "4294967296/1"),
        ("virtual_transaction", "1/4294967296"),
        ("virtual_transaction", "10000000000/1"),
        ("own_virtual_xid", "7/12"),
        ("own_virtual_xid", 7),
        ("own_lock_mode", "ShareLock"),
        ("own_lock_mode", True),
        ("own_lock_granted", 1),
        ("own_lock_granted", False),
    ],
)
@pytest.mark.asyncio
async def test_transaction_metadata_native_and_format_corruption_is_failed(
    field: str, bad: object
) -> None:
    connection = InjectedConnection()
    row = transaction_row(**{field: bad})
    if field == "virtual_transaction":
        row["own_virtual_xid"] = bad  # Otherwise valid equality, isolate format guard.
    connection.transaction_rows = [row]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.kind is reader.ReadKind.FAILED
    assert result.code is reader.ReaderCode.MATERIAL_INVALID
    assert result.snapshot is None and result.acquisition is None
    assert connection.transaction_reads == 1
    assert not material_calls(connection, "user")


@pytest.mark.parametrize("missing", list(transaction_row()))
def test_transaction_metadata_every_alias_required(missing: str) -> None:
    row = transaction_row()
    del row[missing]
    with pytest.raises(reader._Fault) as failure:
        reader._transaction_identity([row])
    assert failure.value.code is reader.ReaderCode.SCHEMA_MISMATCH


def test_transaction_identity_limits_do_not_equate_pid_with_virtual_backend() -> None:
    identity = reader._transaction_identity(
        [
            transaction_row(
                backend_pid=2147483647,
                virtual_transaction="4294967295/4294967295",
                own_virtual_xid="4294967295/4294967295",
            )
        ]
    )
    assert identity.backend_pid == 2147483647
    assert identity.virtual_xid == "4294967295/4294967295"
    assert len(identity.virtual_xid.encode("ascii")) == 21


@pytest.mark.parametrize(
    "changes",
    [
        {"backend_pid": 124},
        {"postmaster_started_at": datetime(2026, 10, 3, tzinfo=timezone.utc)},
        {"virtual_transaction": "7/12", "own_virtual_xid": "7/12"},
    ],
)
@pytest.mark.asyncio
async def test_identity_change_within_call_is_failed_not_changed(
    changes: dict[str, object],
) -> None:
    connection = InjectedConnection()
    connection.transaction_rows_after = [transaction_row(**changes)]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.kind is reader.ReadKind.FAILED
    assert result.code is reader.ReaderCode.MATERIAL_INVALID
    assert result.snapshot is None and result.acquisition is None
    assert connection.transaction_reads == 2
    assert connection.calls[-1][0] == "ROLLBACK"
    assert not any(call[0] == "COMMIT" for call in connection.calls)


@pytest.mark.asyncio
async def test_equal_across_call_identity_is_exposed_without_freshness_state() -> None:
    connection = InjectedConnection()
    first = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    second = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert first.kind is second.kind is reader.ReadKind.OBSERVED
    a = cast(reader.ReadAcquisition, first.acquisition)
    b = cast(reader.ReadAcquisition, second.acquisition)
    assert a.transaction == b.transaction
    assert a.completed_ns <= b.started_ns
    connection.transaction_rows = [transaction_row(backend_pid=999)]
    third = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert third.kind is reader.ReadKind.OBSERVED
    assert (
        cast(reader.ReadAcquisition, third.acquisition).transaction.backend_pid == 999
    )
    assert connection.transaction_reads == 6


@pytest.mark.asyncio
async def test_changed_domain_snapshot_still_has_actual_successful_acquisition() -> (
    None
):
    connection = InjectedConnection()
    connection.change_after["user"] = [source_row("user", id=U)]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.kind is reader.ReadKind.CHANGED and result.acquisition is not None
    snapshot = cast(reader.SetupSnapshot, result.snapshot)
    assert result.acquisition.transaction is snapshot.transaction_facts.identity
    assert connection.calls[-1][0] == "COMMIT"


@pytest.mark.parametrize("point", ["before", "after"])
@pytest.mark.parametrize("action", ["stall", "cancel"])
@pytest.mark.asyncio
async def test_metadata_timeout_and_cancellation_drop_acquisition(
    point: str, action: str
) -> None:
    class MetadataFailure(InjectedConnection):
        async def effect(self, sql: str) -> None:
            if sql == reader._TRANSACTION_IDENTITY_SQL and self.transaction_reads == (
                0 if point == "before" else 1
            ):
                if action == "cancel":
                    raise asyncio.CancelledError
                await asyncio.Event().wait()
            await super().effect(sql)

    connection = MetadataFailure()
    if action == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    else:
        result = await reader.read_setup(
            observer(connection), IDS, deadline_ns=monotonic_ns() + 50_000_000
        )
        assert result.kind is reader.ReadKind.FAILED
        assert result.code is reader.ReaderCode.CONNECTION_TAINTED
        assert result.snapshot is None and result.acquisition is None
    assert connection.terminated
    assert not any(call[0] in ("COMMIT", "ROLLBACK") for call in connection.calls)


def test_acquisition_sql_and_reserve_are_fixed_and_bounded() -> None:
    sql = reader._TRANSACTION_IDENTITY_SQL
    assert sql.endswith("LIMIT 2")
    assert "l.pid = pg_catalog.pg_backend_pid()" in sql
    assert "l.virtualxid = l.virtualtransaction" in sql
    assert "l.locktype = 'virtualxid'" in sql
    assert "l.mode = 'ExclusiveLock'" in sql and "l.granted IS TRUE" in sql
    assert "pg_catalog.pg_postmaster_start_time() AS postmaster_started_at" in sql
    assert not any(
        word in sql.lower()
        for word in ("txid_current", "pg_current_xact_id", "for update", "advisory")
    )
    observation = reader._Read(observer(InjectedConnection()), deadline())
    assert reader.ACQUISITION_RESERVE_BYTES == 4096
    assert observation.remaining_bytes == 1048576 - 4096
    assert 3 * (256 + 6 * 128) + 1024 == reader.ACQUISITION_RESERVE_BYTES


@pytest.mark.asyncio
async def test_acquisition_reserve_reduces_domain_material_budget_once() -> None:
    connection = InjectedConnection()
    connection.rows["user"] = [source_row("user", id=U)]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.kind is reader.ReadKind.OBSERVED
    assert material_calls(connection, "user")[0][1][-1] == 1048576 - 4096
    assert connection.transaction_reads == 2


@pytest.mark.asyncio
async def test_reserved_bytes_cannot_be_spent_on_domain_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = InjectedConnection()
    connection.rows["user"] = [source_row("user", id=U)]
    group = reader._GROUPS["user"]
    # Deliberately reduce the unit admission ceiling: the otherwise valid row
    # fits that ceiling before reserve, and fails after the one-time reserve.
    row_charge = cast(
        int, material_record(group, connection.rows["user"][0], 1048576)["row_charge"]
    )
    monkeypatch.setattr(reader, "MAX_SNAPSHOT_BYTES", row_charge + 4095)
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.MATERIAL_OVERSIZE
    assert result.snapshot is None and result.acquisition is None
    assert connection.transaction_reads == 1
    assert connection.calls[-1][0] == "ROLLBACK"
    assert not connection.terminated


@pytest.mark.parametrize("completed", [99, 0, True, 3_000_000_101])
@pytest.mark.asyncio
async def test_actual_completed_sample_itself_must_be_valid_and_within_end(
    monkeypatch: pytest.MonkeyPatch, completed: object
) -> None:
    committed = [False]
    samples = iter([100, completed])

    def clock() -> Any:
        return next(samples, 3_000_000_101) if committed[0] else 100

    class CommittedConnection(InjectedConnection):
        async def execute(self, sql: str, *, timeout: float) -> str:
            result = await super().execute(sql, timeout=timeout)
            if sql == "COMMIT":
                committed[0] = True
            return result

    monkeypatch.setattr(reader, "monotonic_ns", clock)
    connection = CommittedConnection()
    result = await reader.read_setup(
        observer(connection), IDS, deadline_ns=10_000_000_100
    )
    assert committed[0] and connection.terminated
    assert result.code is reader.ReaderCode.CONNECTION_TAINTED
    assert result.snapshot is None and result.acquisition is None
    assert connection.calls[-1][0] == "COMMIT"


@pytest.mark.parametrize("started", [0, -1, True, 3_000_000_101])
@pytest.mark.asyncio
async def test_started_sample_is_strict_before_actual_begin(
    monkeypatch: pytest.MonkeyPatch,
    started: object,
) -> None:
    samples = iter([100, 100, 100, started])
    monkeypatch.setattr(reader, "monotonic_ns", lambda: next(samples, 100))
    connection = InjectedConnection()
    result = await reader.read_setup(
        observer(connection), IDS, deadline_ns=10_000_000_100
    )
    assert result.code is reader.ReaderCode.CONNECTION_TAINTED
    assert result.snapshot is None and result.acquisition is None
    assert not connection.calls and connection.terminated


@pytest.mark.parametrize(
    "completed,observed", [(999999999, True), (1000000100, True), (1000000101, False)]
)
@pytest.mark.asyncio
async def test_completed_sample_respects_unchanged_shorter_case_end(
    monkeypatch: pytest.MonkeyPatch,
    completed: int,
    observed: bool,
) -> None:
    committed = [False]
    samples = iter([100, completed])
    monkeypatch.setattr(
        reader,
        "monotonic_ns",
        lambda: next(samples, 1000000101) if committed[0] else 100,
    )

    class CommittedConnection(InjectedConnection):
        async def execute(self, sql: str, *, timeout: float) -> str:
            result = await super().execute(sql, timeout=timeout)
            if sql == "COMMIT":
                committed[0] = True
            return result

    connection = CommittedConnection()
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=1000000100)
    assert committed[0]
    if observed:
        assert result.kind is reader.ReadKind.OBSERVED and not connection.terminated
        assert (
            cast(reader.ReadAcquisition, result.acquisition).completed_ns == completed
        )
    else:
        assert (
            result.code is reader.ReaderCode.CONNECTION_TAINTED
            and connection.terminated
        )
        assert result.snapshot is None and result.acquisition is None


@pytest.mark.parametrize("containers", [64, 65])
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_exact_container_depth_empty_and_mixed_boundaries(
    containers: int, mixed: bool, empty: bool
) -> None:
    value: object = [] if empty else 0
    wrapping = containers - 1 if empty else containers
    for index in range(wrapping):
        value = {"child": value} if mixed and index % 2 == 0 else [value]
    raw = json.dumps(value)
    if containers == 64:
        parsed = reader._parse_json(raw, "json_container")
        assert isinstance(parsed, (Mapping, tuple))
    else:
        with pytest.raises(reader._Fault) as failure:
            reader._parse_json(raw, "json_container")
        assert failure.value.code is reader.ReaderCode.MATERIAL_INVALID


@pytest.mark.parametrize("bad", [True, -1, "22", None])
@pytest.mark.asyncio
async def test_otherwise_valid_metadata_lengths_reject_before_private_fetch(
    bad: object,
) -> None:
    connection = InjectedConnection()
    connection.metadata_override["user"] = [
        {"id": U, "email_bytes": bad, "name_bytes": None}
    ]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.MATERIAL_INVALID
    assert result.snapshot is None and result.acquisition is None
    assert not material_calls(connection, "user")


@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.asyncio
async def test_otherwise_valid_metadata_alias_corruption_is_schema_mismatch(
    extra: bool,
) -> None:
    connection = InjectedConnection()
    metadata: dict[str, object] = {"id": U, "email_bytes": 22, "name_bytes": None}
    if extra:
        metadata["unexpected"] = MARKER
    else:
        del metadata["name_bytes"]
    connection.metadata_override["user"] = [metadata]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.SCHEMA_MISMATCH
    assert result.snapshot is None and result.acquisition is None
    assert not material_calls(connection, "user")


@pytest.mark.asyncio
async def test_duplicate_admitted_identity_below_count_ceiling_fails_before_fetch() -> (
    None
):
    connection = InjectedConnection()
    row = source_row("agent_tools", agent_id=A, workflow_id=reader.CAPACITY_ID)
    connection.rows["agent_tools"] = [row, dict(row)]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.MATERIAL_INVALID
    assert result.snapshot is None and result.acquisition is None
    assert not material_calls(connection, "agent_tools")


@pytest.mark.asyncio
async def test_wrong_returned_material_identity_and_recheck_growth_never_succeed() -> (
    None
):
    connection = InjectedConnection()
    connection.rows["user"] = [source_row("user", id=U)]
    connection.material_override["user"] = [
        material_record(
            reader._GROUPS["user"], source_row("user", id=PA), reader.MAX_SNAPSHOT_BYTES
        )
    ]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.MATERIAL_INVALID
    assert result.snapshot is None and result.acquisition is None
    assert connection.transaction_reads == 1
    connection = InjectedConnection()
    connection.rows["user"] = [source_row("user", id=U)]
    connection.change_after["user"] = [
        source_row("user", id=U),
        source_row("user", id=PA),
    ]
    result = await reader.read_setup(observer(connection), IDS, deadline_ns=deadline())
    assert result.code is reader.ReaderCode.CARDINALITY_EXCESS
    assert result.snapshot is None and result.acquisition is None
    assert len(material_calls(connection, "user")) == 1
    assert connection.transaction_reads == 1
