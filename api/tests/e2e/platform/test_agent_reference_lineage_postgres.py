"""Actual PostgreSQL material-reader mechanics, never nominal lineage acceptance."""

from collections.abc import AsyncIterator
from dataclasses import replace
from time import monotonic_ns
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from asyncpg.pgproto.pgproto import UUID as DriverUUID
from sqlalchemy.engine import make_url
from tests.conftest import TEST_DATABASE_URL
from tests.e2e.platform import agent_reference_lineage as material

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def reader_connection() -> AsyncIterator[asyncpg.Connection]:
    # Preserve the configured lane's authority; only adapt its SQLAlchemy scheme.
    dsn = make_url(TEST_DATABASE_URL).set(drivername="postgresql")
    connection = await asyncpg.connect(
        dsn.render_as_string(hide_password=False),
        statement_cache_size=0,
        timeout=material.MAX_SNAPSHOT_SECONDS,
    )
    try:
        version = await connection.fetchval(
            "SELECT current_setting('server_version_num')::integer",
            timeout=material.MAX_SNAPSHOT_SECONDS,
        )
        assert type(version) is int and 160000 <= version < 170000
        yield connection
    finally:
        try:
            await connection.close(timeout=material.MAX_SNAPSHOT_SECONDS)
        finally:
            if not connection.is_closed():
                connection.terminate()


def _ids() -> material.SetupIds:
    return material.SetupIds(*(uuid4() for _ in range(9)))


def _deadline() -> int:
    return monotonic_ns() + material.MAX_SNAPSHOT_SECONDS * 1_000_000_000


async def test_reader_commits_actual_readonly_snapshots(reader_connection):
    ids = _ids()
    for stage in (None, material.RunReadStage.POLL, material.RunReadStage.FINAL):
        before = monotonic_ns()
        deadline = _deadline()
        result = (
            await material.read_setup(reader_connection, ids, deadline_ns=deadline)
            if stage is None
            else await material.read_run(
                reader_connection,
                material.RunIds(ids, uuid4()),
                stage=stage,
                deadline_ns=deadline,
            )
        )
        after = monotonic_ns()
        assert result.kind is material.ReadKind.OBSERVED
        assert result.code is None
        assert result.snapshot is not None and result.acquisition is not None
        facts = result.snapshot.transaction_facts
        assert facts.read_only is True
        assert facts.isolation == (
            "repeatable read"
            if stage is material.RunReadStage.FINAL
            else "read committed"
        )
        identity = facts.identity
        assert type(identity.backend_pid) is int and identity.backend_pid > 0
        assert identity.postmaster_started_at.utcoffset() is not None
        assert all(part.isdecimal() for part in identity.virtual_xid.split("/"))
        assert len(identity.virtual_xid.split("/")) == 2
        assert result.acquisition.transaction == identity
        started = result.acquisition.started_ns
        completed = result.acquisition.completed_ns
        assert before <= started <= completed
        assert result.acquisition.completed_ns <= min(after, deadline)
        assert reader_connection.is_in_transaction() is False
        setup = (
            result.snapshot.setup_material
            if isinstance(result.snapshot, material.RunSnapshot)
            else result.snapshot
        )
        assert setup.principal_rows.user is None
        assert setup.principal_rows.organization is None
        assert setup.agent_row is None and setup.solution_row is None
        if isinstance(result.snapshot, material.RunSnapshot):
            assert result.snapshot.run_row is None
            assert result.snapshot.discovered_execution_id is None
            assert result.snapshot.deferred_child_groups == tuple(
                material.DeferredChildGroup
            )
    # No across-call inequality: equal backend/vxid observations must stay exposed.


async def test_frozen_material_sql_on_postgres(reader_connection):
    """Absent native keys still compile every column, including deferred child SQL."""
    assert len(material._GROUPS) == 32
    deadline = _deadline()

    def remaining() -> float:
        value = (deadline - monotonic_ns()) / 1_000_000_000
        assert value > 0
        return value

    await reader_connection.execute(material._BEGIN_FINAL, timeout=remaining())
    try:
        settings = await reader_connection.fetch(
            material._SETTINGS_SQL, timeout=remaining()
        )
        assert type(settings[0]) is asyncpg.Record
        assert dict(settings[0]) == {"isolation": "repeatable read", "read_only": "on"}
        native_rows = await reader_connection.fetch(
            "SELECT $1::uuid AS id,0::integer AS email_bytes,NULL::integer AS name_bytes",
            uuid4(),
            timeout=remaining(),
        )
        assert len(native_rows) == 1 and type(native_rows[0]) is asyncpg.Record
        native_id = native_rows[0]["id"]
        assert type(native_id) is DriverUUID
        identities = material._metadata(material._GROUPS["user"], native_rows)
        assert len(identities) == 1 and len(identities[0]) == 1
        assert identities[0][0] is native_id
        material_rows = await reader_connection.fetch(
            "SELECT $1::uuid AS id,true::boolean AS is_active,"
            "false::boolean AS is_provider,896::bigint AS row_charge,"
            "false::boolean AS oversize",
            uuid4(),
            timeout=remaining(),
        )
        assert len(material_rows) == 1 and type(material_rows[0]) is asyncpg.Record
        material_id = material_rows[0]["id"]
        assert type(material_id) is DriverUUID
        row, charge = material._material(
            material._GROUPS["organization"],
            material_rows,
            (material_id,),
            material.MAX_SNAPSHOT_BYTES,
        )
        assert type(row) is material.OrganizationRow
        assert row.id is material_id
        assert row.is_active is True and row.is_provider is False
        assert charge == material._GROUPS["organization"].fixed_charge == 896
        for group in material._GROUPS.values():
            kinds = {cell.name: cell.kind for cell in group.cells}
            sentinels = {"uuid": uuid4(), "int": -(2**31), "text": str(uuid4())}
            assert all(kinds[key] in sentinels for key in group.keys)
            keys = tuple(sentinels[kinds[key]] for key in group.keys)
            rows = await reader_connection.fetch(
                group.material_sql,
                *keys,
                material.MAX_SNAPSHOT_BYTES,
                timeout=remaining(),
            )
            assert rows == []
        status = await reader_connection.execute("COMMIT", timeout=remaining())
        assert status == "COMMIT"
        assert reader_connection.is_in_transaction() is False
    finally:
        if reader_connection.is_in_transaction():
            reader_connection.terminate()


async def test_existing_transaction_is_not_tainted(reader_connection):
    await reader_connection.execute(
        material._BEGIN_FINAL, timeout=material.MAX_SNAPSHOT_SECONDS
    )
    try:
        result = await material.read_setup(
            reader_connection, _ids(), deadline_ns=_deadline()
        )
        assert result.kind is material.ReadKind.FAILED
        assert result.code is material.ReaderCode.ACTIVE_TRANSACTION
        assert result.snapshot is None and result.acquisition is None
        assert reader_connection.is_in_transaction() is True
        assert not reader_connection.is_closed()
        status = await reader_connection.execute(
            "ROLLBACK", timeout=material.MAX_SNAPSHOT_SECONDS
        )
        assert status == "ROLLBACK"
    finally:
        if reader_connection.is_in_transaction():
            reader_connection.terminate()


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        (
            "SELECT __reference_missing_column AS id,0::integer AS email_bytes,"
            "NULL::integer AS name_bytes FROM users WHERE id=$1::uuid",
            material.ReaderCode.SCHEMA_MISMATCH,
        ),
        (
            "SELECT $1::uuid::text AS id,0::integer AS email_bytes,NULL::integer AS name_bytes",
            material.ReaderCode.MATERIAL_INVALID,
        ),
        (
            "SELECT $1::uuid AS id,0::integer AS email_bytes,NULL::integer AS name_bytes,"
            "1 AS unexpected",
            material.ReaderCode.SCHEMA_MISMATCH,
        ),
    ],
)
async def test_actual_metadata_drift(reader_connection, monkeypatch, sql, code):
    groups = dict(material._GROUPS)
    groups["user"] = replace(groups["user"], metadata_sql=sql)
    monkeypatch.setattr(material, "_GROUPS", groups)
    result = await material.read_setup(
        reader_connection, _ids(), deadline_ns=_deadline()
    )
    assert result.kind is material.ReadKind.FAILED
    assert result.code is code
    assert result.snapshot is None and result.acquisition is None
    assert reader_connection.is_in_transaction() is False
    assert not reader_connection.is_closed()
