"""Real PostgreSQL transaction/ownership tests; no external provider calls."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from src.jobs.rabbitmq import BaseConsumer, RetryableConsumerError
from src.models.orm.work_deliveries import WorkDelivery
from src.services.work_delivery_store import (
    DeliveryOwnershipLost,
    claim_deliveries,
    current_delivery,
    enqueue_delivery,
    interrupt_delivery,
    interrupt_expired_deliveries,
    recover_interrupted_delivery,
    renew_delivery,
    require_delivery_ownership,
    retire_workflow_delivery_for_retry,
    settle_delivery,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class RecordingConsumer(BaseConsumer):
    def __init__(self, queue_name, handler):
        super().__init__(queue_name=queue_name)
        self.handler = handler

    async def process_message(self, body):
        await self.handler(body)


@pytest_asyncio.fixture
async def postgres_transport(monkeypatch, async_session_factory):
    from src.core import database
    from src.jobs import postgres_delivery, rabbitmq

    @asynccontextmanager
    async def session():
        async with async_session_factory() as db:
            yield db

    monkeypatch.setattr(database, "get_db_context", session)
    monkeypatch.setattr(postgres_delivery, "get_db_context", session)
    monkeypatch.setattr(postgres_delivery, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(postgres_delivery, "HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(
        rabbitmq,
        "get_settings",
        lambda: SimpleNamespace(work_delivery_backend="postgres"),
    )
    monkeypatch.setattr(
        rabbitmq.rabbitmq,
        "init_pools",
        AsyncMock(side_effect=AssertionError("Rabbit must remain unused")),
    )
    yield rabbitmq


async def wait_delivery_status(factory, queue, status):
    async with asyncio.timeout(10):
        while True:
            async with factory() as db:
                row = (
                    await db.execute(
                        select(WorkDelivery).where(
                            WorkDelivery.queue_name == queue,
                            WorkDelivery.status == status,
                        )
                    )
                ).scalar_one_or_none()
                if row is not None:
                    return row
            await asyncio.sleep(0.02)


@pytest_asyncio.fixture
async def delivery_queue(async_session_factory):
    queue = f"delivery-test-{uuid4()}"
    yield queue
    async with async_session_factory() as db:
        await db.execute(delete(WorkDelivery).where(WorkDelivery.queue_name == queue))
        await db.commit()


async def enqueue(db, queue, message_id="one"):
    return await enqueue_delivery(
        db,
        queue_name=queue,
        message_id=message_id,
        envelope={"body": {"private_input": "never plaintext"}, "headers": {}},
    )


async def test_acceptance_is_in_callers_transaction(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as publisher:
        discarded = await enqueue(publisher, delivery_queue)
        async with async_session_factory() as observer:
            assert await observer.get(WorkDelivery, discarded) is None
        await publisher.rollback()
    async with async_session_factory() as publisher:
        accepted = await enqueue(publisher, delivery_queue)
        await publisher.commit()
    async with async_session_factory() as observer:
        assert await observer.get(WorkDelivery, discarded) is None
        stored = await observer.get(WorkDelivery, accepted)
        assert stored is not None and stored.status == "queued"
        assert "never plaintext" not in stored.encrypted_envelope


async def test_active_admission_deduplicates(async_session_factory, delivery_queue):
    async with async_session_factory() as db:
        first = await enqueue(db, delivery_queue)
        await db.commit()
    async with async_session_factory() as db:
        assert await enqueue(db, delivery_queue) == first
        await db.commit()
        leases = await claim_deliveries(db, queue_name=delivery_queue, owner="worker")
        assert len(leases) == 1
        assert leases[0].envelope["body"]["private_input"] == "never plaintext"
        await db.commit()
    async with async_session_factory() as db:
        assert await enqueue(db, delivery_queue) == first
        await db.commit()


async def test_concurrent_claims_skip_locked_and_are_bounded(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        for number in range(3):
            await enqueue(db, delivery_queue, str(number))
        await db.commit()
    async with async_session_factory() as first, async_session_factory() as second:
        a = await claim_deliveries(
            first, queue_name=delivery_queue, owner="first", limit=1
        )
        # First claim deliberately holds its transaction open. Second must skip
        # its row rather than block or claim the same work.
        b = await claim_deliveries(
            second, queue_name=delivery_queue, owner="second", limit=2
        )
        assert len(a) == 1 and len(b) == 2
        assert {item.id for item in a}.isdisjoint(item.id for item in b)
        await second.commit()
        await first.commit()


async def test_retry_is_atomic_and_old_owner_cannot_settle(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        await enqueue(db, delivery_queue)
        await db.commit()
        (old,) = await claim_deliveries(db, queue_name=delivery_queue, owner="first")
        await db.commit()
        envelope = {**old.envelope, "headers": {"retry_count": 1}}
        assert await settle_delivery(
            db, old, status="queued", envelope=envelope, delay_seconds=30
        )
        await db.commit()
        assert (
            await claim_deliveries(db, queue_name=delivery_queue, owner="second") == []
        )
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id == old.id)
            .values(
                available_at=func.clock_timestamp() - timedelta(seconds=1),
            )
        )
        await db.commit()
        (new,) = await claim_deliveries(db, queue_name=delivery_queue, owner="second")
        await db.commit()
        assert new.id == old.id and new.token != old.token and new.claim_count == 2
        assert new.envelope["headers"]["retry_count"] == 1
        assert not await renew_delivery(db, old)
        assert not await settle_delivery(db, old, status="completed")
        assert await settle_delivery(db, new, status="completed")
        await db.commit()


async def test_expired_claim_requires_recovery_not_blind_retry(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        await enqueue(db, delivery_queue)
        await db.commit()
        (lease,) = await claim_deliveries(db, queue_name=delivery_queue, owner="lost")
        await db.commit()
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id == lease.id)
            .values(
                lease_expires_at=func.clock_timestamp() - timedelta(seconds=1),
            )
        )
        await db.commit()
        assert not await renew_delivery(db, lease)
        assert not await settle_delivery(db, lease, status="completed")
        assert lease.id in await interrupt_expired_deliveries(db)
        await db.commit()
        assert (
            await claim_deliveries(db, queue_name=delivery_queue, owner="replacement")
            == []
        )
        status = (
            await db.execute(
                select(WorkDelivery.status).where(
                    WorkDelivery.id == lease.id,
                )
            )
        ).scalar_one()
        assert status == "interrupted"
        assert await enqueue(db, delivery_queue) == lease.id
        await db.commit()


async def test_poison_retains_work_and_rejects_repeat_settlement(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        await enqueue(db, delivery_queue)
        await db.commit()
        (lease,) = await claim_deliveries(db, queue_name=delivery_queue, owner="worker")
        await db.commit()
        assert await settle_delivery(db, lease, status="poison")
        await db.commit()
        assert not await settle_delivery(db, lease, status="completed")
    async with async_session_factory() as db:
        stored = await db.get(WorkDelivery, lease.id)
        assert stored is not None and stored.status == "poison"
        assert stored.settled_at is not None and stored.lease_token is None


async def test_recovery_resumes_unstarted_claim_and_fences_old_handler(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        await enqueue(db, delivery_queue)
        await db.commit()
        (old,) = await claim_deliveries(db, queue_name=delivery_queue, owner="lost")
        await db.commit()
        assert await interrupt_delivery(db, old)
        await db.commit()
        assert await recover_interrupted_delivery(db, old.id)
        await db.commit()
        (new,) = await claim_deliveries(
            db, queue_name=delivery_queue, owner="replacement"
        )
        await db.commit()
        scope = current_delivery.set(old)
        try:
            with pytest.raises(DeliveryOwnershipLost):
                await require_delivery_ownership(db)
            await db.rollback()
        finally:
            current_delivery.reset(scope)
        scope = current_delivery.set(new)
        try:
            await require_delivery_ownership(db)
            await db.commit()
        finally:
            current_delivery.reset(scope)
        assert await interrupt_delivery(db, new)
        await db.commit()
        # A handler crossed the start fence. Unknown effects cannot be replayed.
        assert not await recover_interrupted_delivery(db, new.id)
        await db.commit()
        assert (
            await claim_deliveries(db, queue_name=delivery_queue, owner="third") == []
        )


async def test_domain_retry_replaces_dispatch_atomically_and_rejects_late_ack(
    async_session_factory,
):
    execution_id = str(uuid4())
    async with async_session_factory() as db:
        old_id = await enqueue(db, "workflow-executions", execution_id)
        await db.commit()
        (old,) = await claim_deliveries(
            db, queue_name="workflow-executions", owner="old"
        )
        await db.commit()
        try:
            await retire_workflow_delivery_for_retry(db, execution_id)
            rolled_back = await enqueue(db, "workflow-executions", execution_id)
            await db.rollback()
            assert await db.get(WorkDelivery, rolled_back) is None
            assert await renew_delivery(db, old)
            await db.commit()
            await retire_workflow_delivery_for_retry(db, execution_id)
            new_id = await enqueue(db, "workflow-executions", execution_id)
            await db.commit()
            assert new_id != old_id
            assert not await settle_delivery(db, old, status="completed")
            await db.commit()
            (new,) = await claim_deliveries(
                db, queue_name="workflow-executions", owner="new"
            )
            assert new.id == new_id
            await db.commit()
        finally:
            await db.execute(
                delete(WorkDelivery).where(
                    WorkDelivery.queue_name == "workflow-executions",
                    WorkDelivery.message_id == execution_id,
                )
            )
            await db.commit()


@pytest.mark.parametrize(
    "domain_status,active_attempt,expected",
    [
        ("Pending", False, "queued"),
        ("Pending", True, "interrupted"),
        ("Running", False, "interrupted"),
        ("Success", False, "completed"),
    ],
)
async def test_workflow_recovery_obeys_durable_domain_outcome(
    async_session_factory, domain_status, active_attempt, expected
):
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution

    execution_id = uuid4()
    async with async_session_factory() as db:
        db.add(
            Execution(
                id=execution_id,
                workflow_name="delivery-recovery-test",
                executed_by_name="test",
                status=ExecutionStatus(domain_status),
            )
        )
        await db.flush()
        await enqueue(db, "workflow-executions", str(execution_id))
        if active_attempt:
            from src.services.execution.attempts import create_claimed_attempt

            execution = await db.get(Execution, execution_id)
            await create_claimed_attempt(
                db, execution, worker_id="still-running", worker_incarnation_id=uuid4()
            )
        await db.commit()
        leases = await claim_deliveries(
            db, queue_name="workflow-executions", owner="test"
        )
        (lease,) = [item for item in leases if item.message_id == str(execution_id)]
        await db.commit()
        scope = current_delivery.set(lease)
        try:
            await require_delivery_ownership(db)
            await db.commit()
        finally:
            current_delivery.reset(scope)
        assert await interrupt_delivery(db, lease)
        await db.commit()
        try:
            await recover_interrupted_delivery(db, lease.id)
            await db.commit()
            status = (
                await db.execute(
                    select(WorkDelivery.status).where(WorkDelivery.id == lease.id)
                )
            ).scalar_one()
            assert status == expected
        finally:
            await db.execute(delete(WorkDelivery).where(WorkDelivery.id == lease.id))
            await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.commit()


async def test_consumer_completes_through_shared_policy_without_rabbit(
    async_session_factory,
    delivery_queue,
    postgres_transport,
):
    handler = AsyncMock()
    consumer = RecordingConsumer(delivery_queue, handler)
    await postgres_transport.publish_message(delivery_queue, {"id": "one", "value": 42})
    try:
        await consumer.start()
        row = await wait_delivery_status(
            async_session_factory, delivery_queue, "completed"
        )
        assert row.claim_count == 1
        handler.assert_awaited_once_with({"id": "one", "value": 42})
        assert consumer._channel is None
    finally:
        await consumer.drain(deadline=1)


async def test_consumer_retry_is_not_immediately_consumed(
    async_session_factory,
    delivery_queue,
    postgres_transport,
):
    called = asyncio.Event()

    async def handler(body):
        called.set()
        raise RetryableConsumerError("temporary admission pressure")

    consumer = RecordingConsumer(delivery_queue, handler)
    await postgres_transport.publish_message(delivery_queue, {"id": "one"})
    try:
        await consumer.start()
        await asyncio.wait_for(called.wait(), 10)
        row = await wait_delivery_status(
            async_session_factory, delivery_queue, "queued"
        )
        assert row.claim_count == 1
        async with async_session_factory() as db:
            delay = (
                await db.execute(
                    select(
                        WorkDelivery.available_at > func.clock_timestamp(),
                    ).where(WorkDelivery.id == row.id)
                )
            ).scalar_one()
            assert delay
    finally:
        await consumer.drain(deadline=1)


async def test_shutdown_interrupts_running_handler_without_replaying(
    async_session_factory,
    delivery_queue,
    postgres_transport,
):
    started = asyncio.Event()
    effects = []

    async def handler(body):
        effects.append("external effect may have happened")
        started.set()
        await asyncio.Event().wait()

    consumer = RecordingConsumer(delivery_queue, handler)
    await postgres_transport.publish_message(delivery_queue, {"id": "one"})
    try:
        await consumer.start()
        await asyncio.wait_for(started.wait(), 10)
    finally:
        await consumer.drain(deadline=0.05)
    row = await wait_delivery_status(
        async_session_factory, delivery_queue, "interrupted"
    )
    assert row.claim_count == 1 and len(effects) == 1
    async with async_session_factory() as db:
        assert await claim_deliveries(db, queue_name=delivery_queue, owner="new") == []
