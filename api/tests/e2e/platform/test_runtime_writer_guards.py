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
    from sqlalchemy import text

    output_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["ready", "missing_keys"],
        "properties": {
            "ready": {"type": "boolean"},
            "missing_keys": {"type": ["array", "null"], "items": {"type": "string"}},
        },
    }
    # Same neutral type shape as the readiness example; still synthetic artifact
    # metadata, not accepted registration or a compiled workload execution.
    statement = text(
        ARTIFACT_INSERT.text.replace(
            "'{}'::jsonb, '{}'::jsonb", "'{}'::jsonb, CAST(:output_schema AS jsonb)"
        )
    )
    await db_session.execute(
        statement, {**association, "output_schema": json.dumps(output_schema)}
    )
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
async def prepared_start_facts(db_session, association, request):
    """Actual parent/FK graph and wire hashes; synthetic artifact/custody only."""
    from datetime import timedelta
    from hashlib import sha256
    from sqlalchemy import text

    artifact = {
        **json.loads(association["artifact"]),
        "adapter_sha256": "d" * 64,
        "executable_sha256": "e" * 64,
        "image_digest": None,
        "sdk": {"distribution": "bifrost-go", "version": "synthetic-test"},
        "platform": {"os": "linux", "architecture": "amd64"},
        "toolchain": {"implementation": "go", "version": "synthetic-test"},
        "dependencies": {"kind": "go-module-lock/v1", "digest": "sha256:" + "f" * 64},
    }
    input_schema = {
        "type": "object",
        "required": ["integration_name"],
        "additionalProperties": False,
        "properties": {"integration_name": {"type": "string", "minLength": 1}},
    }
    case = getattr(
        request,
        "param",
        "provision" if "provision_facts" in request.fixturenames else "valid",
    )
    input_value = {
        "integration_name": ""
        if case == "invalid-input"
        else "Synthetic readiness fixture"
    }
    statement = text(
        ARTIFACT_INSERT.text.replace(
            "'{}'::jsonb, '{}'::jsonb", "CAST(:input_schema AS jsonb), '{}'::jsonb"
        )
    )
    await db_session.execute(
        statement,
        {
            **association,
            "artifact": json.dumps(artifact),
            "input_schema": json.dumps(input_schema),
        },
    )
    (
        template,
        execution,
        attempt,
        claim,
        worker,
        owner,
        session,
        supervisor,
        runtime,
        prepare_id,
        prepared_id,
        start,
        message,
    ) = [uuid4() for _ in range(13)]
    db_session.add(
        Execution(
            id=template,
            workflow_id=association["workflow"],
            solution_deployment_id=association["deployment"],
            workflow_name="Synthetic Start fixture",
            executed_by_name="Synthetic",
            status=ExecutionStatus.PENDING,
            parameters=input_value,
        )
    )
    await db_session.commit()
    if case in {"admit", "provision"}:
        # Synthetic source acceptance only. No archive/binary custody or runtime
        # acceptance is claimed by selecting an active fixture deployment.
        # Follow the actual migrated deployment transition guard. Never bypass
        # it or claim synthetic source fixtures performed build/activation work.
        previous = "draft"
        for state in ("building", "validated", "ready", "activating", "active"):
            retained = await db_session.scalar(
                text(
                    "UPDATE solution_deployments SET state=:state "
                    "WHERE id=:deployment AND state=:previous RETURNING state"
                ),
                {
                    "deployment": association["deployment"],
                    "state": state,
                    "previous": previous,
                },
            )
            assert retained == state
            previous = state
        await db_session.execute(
            text(
                "UPDATE solutions SET active_deployment_id=:deployment,execution_runtime_mode='deployment-v1' WHERE id=:solution"
            ),
            association,
        )
        await db_session.commit()
    async with connection("wex_core") as conn:
        org = await conn.fetchval(
            "SELECT organization_id FROM solutions WHERE id=$1", association["solution"]
        )
        caller = {
            "caller_user_id": str(association["reviewer"]),
            "caller_organization_id": str(org),
            "effective_organization_id": str(org),
        }
        caller_hash = association["source"]
        binding = {
            "kind": "execution-binding/v1",
            "execution_kind": "workflow",
            "execution_id": str(execution),
            "attempt_id": str(attempt),
            "attempt_number": 1,
            "solution_id": str(association["solution"]),
            "deployment_id": str(association["deployment"]),
            "artifact_id": association["artifact_id"],
            "session_id": str(session),
            "supervisor_incarnation_id": str(supervisor),
            "runtime_incarnation_id": str(runtime),
            "original_caller": {
                "caller_id": str(association["reviewer"]),
                "organization_id": str(org),
            },
            "effective_scope": {"kind": "organization", "organization_id": str(org)},
        }
        context = {
            key: binding[key]
            for key in (
                "execution_kind",
                "execution_id",
                "attempt_id",
                "attempt_number",
                "solution_id",
                "deployment_id",
                "artifact_id",
                "effective_scope",
            )
        }
        context.update(kind="tenant-context/v1", caller_id=str(association["reviewer"]))
        prepare = {
            "protocol": "bifrost.runtime/v1",
            "type": "Prepare",
            "session_id": str(session),
            "message_id": str(prepare_id),
            "sequence": 2,
            "correlation_id": str(uuid4()),
            "body": {
                "binding": binding,
                "artifact": artifact,
                "context": context,
                "workload": {
                    "input": input_value,
                    "input_schema_digest": "sha256:"
                    + sha256(
                        json.dumps(input_schema, separators=(",", ":")).encode()
                    ).hexdigest(),
                    "output_schema_digest": "sha256:" + sha256(b"{}").hexdigest(),
                    "deadline_utc": (datetime.now(UTC) + timedelta(seconds=10))
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                },
            },
        }
        prepared = {
            "protocol": "bifrost.runtime/v1",
            "type": "Prepared",
            "session_id": str(session),
            "message_id": str(prepared_id),
            "sequence": 2,
            "correlation_id": str(prepare_id),
            "body": {"prepare_message_id": str(prepare_id), "artifact": artifact},
        }
        if case == "expired":
            prepare["body"]["workload"]["deadline_utc"] = (
                (datetime.now(UTC) - timedelta(seconds=1))
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            )
        if case == "context":
            prepare["body"]["context"]["caller_id"] = str(uuid4())
        if case == "caller":
            caller["caller_user_id"] = str(uuid4())
        payload = json.dumps(prepare, separators=(",", ":")).encode()
        binding_hash = sha256(
            json.dumps(binding, separators=(",", ":")).encode()
        ).hexdigest()
        if case not in {"admit", "provision"}:
            async with conn.transaction():
                await clone(
                    conn,
                    "executions",
                    template,
                    {
                        "id": execution,
                        "isolated_owner": "coordinator",
                        "runtime_mode": "deployment-v1",
                    },
                )
                await conn.execute(
                    "INSERT INTO runtime_execution_owners (execution_id,owner_incarnation_id,workflow_id,deployment_id,artifact_id,caller_snapshot,caller_sha256) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7)",
                    execution,
                    owner,
                    association["workflow"],
                    association["deployment"],
                    association["artifact_id"],
                    json.dumps(caller),
                    caller_hash,
                )
                await conn.execute(
                    "INSERT INTO workflow_execution_attempts (id,execution_id,attempt_number,status,phase,claim_token,worker_incarnation_id,published_at,claimed_at,runtime_mode,isolated_owner) "
                    "VALUES ($1,$2,1,'claimed','admission',$3,$4,clock_timestamp(),clock_timestamp(),'deployment-v1','coordinator')",
                    attempt,
                    execution,
                    claim,
                    worker,
                )
                await conn.execute(
                    "INSERT INTO runtime_sessions (id,execution_id,owner_incarnation_id,workflow_attempt_id,claim_token,worker_incarnation_id,supervisor_incarnation_id,runtime_incarnation_id,channel_custody_sha256,binding_sha256,prepare_id,prepare_sha256) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
                    session,
                    execution,
                    owner,
                    attempt,
                    claim,
                    worker,
                    supervisor,
                    runtime,
                    "c" * 64,
                    binding_hash,
                    prepare_id,
                    sha256(payload).hexdigest(),
                )
    return {
        "fence": [
            str(execution),
            str(owner),
            str(attempt),
            str(claim),
            str(worker),
            str(session),
            str(supervisor),
            str(runtime),
            binding_hash,
            "c" * 64,
        ],
        "execution": execution,
        "attempt": attempt,
        "session": session,
        "start": start,
        "message": message,
        "workflow": association["workflow"],
        "caller": association["reviewer"],
        "prepare": payload,
        "prepared": json.dumps(prepared).encode(),
    }


async def start_probe(
    facts, operation="record", role="wex_core", exit_after_commit=False
):
    executable = Path("/app/scripts/runtime-owner-start")
    assert executable.is_file(), "Required source-bound Rust Start artifact is missing"
    process = await asyncio.create_subprocess_exec(
        str(executable),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "BIFROST_ISOLATED_OWNER_TEST": "1",
            "BIFROST_OWNER_TEST_DATABASE_URL": f"postgresql://{role}:{PASSWORDS[role]}@writer-guard-pool/bifrost_test",
            "BIFROST_OWNER_TEST_START_ACTION": operation,
            "BIFROST_OWNER_TEST_EXIT_AFTER_START_COMMIT": "1"
            if exit_after_commit
            else "0",
        },
    )
    try:
        header = (
            "\n".join([*facts["fence"], str(facts["start"]), str(facts["message"])])
            + "\n"
        ).encode()
        payload = json.dumps(
            [facts["prepare"].decode(), facts["prepared"].decode()]
        ).encode()
        out, err = await asyncio.wait_for(
            process.communicate(header + payload), timeout=8
        )
        assert process.returncode == (73 if exit_after_commit else 0)
        assert err == b"" and len(out) <= 512
        if exit_after_commit:
            assert out == b""
            return "reply_lost"
        response = out.decode().strip()
        return json.loads(response) if response.startswith("{") else response
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def start_snapshot(facts):
    async with connection("wex_core") as conn:
        return await conn.fetchval(
            "SELECT jsonb_build_object('execution',to_jsonb(e),'attempt',to_jsonb(a),'session',to_jsonb(s),'starts',"
            "(SELECT jsonb_agg(to_jsonb(st) ORDER BY st.id) FROM runtime_starts st WHERE st.session_id=s.id))::text "
            "FROM executions e JOIN workflow_execution_attempts a ON a.execution_id=e.id JOIN runtime_sessions s ON s.workflow_attempt_id=a.id "
            "WHERE e.id=$1 AND a.id=$2 AND s.id=$3",
            facts["execution"],
            facts["attempt"],
            facts["session"],
        )


async def test_rust_start_commits_running_and_exact_clock_once(prepared_start_facts):
    facts = prepared_start_facts
    before = await start_snapshot(facts)
    assert await start_probe(facts, operation="observe") == "start_not_retained"
    assert await start_snapshot(facts) == before
    body = await start_probe(facts)
    assert body["committed_start_id"] == str(facts["start"])
    assert body["prepare_message_id"] == json.loads(facts["prepare"])["message_id"]
    assert 0 < body["remaining_run_ms"] <= 10000
    committed = await start_snapshot(facts)
    retained = json.loads(committed)
    assert retained["execution"]["status"] == "Running"
    assert (
        retained["attempt"]["status"] == "running"
        and retained["attempt"]["phase"] == "execution"
    )
    assert (
        retained["execution"]["started_at"]
        == retained["attempt"]["started_at"]
        == retained["starts"][0]["started_at"]
    )
    assert await start_probe(facts) == "rejected"
    assert await start_probe(facts, operation="observe") == "start_retained"
    assert await start_snapshot(facts) == committed


async def test_rust_start_lost_reply_is_observed_without_repeating_write(
    prepared_start_facts,
):
    facts = prepared_start_facts
    assert await start_probe(facts, exit_after_commit=True) == "reply_lost"
    before = await start_snapshot(facts)
    assert await start_probe(facts, operation="observe") == "start_retained"
    assert await start_snapshot(facts) == before


@pytest.mark.parametrize("field", range(10))
async def test_rust_start_stale_fence_has_no_effects(prepared_start_facts, field):
    facts = prepared_start_facts
    changed = {**facts, "fence": list(facts["fence"])}
    changed["fence"][field] = str(uuid4()) if field < 8 else "a" * 64
    before = await start_snapshot(facts)
    assert await start_probe(changed) == "rejected"
    assert await start_snapshot(facts) == before


async def test_rust_start_noncore_role_has_no_effects(prepared_start_facts):
    facts = prepared_start_facts
    before = await start_snapshot(facts)
    assert await start_probe(facts, role="wex_incumbent") == "rejected"
    assert await start_snapshot(facts) == before


async def test_rust_start_concurrent_writers_have_one_committed_decision(
    prepared_start_facts,
):
    facts = prepared_start_facts
    responses = await asyncio.gather(start_probe(facts), start_probe(facts))
    assert sum(isinstance(response, dict) for response in responses) == 1
    assert responses.count("rejected") == 1
    retained = json.loads(await start_snapshot(facts))
    assert len(retained["starts"]) == 1


@pytest.mark.parametrize(
    "prepared_start_facts",
    ["invalid-input", "expired", "context", "caller"],
    indirect=True,
)
async def test_rust_start_rejects_invalid_retained_preparation_without_effects(
    prepared_start_facts,
):
    facts = prepared_start_facts
    before = await start_snapshot(facts)
    assert await start_probe(facts) == "rejected"
    assert await start_snapshot(facts) == before


async def test_rust_start_close_before_decision_has_no_effects(prepared_start_facts):
    facts = prepared_start_facts
    async with connection("wex_core") as conn:
        await conn.execute(
            "UPDATE runtime_sessions SET closed_at=clock_timestamp(),close_reason='test_closed' WHERE id=$1",
            facts["session"],
        )
    before = await start_snapshot(facts)
    assert await start_probe(facts) == "rejected"
    assert await start_snapshot(facts) == before


async def test_rust_start_late_attempt_fault_rolls_back_start_and_running(
    prepared_start_facts, db_session
):
    from sqlalchemy import text

    facts = prepared_start_facts
    await db_session.execute(
        text(
            "CREATE FUNCTION isolated_start_attempt_fault() RETURNS trigger LANGUAGE plpgsql AS $body$ "
            "BEGIN RAISE EXCEPTION 'synthetic Start attempt fault' USING ERRCODE='42501'; END $body$"
        )
    )
    await db_session.execute(
        text(
            "CREATE TRIGGER isolated_start_attempt_fault BEFORE UPDATE ON workflow_execution_attempts "
            "FOR EACH ROW EXECUTE FUNCTION isolated_start_attempt_fault()"
        )
    )
    await db_session.commit()
    before = await start_snapshot(facts)
    try:
        assert await start_probe(facts) == "database_failure"
        assert await start_snapshot(facts) == before
    finally:
        await db_session.execute(
            text(
                "DROP TRIGGER isolated_start_attempt_fault ON workflow_execution_attempts"
            )
        )
        await db_session.execute(text("DROP FUNCTION isolated_start_attempt_fault()"))
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
async def provisioned_release_facts(running_cancel_facts):
    """Synthetic provision and unsigned grant, never live custody/issuer evidence."""
    facts = {
        **running_cancel_facts,
        "provision": uuid4(),
        "delivery": uuid4(),
        "release": uuid4(),
    }
    async with connection("wex_core") as conn:
        await conn.execute(
            "INSERT INTO runtime_admissions "
            "(id,purpose,session_id,committed_start_id,start_message_id,grant_id,"
            "delivery_id,operations_digest,expires_at,frontier_sha256,admitted_at) "
            "VALUES ($1,'provision',$2,$3,$4,$5,$6,$7,$8,$7,$9)",
            facts["provision"],
            facts["session"],
            facts["start"],
            facts["start_message"],
            facts["grant"],
            facts["delivery"],
            facts["source"],
            facts["expires"],
            facts["issued"],
        )
    return facts


async def release_probe(
    fence, facts, role="wex_core", exit_after_commit=False, operation="record"
):
    executable = Path("/app/scripts/runtime-owner-release")
    assert executable.is_file(), (
        "Required source-bound Rust release artifact is missing"
    )
    process = await asyncio.create_subprocess_exec(
        str(executable),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "BIFROST_ISOLATED_OWNER_TEST": "1",
            "BIFROST_OWNER_TEST_RELEASE_ACTION": operation,
            "BIFROST_OWNER_TEST_DATABASE_URL": (
                f"postgresql://{role}:{PASSWORDS[role]}@writer-guard-pool/bifrost_test"
            ),
            "BIFROST_OWNER_TEST_EXIT_AFTER_RELEASE_COMMIT": "1"
            if exit_after_commit
            else "0",
        },
    )
    try:
        fields = [
            *fence,
            str(facts["release"]),
            str(facts["provision"]),
            str(facts["grant"]),
            str(facts["delivery"]),
            facts["source"],
            facts["source"],
        ]
        out, err = await asyncio.wait_for(
            process.communicate(("\n".join(fields) + "\n").encode()), timeout=8
        )
        assert process.returncode == (73 if exit_after_commit else 0)
        assert err == b"" and len(out) <= 32
        if exit_after_commit:
            assert out == b""
            return "reply_lost"
        return out.decode().strip()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def release_snapshot(facts):
    async with connection("wex_core") as conn:
        admissions = await conn.fetchval(
            "SELECT jsonb_agg(to_jsonb(a) ORDER BY purpose,id)::text "
            "FROM runtime_admissions a WHERE session_id=$1",
            facts["session"],
        )
    return (await result_snapshot(facts), admissions)


@pytest.fixture
async def sdk_admission_facts(provision_facts):
    """Real Rust owner birth/Start/provision/release; synthetic artifact/custody."""
    facts = dict(provision_facts)
    assert await provision_probe(facts) == "newly_committed"
    snapshot, _, _, _ = facts["issuer_inputs"]
    request = facts["provision_request"]
    facts.update(
        grant=snapshot.id,
        grant_digest=request["grant_digest"],
        provision=UUID(request["provision_id"]),
        delivery=UUID(request["delivery_id"]),
        release=uuid4(),
        source=snapshot.operations_digest,
        org=snapshot.effective_organization_id,
        solution=snapshot.solution_install_id,
        expires=snapshot.initial_access_expires_at,
    )
    assert await release_probe(facts["fence"], facts) == "newly_committed"
    return facts


async def sdk_admission_probe(
    facts, fence=None, request=None, role="wex_core", purpose="sdk"
):
    executable = Path("/app/scripts/runtime-owner-sdk-admission")
    assert executable.is_file(), (
        "Required source-bound Rust SDK admission artifact is missing"
    )
    fields = request or [
        str(facts["grant"]),
        facts["grant_digest"],
        "Fixture",
        str(facts["org"]),
        str(facts["solution"]),
    ]
    process = await asyncio.create_subprocess_exec(
        str(executable),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "BIFROST_ISOLATED_OWNER_TEST": "1",
            "BIFROST_OWNER_TEST_SDK_ACTION": purpose,
            "BIFROST_OWNER_TEST_DATABASE_URL": f"postgresql://{role}:{PASSWORDS[role]}@writer-guard-pool/bifrost_test",
        },
    )
    try:
        out, err = await asyncio.wait_for(
            process.communicate(
                ("\n".join([*(fence or facts["fence"]), *fields]) + "\n").encode()
            ),
            timeout=8,
        )
        assert process.returncode == 0 and err == b"" and len(out) <= 32
        return out.decode().strip()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def sdk_admission_snapshot(facts):
    async with connection("wex_core") as conn:
        sdk = await conn.fetchval(
            "SELECT jsonb_build_object('grant',to_jsonb(g),'operations',"
            "(SELECT jsonb_agg(to_jsonb(op) ORDER BY ordinal) FROM workflow_runtime_sdk_grant_operations op WHERE op.grant_id=g.id),"
            "'admissions',(SELECT jsonb_agg(to_jsonb(a) ORDER BY purpose,id) FROM runtime_admissions a WHERE a.session_id=g.runtime_session_id))::text "
            "FROM workflow_runtime_sdk_grants g WHERE g.id=$1",
            facts["grant"],
        )
    return (await start_snapshot(facts), sdk)


async def test_rust_sdk_admission_is_bounded_and_read_only(sdk_admission_facts):
    facts = sdk_admission_facts
    before = await sdk_admission_snapshot(facts)
    assert await sdk_admission_probe(facts) == "sdk_admitted"
    assert await sdk_admission_snapshot(facts) == before


@pytest.mark.parametrize("field", ["is_active", "email", "name", "is_superuser"])
async def test_rust_sdk_admission_denies_current_caller_drift(
    sdk_admission_facts, db_session, field
):
    from sqlalchemy import text

    facts = sdk_admission_facts
    original = await db_session.scalar(
        text(f"SELECT {field} FROM users WHERE id=:caller"),
        {"caller": facts["caller"]},
    )
    changed = (
        False
        if field == "is_active"
        else not original
        if field == "is_superuser"
        else f"sdk-drift-{uuid4()}@example.test"
        if field == "email"
        else f"{original or ''} SDK drift"
    )
    before = await sdk_admission_snapshot(facts)
    try:
        await db_session.execute(
            text(f"UPDATE users SET {field}=:value WHERE id=:caller"),
            {"value": changed, "caller": facts["caller"]},
        )
        await db_session.commit()
        assert await sdk_admission_probe(facts) == "rejected"
        assert await sdk_admission_snapshot(facts) == before
    finally:
        await db_session.rollback()
        await db_session.execute(
            text(f"UPDATE users SET {field}=:value WHERE id=:caller"),
            {"value": original, "caller": facts["caller"]},
        )
        await db_session.commit()
    assert await sdk_admission_probe(facts) == "sdk_admitted"


async def test_rust_sdk_admission_denies_inactive_current_workflow(
    sdk_admission_facts, db_session
):
    from sqlalchemy import text

    facts = sdk_admission_facts
    before = await sdk_admission_snapshot(facts)
    await db_session.execute(
        text("UPDATE workflows SET is_active=false WHERE id=:workflow"),
        {"workflow": facts["workflow"]},
    )
    await db_session.commit()
    assert await sdk_admission_probe(facts) == "rejected"
    assert await sdk_admission_snapshot(facts) == before


async def test_rust_sdk_admission_denies_changed_current_role_snapshot(
    sdk_admission_facts, db_session
):
    from sqlalchemy import text
    from src.models.orm.users import Role, UserRole

    facts = sdk_admission_facts
    role_id = uuid4()
    before = await sdk_admission_snapshot(facts)
    try:
        db_session.add(
            Role(id=role_id, name=f"sdk-drift-{role_id}", created_by="isolated fixture")
        )
        await db_session.flush()
        db_session.add(
            UserRole(
                user_id=facts["caller"],
                role_id=role_id,
                assigned_by="isolated fixture",
            )
        )
        await db_session.commit()
        assert await sdk_admission_probe(facts) == "rejected"
        assert await sdk_admission_snapshot(facts) == before
    finally:
        await db_session.rollback()
        await db_session.execute(
            text("DELETE FROM roles WHERE id=:id"), {"id": role_id}
        )
        await db_session.commit()
    assert await sdk_admission_probe(facts) == "sdk_admitted"


async def test_rust_sdk_admission_denies_changed_current_solution_runtime(
    sdk_admission_facts, db_session
):
    from sqlalchemy import text

    facts = sdk_admission_facts
    before = await sdk_admission_snapshot(facts)
    await db_session.execute(
        text(
            "UPDATE solutions SET execution_runtime_mode='repo-v1' WHERE id=:solution"
        ),
        {"solution": facts["solution"]},
    )
    await db_session.commit()
    assert await sdk_admission_probe(facts) == "rejected"
    assert await sdk_admission_snapshot(facts) == before


@pytest.mark.parametrize("field", range(5))
async def test_rust_sdk_admission_denies_each_credential_or_operation_drift(
    sdk_admission_facts, field
):
    facts = sdk_admission_facts
    before = await sdk_admission_snapshot(facts)
    request = [
        str(facts["grant"]),
        facts["grant_digest"],
        "Fixture",
        str(facts["org"]),
        str(facts["solution"]),
    ]
    request[field] = (
        "f" * 64
        if field == 1
        else "DifferentIntegration"
        if field == 2
        else str(uuid4())
    )
    assert await sdk_admission_probe(facts, request=request) == "rejected"
    assert await sdk_admission_snapshot(facts) == before


@pytest.mark.parametrize("field", range(10))
async def test_rust_sdk_admission_denies_each_stale_session_fence(
    sdk_admission_facts, field
):
    facts = sdk_admission_facts
    before = await sdk_admission_snapshot(facts)
    fence = list(facts["fence"])
    fence[field] = str(uuid4()) if field < 8 else "e" * 64
    assert await sdk_admission_probe(facts, fence=fence) == "rejected"
    assert await sdk_admission_snapshot(facts) == before


async def test_rust_sdk_admission_denies_noncore_pool_identity(sdk_admission_facts):
    facts = sdk_admission_facts
    before = await sdk_admission_snapshot(facts)
    assert await sdk_admission_probe(facts, role="wex_incumbent") == "rejected"
    assert await sdk_admission_snapshot(facts) == before


async def test_rust_cancel_tombstone_blocks_later_sdk_admission(sdk_admission_facts):
    facts = sdk_admission_facts
    assert (
        await probe(facts["fence"], operation="request-running-cancel")
        == "cancel_committed"
    )
    before = await sdk_admission_snapshot(facts)
    assert await sdk_admission_probe(facts) == "rejected"
    assert await sdk_admission_snapshot(facts) == before


async def test_rust_sdk_admission_rejects_additional_operation(sdk_admission_facts):
    facts = sdk_admission_facts
    async with connection("wex_core") as conn:
        await conn.execute(
            "INSERT INTO workflow_runtime_sdk_grant_operations "
            "(grant_id,ordinal,operation,integration_name,scope_kind,scope_organization_id,resolved_organization_id) "
            "VALUES ($1,1,'mapping-get','Fixture','organization',$2,$2)",
            facts["grant"],
            facts["org"],
        )
    before = await sdk_admission_snapshot(facts)
    assert await sdk_admission_probe(facts) == "rejected"
    assert await sdk_admission_snapshot(facts) == before


async def test_rust_sdk_admission_denies_explicitly_revoked_open_session(
    sdk_admission_facts,
):
    facts = sdk_admission_facts
    async with connection("wex_core") as conn:
        await conn.execute(
            "UPDATE workflow_runtime_sdk_grants SET revoked_at=clock_timestamp(),revocation_reason='explicit_revoke' WHERE id=$1",
            facts["grant"],
        )
    before = await sdk_admission_snapshot(facts)
    assert await sdk_admission_probe(facts) == "rejected"
    assert await sdk_admission_snapshot(facts) == before


async def test_rust_sdk_admission_denies_naturally_expired_grant(sdk_admission_facts):
    facts = sdk_admission_facts
    before = await sdk_admission_snapshot(facts)
    await asyncio.sleep(
        max(0, (facts["expires"] - datetime.now(UTC)).total_seconds()) + 0.01
    )
    assert facts["expires"] < datetime.now(UTC)
    assert await sdk_admission_probe(facts) == "rejected"
    assert await sdk_admission_snapshot(facts) == before


async def test_rust_release_observed_commit_and_no_replay(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    assert await release_probe(session_fence, facts) == "newly_committed"
    before = await release_snapshot(facts)
    async with connection("wex_core") as conn:
        row = await conn.fetchrow(
            "SELECT provision_admission_id,grant_id,delivery_id,frontier_sha256 "
            "FROM runtime_admissions WHERE id=$1 AND purpose='release'",
            facts["release"],
        )
    assert tuple(row) == (
        facts["provision"],
        facts["grant"],
        facts["delivery"],
        facts["source"],
    )
    assert await release_probe(session_fence, facts) == "already_retained"
    assert await release_snapshot(facts) == before


async def test_rust_release_commit_reply_loss_retains_same_release(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    assert (
        await release_probe(session_fence, facts, exit_after_commit=True)
        == "reply_lost"
    )
    before = await release_snapshot(facts)
    assert (
        await release_probe(session_fence, facts, operation="observe")
        == "already_retained"
    )
    assert await release_snapshot(facts) == before


async def test_rust_release_read_only_absence_never_creates_admission(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    before = await release_snapshot(facts)
    assert (
        await release_probe(session_fence, facts, operation="observe")
        == "release_not_retained"
    )
    assert await release_snapshot(facts) == before


async def test_rust_release_read_only_after_cancel_keeps_retained_identity(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    assert await release_probe(session_fence, facts) == "newly_committed"
    assert (
        await probe(session_fence, operation="request-running-cancel")
        == "cancel_committed"
    )
    before = await release_snapshot(facts)
    assert (
        await release_probe(session_fence, facts, operation="observe")
        == "already_retained"
    )
    assert await release_snapshot(facts) == before


async def test_rust_release_conflicting_identity_has_no_replacement(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    assert await release_probe(session_fence, facts) == "newly_committed"
    before = await release_snapshot(facts)
    assert (
        await release_probe(session_fence, {**facts, "release": uuid4()}) == "rejected"
    )
    assert await release_snapshot(facts) == before


async def test_rust_release_closed_before_commit_has_no_release(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    assert (
        await probe(session_fence, operation="request-running-cancel")
        == "cancel_committed"
    )
    before = await release_snapshot(facts)
    assert await release_probe(session_fence, facts) == "rejected"
    assert await release_snapshot(facts) == before


@pytest.mark.parametrize("running_cancel_facts", [20], indirect=True)
async def test_rust_release_expired_grant_has_no_release(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    before = await release_snapshot(facts)
    assert await release_probe(session_fence, facts) == "rejected"
    assert await release_snapshot(facts) == before


async def test_rust_release_incumbent_cannot_commit(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    before = await release_snapshot(facts)
    assert await release_probe(session_fence, facts, role="wex_incumbent") == "rejected"
    assert await release_snapshot(facts) == before


@pytest.mark.parametrize("field", ["provision", "grant", "delivery"])
async def test_rust_release_wrong_material_reference_has_no_effects(
    provisioned_release_facts,
    session_fence,
    field,
):
    facts = provisioned_release_facts
    before = await release_snapshot(facts)
    assert await release_probe(session_fence, {**facts, field: uuid4()}) == "rejected"
    assert await release_snapshot(facts) == before


async def test_rust_release_concurrent_commit_has_one_fresh_observation(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    responses = await asyncio.gather(
        release_probe(session_fence, facts),
        release_probe(session_fence, facts),
    )
    assert sorted(responses) == ["already_retained", "newly_committed"]
    async with connection("wex_core") as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM runtime_admissions WHERE session_id=$1 AND purpose='release'",
                facts["session"],
            )
            == 1
        )


async def test_rust_release_cancel_race_retains_winner_without_replay(
    provisioned_release_facts,
    session_fence,
):
    facts = provisioned_release_facts
    release, cancel = await asyncio.gather(
        release_probe(session_fence, facts),
        probe(session_fence, operation="request-running-cancel"),
    )
    assert release in {"newly_committed", "rejected"}
    assert cancel == "cancel_committed"
    state = await cancel_snapshot(facts)
    assert state["status"] == "Cancelling" and state["closed_at"] is not None
    assert state["revoked_at"] is not None
    async with connection("wex_core") as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM runtime_admissions WHERE session_id=$1 AND purpose='release'",
            facts["session"],
        )
    assert count == (1 if release == "newly_committed" else 0)
    before = await release_snapshot(facts)
    assert await release_probe(session_fence, facts) == "rejected"
    assert await release_snapshot(facts) == before


@pytest.mark.parametrize("index", range(10))
async def test_rust_release_stale_fence_has_no_effects(
    provisioned_release_facts,
    session_fence,
    index,
):
    facts = provisioned_release_facts
    changed = list(session_fence)
    changed[index] = str(uuid4()) if index < 8 else "f" * 64
    before = await release_snapshot(facts)
    assert await release_probe(changed, facts) == "rejected"
    assert await release_snapshot(facts) == before


@pytest.fixture
async def released_result_facts(provisioned_release_facts, session_fence):
    """Real Rust release transaction; Start/grant/provision remain synthetic.

    This composes the two owner transactions without claiming live material,
    accepted source custody, physical launch or a Go-produced Result.
    """
    facts = provisioned_release_facts
    assert await release_probe(session_fence, facts) == "newly_committed"
    async with connection("wex_core") as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM runtime_admissions "
                "WHERE id=$1 AND purpose='release' AND session_id=$2 "
                "AND provision_admission_id=$3",
                facts["release"],
                facts["session"],
                facts["provision"],
            )
            == 1
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


async def test_rust_lost_release_ack_observed_before_result_and_api_readback(
    provisioned_release_facts, session_fence, e2e_client, platform_admin
):
    facts = provisioned_release_facts
    assert (
        await release_probe(session_fence, facts, exit_after_commit=True)
        == "reply_lost"
    )
    before = await release_snapshot(facts)
    assert (
        await release_probe(session_fence, facts, operation="observe")
        == "already_retained"
    )
    assert await release_snapshot(facts) == before
    payload = result_payload(facts)
    receipt = await result_probe(session_fence, payload)
    assert receipt["disposition"] == "accepted" and receipt["winner"] == "result"
    committed = await release_snapshot(facts)
    # Finalization changes projection/session/grant; the release admission is
    # retained verbatim, without reissuing it or assigning a replacement owner.
    assert committed[1] == before[1]
    assert (
        await release_probe(session_fence, facts, operation="observe")
        == "already_retained"
    )
    assert await result_probe(session_fence, payload) == receipt
    response = e2e_client.get(
        f"/api/executions/{facts['execution']}", headers=platform_admin.headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "Success"
    assert response.json()["result"] == {"ready": True, "missing_keys": []}
    assert await release_snapshot(facts) == committed


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


@pytest.mark.parametrize("running_cancel_facts", [0], indirect=True)
async def test_rust_result_expired_grant_has_no_projection(
    released_result_facts, session_fence
):
    facts = released_result_facts
    before = await result_snapshot(facts)
    # Release was valid at its own commit. Let that same immutable finite grant
    # expire before Result; never rewrite its deadline or bypass release checks.
    await asyncio.sleep(
        max(0, (facts["expires"] - datetime.now(UTC)).total_seconds()) + 0.01
    )
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


@pytest.mark.parametrize(
    "value",
    (
        {"ready": "true", "missing_keys": []},
        {"ready": True, "missing_keys": "key"},
        {"ready": True, "missing_keys": [False]},
        {"ready": True},
        {"ready": True, "missing_keys": [], "extra": True},
    ),
)
async def test_rust_result_retained_output_schema_rejects_invalid_value_without_writes(
    released_result_facts, session_fence, value
):
    facts = released_result_facts
    before = await result_snapshot(facts)
    payload = result_payload(
        facts,
        body={
            "start_message_id": str(facts["start_message"]),
            "outcome": "success",
            "value": value,
        },
    )
    assert await result_probe(session_fence, payload) == "rejected"
    assert await result_snapshot(facts) == before


async def test_rust_result_retained_output_schema_allows_declared_nullable_array(
    released_result_facts, session_fence
):
    facts = released_result_facts
    value = {"ready": True, "missing_keys": None}
    payload = result_payload(
        facts,
        body={
            "start_message_id": str(facts["start_message"]),
            "outcome": "success",
            "value": value,
        },
    )
    receipt = await result_probe(session_fence, payload)
    assert isinstance(receipt, dict) and receipt["disposition"] == "accepted"
    assert json.loads(await result_snapshot(facts))["execution"]["result"] == value


@pytest.fixture
async def provision_facts(prepared_start_facts):
    """Real Rust birth/Start and canonical preimages; synthetic source/custody."""
    from src.core.runtime_sdk_credentials import (
        AcceptedManifestIdentity,
        AuthorizedCallerSnapshot,
        GrantSnapshot,
        SDKOperation,
        SelectedSDKPolicy,
        caller_digest,
        grant_digest,
        operations_digest,
        source_digest,
    )

    facts = dict(prepared_start_facts)
    assert await admit_probe(facts) == "newly_committed"
    assert isinstance(await start_probe(facts), dict)
    async with connection("wex_core") as conn:
        row = await conn.fetchrow(
            "SELECT o.workflow_id,o.deployment_id,o.caller_snapshot::text AS caller,"
            "d.solution_id,d.compiled_manifest_hash,d.resolution_map_hash,"
            "s.claim_token_digest,s.worker_incarnation_id,s.supervisor_incarnation_id,"
            "st.started_at,st.deadline_utc,clock_timestamp() AS issued "
            "FROM runtime_sessions s JOIN runtime_starts st ON st.session_id=s.id "
            "JOIN runtime_execution_owners o ON o.execution_id=s.execution_id "
            "JOIN solution_deployments d ON d.id=o.deployment_id WHERE s.id=$1",
            facts["session"],
        )
    assert row is not None
    caller = AuthorizedCallerSnapshot.model_validate_json(row["caller"])
    source = AcceptedManifestIdentity(
        source_id=row["deployment_id"],
        solution_install_id=row["solution_id"],
        source_manifest_digest=row["compiled_manifest_hash"],
        source_resolution_digest=row["resolution_map_hash"],
        source_global_permission=False,
    )
    policy = SelectedSDKPolicy(
        operations=(
            SDKOperation(
                operation="integration-get",
                integration_name="Fixture",
                scope_kind="organization",
                scope_organization_id=caller.effective_organization_id,
                resolved_organization_id=caller.effective_organization_id,
                solution_install_id=row["solution_id"],
            ),
        )
    )
    snapshot = GrantSnapshot(
        id=uuid4(),
        schema_version="cred-p1/v1",
        workflow_attempt_id=facts["attempt"],
        execution_id=facts["execution"],
        attempt_number=1,
        claim_token_digest=row["claim_token_digest"],
        worker_incarnation_id=row["worker_incarnation_id"],
        supervisor_incarnation_id=row["supervisor_incarnation_id"],
        runtime_session_id=facts["session"],
        started_at=row["started_at"],
        issued_at=row["issued"],
        timeout_seconds=10,
        credential_deadline=row["deadline_utc"],
        initial_access_expires_at=row["deadline_utc"],
        caller_user_id=caller.caller_user_id,
        caller_organization_id=caller.caller_organization_id,
        effective_organization_id=caller.effective_organization_id,
        caller_email=caller.caller_email,
        caller_name=caller.caller_name,
        caller_admin=int(caller.caller_admin),
        caller_provider=int(caller.caller_provider),
        caller_external=int(caller.caller_external),
        caller_snapshot_digest=caller_digest(caller),
        workflow_id=row["workflow_id"],
        solution_install_id=row["solution_id"],
        source_kind="solution-deployment",
        source_id=row["deployment_id"],
        source_manifest_digest=row["compiled_manifest_hash"],
        source_resolution_digest=row["resolution_map_hash"],
        source_global_permission=0,
        source_digest=source_digest(source),
        operations_digest=operations_digest(policy),
    )
    facts["issuer_inputs"] = (snapshot, caller, source, policy)
    facts["provision_request"] = {
        "snapshot": snapshot.model_dump(mode="json"),
        "grant_digest": grant_digest(snapshot),
        "integration_name": "Fixture",
        "provision_id": str(uuid4()),
        "delivery_id": str(uuid4()),
        "frontier_sha256": "a" * 64,
    }
    return facts


async def provision_probe(facts, role="wex_core", fence=None, request=None):
    executable = Path("/app/scripts/runtime-owner-provision")
    assert executable.is_file(), (
        "Required source-bound Rust provision artifact is missing"
    )
    process = await asyncio.create_subprocess_exec(
        str(executable),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "BIFROST_ISOLATED_OWNER_TEST": "1",
            "BIFROST_OWNER_TEST_DATABASE_URL": f"postgresql://{role}:{PASSWORDS[role]}@writer-guard-pool/bifrost_test",
        },
    )
    try:
        payload = ("\n".join(fence or facts["fence"]) + "\n").encode()
        payload += json.dumps(request or facts["provision_request"]).encode()
        out, err = await asyncio.wait_for(process.communicate(payload), timeout=8)
        assert process.returncode == 0 and err == b"" and len(out) <= 32
        return out.decode().strip()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def provision_snapshot(facts):
    async with connection("wex_core") as conn:
        values = []
        for table, field in (
            ("workflow_runtime_sdk_grants", "runtime_session_id"),
            ("runtime_admissions", "session_id"),
        ):
            values.append(
                await conn.fetchval(
                    f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb)::text FROM {table} t WHERE {field}=$1",
                    facts["session"],
                )
            )
        values.append(
            await conn.fetchval(
                "SELECT COALESCE(jsonb_agg(to_jsonb(op) ORDER BY ordinal),'[]'::jsonb)::text "
                "FROM workflow_runtime_sdk_grant_operations op JOIN workflow_runtime_sdk_grants g ON g.id=op.grant_id WHERE g.runtime_session_id=$1",
                facts["session"],
            )
        )
    return (await start_snapshot(facts), values)


@pytest.mark.parametrize("prepared_start_facts", ["provision"], indirect=True)
async def test_rust_provision_commits_canonical_grant_operation_and_admission(
    provision_facts,
):
    facts = provision_facts
    assert await provision_probe(facts) == "newly_committed"
    before = await provision_snapshot(facts)
    assert await provision_probe(facts) == "rejected"
    assert await provision_snapshot(facts) == before
    request = facts["provision_request"]
    async with connection("wex_core") as conn:
        row = await conn.fetchrow(
            "SELECT g.grant_digest,g.operations_digest,op.integration_name,a.delivery_id::text AS delivery "
            "FROM workflow_runtime_sdk_grants g JOIN workflow_runtime_sdk_grant_operations op ON op.grant_id=g.id "
            "JOIN runtime_admissions a ON a.grant_id=g.id WHERE g.runtime_session_id=$1",
            facts["session"],
        )
    assert row is not None
    assert dict(row) == {
        "grant_digest": request["grant_digest"],
        "operations_digest": request["snapshot"]["operations_digest"],
        "integration_name": "Fixture",
        "delivery": request["delivery_id"],
    }
    from src.core.runtime_sdk_credentials import (
        GrantReference,
        decode_runtime_sdk_access,
    )
    from src.core.security import decode_token
    from src.services.isolated_runtime_sdk_tokens import (
        sign_finite_runtime_sdk_access,
        verify_finite_integration_get,
    )

    snapshot, caller, source, policy = facts["issuer_inputs"]
    credential = sign_finite_runtime_sdk_access(
        snapshot,
        caller,
        source,
        policy,
        GrantReference(grant_id=snapshot.id, grant_digest=request["grant_digest"]),
        now=datetime.now(UTC),
    )
    claims = decode_runtime_sdk_access(credential.access_token)
    assert (
        claims.sub == str(snapshot.id)
        and claims.grant_digest == request["grant_digest"]
    )
    assert decode_token(credential.access_token, expected_type="access") is None
    assert decode_token(credential.access_token, expected_type="refresh") is None
    facts.update(
        grant=snapshot.id,
        grant_digest=request["grant_digest"],
        provision=UUID(request["provision_id"]),
        delivery=UUID(request["delivery_id"]),
        release=uuid4(),
        source=snapshot.operations_digest,
    )
    assert await release_probe(facts["fence"], facts) == "newly_committed"
    # Existing SDK request shape, independently verified before private Rust
    # admission. Authority/custody/source remain synthetic in this component.
    intent = verify_finite_integration_get(
        credential.access_token,
        json.dumps(
            {
                "name": "Fixture",
                "scope": str(snapshot.effective_organization_id),
                "solution": str(snapshot.solution_install_id),
            }
        ).encode(),
        snapshot,
        caller,
        source,
        policy,
        now=datetime.now(UTC),
    )
    assert (
        await sdk_admission_probe(
            facts,
            request=[
                str(intent.grant_id),
                intent.grant_digest,
                intent.integration_name,
                str(intent.organization_id),
                str(intent.solution_id),
            ],
        )
        == "sdk_admitted"
    )


@pytest.mark.parametrize("prepared_start_facts", ["provision"], indirect=True)
@pytest.mark.parametrize("field", range(10))
async def test_rust_provision_stale_fence_cannot_create_absent_grant(
    provision_facts, field
):
    facts = provision_facts
    before = await provision_snapshot(facts)
    fence = list(facts["fence"])
    fence[field] = str(uuid4()) if field < 8 else "0" * 64
    assert await provision_probe(facts, fence=fence) == "rejected"
    assert await provision_snapshot(facts) == before


@pytest.mark.parametrize("prepared_start_facts", ["provision"], indirect=True)
@pytest.mark.parametrize(
    "change",
    ["grant-digest", "name", "source", "operation", "caller", "expanded-field"],
)
async def test_rust_provision_invalid_preimages_roll_back_all_birth(
    provision_facts, change
):
    facts = provision_facts
    before = await provision_snapshot(facts)
    request = json.loads(json.dumps(facts["provision_request"]))
    if change == "grant-digest":
        request["grant_digest"] = "0" * 64
    elif change == "name":
        request["integration_name"] = "Different"
    else:
        field = {
            "source": "source_digest",
            "operation": "operations_digest",
            "caller": "caller_snapshot_digest",
            "expanded-field": "owner_incarnation_id",
        }[change]
        request["snapshot"][field] = "0" * 64
    if change in {"source", "operation", "caller"}:
        from src.core.runtime_sdk_credentials import GrantSnapshot, grant_digest

        # A correctly rehashed malicious snapshot still cannot change admitted
        # source, operation or immutable owner evidence.
        mutated = GrantSnapshot.model_validate_json(json.dumps(request["snapshot"]))
        request["grant_digest"] = grant_digest(mutated)
    assert await provision_probe(facts, request=request) == "rejected"
    assert await provision_snapshot(facts) == before


@pytest.mark.parametrize("prepared_start_facts", ["provision"], indirect=True)
async def test_rust_provision_noncore_login_cannot_create_grant(provision_facts):
    facts = provision_facts
    before = await provision_snapshot(facts)
    assert await provision_probe(facts, role="wex_incumbent") == "rejected"
    assert await provision_snapshot(facts) == before


@pytest.mark.parametrize("prepared_start_facts", ["provision"], indirect=True)
async def test_rust_cancel_before_grant_birth_blocks_provision(provision_facts):
    facts = provision_facts
    assert (
        await probe(facts["fence"], operation="request-running-cancel")
        == "cancel_committed"
    )
    before = await provision_snapshot(facts)
    assert await provision_probe(facts) == "rejected"
    assert await provision_snapshot(facts) == before


@pytest.mark.parametrize("prepared_start_facts", ["provision"], indirect=True)
async def test_rust_provision_late_failure_rolls_back_grant_and_operation(
    provision_facts, db_session
):
    from sqlalchemy import text

    facts = provision_facts
    await db_session.execute(
        text("""
        CREATE FUNCTION isolated_provision_fault() RETURNS trigger LANGUAGE plpgsql AS $body$
        BEGIN RAISE EXCEPTION 'synthetic provision fault' USING ERRCODE='42501'; END $body$
        """)
    )
    await db_session.execute(
        text("""
        CREATE TRIGGER isolated_provision_fault BEFORE INSERT ON runtime_admissions
        FOR EACH ROW EXECUTE FUNCTION isolated_provision_fault()
        """)
    )
    await db_session.commit()
    try:
        before = await provision_snapshot(facts)
        assert await provision_probe(facts) == "database_failure"
        assert await provision_snapshot(facts) == before
    finally:
        await db_session.execute(
            text("DROP TRIGGER isolated_provision_fault ON runtime_admissions")
        )
        await db_session.execute(text("DROP FUNCTION isolated_provision_fault()"))
        await db_session.commit()


async def admit_probe(facts, role="wex_core", payload=None):
    executable = Path("/app/scripts/runtime-owner-admit")
    assert executable.is_file(), (
        "Required source-bound Rust admission artifact is missing"
    )
    process = await asyncio.create_subprocess_exec(
        str(executable),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "BIFROST_ISOLATED_OWNER_TEST": "1",
            "BIFROST_OWNER_TEST_DATABASE_URL": f"postgresql://{role}:{PASSWORDS[role]}@writer-guard-pool/bifrost_test",
        },
    )
    try:
        header = (
            "\n".join([*facts["fence"], str(facts["workflow"]), str(facts["caller"])])
            + "\n"
        ).encode()
        data = json.dumps([(payload or facts["prepare"]).decode()]).encode()
        out, err = await asyncio.wait_for(process.communicate(header + data), timeout=8)
        assert process.returncode == 0 and err == b"" and len(out) <= 32
        return out.decode().strip()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def admit_snapshot(facts):
    async with connection("wex_core") as conn:
        return [
            await conn.fetchval(
                f"SELECT COALESCE(jsonb_agg(to_jsonb(t)),'[]'::jsonb)::text FROM {table} t WHERE {field}=$1",
                identity,
            )
            for table, field, identity in (
                ("executions", "id", facts["execution"]),
                ("runtime_execution_owners", "execution_id", facts["execution"]),
                ("workflow_execution_attempts", "id", facts["attempt"]),
                ("runtime_sessions", "id", facts["session"]),
            )
        ]


@pytest.mark.parametrize("prepared_start_facts", ["admit"], indirect=True)
async def test_rust_admit_creates_existing_domain_owner_attempt_and_session_together(
    prepared_start_facts,
):
    facts = prepared_start_facts
    assert await admit_snapshot(facts) == ["[]"] * 4
    assert await admit_probe(facts) == "newly_committed"
    before = await admit_snapshot(facts)
    assert all(len(json.loads(row)) == 1 for row in before)
    assert json.loads(before[0])[0]["status"] == "Pending"
    assert json.loads(before[1])[0]["owner_incarnation_id"] == facts["fence"][1]
    assert json.loads(before[2])[0]["status"] == "claimed"
    assert await admit_probe(facts) == "rejected"
    assert await admit_snapshot(facts) == before
    assert isinstance(await start_probe(facts), dict)
    assert json.loads(await start_snapshot(facts))["execution"]["status"] == "Running"


@pytest.mark.parametrize("prepared_start_facts", ["admit"], indirect=True)
@pytest.mark.parametrize(
    "change", ["caller", "input", "artifact", "expired", "session"]
)
async def test_rust_admit_invalid_preparation_has_no_partial_birth(
    prepared_start_facts, change
):
    from datetime import timedelta

    facts = prepared_start_facts
    frame = json.loads(facts["prepare"])
    if change == "caller":
        frame["body"]["binding"]["original_caller"]["caller_id"] = str(uuid4())
    elif change == "input":
        frame["body"]["workload"]["input"] = {"integration_name": ""}
    elif change == "artifact":
        frame["body"]["artifact"]["executable_sha256"] = "0" * 64
    elif change == "expired":
        frame["body"]["workload"]["deadline_utc"] = (
            (datetime.now(UTC) - timedelta(seconds=1))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    else:
        frame["session_id"] = str(uuid4())
    assert await admit_probe(facts, payload=json.dumps(frame).encode()) == "rejected"
    assert await admit_snapshot(facts) == ["[]"] * 4


@pytest.mark.parametrize("prepared_start_facts", ["admit"], indirect=True)
async def test_rust_admit_incumbent_pool_cannot_birth_coordinator_owner(
    prepared_start_facts,
):
    facts = prepared_start_facts
    assert await admit_probe(facts, role="wex_incumbent") == "rejected"
    assert await admit_snapshot(facts) == ["[]"] * 4


@pytest.mark.parametrize("prepared_start_facts", ["admit"], indirect=True)
async def test_rust_admit_inactive_workflow_is_not_accepted(
    prepared_start_facts, db_session
):
    from sqlalchemy import text

    facts = prepared_start_facts
    await db_session.execute(
        text("UPDATE workflows SET is_active=false WHERE id=:id"),
        {"id": facts["workflow"]},
    )
    await db_session.commit()
    assert await admit_probe(facts) == "rejected"
    assert await admit_snapshot(facts) == ["[]"] * 4


@pytest.mark.parametrize("prepared_start_facts", ["admit"], indirect=True)
async def test_rust_admit_late_session_failure_rolls_back_whole_birth(
    prepared_start_facts, db_session
):
    from sqlalchemy import text

    facts = prepared_start_facts
    await db_session.execute(
        text("""
        CREATE FUNCTION isolated_admit_fault() RETURNS trigger LANGUAGE plpgsql AS $body$
        BEGIN RAISE EXCEPTION 'synthetic session birth fault' USING ERRCODE='42501'; END $body$
    """)
    )
    await db_session.execute(
        text("""
        CREATE TRIGGER isolated_admit_fault BEFORE INSERT ON runtime_sessions
        FOR EACH ROW EXECUTE FUNCTION isolated_admit_fault()
    """)
    )
    await db_session.commit()
    try:
        assert await admit_probe(facts) == "database_failure"
        assert await admit_snapshot(facts) == ["[]"] * 4
    finally:
        await db_session.execute(
            text("DROP TRIGGER isolated_admit_fault ON runtime_sessions")
        )
        await db_session.execute(text("DROP FUNCTION isolated_admit_fault()"))
        await db_session.commit()


@pytest.mark.parametrize("prepared_start_facts", ["admit"], indirect=True)
async def test_rust_competing_births_retain_one_owner_and_attempt(prepared_start_facts):
    facts = prepared_start_facts
    decisions = await asyncio.gather(admit_probe(facts), admit_probe(facts))
    assert decisions.count("newly_committed") == 1
    assert all(
        value in {"newly_committed", "rejected", "lock_contention", "database_failure"}
        for value in decisions
    )
    assert all(len(json.loads(row)) == 1 for row in await admit_snapshot(facts))


@pytest.mark.parametrize("prepared_start_facts", ["provision"], indirect=True)
async def test_reserved_ingress_reads_only_its_committed_open_provision(
    provision_facts,
):
    from src.core.runtime_sdk_credentials import RuntimeSDKDenied, grant_digest
    from src.services.isolated_runtime_sdk_snapshot import (
        load_committed_finite_snapshot,
    )

    facts = provision_facts
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
    identity = {
        "grant_id": UUID(facts["provision_request"]["snapshot"]["id"]),
        "execution_id": facts["execution"],
        "session_id": facts["session"],
        "owner_incarnation_id": UUID(facts["fence"][1]),
        "attempt_id": facts["attempt"],
    }
    try:
        # Reservation/Start cannot substitute for a committed real provision.
        with pytest.raises(RuntimeSDKDenied):
            await load_committed_finite_snapshot(factory, **identity)
        assert await provision_probe(facts) == "newly_committed"
        retained = await provision_snapshot(facts)
        snapshot = await load_committed_finite_snapshot(factory, **identity)
        assert grant_digest(snapshot) == facts["provision_request"]["grant_digest"]
        for field in identity:
            wrong = dict(identity)
            wrong[field] = uuid4()
            with pytest.raises(RuntimeSDKDenied):
                await load_committed_finite_snapshot(factory, **wrong)
        assert await provision_snapshot(facts) == retained
        assert (
            await probe(facts["fence"], operation="request-running-cancel")
            == "cancel_committed"
        )
        with pytest.raises(RuntimeSDKDenied):
            await load_committed_finite_snapshot(factory, **identity)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("prepared_start_facts", ["provision"], indirect=True)
async def test_finite_issuance_requires_committed_unreleased_provision(provision_facts):
    facts = provision_facts
    snapshot, _, _, _ = facts["issuer_inputs"]
    request = facts["provision_request"]
    facts.update(
        grant=snapshot.id,
        grant_digest=request["grant_digest"],
        provision=UUID(request["provision_id"]),
        delivery=UUID(request["delivery_id"]),
        release=uuid4(),
        source=snapshot.operations_digest,
        org=snapshot.effective_organization_id,
        solution=snapshot.solution_install_id,
        expires=snapshot.initial_access_expires_at,
    )
    before = await provision_snapshot(facts)
    assert await sdk_admission_probe(facts, purpose="issuance") == "rejected"
    assert await provision_snapshot(facts) == before
    assert await provision_probe(facts) == "newly_committed"
    before = await sdk_admission_snapshot(facts)
    assert await sdk_admission_probe(facts, purpose="issuance") == "issuance_admitted"
    assert await sdk_admission_probe(facts) == "rejected"
    assert (
        await sdk_admission_probe(facts, purpose="issuance", role="wex_incumbent")
        == "rejected"
    )
    for field in range(5):
        fields = [
            str(facts["grant"]),
            facts["grant_digest"],
            "Fixture",
            str(facts["org"]),
            str(facts["solution"]),
        ]
        fields[field] = (
            "Other" if field == 2 else "b" * 64 if field == 1 else str(uuid4())
        )
        assert (
            await sdk_admission_probe(facts, purpose="issuance", request=fields)
            == "rejected"
        )
    assert await sdk_admission_snapshot(facts) == before
    assert await release_probe(facts["fence"], facts) == "newly_committed"
    before = await sdk_admission_snapshot(facts)
    assert await sdk_admission_probe(facts, purpose="issuance") == "rejected"
    assert await sdk_admission_probe(facts) == "sdk_admitted"
    assert await sdk_admission_snapshot(facts) == before


@pytest.mark.parametrize("prepared_start_facts", ["provision"], indirect=True)
async def test_cancelled_provision_cannot_authorize_finite_issuance(provision_facts):
    facts = provision_facts
    snapshot, _, _, _ = facts["issuer_inputs"]
    facts.update(
        grant=snapshot.id,
        grant_digest=facts["provision_request"]["grant_digest"],
        org=snapshot.effective_organization_id,
        solution=snapshot.solution_install_id,
    )
    assert await provision_probe(facts) == "newly_committed"
    assert (
        await probe(facts["fence"], operation="request-running-cancel")
        == "cancel_committed"
    )
    before = await sdk_admission_snapshot(facts)
    assert await sdk_admission_probe(facts, purpose="issuance") == "rejected"
    assert await sdk_admission_snapshot(facts) == before
