"""Native claim boundary, not Rust SQL or workflow-runtime acceptance.

Use real migrated PostgreSQL and the actual consumer admission method. An inert
worker identity replaces pool startup; no Redis, broker, child or runtime runs.
The dedicated queue prevents unrelated workers from consuming these test rows.
"""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.work_deliveries import WorkDelivery
from src.services.execution.attempts import create_claimed_attempt
from src.services.work_delivery_store import (
    DeliveryOwnershipLost,
    claim_deliveries,
    current_delivery,
    enqueue_delivery,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def claim_case(async_session_factory, monkeypatch):
    from src.core import database

    execution_id = uuid4()
    queue = f"workflow-claim-seam-{uuid4()}"
    delivery_id = None

    # Keep production get_db_context's success commit and exception rollback.
    # Only its connection factory changes to this real native test database.
    monkeypatch.setattr(database, "get_session_factory", lambda: async_session_factory)
    # Only the two identity fields used by the actual admission method exist.
    # Calling a pool/runtime method here must fail, rather than start a process.
    consumer = object.__new__(WorkflowExecutionConsumer)
    consumer._pool = SimpleNamespace(
        worker_id="native-claim-seam", worker_incarnation_id=uuid4()
    )
    try:
        async with async_session_factory() as db:
            assert await db.scalar(
                text("SELECT to_regclass('public.runtime_sessions') IS NULL")
            )
            db.add(
                Execution(
                    id=execution_id,
                    workflow_name="native-claim-seam",
                    executed_by_name="test",
                    status=ExecutionStatus.PENDING,
                    attempt_tracking_version="v1",
                )
            )
            await db.flush()
            delivery_id = await enqueue_delivery(
                db,
                queue_name=queue,
                message_id=str(execution_id),
                envelope={"body": {"execution_id": str(execution_id)}, "headers": {}},
            )
            await db.commit()
            (lease,) = await claim_deliveries(db, queue_name=queue, owner="native-seam")
            await db.commit()
        yield SimpleNamespace(
            execution_id=execution_id,
            delivery_id=delivery_id,
            lease=lease,
            consumer=consumer,
        )
    finally:
        async with async_session_factory() as db:
            if delivery_id is not None:
                await db.execute(
                    delete(WorkDelivery).where(WorkDelivery.id == delivery_id)
                )
            await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.commit()


async def _claim(case, lease=None):
    scope = current_delivery.set(lease if lease is not None else case.lease)
    try:
        return await case.consumer._claim_durable_execution(str(case.execution_id))
    finally:
        current_delivery.reset(scope)


@pytest.mark.parametrize(
    "initial_status", [ExecutionStatus.PENDING, ExecutionStatus.SCHEDULED]
)
async def test_native_claim_exposes_running_before_typed_start(
    async_session_factory, claim_case, initial_status
):
    case = claim_case
    async with async_session_factory() as db:
        await db.execute(
            update(Execution)
            .where(Execution.id == case.execution_id)
            .values(status=initial_status)
        )
        await db.commit()
    token = await _claim(case)
    assert token is not None
    async with async_session_factory() as observer:
        execution = await observer.get(Execution, case.execution_id)
        attempt = (
            await observer.execute(
                select(WorkflowExecutionAttempt).where(
                    WorkflowExecutionAttempt.execution_id == case.execution_id
                )
            )
        ).scalar_one()
        delivery = await observer.get(WorkDelivery, case.delivery_id)
        assert execution.status == ExecutionStatus.RUNNING
        assert execution.started_at is None and execution.completed_at is None
        assert attempt.status == "claimed" and attempt.phase == "claim"
        assert attempt.started_at is None and attempt.completed_at is None
        assert attempt.claim_token == UUID(token)
        assert (
            attempt.worker_incarnation_id == case.consumer._pool.worker_incarnation_id
        )
        assert attempt.attempt_number == 1
        assert delivery.status == "claimed" and delivery.lease_token == case.lease.token
        assert delivery.started_at is not None
        first_delivery_start = delivery.started_at
    # A duplicate with the still-current transport fence does not create attempt2
    # or advance the immutable first-handler clock.
    assert await _claim(case) is None
    async with async_session_factory() as observer:
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(WorkflowExecutionAttempt)
                .where(WorkflowExecutionAttempt.execution_id == case.execution_id)
            )
            == 1
        )
        assert (
            await observer.get(WorkDelivery, case.delivery_id)
        ).started_at == first_delivery_start


@pytest.mark.parametrize("lost", ["expired", "wrong-token"])
async def test_native_claim_rejects_lost_delivery_before_domain_mutation(
    async_session_factory, claim_case, lost
):
    case = claim_case
    lease = case.lease
    if lost == "expired":
        async with async_session_factory() as db:
            await db.execute(
                update(WorkDelivery)
                .where(WorkDelivery.id == case.delivery_id)
                .values(lease_expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )
            await db.commit()
    else:
        lease = replace(lease, token=uuid4())
    with pytest.raises(DeliveryOwnershipLost):
        await _claim(case, lease)
    async with async_session_factory() as observer:
        assert (
            await observer.get(Execution, case.execution_id)
        ).status == ExecutionStatus.PENDING
        assert (await observer.get(WorkDelivery, case.delivery_id)).started_at is None
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(WorkflowExecutionAttempt)
                .where(WorkflowExecutionAttempt.execution_id == case.execution_id)
            )
            == 0
        )


async def test_native_claim_failure_rolls_back_transport_started_marker(
    async_session_factory, claim_case
):
    case = claim_case
    async with async_session_factory() as db:
        execution = await db.get(Execution, case.execution_id)
        active = await create_claimed_attempt(
            db, execution, worker_id="existing-claim", worker_incarnation_id=uuid4()
        )
        await db.commit()
    with pytest.raises(ValueError, match="execution already has an active attempt"):
        await _claim(case)
    async with async_session_factory() as observer:
        assert (
            await observer.get(Execution, case.execution_id)
        ).status == ExecutionStatus.PENDING
        assert (await observer.get(WorkDelivery, case.delivery_id)).started_at is None
        attempts = (
            (
                await observer.execute(
                    select(WorkflowExecutionAttempt).where(
                        WorkflowExecutionAttempt.execution_id == case.execution_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(attempts) == 1 and attempts[0].id == active.attempt_id
        assert attempts[0].claim_token == active.claim_token
        assert attempts[0].status == "claimed" and attempts[0].started_at is None
