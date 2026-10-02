"""Synthetic contract/red seams; no SQL, product imports or real connections."""

import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tests.e2e.platform import test_writer_catalog_receipt as collector
from tests.helpers import writer_catalog_receipt as contract


@pytest.fixture(autouse=True)
def deny_real_connect(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("real database connect forbidden in contract units")

    monkeypatch.setattr(collector.asyncpg, "connect", forbidden)


def relation(oid, name, *, ancestor=True, dependent=True, boundary=False):
    return {
        "relation_oid": oid,
        "nspname": "public",
        "relname": name,
        "relkind": "r",
        "owner_oid": 11,
        "owner": "bifrost",
        "schema_owner_oid": 11,
        "schema_owner": "bifrost",
        "relrowsecurity": False,
        "relforcerowsecurity": False,
        "relispartition": False,
        "has_inheritance": False,
        "is_root": oid <= 12,
        "is_ancestor": ancestor,
        "is_dependent": dependent,
        "is_boundary": boundary,
        "schema_usage": True,
        "schema_create": True,
        "can_select": True,
        "can_insert": True,
        "can_update": True,
        "can_delete": True,
        "can_truncate": True,
        "can_reference": True,
        "can_trigger": True,
    }


def edge(oid, child, parent, *, composite=False):
    keys = [1, 2] if composite else [1]
    columns = ["id", "solution_id"] if composite else ["id"]
    return {
        "constraint_oid": oid,
        "conname": f"fk_{oid}",
        "child_oid": child,
        "parent_oid": parent,
        "conkey": list(keys),
        "confkey": list(keys),
        "child_columns": list(columns),
        "parent_columns": list(columns),
        "confdeltype": "c",
        "confupdtype": "a",
        "confmatchtype": "s",
        "condeferrable": False,
        "condeferred": False,
        "convalidated": True,
        "conislocal": True,
        "coninhcount": 0,
        "parent_constraint_oid": 0,
    }


def constraint_row(oid, relation_oid, *, check=False):
    return {
        "constraint_oid": oid,
        "relation_oid": relation_oid,
        "conname": f"constraint_{oid}",
        "contype": "c" if check else "p",
        "conkey": None if check else [1],
        "columns": [] if check else ["id"],
        "index_oid": 0 if check else 400,
        "condeferrable": False,
        "condeferred": False,
        "convalidated": True,
        "conislocal": True,
        "coninhcount": 0,
        "connoinherit": False,
        "parent_constraint_oid": 0,
        "index_valid": None if check else True,
        "nulls_not_distinct": None if check else False,
    }


def trigger_row(
    oid,
    relation_oid,
    *,
    constraint_oid=0,
    kind=None,
    child=None,
    parent=None,
    related=0,
    scope="unbound",
):
    return {
        "trigger_oid": oid,
        "relation_oid": relation_oid,
        "tgname": f"trigger_{oid}",
        "tgenabled": "O",
        "tgisinternal": True,
        "tgtype": 5,
        "trigger_columns": [],
        "constraint_oid": constraint_oid,
        "related_oid": related,
        "parent_trigger_oid": 0,
        "tgdeferrable": False,
        "tginitdeferred": False,
        "tgnargs": 0,
        "has_trigger_arguments": False,
        "has_trigger_condition": False,
        "trigger_constraint_type": kind,
        "constraint_relation_oid": child,
        "referenced_relation_oid": parent,
        "reference_scope": scope,
        "function_oid": 1000,
        "function_schema": "pg_catalog",
        "proname": "RI_FKey_check_ins",
        "function_schema_owner_oid": 11,
        "function_schema_owner": "bifrost",
        "function_owner_oid": 11,
        "function_owner": "bifrost",
        "function_owner_super": True,
        "function_owner_bypassrls": True,
        "prosecdef": False,
        "proleakproof": False,
        "provolatile": "v",
        "proparallel": "u",
        "argument_type_oids": [],
        "return_type_oid": 2279,
        "has_function_configuration": False,
        "has_configured_search_path": False,
        "can_execute": True,
    }


@pytest.fixture
def observation():
    roots = [
        {"name": name, "nspname": "public", "relation_oid": index, "relkind": "r"}
        for index, name in enumerate(contract.ROOTS, 1)
    ]
    identity = [
        {
            "database_name": "bifrost_test",
            "session_user": "bifrost",
            "current_user": "bifrost",
            "backend_pid": 101,
            "postmaster_started_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
            "server_version_num": 160000,
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
        }
    ]
    roles = [
        {
            "role_oid": 11,
            "rolname": "bifrost",
            "rolsuper": True,
            "rolinherit": True,
            "rolcreaterole": True,
            "rolcreatedb": True,
            "rolcanlogin": True,
            "rolreplication": True,
            "rolbypassrls": True,
            "session_member": True,
            "session_usage": True,
            "current_member": True,
            "current_usage": True,
        }
    ]
    relations = [relation(index, name) for index, name in enumerate(contract.ROOTS, 1)]
    relations.extend(
        [
            relation(100, "source_a", dependent=False),
            relation(101, "source_b", dependent=False),
            relation(200, "boundary", ancestor=False, dependent=False, boundary=True),
        ]
    )
    edges = [
        edge(501, 2, 1),
        edge(502, 3, 3),
        edge(503, 1, 100, composite=True),
        edge(504, 100, 101),
        edge(505, 101, 100),
        edge(506, 200, 100),
    ]
    constraints = [constraint_row(401, 1), constraint_row(402, 9, check=True)]
    triggers = [
        trigger_row(
            601,
            2,
            constraint_oid=501,
            kind="f",
            child=2,
            parent=1,
            related=1,
            scope="incident_fk",
        ),
        trigger_row(
            602,
            200,
            constraint_oid=900,
            kind="f",
            child=200,
            parent=300,
            related=300,
            scope="outside_incident_scope",
        ),
        trigger_row(
            603,
            1,
            constraint_oid=401,
            kind="p",
            child=1,
            parent=0,
            scope="outside_incident_scope",
        ),
        trigger_row(604, 3),
    ]
    policies = [
        {
            "policy_oid": 701,
            "relation_oid": 3,
            "polname": "observed_policy",
            "polcmd": "*",
            "polpermissive": True,
            "role_oids": [0, 11],
            "has_using_expression": True,
            "has_check_expression": False,
        }
    ]
    revisions = [{"version_num": contract.MIGRATION_REVISION}]
    # SQL ORDER BY identities/names is deterministic; root name ordering differs
    # from source ROOTS declaration ordering, and the contract does not rewrite it.
    roots.sort(key=lambda row: row["name"])
    return [
        roots,
        identity,
        roles,
        relations,
        edges,
        constraints,
        triggers,
        policies,
        revisions,
    ]


def test_cycles_composites_boundaries_and_unsafe_authority_are_observations(
    observation,
):
    graph = contract.validate_snapshot(observation)
    assert graph == {
        "roots": 12,
        "ancestors": 14,
        "dependents": 12,
        "relevant": 14,
        "boundary": 1,
        "relations": 15,
        "incident_fks": 6,
    }
    assert observation[6][1]["referenced_relation_oid"] == 300
    assert observation[2][0]["rolsuper"] is True
    assert contract.tabular_observations(observation)[7]["observed_empty"] is False


@pytest.mark.parametrize("query_index", range(9))
def test_row_cap_is_checked_before_content(query_index, observation):
    row = observation[query_index][0]
    cap = contract.ROW_CAPS[query_index]
    contract.validate_rows(query_index, [row] * cap)
    with pytest.raises(contract.CatalogContractError, match="row-cap"):
        contract.validate_rows(query_index, [row] * (cap + 1))


@pytest.mark.parametrize(
    "value,rule",
    [
        (1, "B"),
        (True, "O"),
        (0, "O"),
        (-1, "Z"),
        (4294967296, "Z"),
        (32768, "H"),
        ([], "AK+"),
        ([True], "AO*"),
        ([0], "AK*"),
        ("r", "Enabled"),
        (b"O", "Enabled"),
        (None, "S"),
        ("x" * 129, "S"),
        ("é" * 65, "S"),
        ("line\nbreak", "S"),
        (datetime(2026, 10, 1), "T"),
        ([1] * 129, "AK*"),
    ],
)
def test_native_types_codes_nullability_and_caps_reject(value, rule):
    with pytest.raises(contract.CatalogContractError):
        contract.validate_value(value, rule)


def test_legal_empty_nullable_and_public_role_arrays():
    for value, rule in (
        (None, "AK?"),
        ([], "AK?"),
        ([], "AK*"),
        ([], "AO*"),
        ([0], "AZ+"),
    ):
        contract.validate_value(value, rule)
    contract.validate_value("x" * 128, "S")
    contract.validate_value("é" * 64, "S")
    contract.validate_value([1] * 128, "AK+")


def test_native_subclasses_are_not_admitted():
    class IntegerSubclass(int):
        pass

    class TextSubclass(str):
        pass

    class ListSubclass(list):
        pass

    for value, rule in (
        (IntegerSubclass(1), "O"),
        (TextSubclass("r"), "RK"),
        (ListSubclass([]), "AK*"),
    ):
        with pytest.raises(contract.CatalogContractError):
            contract.validate_value(value, rule)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-root",
        "nullable-root",
        "duplicate-root",
        "fk-column-pair",
        "fk-endpoint",
        "boundary-class",
        "partition",
        "constraint-parent",
        "trigger-parent",
        "trigger-join",
        "trigger-related",
        "trigger-scope",
        "trigger-local-constraint",
        "check-index",
        "pk-null-index",
        "policy-relation",
        "migration-head",
        "migration-duplicate",
        "wrong-ordered-columns",
    ],
)
def test_incomplete_or_contradictory_collection_rejects(mutation, observation):
    if mutation == "missing-root":
        observation[0].pop()
    elif mutation == "nullable-root":
        observation[0][0]["relation_oid"] = None
    elif mutation == "duplicate-root":
        observation[0][1] = dict(observation[0][0])
    elif mutation == "fk-column-pair":
        observation[4][2]["parent_columns"].pop()
    elif mutation == "fk-endpoint":
        observation[4][0]["parent_oid"] = 999
    elif mutation == "boundary-class":
        observation[3][-1]["is_dependent"] = True
    elif mutation == "partition":
        observation[3][0]["relispartition"] = True
    elif mutation == "constraint-parent":
        observation[4][0]["parent_constraint_oid"] = 999
    elif mutation == "trigger-parent":
        observation[6][0]["parent_trigger_oid"] = 999
    elif mutation == "trigger-join":
        observation[6][0]["trigger_constraint_type"] = None
    elif mutation == "trigger-related":
        observation[6][0]["related_oid"] = 999
    elif mutation == "trigger-scope":
        observation[6][0]["reference_scope"] = "outside_incident_scope"
    elif mutation == "trigger-local-constraint":
        observation[6][2]["constraint_oid"] = 999
    elif mutation == "check-index":
        observation[5][1]["index_oid"] = 500
    elif mutation == "pk-null-index":
        observation[5][0]["index_valid"] = None
    elif mutation == "policy-relation":
        observation[7][0]["relation_oid"] = 999
    elif mutation == "migration-head":
        observation[8][0]["version_num"] = "unexpected"
    elif mutation == "migration-duplicate":
        observation[8].append(dict(observation[8][0]))
    else:
        observation[1][0] = dict(reversed(list(observation[1][0].items())))
    with pytest.raises(contract.CatalogContractError):
        contract.validate_snapshot(observation)


def test_dropped_incident_fk_cannot_be_reclassified_as_boundary(observation):
    # Remove an edge whose omission does not change A/D; joined trigger metadata
    # must still detect its missing identity rather than accepting a weaker graph.
    observation[4].pop(0)
    with pytest.raises(
        contract.CatalogContractError, match="incident-trigger-fk-missing"
    ):
        contract.validate_snapshot(observation)


def test_optional_checks_and_empty_policy_classes_remain_explicit(observation):
    observation[5][1]["conkey"] = []
    observation[7] = []
    contract.validate_snapshot(observation)
    table = contract.tabular_observations(observation)
    assert table[7]["rows"] == [] and table[7]["observed_empty"] is True


def test_two_samples_allow_backend_change_but_reject_structural_drift(observation):
    other = copy.deepcopy(observation)
    other[1][0]["backend_pid"] += 1
    contract.compare_snapshots(observation, other)
    other[4][0]["confdeltype"] = "n"
    with pytest.raises(
        contract.CatalogContractError, match="direct-pool-structural-mismatch"
    ):
        contract.compare_snapshots(observation, other)


def test_serialization_ceiling_is_truthful_and_native_timestamp_retained():
    assert contract.encode_bounded("a", 4) == b'"a"\n'
    with pytest.raises(contract.CatalogContractError, match="serialization-byte-cap"):
        contract.encode_bounded("a", 3)
    value = {"at": datetime(2026, 10, 1, tzinfo=timezone.utc)}
    assert json.loads(contract.encode_bounded(value, 128))["at"].endswith("+00:00")
    with pytest.raises(contract.CatalogContractError, match="serialization-byte-cap"):
        contract.encode_bounded({"large": "x" * 129}, 128)
    with pytest.raises(contract.CatalogContractError, match="serialization-type"):
        contract.encode_bounded(object(), 128)


def test_frozen_sql_hashes_and_no_expression_exports():
    assert collector._query_hashes() == collector.EXPECTED_QUERY_HASHES
    sql = "\n".join(collector.QUERIES)
    for forbidden in (
        "pg_get_triggerdef",
        "pg_get_constraintdef",
        "pg_get_expr",
        "prosrc",
        "tgargs",
        "md5(",
    ):
        assert forbidden not in sql
    assert "has_trigger_condition" in sql and "function_schema_owner_oid" in sql


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    async def start(self):
        self.connection.events.append("start")
        if self.connection.failure == "start":
            raise RuntimeError("fake-secret-from-start")

    async def commit(self):
        self.connection.events.append("commit")
        if self.connection.failure == "commit":
            raise RuntimeError("fake-secret-from-commit")

    async def rollback(self):
        self.connection.events.append("rollback")
        if self.connection.failure == "rollback":
            raise RuntimeError("fake-secret-from-rollback")


class FakeConnection:
    def __init__(self, observation, failure=None):
        self.observation = observation
        self.failure = failure
        self.events = []
        self.index = 0
        self.closed = False
        self.terminated = False

    def transaction(self, **options):
        assert options == {"isolation": "repeatable_read", "readonly": True}
        return FakeTransaction(self)

    async def fetch(self, query, *, timeout):
        assert query == collector.QUERIES[self.index] and 0 < timeout <= 5
        self.events.append("fetch")
        if self.failure in {"fetch", "rollback"}:
            raise RuntimeError("fake-secret-from-driver")
        if self.failure == "cancel":
            raise asyncio.CancelledError("fake-secret-from-cancel")
        if self.failure == "timeout":
            raise TimeoutError("fake-secret-from-timeout")
        rows = self.observation[self.index]
        self.index += 1
        return rows

    async def close(self, *, timeout):
        assert 0 < timeout <= 2
        self.events.append("close")
        if self.failure == "close":
            raise RuntimeError("fake-secret-from-close")
        self.closed = True

    def is_closed(self):
        return self.closed

    def terminate(self):
        self.events.append("terminate")
        self.terminated = True
        self.closed = True


def fake_connect(monkeypatch, connection):
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(collector.asyncpg, "connect", connect)

    def fake_rows(query_index, rows):
        contract.validate_rows(query_index, rows)
        return rows

    monkeypatch.setattr(collector, "_driver_rows", fake_rows)
    return connect


@pytest.mark.asyncio
async def test_fake_connection_positive_seam(monkeypatch, observation):
    connection = FakeConnection(observation)
    connect = fake_connect(monkeypatch, connection)
    rows, snapshot = await collector._snapshot(
        collector._admit_url().set(host="postgres"), "direct", monotonic() + 60
    )
    assert rows == observation and snapshot["graph_counts"]["boundary"] == 1
    assert connection.events[0] == "start" and connection.events[-2:] == [
        "commit",
        "close",
    ]
    assert connect.await_args.kwargs["statement_cache_size"] == 0
    # This is fake seam evidence, never actual-driver codec proof.


@pytest.mark.parametrize(
    "failure", ["start", "fetch", "commit", "rollback", "close", "cancel", "timeout"]
)
@pytest.mark.asyncio
async def test_fake_failures_are_safe_and_cleanup_is_bounded(
    monkeypatch, observation, failure
):
    connection = FakeConnection(observation, failure)
    fake_connect(monkeypatch, connection)
    with pytest.raises(collector.CatalogCollectionError) as caught:
        await collector._snapshot(collector._admit_url(), "pool", monotonic() + 60)
    assert "fake-secret" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert connection.closed
    if failure in {"rollback", "close"}:
        assert connection.terminated


@pytest.mark.asyncio
async def test_expired_budget_never_connects_and_cleanup_terminates(
    monkeypatch, observation
):
    connection = FakeConnection(observation)
    connect = fake_connect(monkeypatch, connection)
    with pytest.raises(collector.CatalogCollectionError):
        await collector._snapshot(collector._admit_url(), "pool", monotonic() - 1)
    connect.assert_not_awaited()
    assert not await collector._cleanup_connection(
        connection, FakeTransaction(connection), True, monotonic() - 1
    )
    assert connection.events == ["terminate"]


@pytest.fixture
def receipt_path(tmp_path, monkeypatch):
    path = tmp_path / "writer-catalog-receipt.log"
    monkeypatch.setattr(collector, "RECEIPT_PATH", str(path))
    return path


def receipt_payload():
    return {
        "schema": "bifrost.writer-catalog-receipt/v1",
        "collection_status": "raw_complete_pending_custody",
    }


def os_proxy(monkeypatch, **overrides):
    names = (
        "O_RDONLY",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_CLOEXEC",
        "O_RDWR",
        "O_CREAT",
        "O_EXCL",
        "open",
        "fstat",
        "stat",
        "fchmod",
        "write",
        "fsync",
        "lseek",
        "read",
        "close",
        "geteuid",
        "SEEK_SET",
    )
    proxy = SimpleNamespace(**{name: getattr(os, name) for name in names})
    for name, value in overrides.items():
        setattr(proxy, name, value)
    monkeypatch.setattr(collector, "os", proxy)
    return proxy


def test_owned_receipt_readback_and_supported_world_writable_parent(receipt_path):
    os.chmod(receipt_path.parent, 0o777)
    result = collector._write_receipt(receipt_payload(), monotonic() + 60)
    data = receipt_path.read_bytes()
    receipt = json.loads(data)
    assert result["byte_count"] == len(data)
    assert receipt["custody"]["directory"]["mode_octal"] == "0777"
    assert receipt["custody"]["directory_authority"] == "not_established"
    assert receipt_path.stat().st_mode & 0o777 == 0o644


def test_collision_preserves_existing_file(receipt_path, monkeypatch):
    receipt_path.write_text("preserve-existing")
    chmod = AsyncMock()
    os_proxy(monkeypatch, fchmod=chmod)
    with pytest.raises(collector.CatalogCollectionError):
        collector._write_receipt(receipt_payload(), monotonic() + 60)
    assert receipt_path.read_text() == "preserve-existing"
    chmod.assert_not_called()


def test_receipt_symlink_preserves_target(receipt_path, tmp_path):
    target = tmp_path / "existing"
    target.write_text("preserve-existing")
    receipt_path.symlink_to(target)
    with pytest.raises(collector.CatalogCollectionError):
        collector._write_receipt(receipt_payload(), monotonic() + 60)
    assert target.read_text() == "preserve-existing"


def test_symlink_parent_is_rejected(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(
        collector, "RECEIPT_PATH", str(link / "writer-catalog-receipt.log")
    )
    with pytest.raises(collector.CatalogCollectionError):
        collector._write_receipt(receipt_payload(), monotonic() + 60)
    assert not list(real.iterdir())


def test_partial_write_progress_is_handled(receipt_path, monkeypatch):
    def partial_write(fd, data):
        return os.write(fd, data[: max(1, len(data) // 2)])

    os_proxy(monkeypatch, write=partial_write)
    result = collector._write_receipt(receipt_payload(), monotonic() + 60)
    assert result["byte_count"] == receipt_path.stat().st_size


@pytest.mark.parametrize(
    "failure", ["zero-write", "readback", "file-inode", "parent-owner", "close"]
)
def test_fd_custody_failures_are_safe(receipt_path, monkeypatch, failure):
    def no_progress(_fd, _data):
        return 0

    def wrong_read(fd, count):
        data = os.read(fd, count)
        return (b"!" + data[1:]) if data else data

    def drifted_stat(path, **kwargs):
        info = os.stat(path, **kwargs)
        values = list(info)
        if failure == "file-inode" and kwargs.get("dir_fd") is not None:
            values[1] += 1
        if failure == "parent-owner" and kwargs.get("dir_fd") is None:
            values[4] += 1
        return os.stat_result(values)

    def failed_close(fd):
        os.close(fd)
        raise OSError("fake-secret-close")

    overrides = {}
    if failure == "zero-write":
        overrides["write"] = no_progress
    elif failure == "readback":
        overrides["read"] = wrong_read
    elif failure in {"file-inode", "parent-owner"}:
        overrides["stat"] = drifted_stat
    else:
        overrides["close"] = failed_close
    os_proxy(monkeypatch, **overrides)
    with pytest.raises(collector.CatalogCollectionError) as caught:
        collector._write_receipt(receipt_payload(), monotonic() + 60)
    assert "fake-secret" not in str(caught.value)
    assert caught.value.__context__ is None


def test_late_blocking_fsync_prevents_success(receipt_path, monkeypatch):
    clock = {"expired": False}
    real_now = monotonic()

    def fsync_then_expire(fd):
        os.fsync(fd)
        clock["expired"] = True

    os_proxy(monkeypatch, fsync=fsync_then_expire)
    monkeypatch.setattr(
        collector, "monotonic", lambda: real_now + 61 if clock["expired"] else real_now
    )
    with pytest.raises(collector.CatalogCollectionError):
        collector._write_receipt(receipt_payload(), real_now + 60)
    assert receipt_path.exists()  # Raw file is not accepted without testcase PASS.


def test_unknown_ordinary_edge_and_action_difference_are_reconciliation_pending(
    observation,
):
    extra = relation(301, "observed_extra", dependent=False)
    extra["nspname"] = "observed_namespace"
    observation[3].append(extra)
    observation[4].append(edge(507, 1, 301))
    observation[4][0]["confdeltype"] = "n"
    graph = contract.validate_snapshot(observation)
    assert graph["ancestors"] == 15 and graph["relations"] == 16
    # No source allowlist/ownership inference rejects complete structural data.


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+asyncpg://bifrost:fake-secret@live:5432/bifrost_test",
        "postgresql+asyncpg://bifrost:fake-secret@pgbouncer:5432/production",
        "postgresql+asyncpg://other:fake-secret@pgbouncer:5432/bifrost_test",
        "postgresql+asyncpg://bifrost@pgbouncer:5432/bifrost_test",
        "postgresql://bifrost:fake-secret@pgbouncer:5432/bifrost_test",
        "postgresql+asyncpg://bifrost:fake-secret@pgbouncer:5432/bifrost_test?sslmode=require",
    ],
)
def test_dsn_admission_has_no_arbitrary_target_or_secret_error(monkeypatch, dsn):
    monkeypatch.setattr(collector, "TEST_DATABASE_URL", dsn)
    with pytest.raises(contract.CatalogContractError) as caught:
        collector._admit_url()
    assert "fake-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_hanging_close_expires_then_terminates(monkeypatch, observation):
    connection = FakeConnection(observation)

    async def never_closes(*, timeout):
        await asyncio.Event().wait()

    monkeypatch.setattr(connection, "close", never_closes)
    assert not await collector._cleanup_connection(
        connection, None, False, monotonic() + 0.01
    )
    assert connection.terminated


@pytest.mark.asyncio
async def test_hanging_transaction_enter_is_in_absolute_budget(
    monkeypatch, observation
):
    connection = FakeConnection(observation)
    fake_connect(monkeypatch, connection)

    async def never_enters():
        await asyncio.Event().wait()

    transaction = FakeTransaction(connection)
    monkeypatch.setattr(transaction, "start", never_enters)
    monkeypatch.setattr(connection, "transaction", lambda **_options: transaction)
    with pytest.raises(collector.CatalogCollectionError) as caught:
        await collector._snapshot(collector._admit_url(), "pool", monotonic() + 0.01)
    assert "fake-secret" not in str(caught.value)
    assert connection.terminated
