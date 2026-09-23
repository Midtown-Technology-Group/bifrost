"""Durable runtime-maintenance fencing against real PostgreSQL."""

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from src.models.orm.config import SystemConfig
from src.models.orm.work_deliveries import WorkDelivery
from src.services.runtime_maintenance import (
    MAINTENANCE_CATEGORY,
    MAINTENANCE_KEY,
    RuntimeMaintenanceActive,
    RuntimeMaintenanceError,
    RuntimeMaintenanceSealed,
    acquire_runtime_maintenance_lock,
    enter_runtime_maintenance,
    exit_runtime_maintenance,
    read_runtime_maintenance_state,
    seal_runtime_maintenance,
)
from src.services.scheduler_diagnostics import start_scheduler_run
from src.services.work_delivery_store import claim_deliveries, enqueue_delivery

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest_asyncio.fixture(autouse=True)
async def clean_runtime_maintenance(async_session_factory):
    async def clean() -> None:
        async with async_session_factory() as db:
            await db.execute(
                delete(SystemConfig).where(
                    SystemConfig.category == MAINTENANCE_CATEGORY,
                    SystemConfig.key == MAINTENANCE_KEY,
                    SystemConfig.organization_id.is_(None),
                )
            )
            await db.commit()

    await clean()
    yield
    await clean()


async def test_state_survives_new_sessions_and_requires_exact_generation(
    async_session_factory,
) -> None:
    async with async_session_factory() as db:
        generation = uuid4()
        state = await enter_runtime_maintenance(
            db,
            requested_by="release@example.com",
            reason="test release",
            generation=generation,
        )
        await db.commit()

    async with async_session_factory() as db:
        persisted = await read_runtime_maintenance_state(db)
        assert persisted.generation == state.generation
        assert persisted.phase == "draining"

        repeated = await enter_runtime_maintenance(
            db,
            requested_by="release@example.com",
            reason="test release",
            generation=generation,
        )
        assert repeated.generation == generation

        with pytest.raises(RuntimeMaintenanceActive, match="already active"):
            await enter_runtime_maintenance(
                db,
                requested_by="release@example.com",
                reason="other release",
                generation=uuid4(),
            )
        await db.rollback()

        with pytest.raises(RuntimeMaintenanceError, match="does not match"):
            await exit_runtime_maintenance(db, generation=uuid4())
        await db.rollback()

    async with async_session_factory() as db:
        await exit_runtime_maintenance(db, generation=state.generation)
        await db.rollback()

    async with async_session_factory() as db:
        assert (await read_runtime_maintenance_state(db)).active is True
        await exit_runtime_maintenance(db, generation=state.generation)
        await db.commit()

    async with async_session_factory() as db:
        assert (await read_runtime_maintenance_state(db)).active is False


async def test_enter_waits_for_an_existing_shared_claim_boundary(
    async_session_factory,
) -> None:
    backend = asyncio.Queue()

    async def enter():
        async with async_session_factory() as db:
            # This transaction pins the same PostgreSQL backend through the lock.
            await backend.put(await db.scalar(text("SELECT pg_backend_pid()")))
            state = await enter_runtime_maintenance(
                db, requested_by="release@example.com", reason="claim race"
            )
            await db.commit()
            return state

    async with async_session_factory() as claim_session:
        await acquire_runtime_maintenance_lock(claim_session, shared=True)
        entering = asyncio.create_task(enter())
        try:
            pid = await asyncio.wait_for(backend.get(), timeout=5)

            async def wait_for_blocked_lock():
                while not await claim_session.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_locks "
                         "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted)"),
                    {"pid": pid},
                ):
                    if entering.done():
                        pytest.fail("Maintenance entered without waiting for the shared lock")
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_blocked_lock(), timeout=5)
            assert entering.done() is False
            await claim_session.commit()
            state = await asyncio.wait_for(entering, timeout=5)
            assert state.phase == "draining"
        finally:
            entering.cancel()
            await asyncio.gather(entering, return_exceptions=True)


async def test_active_maintenance_fences_a_new_scheduler_run(
    async_session_factory,
) -> None:
    async with async_session_factory() as db:
        await enter_runtime_maintenance(
            db, requested_by="release@example.com", reason="trigger fence"
        )
        await db.commit()

    with pytest.raises(RuntimeMaintenanceActive, match="active"):
        await start_scheduler_run("test-trigger", "scheduler-test")


async def test_queued_delivery_prevents_sealing(db_session) -> None:
    state = await enter_runtime_maintenance(
        db_session, requested_by="release@example.com", reason="accepted work"
    )
    await enqueue_delivery(
        db_session, queue_name=f"maintenance-{uuid4()}",
        message_id=str(uuid4()), envelope={"synthetic": True},
    )
    assert state.generation is not None
    observed, counts = await seal_runtime_maintenance(
        db_session, generation=state.generation
    )
    assert observed.phase == "draining"
    assert counts.work_deliveries >= 1
    assert counts.drained is False


async def test_persisted_seal_fences_publication_and_claims_until_exit(db_session) -> None:
    queue = f"maintenance-{uuid4()}"
    state = await enter_runtime_maintenance(
        db_session, requested_by="release@example.com", reason="restart boundary"
    )
    delivery_id = await enqueue_delivery(
        db_session, queue_name=queue, message_id=str(uuid4()),
        envelope={"synthetic": True},
    )
    # Seed the persisted sealed state to exercise transport defenses even if
    # unexpected queued data exists; the seal transition itself must reject it.
    row = (await db_session.execute(select(SystemConfig).where(
        SystemConfig.category == MAINTENANCE_CATEGORY,
        SystemConfig.key == MAINTENANCE_KEY,
        SystemConfig.organization_id.is_(None),
    ))).scalar_one()
    row.value_json = {**row.value_json, "phase": "sealed"}
    await db_session.flush()
    assert await claim_deliveries(db_session, queue_name=queue, owner="test") == []
    with pytest.raises(RuntimeMaintenanceSealed):
        await enqueue_delivery(
            db_session, queue_name=queue, message_id=str(uuid4()), envelope={},
        )
    delivery = await db_session.get(WorkDelivery, delivery_id)
    assert delivery is not None and delivery.status == "queued"
    assert state.generation is not None
    await exit_runtime_maintenance(db_session, generation=state.generation)
    claims = await claim_deliveries(db_session, queue_name=queue, owner="test")
    assert [claim.id for claim in claims] == [delivery_id]
