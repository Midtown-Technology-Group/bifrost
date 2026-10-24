"""Real PostgreSQL lock waits, with bounded transactions and no mutation retries."""

import asyncio
from datetime import timedelta

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
                    task = tasks.create_task(_control(control, definition, operation))
                    await _wait_for_blocker(async_session_factory, waiter_pid, owner_pid)
                    await claimant.commit()
                await task
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
                    task = tasks.create_task(lifecycle.complete_attempt(
                        completion, attempt_id=attempt_id, lease_token=token,
                        reason="requested", now=clock + timedelta(seconds=1),
                    ))
                    await _wait_for_blocker(async_session_factory, waiter_pid, owner_pid)
                    # Old A->D completion holds A while waiting here, deadlocking
                    # this control's D->A transition. Correct completion holds no A.
                    await _control(control, definition, operation)
                    await control.commit()
                await task
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
                    task = tasks.create_task(lifecycle.stop_service(control, definition))
                    await _wait_for_blocker(async_session_factory, waiter_pid, owner_pid)
                    await owner.commit()
                await task
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
