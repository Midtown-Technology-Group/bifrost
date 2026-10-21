"""Durable runtime-maintenance fencing against real PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from src.models.orm.config import SystemConfig
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
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
    runtime_drain_counts,
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


async def test_ordinary_deploy_can_seal_twice_without_consuming_reservations(db_session):
    now = datetime.now(UTC)
    executions = [
        Execution(
            id=uuid4(), workflow_name="maintenance-reservation", executed_by_name="test",
            status=status, scheduled_at=now + timedelta(days=1),
            completed_at=now if status == ExecutionStatus.CANCELLED else None,
        )
        for status in (ExecutionStatus.SCHEDULED, ExecutionStatus.CANCELLED)
    ]
    db_session.add_all(executions)
    await db_session.flush()
    attempts = [
        WorkflowExecutionAttempt(
            id=uuid4(), execution_id=execution.id, attempt_number=1,
            status="dispatching", phase="dispatch",
        )
        for execution in executions
    ]
    db_session.add_all(attempts)
    await db_session.flush()
    before = [(row.id, row.status, row.phase, row.completed_at) for row in attempts]
    # This is the old ordinary-deploy predicate: both untouched reservations
    # falsely count as active and prevent a seal forever.
    assert await db_session.scalar(select(func.count()).select_from(
        WorkflowExecutionAttempt
    ).where(WorkflowExecutionAttempt.completed_at.is_(None))) == 2

    # The recovery release must leave the NEXT ordinary release usable too.
    for _ in range(2):
        state = await enter_runtime_maintenance(
            db_session, requested_by="release@example.com", reason="ordinary deploy"
        )
        assert state.generation is not None
        sealed, counts = await seal_runtime_maintenance(
            db_session, generation=state.generation
        )
        assert counts.workflow_attempts == 0
        assert counts.drained is True
        assert sealed.phase == "sealed"
        with pytest.raises(RuntimeMaintenanceSealed):
            await enqueue_delivery(
                db_session, queue_name="workflow-executions",
                message_id=str(uuid4()), envelope={},
            )
        await exit_runtime_maintenance(db_session, generation=state.generation)
        for attempt in attempts:
            await db_session.refresh(attempt)
        assert [(row.id, row.status, row.phase, row.completed_at) for row in attempts] == before
        for execution in executions:
            await db_session.refresh(execution)
        assert [row.status for row in executions] == [
            ExecutionStatus.SCHEDULED, ExecutionStatus.CANCELLED
        ]

    # Time crossing the due boundary must make the same reservation block again.
    executions[0].scheduled_at = now - timedelta(seconds=1)
    await db_session.flush()
    assert (await runtime_drain_counts(db_session)).workflow_attempts == 1


@pytest.mark.parametrize("evidence", [
    "due", "missing_schedule", "pending", "running", "cancelled_without_completion",
    "published", "claimed", "started", "worker_id", "worker_incarnation_id",
    "process_id", "heartbeat_at", "failure_code", "failure_phase", "phase",
])
async def test_active_or_ambiguous_attempt_still_prevents_sealing(db_session, evidence):
    now = datetime.now(UTC)
    execution = Execution(
        id=uuid4(), workflow_name="maintenance-active", executed_by_name="test",
        status=ExecutionStatus.SCHEDULED, scheduled_at=now + timedelta(days=1),
    )
    if evidence == "due":
        execution.scheduled_at = now - timedelta(seconds=1)
    elif evidence == "missing_schedule":
        execution.scheduled_at = None
    elif evidence in {"pending", "running", "cancelled_without_completion"}:
        execution.status = {
            "pending": ExecutionStatus.PENDING,
            "running": ExecutionStatus.RUNNING,
            "cancelled_without_completion": ExecutionStatus.CANCELLED,
        }[evidence]
    db_session.add(execution)
    await db_session.flush()
    attempt = WorkflowExecutionAttempt(
        id=uuid4(), execution_id=execution.id, attempt_number=1,
        status="dispatching", phase="dispatch",
    )
    if evidence in {"published", "claimed", "started"}:
        attempt.status = "published"
        attempt.phase = "queue"
        attempt.published_at = now
        if evidence in {"claimed", "started"}:
            attempt.status = "claimed" if evidence == "claimed" else "running"
            attempt.phase = "claim" if evidence == "claimed" else "execution"
            attempt.claim_token = uuid4()
            attempt.claimed_at = now
        if evidence == "started":
            attempt.started_at = now
    elif evidence in {"worker_id", "process_id", "failure_code"}:
        setattr(attempt, evidence, "retained-evidence")
    elif evidence == "worker_incarnation_id":
        attempt.worker_incarnation_id = uuid4()
    elif evidence == "heartbeat_at":
        attempt.heartbeat_at = now
    elif evidence == "failure_phase":
        attempt.failure_phase = "dispatch"
    elif evidence == "phase":
        attempt.phase = "queue"
    db_session.add(attempt)
    await db_session.flush()
    state = await enter_runtime_maintenance(
        db_session, requested_by="release@example.com", reason="active work refusal"
    )
    assert state.generation is not None
    observed, counts = await seal_runtime_maintenance(db_session, generation=state.generation)
    assert counts.workflow_attempts >= 1
    assert counts.drained is False
    assert observed.phase == "draining"
    assert (await runtime_drain_counts(db_session)).workflow_attempts >= 1


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
