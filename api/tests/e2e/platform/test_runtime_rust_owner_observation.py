"""Real Rust SQL observation through the isolated authenticated fixture pool.

These cases do not accept a lifecycle writer, admission or live session custody.
"""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tests.e2e.platform.test_runtime_writer_guards import (
    PASSWORDS,
    connection,
    installed_guards as installed_guards,
    rows as rows,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]
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


async def probe(fence, role="wex_core"):
    assert PROBE.is_file(), "Required source-bound Rust build artifact is missing"
    process = await asyncio.create_subprocess_exec(
        str(PROBE),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "BIFROST_ISOLATED_OWNER_TEST": "1",
            "BIFROST_OWNER_TEST_DATABASE_URL": (
                f"postgresql://{role}:{PASSWORDS[role]}@writer-guard-pool/bifrost_test"
            ),
        },
    )
    try:
        out, err = await asyncio.wait_for(
            process.communicate(("\n".join(fence) + "\n").encode()), timeout=8
        )
        assert process.returncode == 0
        assert err == b""
        assert len(out) <= 32
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
