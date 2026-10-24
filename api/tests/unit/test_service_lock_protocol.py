"""Real PostgreSQL lock waits, with bounded transactions and no mutation retries."""

import asyncio
from datetime import UTC, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.workflows import Workflow
from src.services import service_lifecycle as lifecycle

from tests.unit.test_service_lifecycle import _ensure


@pytest_asyncio.fixture
async def service_row(async_session_factory):
    async with async_session_factory() as db:
        definition, workflow = await _ensure(db)
        # Committed fixture rows must not be claimed by the stack's worker.
        clock = lifecycle._now() + timedelta(days=1)
        definition.restart_eligible_at = clock
        await db.commit()
        identity = definition.id, workflow.id, definition.organization_id, clock
    try:
        yield identity
    finally:
        service_id, workflow_id, org_id, _ = identity
        async with async_session_factory() as db:
            await db.execute(delete(ServiceAttempt).where(ServiceAttempt.service_id == service_id))
            await db.execute(delete(ServiceDefinition).where(ServiceDefinition.id == service_id))
            await db.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await db.execute(delete(Organization).where(Organization.id == org_id))
            await db.commit()


async def _bound(db):
    await db.execute(text("SET LOCAL lock_timeout = '3s'"))
    await db.execute(text("SET LOCAL statement_timeout = '5s'"))
    return await db.scalar(text("SELECT pg_backend_pid()"))


async def _wait_for_blocker(factory, waiter, blocker):
    # Observe a database lock wait, not merely that an asyncio task started.
    async with asyncio.timeout(2):
        async with factory() as observer:
            while True:
                blocked_by = await observer.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter}
                )
                if blocker in blocked_by:
                    return
                await asyncio.sleep(0.01)


async def _control(db, definition, operation):
    if operation == "disable":
        return await lifecycle.set_enabled(db, definition, False)
    if operation == "park":
        return await lifecycle.park_definition(db, definition)
    return await getattr(lifecycle, operation + "_service")(db, definition)


async def _claim(db, clock):
    attempt = await lifecycle.claim_eligible_service(db, worker_id="lock-protocol", now=clock)
    assert attempt is not None
    return attempt.id, attempt.lease_token


async def _assert_control(db, service_id, operation):
    definition = await db.get(ServiceDefinition, service_id)
    assert definition.desired_state == ("running" if operation in ("restart", "disable") else "stopped")
    if operation in ("disable", "park"):
        assert definition.enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stop", "disable", "restart", "park"])
async def test_control_waits_for_uncommitted_claim(async_session_factory, service_row, operation):
    service_id, _, _, clock = service_row
    async with asyncio.timeout(8):
        async with async_session_factory(autoflush=False) as claimant:
            owner_pid = await _bound(claimant)
            attempt_id, token = await _claim(claimant, clock)
            async with async_session_factory(autoflush=False) as control:
                waiter_pid = await _bound(control)
                definition = await control.get(ServiceDefinition, service_id)
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(_control(control, definition, operation))
                    await _wait_for_blocker(async_session_factory, waiter_pid, owner_pid)
                    await claimant.commit()
                await _assert_control(control, service_id, operation)
                attempt = await control.get(ServiceAttempt, attempt_id)
                assert attempt.lease_token == token
                assert attempt.stop_requested_at is not None
                assert lifecycle.describe_observed_state(definition, attempt) == "stopping"
                await control.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stop", "disable", "restart", "park"])
async def test_control_and_completion_do_not_invert_locks(async_session_factory, service_row, operation):
    service_id, _, _, clock = service_row
    async with async_session_factory() as setup:
        attempt_id, token = await _claim(setup, clock)
        await setup.commit()
    async with asyncio.timeout(8):
        async with async_session_factory(autoflush=False) as control:
            owner_pid = await _bound(control)
            definition = await lifecycle._lock_definition(control, service_id)
            async with async_session_factory(autoflush=False) as completion:
                waiter_pid = await _bound(completion)
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(lifecycle.complete_attempt(
                        completion, attempt_id=attempt_id, lease_token=token,
                        reason="requested", now=clock + timedelta(seconds=1),
                    ))
                    await _wait_for_blocker(async_session_factory, waiter_pid, owner_pid)
                    # Old A->D completion holds A while waiting here, deadlocking
                    # this control's D->A transition. Correct completion holds no A.
                    await _control(control, definition, operation)
                    await control.commit()
                await completion.commit()
                await _assert_control(completion, service_id, operation)
                attempt = await completion.get(ServiceAttempt, attempt_id)
                assert attempt.state == "stopped"
                assert attempt.exit_reason is None
                assert attempt.lease_token == token
                assert attempt.stop_requested_at is not None
                assert await completion.scalar(select(ServiceAttempt.restart_number).where(
                    ServiceAttempt.id == attempt_id
                )) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stop", "disable", "restart", "park"])
async def test_expiry_skips_control_definition_before_attempt(async_session_factory, service_row, operation):
    service_id, _, _, clock = service_row
    async with async_session_factory() as setup:
        attempt_id, token = await _claim(setup, clock)
        await setup.commit()
    expired_clock = clock + timedelta(seconds=61)
    async with asyncio.timeout(8):
        async with async_session_factory(autoflush=False) as control:
            await _bound(control)
            definition = await lifecycle._lock_definition(control, service_id)
            async with async_session_factory(autoflush=False) as sweeper:
                await _bound(sweeper)
                # Old expiry locks A then waits for D; this must return without
                # taking A, leaving control free to signal the live attempt.
                assert await lifecycle.expire_leases(sweeper, now=expired_clock) == 0
                await sweeper.commit()
                await _control(control, definition, operation)
                await control.commit()
                assert await lifecycle.expire_leases(sweeper, now=expired_clock) == 1
                await sweeper.commit()
                await _assert_control(sweeper, service_id, operation)
                attempt = await sweeper.get(ServiceAttempt, attempt_id)
                assert attempt.state == "failed"
                assert attempt.exit_reason == "lease_expired"
                assert attempt.lease_token == token
                assert attempt.restart_number == 0
                with pytest.raises(lifecycle.StaleLeaseError):
                    await lifecycle.heartbeat_attempt(
                        sweeper, attempt_id=attempt_id, lease_token=token, now=expired_clock,
                    )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completion", "expiry"])
async def test_stop_waits_for_terminal_transition(async_session_factory, service_row, terminal):
    service_id, _, _, clock = service_row
    async with async_session_factory() as setup:
        attempt_id, token = await _claim(setup, clock)
        await setup.commit()
    async with asyncio.timeout(8):
        async with async_session_factory(autoflush=False) as owner:
            owner_pid = await _bound(owner)
            await lifecycle._lock_definition(owner, service_id)
            if terminal == "completion":
                await lifecycle.complete_attempt(owner, attempt_id=attempt_id, lease_token=token,
                                                 reason="clean_return", now=clock + timedelta(seconds=1))
            else:
                assert await lifecycle.expire_leases(owner, now=clock + timedelta(seconds=61)) == 1
            async with async_session_factory(autoflush=False) as control:
                waiter_pid = await _bound(control)
                definition = await control.get(ServiceDefinition, service_id)
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(lifecycle.stop_service(control, definition))
                    await _wait_for_blocker(async_session_factory, waiter_pid, owner_pid)
                    await owner.commit()
                assert definition.desired_state == "stopped"
                assert await lifecycle.get_live_attempt(control, service_id) is None
                assert lifecycle.describe_observed_state(definition, None) == "stopped"
                assert await lifecycle.claim_eligible_service(control, worker_id="replacement", now=clock) is None
                await control.commit()


@pytest.mark.asyncio
async def test_missing_attempt_retains_stale_lease_contract(async_session_factory):
    from uuid import uuid4

    async with async_session_factory(autoflush=False) as db:
        with pytest.raises(lifecycle.StaleLeaseError):
            await lifecycle.heartbeat_attempt(db, attempt_id=uuid4(), lease_token="absent")


@pytest.mark.asyncio
async def test_renewed_lease_not_expired_from_stale_snapshot(async_session_factory, service_row):
    service_id, _, _, clock = service_row
    async with async_session_factory() as setup:
        attempt_id, token = await _claim(setup, clock)
        await setup.commit()
    expiry_clock = clock + timedelta(seconds=61)
    async with async_session_factory(autoflush=False) as observer:
        # Prime an expired-looking ORM identity before a competing heartbeat.
        stale = await observer.get(ServiceAttempt, attempt_id)
        assert stale.lease_expires_at <= expiry_clock
        async with async_session_factory(autoflush=False) as heartbeat:
            await lifecycle.heartbeat_attempt(
                heartbeat, attempt_id=attempt_id, lease_token=token,
                now=clock + timedelta(seconds=59), lease_ttl_seconds=60,
            )
            await heartbeat.commit()
        assert await lifecycle.expire_leases(observer, now=expiry_clock) == 0
        await observer.refresh(stale)
        assert stale.state == "starting"
        assert stale.lease_expires_at > expiry_clock
        assert stale.lease_token == token
        assert stale.service_id == service_id


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["heartbeat_attempt", "mark_attempt_ready", "complete_attempt"])
async def test_owner_expiring_during_parent_wait_is_fenced(
    async_session_factory, service_row, monkeypatch, operation,
):
    service_id, _, _, clock = service_row
    async with async_session_factory() as setup:
        attempt_id, token = await _claim(setup, clock)
        await setup.commit()
    current_time = [clock + timedelta(seconds=1)]
    monkeypatch.setattr(lifecycle, "_now", lambda: current_time[0])
    async with asyncio.timeout(8):
        async with async_session_factory(autoflush=False) as blocker:
            owner_pid = await _bound(blocker)
            await lifecycle._lock_definition(blocker, service_id)
            async with async_session_factory(autoflush=False) as owner:
                waiter_pid = await _bound(owner)

                async def invoke():
                    kwargs = {"attempt_id": attempt_id, "lease_token": token}
                    if operation == "complete_attempt":
                        kwargs["reason"] = "clean_return"
                    with pytest.raises(lifecycle.StaleLeaseError):
                        await getattr(lifecycle, operation)(owner, **kwargs)

                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(invoke())
                    await _wait_for_blocker(async_session_factory, waiter_pid, owner_pid)
                    # Advance the injected clock only AFTER observing the real
                    # PostgreSQL wait. No 60s sleep or lease-duration change.
                    current_time[0] = clock + timedelta(seconds=61)
                    await blocker.commit()
                attempt = await owner.get(ServiceAttempt, attempt_id)
                assert attempt.state == "starting"
                assert attempt.lease_expires_at == clock + timedelta(seconds=60)
                assert attempt.lease_token == token


@pytest.mark.asyncio
async def test_two_service_indexer_and_beats_release_previous_definition(
    async_session_factory, service_row, monkeypatch,
):
    """AST high->low indexing must coexist with low->high owned beats."""
    from hashlib import sha256
    from unittest.mock import AsyncMock

    from src.services.file_storage.indexers.workflow import WorkflowIndexer
    from src.services.service_claim import OwnedAttempt, ServiceClaimLoop

    first_id, _, _, clock = service_row
    async with async_session_factory() as setup:
        second, second_workflow = await _ensure(setup)
        second_id, second_workflow_id, second_org_id = second.id, second_workflow.id, second.organization_id
        second.restart_eligible_at = clock
        workflows = list((await setup.scalars(select(Workflow).join(
            ServiceDefinition, ServiceDefinition.workflow_id == Workflow.id,
        ).where(ServiceDefinition.id.in_([first_id, second_id])))).all())
        path = f"workflows/lock_order_{first_id.hex}.py"
        for workflow in workflows:
            workflow.path = path
        await setup.flush()
        attempts = {}
        for _ in range(2):
            attempt_id, token = await _claim(setup, clock)
            attempt = await setup.get(ServiceAttempt, attempt_id)
            attempts[attempt.service_id] = OwnedAttempt(attempt_id, attempt.service_id, token)
        low, high = sorted(attempts)
        names = {workflow.id: workflow.function_name for workflow in workflows}
        definitions = list((await setup.scalars(select(ServiceDefinition).where(
            ServiceDefinition.id.in_([low, high]),
        ))).all())
        functions = {definition.id: names[definition.workflow_id] for definition in definitions}
        await setup.commit()
    content = ("from bifrost import service\n" + "".join(
        f"@service(name='{functions[ident]}')\nasync def {functions[ident]}():\n    pass\n"
        for ident in [high, low]
    )).encode()
    high_indexed, advance_indexer, low_paused = asyncio.Event(), asyncio.Event(), asyncio.Event()
    advance_low, low_indexed, finish_indexer, high_started = (
        asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
    )
    pids = {}
    real_note = lifecycle.note_source_revision

    async def note(db, definition, revision):
        result = await real_note(db, definition, revision)
        if definition.id == high:
            high_indexed.set()
            await advance_indexer.wait()
        else:
            low_indexed.set()
            await finish_indexer.wait()
        return result

    monkeypatch.setattr(lifecycle, "note_source_revision", note)
    monkeypatch.setattr(lifecycle, "_now", lambda: clock + timedelta(seconds=1))
    loop = ServiceClaimLoop(worker_id="lock-protocol", beat_interval_seconds=0)
    loop._owned = {str(owned.attempt_id): owned for owned in attempts.values()}
    for name in ["_drain_ready", "_mirror_stop", "_report_memory", "_rotate_token"]:
        monkeypatch.setattr(loop, name, AsyncMock())

    async def flush(db, owned):
        if owned.service_id == low:
            low_paused.set()
            await advance_low.wait()

    monkeypatch.setattr(loop, "_flush_logs", flush)
    real_beat = loop._beat_one

    async def beat_one(db, owned):
        pids[owned.service_id] = await _bound(db)
        if owned.service_id == high:
            high_started.set()
        await real_beat(db, owned)

    monkeypatch.setattr(loop, "_beat_one", beat_one)
    try:
        async with asyncio.timeout(12):
            async with async_session_factory(autoflush=False) as indexer_db:
                indexer_pid = await _bound(indexer_db)

                async def index():
                    await WorkflowIndexer(indexer_db).index_python_file(path, content)
                    await indexer_db.commit()

                async with (
                    async_session_factory(autoflush=False) as beat_db,
                    asyncio.TaskGroup() as tasks,
                ):
                    tasks.create_task(index())
                    await high_indexed.wait()
                    tasks.create_task(loop._beat_owned(beat_db))
                    await low_paused.wait()
                    advance_indexer.set()
                    await _wait_for_blocker(async_session_factory, indexer_pid, pids[low])
                    advance_low.set()
                    await low_indexed.wait()
                    await high_started.wait()
                    await _wait_for_blocker(async_session_factory, pids[high], indexer_pid)
                    finish_indexer.set()
        async with async_session_factory() as check:
            for ident, owned in attempts.items():
                definition = await check.get(ServiceDefinition, ident)
                attempt = await check.get(ServiceAttempt, owned.attempt_id)
                assert definition.current_revision == sha256(content).hexdigest()
                assert attempt.lease_token == owned.lease_token
                assert attempt.state == "starting"
                assert attempt.lease_expires_at == clock + timedelta(seconds=61)
            assert len(loop._owned) == 2
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(ServiceAttempt).where(ServiceAttempt.service_id == second_id))
            await cleanup.execute(delete(ServiceDefinition).where(ServiceDefinition.id == second_id))
            await cleanup.execute(delete(Workflow).where(Workflow.id == second_workflow_id))
            await cleanup.execute(delete(Organization).where(Organization.id == second_org_id))
            await cleanup.commit()


@pytest.mark.asyncio
async def test_owned_heartbeat_can_complete_startup_grace_in_same_transaction(
    async_session_factory, service_row, monkeypatch,
):
    from datetime import datetime

    from src.services.service_claim import OwnedAttempt, ServiceClaimLoop

    service_id, _, _, clock = service_row
    async with async_session_factory() as setup:
        attempt_id, token = await _claim(setup, clock)
        attempt = await setup.get(ServiceAttempt, attempt_id)
        attempt.started_at = datetime.now(UTC) - timedelta(seconds=600)
        await setup.commit()
    monkeypatch.setattr(lifecycle, "_now", lambda: clock + timedelta(seconds=1))
    loop = ServiceClaimLoop(worker_id="lock-protocol", beat_interval_seconds=0)
    loop._owned[str(attempt_id)] = OwnedAttempt(attempt_id, service_id, token)
    async with asyncio.timeout(8):
        async with async_session_factory(autoflush=False) as db:
            await _bound(db)
            await loop._beat_owned(db)
        async with async_session_factory() as check:
            attempt = await check.get(ServiceAttempt, attempt_id)
            assert attempt.state == "failed"
            assert attempt.exit_reason == "startup_grace_exceeded"
            assert attempt.lease_token == token
            assert str(attempt_id) not in loop._owned
            definition = await check.get(ServiceDefinition, service_id)
            assert definition.restart_eligible_at > clock


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_cursor,taken_over", [
    (None, False), ("older-committed", False), ("older-committed", True),
])
async def test_failed_beat_commit_discards_only_its_uncommitted_cursor(
    monkeypatch, previous_cursor, taken_over,
):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from src.services.service_claim import OwnedAttempt, ServiceClaimLoop

    ident = uuid4()
    owned = OwnedAttempt(ident, uuid4(), "token")
    loop = ServiceClaimLoop(worker_id="cursor-probe", beat_interval_seconds=0)
    loop._owned[str(ident)] = owned
    loop._pending_cursors["other-committed"] = "durable"
    if previous_cursor is not None:
        loop._pending_cursors[str(ident)] = previous_cursor

    async def stage(db, item):
        loop._pending_cursors[str(item.attempt_id)] = "uncommitted"
        if taken_over:
            loop._completed_cursors.add(str(item.attempt_id))

    monkeypatch.setattr(loop, "_beat_one", stage)
    db = AsyncMock()
    db.commit.side_effect = RuntimeError("injected commit failure")
    with pytest.raises(RuntimeError, match="injected commit failure"):
        await loop._beat_owned(db)
    assert loop._pending_cursors == {
        "other-committed": "durable", **({str(ident): previous_cursor} if previous_cursor and not taken_over else {}),
    }
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_owned_completion_during_prior_beat_skips_removed_entry(monkeypatch):
    from unittest.mock import AsyncMock
    from uuid import UUID, uuid4

    from src.services.service_claim import OwnedAttempt, ServiceClaimLoop

    first = OwnedAttempt(uuid4(), UUID(int=1), "first")
    second = OwnedAttempt(uuid4(), UUID(int=2), "second")
    loop = ServiceClaimLoop(worker_id="snapshot-probe", beat_interval_seconds=0)
    loop._owned = {str(item.attempt_id): item for item in [first, second]}
    seen = []

    async def complete_neighbor(db, owned):
        seen.append(owned.attempt_id)
        loop._owned.pop(str(second.attempt_id))

    monkeypatch.setattr(loop, "_beat_one", complete_neighbor)
    db = AsyncMock()
    await loop._beat_owned(db)
    assert seen == [first.attempt_id]
    db.commit.assert_awaited_once()
