"""Durable runtime-maintenance fencing against real PostgreSQL."""

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from src.models.orm.config import SystemConfig
from src.services.runtime_maintenance import (
    MAINTENANCE_CATEGORY,
    MAINTENANCE_KEY,
    RuntimeMaintenanceActive,
    RuntimeMaintenanceError,
    acquire_runtime_maintenance_lock,
    enter_runtime_maintenance,
    exit_runtime_maintenance,
    read_runtime_maintenance_state,
)
from src.services.scheduler_diagnostics import start_scheduler_run

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
    claim_session = async_session_factory()
    await acquire_runtime_maintenance_lock(claim_session, shared=True)

    async def enter():
        async with async_session_factory() as db:
            state = await enter_runtime_maintenance(
                db, requested_by="release@example.com", reason="claim race"
            )
            await db.commit()
            return state

    entering = asyncio.create_task(enter())
    await asyncio.sleep(0.05)
    assert entering.done() is False

    await claim_session.commit()
    await claim_session.close()
    state = await asyncio.wait_for(entering, timeout=2)
    assert state.phase == "draining"


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
