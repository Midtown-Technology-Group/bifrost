"""Real PostgreSQL stop/claim interleaving, reconciliation and cancellation."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from src.core.cache.keys import service_stop_key
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.workflows import Workflow
from src.services import service_lifecycle
from src.services.service_claim import OwnedAttempt, ServiceClaimLoop
from tests.unit.test_service_claim import FakeRedis, StubPool, _fake_redis_holder
from tests.unit.test_service_lifecycle import _ensure


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stop", "disable", "park"])
@pytest.mark.parametrize("cancel_at", [None, "redis", "child"])
async def test_stop_claim_window_reconciles_without_orphan_or_restart(
    async_session_factory, monkeypatch, operation, cancel_at
):
    """Late claims honor durable intent, including a cancelled supervision tick."""
    async with async_session_factory() as setup:
        definition, workflow = await _ensure(setup)
        service_id = definition.id
        workflow_id = workflow.id
        org_id = definition.organization_id
        # The stack worker uses wall-clock time and cannot consume this fixture.
        claim_time = service_lifecycle._now() + timedelta(days=1)
        definition.restart_eligible_at = claim_time
        await setup.commit()

    original_lookup = service_lifecycle.get_live_attempt_for_update
    claims = []

    async def claim_after_empty_lookup(db, requested_service_id):
        live = await original_lookup(db, requested_service_id)
        assert live is None
        async with async_session_factory(autoflush=False) as claimant:
            attempt = await service_lifecycle.claim_eligible_service(
                claimant, worker_id="stop-claim-probe", now=claim_time
            )
            assert attempt is not None and attempt.service_id == service_id
            claims.append((attempt.id, attempt.lease_token))
            await claimant.commit()
        return live

    monkeypatch.setattr(
        service_lifecycle, "get_live_attempt_for_update", claim_after_empty_lookup
    )
    try:
        async with async_session_factory(autoflush=False) as stopping:
            definition = await stopping.get(ServiceDefinition, service_id)
            if operation == "stop":
                await service_lifecycle.stop_service(stopping, definition)
            elif operation == "disable":
                await service_lifecycle.set_enabled(stopping, definition, False)
            else:
                await service_lifecycle.park_definition(stopping, definition)
            await stopping.commit()
        assert len(claims) == 1
        async with async_session_factory() as before_beat:
            definition = await before_beat.get(ServiceDefinition, service_id)
            live = await service_lifecycle.get_live_attempt(before_beat, service_id)
            assert live is not None and live.stop_requested_at is None
            assert service_lifecycle.describe_observed_state(definition, live) == "stopping"
        pool = StubPool()
        pool.stop_service_child = AsyncMock(return_value=True)
        loop = ServiceClaimLoop(worker_id="stop-claim-probe", pool=pool)
        attempt_id, lease_token = claims[0]
        loop._owned[str(attempt_id)] = OwnedAttempt(attempt_id, service_id, lease_token)
        redis = FakeRedis()
        if cancel_at == "redis":
            setex = redis.setex
            cancelled = False

            async def cancel_stop_key(key, ttl, value):
                nonlocal cancelled
                if key == service_stop_key(str(attempt_id)) and not cancelled:
                    cancelled = True
                    raise asyncio.CancelledError
                await setex(key, ttl, value)

            redis.setex = cancel_stop_key
        elif cancel_at == "child":
            pool.stop_service_child.side_effect = [asyncio.CancelledError(), True]
        with patch("src.core.cache.get_redis", lambda: _fake_redis_holder({"redis": redis})):
            if cancel_at is not None:
                with pytest.raises(asyncio.CancelledError):
                    async with async_session_factory(autoflush=False) as interrupted:
                        await loop._beat_owned(interrupted)
                        await interrupted.commit()
                assert not loop._owned[str(attempt_id)].stop_mirrored
                async with async_session_factory() as observer:
                    live = await service_lifecycle.get_live_attempt(observer, service_id)
                    assert live.stop_requested_at is None  # Cancelled transaction rolled back.
                # Explicitly exercise the next tick; do not wait on wall-clock time.
                loop._last_beat = 0
            async with async_session_factory(autoflush=False) as supervisor:
                await loop._beat_owned(supervisor)
                await supervisor.commit()
            stop_calls = 2 if cancel_at == "child" else 1
            assert pool.stop_service_child.await_count == stop_calls
            assert redis.values[service_stop_key(str(attempt_id))] == "1"
            assert loop._owned[str(attempt_id)].stop_mirrored
            loop._last_beat = 0
            async with async_session_factory(autoflush=False) as steady_tick:
                await loop._beat_owned(steady_tick)
                await steady_tick.commit()
            assert pool.stop_service_child.await_count == stop_calls
            async with async_session_factory() as observer:
                definition = await observer.get(ServiceDefinition, service_id)
                live = await service_lifecycle.get_live_attempt(observer, service_id)
                assert live is not None and live.stop_requested_at is not None
                assert service_lifecycle.describe_observed_state(definition, live) == "stopping"
            # The real completion path clears ownership and suppresses relaunch.
            pool.stop_service_child.side_effect = None
            await loop.handle_service_result({
                "success": False, "status": "Failed", "error": "requested shutdown",
                "service": {"service_id": str(service_id), "attempt_id": str(attempt_id),
                            "lease_token": lease_token},
            })
            assert str(attempt_id) not in loop._owned
            async with async_session_factory(autoflush=False) as restart:
                terminal = await restart.get(ServiceAttempt, attempt_id)
                assert terminal.state == "stopped" and terminal.error is None
                assert await service_lifecycle.get_live_attempt(restart, service_id) is None
                assert await service_lifecycle.claim_eligible_service(
                    restart, worker_id="no-restart-probe", now=claim_time
                ) is None
                definition = await restart.get(ServiceDefinition, service_id)
                await service_lifecycle.set_enabled(restart, definition, True)
                await service_lifecycle.start_service(restart, definition)
                fresh = await service_lifecycle.claim_eligible_service(
                    restart, worker_id="explicit-restart-probe"
                )
                assert fresh is not None and fresh.id != attempt_id
                assert fresh.lease_token != lease_token and fresh.stop_requested_at is None
                fresh_id, fresh_token = fresh.id, fresh.lease_token
                await restart.commit()
            loop._owned[str(fresh_id)] = OwnedAttempt(fresh_id, service_id, fresh_token)
            loop._last_beat = 0
            completed_calls = pool.stop_service_child.await_count
            async with async_session_factory(autoflush=False) as running_tick:
                await loop._beat_owned(running_tick)
                await running_tick.commit()
            assert pool.stop_service_child.await_count == completed_calls
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                delete(ServiceAttempt).where(ServiceAttempt.service_id == service_id)
            )
            await cleanup.execute(
                delete(ServiceDefinition).where(ServiceDefinition.id == service_id)
            )
            await cleanup.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await cleanup.execute(delete(Organization).where(Organization.id == org_id))
            await cleanup.commit()


@pytest.mark.asyncio
async def test_cancel_after_child_stop_keeps_requested_completion(async_session_factory, monkeypatch):
    """A rolled-back beat cannot turn an intentional child stop into a failure."""
    async with async_session_factory() as setup:
        definition, workflow = await _ensure(setup)
        service_id, workflow_id, org_id = definition.id, workflow.id, definition.organization_id
        claim_time = service_lifecycle._now() + timedelta(days=1)
        definition.restart_eligible_at = claim_time
        await setup.flush()
        attempt = await service_lifecycle.claim_eligible_service(
            setup, worker_id="cancelled-beat-probe", now=claim_time
        )
        assert attempt is not None and attempt.service_id == service_id
        attempt_id, lease_token = attempt.id, attempt.lease_token
        # Reproduce the durable outcome of the proven late-claim interleaving.
        definition.desired_state = "stopped"
        await setup.commit()
    pool = StubPool()
    loop = ServiceClaimLoop(worker_id="cancelled-beat-probe", pool=pool)
    loop._owned[str(attempt_id)] = OwnedAttempt(attempt_id, service_id, lease_token)
    monkeypatch.setattr(loop, "_report_memory", AsyncMock(side_effect=asyncio.CancelledError))
    redis = FakeRedis()
    try:
        with patch("src.core.cache.get_redis", lambda: _fake_redis_holder({"redis": redis})):
            with pytest.raises(asyncio.CancelledError):
                async with async_session_factory(autoflush=False) as interrupted:
                    await loop._beat_owned(interrupted)
                    await interrupted.commit()
            assert loop._owned[str(attempt_id)].stop_mirrored
            pool.stop_service_child.assert_awaited_once_with(str(attempt_id))
            async with async_session_factory() as observer:
                live = await service_lifecycle.get_live_attempt(observer, service_id)
                assert live is not None and live.stop_requested_at is None
            await loop.handle_service_result({
                "success": False, "status": "Failed", "error": "requested shutdown",
                "service": {"service_id": str(service_id), "attempt_id": str(attempt_id),
                            "lease_token": lease_token},
            })
            assert str(attempt_id) not in loop._owned
            async with async_session_factory() as observer:
                terminal = await observer.get(ServiceAttempt, attempt_id)
                assert terminal.state == "stopped" and terminal.error is None
                assert terminal.exit_reason == "stop_requested"
                assert await service_lifecycle.get_live_attempt(observer, service_id) is None
                assert await service_lifecycle.claim_eligible_service(
                    observer, worker_id="cancelled-beat-no-restart", now=claim_time
                ) is None
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                delete(ServiceAttempt).where(ServiceAttempt.service_id == service_id)
            )
            await cleanup.execute(
                delete(ServiceDefinition).where(ServiceDefinition.id == service_id)
            )
            await cleanup.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await cleanup.execute(delete(Organization).where(Organization.id == org_id))
            await cleanup.commit()


@pytest.mark.asyncio
async def test_stop_and_completion_preserve_attempt_before_definition_lock_order(
    async_session_factory, monkeypatch
):
    """Completion holding an attempt can finish while stop enters its lookup."""
    async with async_session_factory() as setup:
        definition, workflow = await _ensure(setup)
        service_id, workflow_id, org_id = definition.id, workflow.id, definition.organization_id
        claim_time = service_lifecycle._now() + timedelta(days=1)
        definition.restart_eligible_at = claim_time
        await setup.flush()
        attempt = await service_lifecycle.claim_eligible_service(
            setup, worker_id="completion-order-probe", now=claim_time
        )
        assert attempt is not None and attempt.service_id == service_id
        attempt_id, lease_token = attempt.id, attempt.lease_token
        await setup.commit()

    attempt_locked = asyncio.Event()
    stop_reached_lookup = asyncio.Event()
    locked_attempt = service_lifecycle._locked_live_attempt
    live_lookup = service_lifecycle.get_live_attempt_for_update

    async def hold_completion_lock(*args, **kwargs):
        attempt = await locked_attempt(*args, **kwargs)
        attempt_locked.set()
        await stop_reached_lookup.wait()
        return attempt

    async def release_completion_at_stop_lookup(*args, **kwargs):
        stop_reached_lookup.set()
        return await live_lookup(*args, **kwargs)

    monkeypatch.setattr(service_lifecycle, "_locked_live_attempt", hold_completion_lock)
    monkeypatch.setattr(
        service_lifecycle, "get_live_attempt_for_update", release_completion_at_stop_lookup
    )

    async def complete():
        async with async_session_factory(autoflush=False) as db:
            await service_lifecycle.complete_attempt(
                db, attempt_id=attempt_id, lease_token=lease_token, reason="clean_return"
            )
            await db.commit()

    async def stop():
        await attempt_locked.wait()
        async with async_session_factory(autoflush=False) as db:
            definition = await db.get(ServiceDefinition, service_id)
            await service_lifecycle.stop_service(db, definition)
            await db.commit()

    async def interleave():
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(complete())
            tasks.create_task(stop())

    try:
        await asyncio.wait_for(interleave(), timeout=10)
        async with async_session_factory() as observer:
            definition = await observer.get(ServiceDefinition, service_id)
            assert definition.desired_state == "stopped"
            assert await service_lifecycle.get_live_attempt(observer, service_id) is None
            assert await service_lifecycle.claim_eligible_service(
                observer, worker_id="no-orphan-order-probe", now=claim_time
            ) is None
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                delete(ServiceAttempt).where(ServiceAttempt.service_id == service_id)
            )
            await cleanup.execute(
                delete(ServiceDefinition).where(ServiceDefinition.id == service_id)
            )
            await cleanup.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await cleanup.execute(delete(Organization).where(Organization.id == org_id))
            await cleanup.commit()
