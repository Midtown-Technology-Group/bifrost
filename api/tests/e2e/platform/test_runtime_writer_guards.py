"""Actual migrated-schema guard candidate; hosted disposable database only.

Synthetic authority labels and logins are not a Rust owner or runtime acceptance.
The task's stack-down gate owns removal of the committed fixture and principals.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4
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
            published_at=datetime.now(UTC),
            claimed_at=datetime.now(UTC),
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


# Real Rust common-order observation uses this same installed guard fixture.
PROBE = Path("/app/scripts/runtime-owner-observation")


@pytest.fixture
async def session_fence(rows):
    execution = rows["coordinator"]["executions"]
    attempt = rows["coordinator"]["workflow_execution_attempts"]
    session, supervisor, runtime, prepare = [uuid4() for _ in range(4)]
    async with connection("wex_core") as conn:
        retained = await conn.fetchrow(
            "SELECT o.owner_incarnation_id, a.claim_token, a.worker_incarnation_id "
            "FROM runtime_execution_owners o JOIN workflow_execution_attempts a "
            "ON a.execution_id=o.execution_id WHERE o.execution_id=$1 AND a.id=$2",
            execution,
            attempt,
        )
        assert retained is not None
        await conn.execute(
            "INSERT INTO runtime_sessions "
            "(id,execution_id,owner_incarnation_id,workflow_attempt_id,claim_token,"
            "worker_incarnation_id,supervisor_incarnation_id,runtime_incarnation_id,"
            "channel_custody_sha256,binding_sha256,prepare_id,prepare_sha256) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            session,
            execution,
            retained["owner_incarnation_id"],
            attempt,
            retained["claim_token"],
            retained["worker_incarnation_id"],
            supervisor,
            runtime,
            "c" * 64,
            "b" * 64,
            prepare,
            "d" * 64,
        )
    return [
        str(execution),
        str(retained["owner_incarnation_id"]),
        str(attempt),
        str(retained["claim_token"]),
        str(retained["worker_incarnation_id"]),
        str(session),
        str(supervisor),
        str(runtime),
        "b" * 64,
        "c" * 64,
    ]


async def probe(
    fence,
    role="wex_core",
    operation="observe",
    default_float_digits=False,
    exit_after_cancel_commit=False,
):
    assert PROBE.is_file(), "Required source-bound Rust build artifact is missing"
    process = await asyncio.create_subprocess_exec(
        str(PROBE),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "BIFROST_ISOLATED_OWNER_TEST": "1",
            "BIFROST_OWNER_TEST_ACTION": operation,
            "BIFROST_OWNER_TEST_EXIT_AFTER_CANCEL_COMMIT": "1"
            if exit_after_cancel_commit
            else "0",
            "BIFROST_OWNER_TEST_DEFAULT_FLOAT_DIGITS": "1"
            if default_float_digits
            else "0",
            "BIFROST_OWNER_TEST_DATABASE_URL": (
                f"postgresql://{role}:{PASSWORDS[role]}@writer-guard-pool/bifrost_test"
            ),
        },
    )
    try:
        out, err = await asyncio.wait_for(
            process.communicate(("\n".join(fence) + "\n").encode()), timeout=8
        )
        assert process.returncode == (73 if exit_after_cancel_commit else 0)
        assert err == b""
        assert len(out) <= 32
        if exit_after_cancel_commit:
            assert out == b""
            return "reply_lost"
        return out.decode().strip()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_rust_observes_open_and_retained_closed_session(session_fence):
    assert await probe(session_fence) == "open"
    async with connection("wex_core") as conn:
        async with conn.transaction():
            # A committed observation must release every acquired row/source lock.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('bifrost:workspace-release'))"
            )
            await conn.execute(
                "SELECT id FROM workflow_execution_attempts WHERE id=$1 FOR UPDATE NOWAIT",
                UUID(session_fence[2]),
            )
            await conn.execute(
                "UPDATE runtime_sessions SET closed_at=clock_timestamp(), "
                "close_reason='cancelled' WHERE id=$1",
                UUID(session_fence[5]),
            )
    assert await probe(session_fence) == "closed"


@pytest.mark.parametrize("field", range(10))
async def test_rust_rejects_each_mismatched_retained_identity(session_fence, field):
    forged = list(session_fence)
    forged[field] = str(uuid4()) if field < 8 else "e" * 64
    assert await probe(forged) == "rejected"


async def test_rust_rejects_incumbent_backend_identity(session_fence):
    assert await probe(session_fence, "wex_incumbent") == "rejected"


async def test_rust_secondary_nowait_breaks_execution_first_interlock(session_fence):
    async with connection("wex_incumbent") as incumbent:
        async with incumbent.transaction():
            before = await incumbent.fetchval(
                "SELECT to_jsonb(e)::text FROM executions e WHERE id=$1 FOR UPDATE",
                UUID(session_fence[0]),
            )
            assert await probe(session_fence) == "lock_contention"
            # Rust acquired the attempt first, then aborted rather than waiting
            # behind this execution-first incumbent transaction. Its attempt lock
            # must be released, allowing the incumbent graph to continue.
            await incumbent.execute(
                "SELECT id FROM workflow_execution_attempts WHERE id=$1 FOR UPDATE NOWAIT",
                UUID(session_fence[2]),
            )
            after = await incumbent.fetchval(
                "SELECT to_jsonb(e)::text FROM executions e WHERE id=$1",
                UUID(session_fence[0]),
            )
            assert after == before


async def test_rust_source_fence_contention_aborts_without_replay(session_fence):
    async with connection("wex_incumbent") as incumbent:
        async with incumbent.transaction():
            await incumbent.execute(
                "SELECT pg_advisory_xact_lock(hashtext('bifrost:workspace-release'))"
            )
            assert await probe(session_fence) == "lock_contention"
    # This subsequent read is a new observation; no authority action was retried.
    assert await probe(session_fence) == "open"


@pytest.mark.parametrize(
    "table,key", [("solutions", "solution"), ("solution_deployments", "deployment")]
)
async def test_source_lock_privilege_never_permits_coordinator_update(rows, table, key):
    import asyncpg

    async with connection("wex_core") as conn:
        target = rows["association"][key]
        before = await conn.fetchval(
            f'SELECT to_jsonb(t)::text FROM "{table}" t WHERE id=$1', target
        )
        async with conn.transaction():
            await conn.execute(
                f'SELECT id FROM "{table}" WHERE id=$1 FOR UPDATE NOWAIT', target
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(f'UPDATE "{table}" SET id=id WHERE id=$1', target)
        assert (
            await conn.fetchval(
                f'SELECT to_jsonb(t)::text FROM "{table}" t WHERE id=$1', target
            )
            == before
        )


async def test_source_lock_guard_custody_cannot_be_bypassed(rows):
    for role in PASSWORDS:
        async with connection(role) as conn:
            assert not await conn.fetchval(
                "SELECT has_function_privilege(session_user,'public.isolated_source_lock_guard()','EXECUTE')"
            )
            owner = await conn.fetchrow(
                "SELECT r.rolname,r.rolcanlogin,r.rolsuper,p.prosecdef,p.proconfig "
                "FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner "
                "WHERE p.oid='public.isolated_source_lock_guard()'::regprocedure"
            )
            assert tuple(owner) == (
                "isolated_writer_guard",
                False,
                False,
                False,
                ["search_path=pg_catalog, public"],
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM pg_trigger WHERE tgname='isolated_source_lock_only' "
                    "AND tgenabled='O' AND NOT tgisinternal"
                )
                == 2
            )


@pytest.mark.parametrize(
    "table,key", [("solutions", "solution"), ("solution_deployments", "deployment")]
)
async def test_existing_incumbent_source_write_path_is_unchanged(
    db_session, rows, table, key
):
    from sqlalchemy import text

    assert await db_session.scalar(text("SELECT session_user::text")) == "bifrost"
    target = rows["association"][key]
    assert (
        await db_session.scalar(
            text(f'UPDATE "{table}" SET id=id WHERE id=:id RETURNING id'),
            {"id": target},
        )
        == target
    )
    await db_session.commit()


@pytest.fixture
async def running_cancel_facts(rows, session_fence, request):
    """Synthetic retained Start/grant metadata, not admission or token issuance."""
    import re
    from datetime import timedelta
    from hashlib import sha256

    from tests.e2e.platform.test_runtime_deployment_artifacts import GRANT_INSERT

    start, message, grant = [uuid4() for _ in range(3)]
    facts = {
        **rows["association"],
        "execution": UUID(session_fence[0]),
        "owner": UUID(session_fence[1]),
        "attempt": UUID(session_fence[2]),
        "claim": UUID(session_fence[3]),
        "worker": UUID(session_fence[4]),
        "session": UUID(session_fence[5]),
        "supervisor": UUID(session_fence[6]),
        "start": start,
        "start_message": message,
        "grant": grant,
        "number": 1,
        "caller_digest": rows["association"]["source"],
        "manifest_digest": "sha256:" + "b" * 64,
        "resolution_digest": "sha256:" + "c" * 64,
        "claim_digest": sha256(
            b"16:cred-p1/claim/v1,36:" + session_fence[3].encode("ascii") + b","
        ).hexdigest(),
    }
    async with connection("wex_core") as conn:
        async with conn.transaction():
            facts["org"] = await conn.fetchval(
                "SELECT organization_id FROM solutions WHERE id=$1", facts["solution"]
            )
            facts["started"] = await conn.fetchval(
                "INSERT INTO runtime_starts "
                "(id,session_id,execution_id,owner_incarnation_id,workflow_attempt_id,"
                "start_message_id,input_sha256,context_sha256,started_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$7,"
                "clock_timestamp()-($8::int * interval '1 second')) RETURNING started_at",
                start,
                facts["session"],
                facts["execution"],
                facts["owner"],
                facts["attempt"],
                message,
                facts["source"],
                getattr(request, "param", 0),
            )
            facts["issued"] = facts["started"] + timedelta(milliseconds=1)
            facts["expires"] = facts["started"] + timedelta(seconds=10)
            # Reuse the canonical storage fixture INSERT, adapting only its named
            # binds to this separately authenticated asyncpg connection.
            keys = list(dict.fromkeys(re.findall(r"(?<!:):(\w+)", GRANT_INSERT.text)))
            prepared = re.sub(
                r"(?<!:):(\w+)",
                lambda match: "$" + str(keys.index(match[1]) + 1),
                GRANT_INSERT.text,
            )
            await conn.execute(prepared, *(facts[key] for key in keys))
            await conn.execute(
                "UPDATE executions SET status='Running',started_at=$2 WHERE id=$1",
                facts["execution"],
                facts["started"],
            )
            await conn.execute(
                "UPDATE workflow_execution_attempts SET status='running',phase='execution',"
                "started_at=$2,heartbeat_at=$2 WHERE id=$1",
                facts["attempt"],
                facts["started"],
            )
    return facts


async def cancel_snapshot(facts):
    async with connection("wex_core") as conn:
        return await conn.fetchrow(
            "SELECT e.status::text AS status,e.result::text AS result,"
            "a.status AS attempt_status,a.completed_at,s.closed_at,s.close_reason,"
            "g.revoked_at,g.revocation_reason FROM executions e "
            "JOIN workflow_execution_attempts a ON a.execution_id=e.id "
            "JOIN runtime_sessions s ON s.workflow_attempt_id=a.id "
            "JOIN workflow_runtime_sdk_grants g ON g.runtime_session_id=s.id "
            "WHERE e.id=$1 AND a.id=$2 AND s.id=$3 AND g.id=$4",
            facts["execution"],
            facts["attempt"],
            facts["session"],
            facts["grant"],
        )


async def test_rust_running_cancel_commits_projection_close_and_revoke_once(
    running_cancel_facts, session_fence
):
    assert (
        await probe(session_fence, operation="request-running-cancel")
        == "cancel_committed"
    )
    first = await cancel_snapshot(running_cancel_facts)
    assert first["status"] == "Cancelling"
    assert first["attempt_status"] == "running"
    assert first["completed_at"] is None
    assert first["closed_at"] is not None
    assert first["close_reason"] == "cancel_requested"
    assert first["revoked_at"] is not None
    assert first["revocation_reason"] == "session_closed"
    assert (
        await probe(session_fence, operation="request-running-cancel")
        == "cancel_already_committed"
    )
    assert await cancel_snapshot(running_cancel_facts) == first
    # A database cancellation decision is not process stop or final outcome.
    assert first["status"] != "Cancelled"


@pytest.mark.parametrize("field", range(10))
async def test_rust_cancel_rejects_each_stale_identity_without_writes(
    running_cancel_facts, session_fence, field
):
    before = await cancel_snapshot(running_cancel_facts)
    stale = list(session_fence)
    stale[field] = str(uuid4()) if field < 8 else "e" * 64
    assert await probe(stale, operation="request-running-cancel") == "rejected"
    assert await cancel_snapshot(running_cancel_facts) == before


async def test_rust_cancel_rejects_incumbent_without_writes(
    running_cancel_facts, session_fence
):
    before = await cancel_snapshot(running_cancel_facts)
    assert (
        await probe(session_fence, "wex_incumbent", "request-running-cancel")
        == "rejected"
    )
    assert await cancel_snapshot(running_cancel_facts) == before


async def test_rust_cancel_without_retained_start_has_no_projection(session_fence):
    assert await probe(session_fence, operation="request-running-cancel") == "rejected"
    async with connection("wex_core") as conn:
        row = await conn.fetchrow(
            "SELECT e.status::text,s.closed_at FROM executions e JOIN runtime_sessions s "
            "ON s.execution_id=e.id WHERE s.id=$1",
            UUID(session_fence[5]),
        )
        assert tuple(row) == ("Pending", None)


async def test_rust_cancel_tail_contention_aborts_before_any_projection(
    running_cancel_facts, session_fence
):
    before = await cancel_snapshot(running_cancel_facts)
    async with connection("wex_core") as competing:
        async with competing.transaction():
            await competing.execute(
                "SELECT id FROM workflow_runtime_sdk_grants WHERE id=$1 FOR UPDATE",
                running_cancel_facts["grant"],
            )
            assert (
                await probe(session_fence, operation="request-running-cancel")
                == "lock_contention"
            )
    assert await cancel_snapshot(running_cancel_facts) == before


async def test_rust_cancel_late_revoke_failure_rolls_back_all_projection(
    db_session, running_cancel_facts, session_fence
):
    from sqlalchemy import text

    # Actual PostgreSQL rejection after the Rust root/session updates, not an
    # injected commit oracle. The custodian/test fixture owns this temporary DDL.
    await db_session.execute(
        text("""
        CREATE FUNCTION isolated_cancel_revoke_fault() RETURNS trigger LANGUAGE plpgsql AS $body$
        BEGIN RAISE EXCEPTION 'synthetic revoke fault' USING ERRCODE='42501'; END $body$
    """)
    )
    await db_session.execute(
        text("""
        CREATE TRIGGER isolated_cancel_revoke_fault BEFORE UPDATE ON workflow_runtime_sdk_grants
        FOR EACH ROW EXECUTE FUNCTION isolated_cancel_revoke_fault()
    """)
    )
    await db_session.commit()
    try:
        before = await cancel_snapshot(running_cancel_facts)
        assert (
            await probe(session_fence, operation="request-running-cancel")
            == "database_failure"
        )
        assert await cancel_snapshot(running_cancel_facts) == before
    finally:
        await db_session.execute(
            text(
                "DROP TRIGGER isolated_cancel_revoke_fault ON workflow_runtime_sdk_grants"
            )
        )
        await db_session.execute(text("DROP FUNCTION isolated_cancel_revoke_fault()"))
        await db_session.commit()


async def test_rust_cancel_preserves_committed_success(
    running_cancel_facts, session_fence
):
    async with connection("wex_core") as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE executions SET status='Success',result='{}',completed_at=clock_timestamp() WHERE id=$1",
                running_cancel_facts["execution"],
            )
            await conn.execute(
                "UPDATE workflow_execution_attempts SET status='succeeded',phase='terminal',"
                "completed_at=clock_timestamp() WHERE id=$1",
                running_cancel_facts["attempt"],
            )
    before = await cancel_snapshot(running_cancel_facts)
    assert await probe(session_fence, operation="request-running-cancel") == "rejected"
    assert await cancel_snapshot(running_cancel_facts) == before


async def test_rust_cancel_rejects_inconsistent_winning_receipt(
    running_cancel_facts, session_fence
):
    async with connection("wex_core") as conn:
        await conn.execute(
            "INSERT INTO runtime_report_receipts "
            "(session_id,result_message_id,committed_start_id,start_message_id,"
            "raw_result_payload,result_sha256,decision_id,disposition,winner) "
            "VALUES ($1,$2,$3,$4,$5,encode(sha256($5),'hex'),$6,'accepted','result')",
            running_cancel_facts["session"],
            uuid4(),
            running_cancel_facts["start"],
            running_cancel_facts["start_message"],
            b"{}",
            uuid4(),
        )
    before = await cancel_snapshot(running_cancel_facts)
    assert await probe(session_fence, operation="request-running-cancel") == "rejected"
    assert await cancel_snapshot(running_cancel_facts) == before


async def test_sqlx_default_startup_option_is_rejected_without_pool_policy_bypass(
    session_fence,
):
    assert (
        await probe(session_fence, default_float_digits=True)
        == "startup_parameter_rejected"
    )
    assert await probe(session_fence) == "open"


async def test_rust_cancel_readback_is_read_only_before_and_after_commit(
    running_cancel_facts, session_fence
):
    before = await cancel_snapshot(running_cancel_facts)
    assert (
        await probe(session_fence, operation="observe-cancel-decision")
        == "cancel_not_committed"
    )
    assert await cancel_snapshot(running_cancel_facts) == before
    assert (
        await probe(session_fence, operation="request-running-cancel")
        == "cancel_committed"
    )
    committed = await cancel_snapshot(running_cancel_facts)
    assert (
        await probe(session_fence, operation="observe-cancel-decision")
        == "cancel_observed_committed"
    )
    assert await cancel_snapshot(running_cancel_facts) == committed


async def test_rust_cancel_process_exit_before_reply_reconciles_without_replay(
    running_cancel_facts, session_fence
):
    assert (
        await probe(
            session_fence,
            operation="request-running-cancel",
            exit_after_cancel_commit=True,
        )
        == "reply_lost"
    )
    committed = await cancel_snapshot(running_cancel_facts)
    assert committed["status"] == "Cancelling"
    assert committed["closed_at"] is not None
    assert committed["revoked_at"] is not None
    # A fresh Rust process reads the retained same-session decision; no write
    # request is repeated and no owner/attempt/session identity is replaced.
    assert (
        await probe(session_fence, operation="observe-cancel-decision")
        == "cancel_observed_committed"
    )
    assert await cancel_snapshot(running_cancel_facts) == committed


@pytest.mark.parametrize("field", range(10))
async def test_rust_cancel_readback_rejects_stale_identity_without_writes(
    running_cancel_facts, session_fence, field
):
    before = await cancel_snapshot(running_cancel_facts)
    stale = list(session_fence)
    stale[field] = str(uuid4()) if field < 8 else "e" * 64
    assert await probe(stale, operation="observe-cancel-decision") == "rejected"
    assert await cancel_snapshot(running_cancel_facts) == before


async def test_rust_cancel_readback_rejects_incumbent_without_writes(
    running_cancel_facts, session_fence
):
    before = await cancel_snapshot(running_cancel_facts)
    assert (
        await probe(session_fence, "wex_incumbent", "observe-cancel-decision")
        == "rejected"
    )
    assert await cancel_snapshot(running_cancel_facts) == before


async def test_rust_cancel_readback_rejects_partial_close_without_repair(
    running_cancel_facts, session_fence
):
    async with connection("wex_core") as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE executions SET status='Cancelling' WHERE id=$1",
                running_cancel_facts["execution"],
            )
            await conn.execute(
                "UPDATE runtime_sessions SET closed_at=clock_timestamp(),"
                "close_reason='cancel_requested' WHERE id=$1",
                running_cancel_facts["session"],
            )
    before = await cancel_snapshot(running_cancel_facts)
    assert before["revoked_at"] is None
    assert await probe(session_fence, operation="observe-cancel-decision") == "rejected"
    assert await cancel_snapshot(running_cancel_facts) == before


async def test_rust_cancel_readback_tail_contention_has_no_effects(
    running_cancel_facts, session_fence
):
    before = await cancel_snapshot(running_cancel_facts)
    async with connection("wex_core") as competing:
        async with competing.transaction():
            await competing.execute(
                "SELECT id FROM workflow_runtime_sdk_grants WHERE id=$1 FOR UPDATE",
                running_cancel_facts["grant"],
            )
            assert (
                await probe(session_fence, operation="observe-cancel-decision")
                == "lock_contention"
            )
    assert await cancel_snapshot(running_cancel_facts) == before


@pytest.fixture
async def released_result_facts(running_cancel_facts):
    """Synthetic immutable provision/release facts; no physical launch claim."""
    facts = {**running_cancel_facts, "provision": uuid4(), "delivery": uuid4()}
    async with connection("wex_core") as conn:
        async with conn.transaction():
            for purpose in ("provision", "release"):
                await conn.execute(
                    "INSERT INTO runtime_admissions "
                    "(id,purpose,session_id,committed_start_id,start_message_id,grant_id,"
                    "delivery_id,operations_digest,expires_at,provision_admission_id,"
                    "provision_purpose,frontier_sha256,admitted_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$8,$12)",
                    facts["provision"] if purpose == "provision" else uuid4(),
                    purpose,
                    facts["session"],
                    facts["start"],
                    facts["start_message"],
                    facts["grant"],
                    facts["delivery"],
                    facts["source"],
                    facts["expires"],
                    None if purpose == "provision" else facts["provision"],
                    None if purpose == "provision" else "provision",
                    facts["issued"],
                )
    return facts


def result_payload(facts, **changes):
    frame = {
        "protocol": "bifrost.runtime/v1",
        "type": "Result",
        "session_id": str(facts["session"]),
        "message_id": str(uuid4()),
        "sequence": 1,
        "correlation_id": str(facts["start_message"]),
        "body": {
            "start_message_id": str(facts["start_message"]),
            "outcome": "success",
            "value": {"ready": True, "missing_keys": []},
        },
        **changes,
    }
    # Intentional whitespace: receipt must retain these exact bytes.
    return json.dumps(frame, indent=2).encode()


async def result_probe(
    fence, payload, role="wex_core", decision=None, exit_after_commit=False
):
    executable = Path("/app/scripts/runtime-owner-result")
    assert executable.is_file(), "Required source-bound Rust Result artifact is missing"
    process = await asyncio.create_subprocess_exec(
        str(executable),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "BIFROST_ISOLATED_OWNER_TEST": "1",
            "BIFROST_OWNER_TEST_DATABASE_URL": (
                f"postgresql://{role}:{PASSWORDS[role]}@writer-guard-pool/bifrost_test"
            ),
            "BIFROST_OWNER_TEST_EXIT_AFTER_RESULT_COMMIT": "1"
            if exit_after_commit
            else "0",
        },
    )
    try:
        request = ("\n".join([*fence, str(decision or uuid4())]) + "\n").encode()
        out, err = await asyncio.wait_for(
            process.communicate(request + payload), timeout=8
        )
        assert process.returncode == (73 if exit_after_commit else 0)
        assert err == b"" and len(out) <= 1024
        if exit_after_commit:
            assert out == b""
            return "reply_lost"
        response = out.decode().strip()
        return json.loads(response) if response.startswith("{") else response
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def result_snapshot(facts):
    async with connection("wex_core") as conn:
        return await conn.fetchval(
            "SELECT jsonb_build_object('execution',to_jsonb(e),'attempt',to_jsonb(a),"
            "'session',to_jsonb(s),'grant',to_jsonb(g),'receipts',"
            "(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.result_message_id) "
            "FROM runtime_report_receipts r WHERE r.session_id=s.id))::text "
            "FROM executions e JOIN workflow_execution_attempts a ON a.execution_id=e.id "
            "JOIN runtime_sessions s ON s.workflow_attempt_id=a.id "
            "JOIN workflow_runtime_sdk_grants g ON g.runtime_session_id=s.id "
            "WHERE e.id=$1 AND a.id=$2 AND s.id=$3 AND g.id=$4",
            facts["execution"],
            facts["attempt"],
            facts["session"],
            facts["grant"],
        )


async def test_rust_result_commits_exact_receipt_and_existing_projection_once(
    released_result_facts, session_fence, e2e_client, platform_admin
):
    from hashlib import sha256

    facts = released_result_facts
    payload = result_payload(facts)
    receipt = await result_probe(session_fence, payload)
    assert receipt["disposition"] == "accepted" and receipt["winner"] == "result"
    assert receipt["result_sha256"] == sha256(payload).hexdigest()
    committed = await result_snapshot(facts)
    retained = json.loads(committed)
    assert retained["execution"]["status"] == "Success"
    assert retained["execution"]["result"] == {"ready": True, "missing_keys": []}
    assert retained["attempt"]["status"] == "succeeded"
    assert retained["attempt"]["phase"] == "terminal"
    assert retained["session"]["close_reason"] == "result_committed"
    assert retained["grant"]["revocation_reason"] == "session_closed"
    assert len(retained["receipts"]) == 1
    async with connection("wex_core") as conn:
        assert (
            await conn.fetchval(
                "SELECT raw_result_payload FROM runtime_report_receipts WHERE session_id=$1",
                facts["session"],
            )
            == payload
        )
    assert await result_probe(session_fence, payload) == receipt
    assert await result_snapshot(facts) == committed
    # Existing real authenticated HTTP read, no route or dependency override.
    response = e2e_client.get(
        f"/api/executions/{facts['execution']}", headers=platform_admin.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Success"
    assert body["result"] == {"ready": True, "missing_keys": []}
    assert await result_snapshot(facts) == committed


async def test_rust_result_lost_receipt_reads_same_decision_without_projection_replay(
    released_result_facts, session_fence
):
    facts = released_result_facts
    payload, decision = result_payload(facts), uuid4()
    assert (
        await result_probe(
            session_fence, payload, decision=decision, exit_after_commit=True
        )
        == "reply_lost"
    )
    committed = await result_snapshot(facts)
    receipt = await result_probe(session_fence, payload)
    assert receipt["decision_id"] == str(decision)
    assert receipt["disposition"] == "accepted"
    assert await result_snapshot(facts) == committed


@pytest.mark.parametrize("conflict", ("bytes", "message"))
async def test_rust_result_conflicting_duplicate_has_no_effects(
    released_result_facts, session_fence, conflict
):
    facts = released_result_facts
    payload = result_payload(facts)
    assert (await result_probe(session_fence, payload))["disposition"] == "accepted"
    committed = await result_snapshot(facts)
    frame = json.loads(payload)
    if conflict == "message":
        frame["message_id"] = str(uuid4())
    conflicting = json.dumps(frame, separators=(",", ":")).encode()
    assert await result_probe(session_fence, conflicting) == "rejected"
    assert await result_snapshot(facts) == committed


async def test_rust_result_error_projects_common_error_without_rust_wire_variants(
    released_result_facts, session_fence
):
    facts = released_result_facts
    error = {
        "code": "IntegrationUnavailable",
        "message": "Synthetic unavailable",
        "details": None,
    }
    payload = result_payload(
        facts,
        body={
            "start_message_id": str(facts["start_message"]),
            "outcome": "error",
            "error": error,
        },
    )
    assert (await result_probe(session_fence, payload))["disposition"] == "accepted"
    state = json.loads(await result_snapshot(facts))
    assert state["execution"]["status"] == "Failed"
    assert state["execution"]["result"] == {"error": error}
    assert state["execution"]["error_message"] == error["message"]
    assert state["attempt"]["status"] == "failed"
    assert state["attempt"]["failure_phase"] == "result"


async def test_rust_result_preserves_cancel_winner_without_early_terminalization(
    released_result_facts, session_fence
):
    facts = released_result_facts
    assert (
        await probe(session_fence, operation="request-running-cancel")
        == "cancel_committed"
    )
    projection = await cancel_snapshot(facts)
    payload = result_payload(facts)
    receipt = await result_probe(session_fence, payload)
    assert receipt["winner"] == "cancel" and receipt["disposition"] == "retained"
    assert await cancel_snapshot(facts) == projection
    assert projection["status"] == "Cancelling" and projection["completed_at"] is None
    committed = await result_snapshot(facts)
    assert await result_probe(session_fence, payload) == receipt
    assert await result_snapshot(facts) == committed


async def test_rust_result_winner_cannot_be_overwritten_by_cancel(
    released_result_facts, session_fence
):
    facts = released_result_facts
    assert (await result_probe(session_fence, result_payload(facts)))[
        "winner"
    ] == "result"
    committed = await result_snapshot(facts)
    assert await probe(session_fence, operation="request-running-cancel") == "rejected"
    assert await result_snapshot(facts) == committed


async def test_rust_result_cancel_concurrent_transactions_have_one_winner(
    released_result_facts, session_fence
):
    facts = released_result_facts
    receipt, cancel = await asyncio.gather(
        result_probe(session_fence, result_payload(facts)),
        probe(session_fence, operation="request-running-cancel"),
    )
    assert isinstance(receipt, dict)
    state = json.loads(await result_snapshot(facts))
    assert len(state["receipts"]) == 1
    if receipt["winner"] == "result":
        assert cancel == "rejected" and state["execution"]["status"] == "Success"
    else:
        assert receipt["winner"] == "cancel" and cancel == "cancel_committed"
        assert state["execution"]["status"] == "Cancelling"


@pytest.mark.parametrize("field", range(10))
async def test_rust_result_rejects_stale_identity_without_writes(
    released_result_facts, session_fence, field
):
    facts = released_result_facts
    before = await result_snapshot(facts)
    stale = list(session_fence)
    stale[field] = str(uuid4()) if field < 8 else "e" * 64
    assert await result_probe(stale, result_payload(facts)) == "rejected"
    assert await result_snapshot(facts) == before


async def test_rust_result_rejects_incumbent_backend_without_writes(
    released_result_facts, session_fence
):
    facts = released_result_facts
    before = await result_snapshot(facts)
    assert (
        await result_probe(session_fence, result_payload(facts), role="wex_incumbent")
        == "rejected"
    )
    assert await result_snapshot(facts) == before


async def test_rust_result_without_release_has_no_projection(
    running_cancel_facts, session_fence
):
    facts = running_cancel_facts
    before = await result_snapshot(facts)
    assert await result_probe(session_fence, result_payload(facts)) == "rejected"
    assert await result_snapshot(facts) == before


@pytest.mark.parametrize("running_cancel_facts", [20], indirect=True)
async def test_rust_result_expired_grant_has_no_projection(
    released_result_facts, session_fence
):
    facts = released_result_facts
    before = await result_snapshot(facts)
    assert facts["expires"] < datetime.now(UTC)
    assert await result_probe(session_fence, result_payload(facts)) == "rejected"
    assert await result_snapshot(facts) == before


@pytest.mark.parametrize(
    "invalid", ("duplicate-key", "session", "correlation", "unknown-field")
)
async def test_rust_result_strict_wire_validation_precedes_projection(
    released_result_facts, session_fence, invalid
):
    facts = released_result_facts
    before = await result_snapshot(facts)
    payload = result_payload(facts)
    if invalid == "duplicate-key":
        payload = payload.replace(
            b'"type": "Result"', b'"type": "Result", "type": "Result"'
        )
    else:
        frame = json.loads(payload)
        if invalid == "session":
            frame["session_id"] = str(uuid4())
        elif invalid == "correlation":
            frame["correlation_id"] = str(uuid4())
        else:
            frame["body"]["unknown"] = True
        payload = json.dumps(frame).encode()
    assert await result_probe(session_fence, payload) == "rejected"
    assert await result_snapshot(facts) == before


async def test_rust_result_late_receipt_failure_rolls_back_all_projection(
    db_session, released_result_facts, session_fence
):
    from sqlalchemy import text

    facts = released_result_facts
    await db_session.execute(
        text("""
        CREATE FUNCTION isolated_result_receipt_fault() RETURNS trigger LANGUAGE plpgsql AS $body$
        BEGIN RAISE EXCEPTION 'synthetic receipt fault' USING ERRCODE='42501'; END $body$
    """)
    )
    await db_session.execute(
        text("""
        CREATE TRIGGER isolated_result_receipt_fault BEFORE INSERT ON runtime_report_receipts
        FOR EACH ROW EXECUTE FUNCTION isolated_result_receipt_fault()
    """)
    )
    await db_session.commit()
    try:
        before = await result_snapshot(facts)
        assert (
            await result_probe(session_fence, result_payload(facts))
            == "database_failure"
        )
        assert await result_snapshot(facts) == before
    finally:
        await db_session.execute(
            text(
                "DROP TRIGGER isolated_result_receipt_fault ON runtime_report_receipts"
            )
        )
        await db_session.execute(text("DROP FUNCTION isolated_result_receipt_fault()"))
        await db_session.commit()


async def test_rust_result_transport_closed_session_cannot_restore_authority(
    released_result_facts, session_fence
):
    facts = released_result_facts
    async with connection("wex_core") as conn:
        await conn.execute(
            "UPDATE runtime_sessions SET closed_at=clock_timestamp(),close_reason='transport_loss' WHERE id=$1",
            facts["session"],
        )
    before = await result_snapshot(facts)
    assert await result_probe(session_fence, result_payload(facts)) == "rejected"
    assert await result_snapshot(facts) == before
