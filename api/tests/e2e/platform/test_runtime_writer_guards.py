"""Actual migrated-schema guard candidate; hosted disposable database only.

Synthetic authority labels and logins are not a Rust owner or runtime acceptance.
The task's stack-down gate owns removal of the committed fixture and principals.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from src.services.execution import poison
from src.models.enums import ExecutionStatus
from src.models.orm.ai_usage import AIUsage
from src.models.orm.events import Event, EventDelivery, EventSource, EventSubscription
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.execution_lifecycle_events import ExecutionLifecycleEvent
from src.models.orm.executions import Execution, ExecutionLog, WorkflowExecutionAttempt

from tests.e2e.platform.test_runtime_deployment_artifacts import (
    INSERT as ARTIFACT_INSERT,
)
from tests.e2e.platform.test_runtime_deployment_artifacts import (
    association as association,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]
TABLES = (
    "executions",
    "workflow_execution_attempts",
    "execution_logs",
    "ai_usage",
    "event_deliveries",
    "execution_attempts",
    "execution_lifecycle_events",
)
PASSWORDS = {
    "wex_core": "synthetic_primer_core",
    "wex_incumbent": "synthetic_primer_incumbent",
}


@asynccontextmanager
async def connection(role):
    conn = await asyncpg.connect(
        host="writer-guard-pool",
        database="bifrost_test",
        user=role,
        password=PASSWORDS[role],
        timeout=5,
        command_timeout=5,
        statement_cache_size=0,
    )
    try:
        identity = await conn.fetchrow("SELECT session_user, current_user")
        assert tuple(identity) == (role, role)
        yield conn
    finally:
        await conn.close(timeout=5)


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def installed_guards():
    admin = await asyncpg.connect(
        host="postgres",
        database="bifrost_test",
        user="bifrost",
        password="bifrost_test",
        timeout=5,
        command_timeout=10,
    )
    try:
        assert (
            await admin.fetchval("SELECT version_num FROM alembic_version")
            == "20261010_runtime_fences"
        )
        async with admin.transaction():
            await admin.execute(
                (
                    Path(__file__).parent / "fixtures/runtime_writer_guards.sql"
                ).read_text()
            )
    finally:
        await admin.close(timeout=5)


async def clone(conn, table, template, changes):
    """Clone a real platform fixture while retaining all ordinary/default fields."""
    assert table in TABLES
    columns = await conn.fetch(
        "SELECT attname FROM pg_attribute WHERE attrelid=$1::regclass "
        "AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum",
        table,
    )
    quoted = ",".join('"' + r["attname"] + '"' for r in columns)
    await conn.execute(
        f'INSERT INTO public."{table}" ({quoted}) '
        f'SELECT {quoted} FROM jsonb_populate_record(NULL::public."{table}", '
        f'(SELECT to_jsonb(t) FROM public."{table}" t WHERE id=$1) || $2::jsonb)',
        template,
        json.dumps(changes, default=str),
    )


@pytest.fixture
async def rows(db_session, association):
    """Real parent/FK graph, synthetic descriptor; no deployment admission claim."""
    await db_session.execute(ARTIFACT_INSERT, association)
    execution = uuid4()
    db_session.add(
        Execution(
            id=execution,
            workflow_id=association["workflow"],
            solution_deployment_id=association["deployment"],
            workflow_name="Actual-schema writer fixture",
            executed_by_name="Synthetic",
            status=ExecutionStatus.PENDING,
        )
    )
    await db_session.flush()
    typed, generic, lifecycle, event_source, event, subscription, delivery = [
        uuid4() for _ in range(7)
    ]
    db_session.add(
        WorkflowExecutionAttempt(
            id=typed,
            execution_id=execution,
            attempt_number=1,
            status="claimed",
            phase="admission",
            claim_token=uuid4(),
            worker_incarnation_id=uuid4(),
        )
    )
    log = ExecutionLog(execution_id=execution, level="info", message="incumbent")
    usage = AIUsage(
        execution_id=execution,
        provider="synthetic",
        model="none",
        input_tokens=0,
        output_tokens=0,
    )
    db_session.add_all(
        [
            log,
            usage,
            ExecutionAttempt(
                id=generic,
                logical_job_type="workflow",
                logical_job_id=execution,
                attempt_number=1,
                policy_identifier="fixture",
                workload_class="workflow",
                admission_policy="fixture",
                mechanism="fixture",
            ),
            EventSource(
                id=event_source,
                name=str(event_source),
                source_type="topic",
                created_by="fixture",
            ),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            ExecutionLifecycleEvent(
                id=lifecycle,
                attempt_id=generic,
                sequence=1,
                logical_job_type="workflow",
                logical_job_id=execution,
                event_type="claimed",
                policy_identifier="fixture",
                workload_class="workflow",
                admission_policy="fixture",
                mechanism="fixture",
            ),
            Event(id=event, event_source_id=event_source, data={}),
            EventSubscription(
                id=subscription,
                event_source_id=event_source,
                workflow_id=association["workflow"],
                created_by="fixture",
            ),
        ]
    )
    await db_session.flush()
    db_session.add(
        EventDelivery(
            id=delivery,
            event_id=event,
            event_subscription_id=subscription,
            execution_id=execution,
        )
    )
    await db_session.commit()
    incumbent = dict(
        zip(
            TABLES,
            (execution, typed, log.id, usage.id, delivery, generic, lifecycle),
            strict=True,
        )
    )
    coordinator = {table: uuid4() for table in TABLES}
    async with connection("wex_core") as conn:
        async with conn.transaction():
            for table in ("execution_logs", "ai_usage"):
                coordinator[table] = await conn.fetchval(
                    "SELECT nextval(pg_get_serial_sequence($1,'id'))", table
                )
            await clone(
                conn,
                "executions",
                execution,
                {
                    "id": coordinator["executions"],
                    "isolated_owner": "coordinator",
                    "runtime_mode": "deployment-v1",
                },
            )
            await conn.execute(
                "INSERT INTO runtime_execution_owners (execution_id, owner_incarnation_id, workflow_id, deployment_id, "
                "artifact_id, caller_snapshot, caller_sha256) VALUES ($1,$2,$3,$4,$5,'{}',$6)",
                coordinator["executions"],
                uuid4(),
                association["workflow"],
                association["deployment"],
                association["artifact_id"],
                association["source"],
            )
            for table in TABLES[1:]:
                changes = {"id": coordinator[table], "isolated_owner": "coordinator"}
                if table in ("execution_attempts", "execution_lifecycle_events"):
                    changes["logical_job_id"] = coordinator["executions"]
                    if table == "execution_lifecycle_events":
                        changes["attempt_id"] = coordinator["execution_attempts"]
                else:
                    changes["execution_id"] = coordinator["executions"]
                if table == "workflow_execution_attempts":
                    changes["claim_token"] = uuid4()
                    changes["runtime_mode"] = "deployment-v1"
                await clone(conn, table, incumbent[table], changes)
    return {
        "incumbent": incumbent,
        "coordinator": coordinator,
        "association": association,
        "event_source": event_source,
    }


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("operation", ("update", "delete", "conflict"))
async def test_cross_owner_writes_are_rejected_with_unchanged_readback(
    rows, table, operation
):
    target = rows["coordinator"][table]
    async with connection("wex_incumbent") as conn:
        before = await conn.fetchval(
            f'SELECT to_jsonb(t)::text FROM "{table}" t WHERE id=$1', target
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                if operation == "delete":
                    await conn.execute(f'DELETE FROM "{table}" WHERE id=$1', target)
                elif operation == "update":
                    await conn.execute(
                        f'UPDATE "{table}" SET isolated_owner=isolated_owner WHERE id=$1',
                        target,
                    )
                else:
                    columns = await conn.fetch(
                        "SELECT attname FROM pg_attribute WHERE attrelid=$1::regclass AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum",
                        table,
                    )
                    names = ",".join('"' + r["attname"] + '"' for r in columns)
                    await conn.execute(
                        f'INSERT INTO "{table}" ({names}) SELECT {names} FROM "{table}" WHERE id=$1 ON CONFLICT (id) DO UPDATE SET isolated_owner=EXCLUDED.isolated_owner',
                        target,
                    )
        assert before == await conn.fetchval(
            f'SELECT to_jsonb(t)::text FROM "{table}" t WHERE id=$1', target
        )


@pytest.mark.parametrize("table", TABLES)
async def test_correct_owner_writes_and_reverse_exclusion(rows, table):
    for role, owner in (("wex_core", "coordinator"), ("wex_incumbent", "incumbent")):
        async with connection(role) as conn:
            await conn.execute(
                f'UPDATE "{table}" SET isolated_owner=isolated_owner WHERE id=$1',
                rows[owner][table],
            )
            other = "incumbent" if owner == "coordinator" else "coordinator"
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    f'UPDATE "{table}" SET isolated_owner=isolated_owner WHERE id=$1',
                    rows[other][table],
                )


@pytest.mark.parametrize(
    "statement",
    (
        "SET ROLE wex_core",
        "SET SESSION AUTHORIZATION wex_core",
        "SET session_replication_role='replica'",
        "ALTER TABLE executions DISABLE TRIGGER ALL",
        "TRUNCATE executions CASCADE",
        "ALTER FUNCTION isolated_writer_guard() RENAME TO hacked",
        "CREATE FUNCTION public.hacked() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    ),
)
async def test_privilege_bypasses_are_denied(rows, statement):
    async with connection("wex_incumbent") as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(statement)


async def test_settings_cannot_forge_owner_and_incumbent_cannot_insert_owner(rows):
    async with connection("wex_incumbent") as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL bifrost.control_owner='coordinator'")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "UPDATE executions SET result='{}' WHERE id=$1",
                    rows["coordinator"]["executions"],
                )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO runtime_execution_owners SELECT * FROM runtime_execution_owners LIMIT 1"
            )


async def test_birth_without_retained_owner_rolls_back(rows):
    identity = uuid4()
    async with connection("wex_core") as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with conn.transaction():
                await clone(
                    conn,
                    "executions",
                    rows["incumbent"]["executions"],
                    {
                        "id": identity,
                        "isolated_owner": "coordinator",
                        "runtime_mode": "deployment-v1",
                    },
                )
        assert (
            await conn.fetchval("SELECT count(*) FROM executions WHERE id=$1", identity)
            == 0
        )


async def test_incumbent_non_fk_preexecution_link_prevents_takeover(rows):
    identity = uuid4()
    async with connection("wex_incumbent") as conn:
        await clone(
            conn,
            "event_deliveries",
            rows["incumbent"]["event_deliveries"],
            {"id": uuid4(), "execution_id": identity},
        )
    async with connection("wex_core") as conn:
        with pytest.raises(
            asyncpg.InsufficientPrivilegeError, match="pre-execution link"
        ):
            await clone(
                conn,
                "executions",
                rows["incumbent"]["executions"],
                {
                    "id": identity,
                    "isolated_owner": "coordinator",
                    "runtime_mode": "deployment-v1",
                },
            )


async def test_parent_bindings_cannot_detach_or_reparent(rows):
    async with connection("wex_core") as conn:
        for table in TABLES[1:]:
            field = (
                "logical_job_id"
                if table in ("execution_attempts", "execution_lifecycle_events")
                else "execution_id"
            )
            with pytest.raises(
                asyncpg.InsufficientPrivilegeError, match="immutable writer binding"
            ):
                await conn.execute(
                    f'UPDATE "{table}" SET {field}=$1 WHERE id=$2',
                    rows["incumbent"]["executions"],
                    rows["coordinator"][table],
                )


async def test_ancestor_cascade_rejects_before_foreign_delivery_is_erased(rows):
    # Both incumbent and coordinator deliveries share this actual ancestor.
    # A rejected foreign-child trigger must roll the entire cascade back.
    async with connection("wex_incumbent") as conn:
        # Only the narrowly selected ancestor DML privilege is installed by the fixture.
        with pytest.raises(
            asyncpg.InsufficientPrivilegeError, match="foreign lifecycle owner"
        ):
            await conn.execute(
                "DELETE FROM event_sources WHERE id=$1", rows["event_source"]
            )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM event_deliveries WHERE id=$1",
                rows["coordinator"]["event_deliveries"],
            )
            == 1
        )


async def test_nonblocking_fence_aborts_incumbent_without_touching_owner(rows):
    async with (
        connection("wex_core") as owner,
        connection("wex_incumbent") as incumbent,
    ):
        async with owner.transaction():
            await owner.execute(
                "SELECT pg_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || $1::text))",
                str(rows["coordinator"]["executions"]),
            )
            with pytest.raises(asyncpg.LockNotAvailableError):
                await incumbent.execute(
                    "INSERT INTO execution_logs(execution_id,level,message,sequence) VALUES ($1,'info','foreign',0)",
                    rows["coordinator"]["executions"],
                )
            await owner.execute(
                "UPDATE executions SET result='{\"winner\":true}' WHERE id=$1",
                rows["coordinator"]["executions"],
            )
        assert (
            await incumbent.fetchval(
                "SELECT result::text FROM executions WHERE id=$1",
                rows["coordinator"]["executions"],
            )
            == '{"winner": true}'
        )


async def test_incumbent_non_fk_link_race_is_observed_and_aborts_new_owner(rows):
    identity = uuid4()
    async with (
        connection("wex_incumbent") as incumbent,
        connection("wex_core") as owner,
    ):
        async with incumbent.transaction():
            await clone(
                incumbent,
                "event_deliveries",
                rows["incumbent"]["event_deliveries"],
                {"id": uuid4(), "execution_id": identity},
            )
            # The observed open transaction holds the actual missing-parent fence.
            with pytest.raises(asyncpg.LockNotAvailableError):
                await clone(
                    owner,
                    "executions",
                    rows["incumbent"]["executions"],
                    {
                        "id": identity,
                        "isolated_owner": "coordinator",
                        "runtime_mode": "deployment-v1",
                    },
                )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM executions WHERE id=$1", identity
            )
            == 0
        )


async def test_root_owner_cannot_be_reassigned_even_by_current_owner(rows):
    for role, owner, replacement in (
        ("wex_core", "coordinator", "incumbent"),
        ("wex_incumbent", "incumbent", "coordinator"),
    ):
        async with connection(role) as conn:
            with pytest.raises(
                asyncpg.InsufficientPrivilegeError, match="immutable writer binding"
            ):
                await conn.execute(
                    "UPDATE executions SET isolated_owner=$1 WHERE id=$2",
                    replacement,
                    rows[owner]["executions"],
                )


async def test_actual_pool_principals_and_guard_custody_are_nonprivileged(rows):
    for role in PASSWORDS:
        async with connection(role) as conn:
            flags = await conn.fetchrow(
                "SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=session_user"
            )
            assert tuple(flags) == (False, False, False, False, False)
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=session_user)"
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM pg_class WHERE relowner=(SELECT oid FROM pg_roles WHERE rolname=session_user)"
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT has_schema_privilege(session_user,'public','CREATE')"
                )
                is False
            )
            assert (
                await conn.fetchval(
                    "SELECT has_database_privilege(session_user,current_database(),'TEMP')"
                )
                is False
            )
            assert (
                await conn.fetchval(
                    "SELECT has_function_privilege(session_user,'public.isolated_writer_guard()','EXECUTE')"
                )
                is False
            )
            assert (
                await conn.fetchval(
                    "SELECT has_function_privilege(session_user,'public.isolated_runtime_writer_guard()','EXECUTE')"
                )
                is False
            )
            guard = await conn.fetchrow(
                "SELECT r.rolname,r.rolcanlogin,r.rolsuper,p.prosecdef,p.proconfig FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid='public.isolated_writer_guard()'::regprocedure"
            )
            assert tuple(guard) == (
                "isolated_writer_guard",
                False,
                False,
                True,
                ["search_path=pg_catalog, public"],
            )
            count = await conn.fetchval(
                "SELECT count(*) FROM pg_trigger WHERE tgname='isolated_writer_owner' AND tgenabled='O' AND NOT tgisinternal"
            )
            assert count == len(TABLES)


async def test_real_incumbent_poison_is_excluded_before_external_effects(
    rows, monkeypatch
):
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            username="wex_incumbent",
            password=PASSWORDS["wex_incumbent"],
            host="writer-guard-pool",
            database="bifrost_test",
        ),
        poolclass=NullPool,
        connect_args={
            "prepared_statement_cache_size": 0,
            "statement_cache_size": 0,
            "timeout": 5,
            "command_timeout": 5,
        },
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context():
        async with factory() as session:
            yield session

    cleanup = AsyncMock(return_value=True)
    publish = AsyncMock()
    monkeypatch.setattr(poison, "get_db_context", context)
    monkeypatch.setattr(poison, "_cleanup_transient_poison_state", cleanup)
    monkeypatch.setattr(poison, "_publish_poison_update", publish)
    arguments = dict(
        queue="workflow-execution",
        reason="isolated guard proof",
        retry_count=1,
        replay_count=0,
        message_id=None,
        sync=False,
    )
    try:
        async with connection("wex_incumbent") as reader:
            native = rows["coordinator"]["executions"]
            before = await reader.fetchval(
                "SELECT to_jsonb(t)::text FROM executions t WHERE id=$1", native
            )
            with pytest.raises(DBAPIError, match="foreign lifecycle owner"):
                await poison.finalize_poisoned_execution(
                    execution_id=str(native), **arguments
                )
            cleanup.assert_not_awaited()
            publish.assert_not_awaited()
            assert (
                await reader.fetchval(
                    "SELECT to_jsonb(t)::text FROM executions t WHERE id=$1", native
                )
                == before
            )
            outcome = await poison.finalize_poisoned_execution(
                execution_id=str(rows["incumbent"]["executions"]), **arguments
            )
            assert outcome.disposition == "terminalized" and outcome.status == "Failed"
            cleanup.assert_awaited_once()
            publish.assert_awaited_once()
            assert (
                await reader.fetchval(
                    "SELECT status::text FROM executions WHERE id=$1",
                    rows["incumbent"]["executions"],
                )
                == "Failed"
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "assignment",
    (
        "executed_by_name='forged'",
        "organization_id='11111111-1111-4111-8111-111111111111'",
        "runtime_mode='legacy'",
        "parameters=jsonb_build_object('forged',true)",
        "retry_policy=jsonb_build_object('forged',true)",
        "runtime_evidence_hash='sha256:' || repeat('d',64)",
    ),
)
async def test_coordinator_caller_source_and_input_facts_are_immutable(
    rows, assignment
):
    async with connection("wex_core") as conn:
        with pytest.raises(
            asyncpg.InsufficientPrivilegeError, match="immutable execution facts"
        ):
            await conn.execute(
                f"UPDATE executions SET {assignment} WHERE id=$1",
                rows["coordinator"]["executions"],
            )
