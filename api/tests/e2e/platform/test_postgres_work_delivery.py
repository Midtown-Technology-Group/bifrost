"""Real PostgreSQL transaction/ownership tests; no external provider calls."""

from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from src.models.orm.work_deliveries import WorkDelivery
from src.services.work_delivery_store import (
    claim_deliveries,
    enqueue_delivery,
    interrupt_expired_deliveries,
    renew_delivery,
    settle_delivery,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


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
