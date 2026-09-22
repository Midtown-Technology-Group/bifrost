"""PostgreSQL polling/leases for the existing consumer outcome contract.

The small message adapter lets both transports use the same retry, poison and
domain-finalization code. It does not emulate AMQP topology or connections.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from aio_pika import IncomingMessage
from sqlalchemy import func, select, update

from src.core.database import get_db_context
from src.models.orm.work_deliveries import WorkDelivery
from src.services.work_delivery_store import (
    DeliveryLease,
    DeliveryOwnershipLost,
    claim_deliveries,
    current_delivery,
    interrupt_delivery,
    interrupt_expired_deliveries,
    recover_interrupted_delivery,
    renew_delivery,
    require_delivery_ownership,
    settle_delivery,
)

if TYPE_CHECKING:
    from src.jobs.rabbitmq import _AbstractConsumer

logger = logging.getLogger(__name__)
POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0


class PostgresMessage:
    """Only the incoming-message fields used by the shared consumer policy."""

    def __init__(self, lease: DeliveryLease):
        self.lease = lease
        self.body = json.dumps(lease.envelope["body"]).encode()
        self.headers = dict(lease.envelope.get("headers") or {})
        self.message_id = lease.message_id
        self.correlation_id = self.headers.get("correlation_id")
        self.redelivered = lease.claim_count > 1
        self.delivery_tag = None
        self.routing_key = lease.queue_name
        self.exchange = None
        self._settlement = "completed"
        self._delay = 0
        self._envelope = lease.envelope
        self._settled = False

    async def stage(self, status: str, headers: dict[str, Any], delay: int = 0) -> None:
        # Verify ownership before a shared poison hook mutates domain state.
        async with get_db_context() as db:
            owned = await renew_delivery(db, self.lease)
            await db.commit()
        if not owned:
            raise DeliveryOwnershipLost(str(self.lease.id))
        self._settlement = status
        self._delay = delay
        self._envelope = {**self.lease.envelope, "headers": headers}

    async def ack(self) -> None:
        if self._settled:
            return
        async with get_db_context() as db:
            # The status was selected by the shared consumer's outcome handler.
            if self._settlement == "queued":
                owned = await settle_delivery(
                    db,
                    self.lease,
                    status="queued",
                    envelope=self._envelope,
                    delay_seconds=self._delay,
                )
            elif self._settlement == "poison":
                owned = await settle_delivery(
                    db, self.lease, status="poison", envelope=self._envelope
                )
            else:
                owned = await settle_delivery(db, self.lease, status="completed")
            await db.commit()
        if not owned:
            raise DeliveryOwnershipLost(str(self.lease.id))
        self._settled = True

    async def nack(self, *, requeue: bool = True) -> None:
        self._settlement = "queued" if requeue else "poison"
        self._delay = 0
        await self.ack()

    async def reject(self, *, requeue: bool = False) -> None:
        await self.nack(requeue=requeue)

    async def interrupt(self) -> None:
        if self._settled:
            return
        async with get_db_context() as db:
            await interrupt_delivery(db, self.lease)
            await db.commit()
        self._settled = True


class PostgresConsumerRunner:
    def __init__(self, consumer: _AbstractConsumer):
        self.consumer = consumer
        self.owner = f"{socket.gethostname()}:{uuid4()}"
        self._poller: asyncio.Task[None] | None = None
        self._pause_requested = asyncio.Event()

    def _poller_cancelled(self, poller: asyncio.Task[None]) -> None:
        if self._poller is poller:
            self._poller = None
        with suppress(asyncio.CancelledError):
            poller.exception()

    async def start(self) -> None:
        if self._poller is not None:
            return
        # Fail startup on a missing migration or unusable database, rather than
        # advertising an idle worker whose background poller never connected.
        async with get_db_context() as db:
            await interrupt_expired_deliveries(db, queue_name=self.consumer.queue_name)
            await db.commit()
        self._pause_requested.clear()
        self._poller = asyncio.create_task(self._poll())

    async def pause(self, timeout: float = 300.0) -> None:
        if self._poller is not None:
            # Exit cooperatively so a lease committed by the current poll
            # iteration always becomes a tracked handler before pause returns.
            poller = self._poller
            self._pause_requested.set()
            done, _ = await asyncio.wait({poller}, timeout=max(0.0, timeout))
            if poller not in done:
                logger.error(
                    "PostgreSQL delivery poller did not pause within %.1fs for %s",
                    timeout,
                    self.consumer.queue_name,
                )
                poller.cancel()
                poller.add_done_callback(self._poller_cancelled)
                raise TimeoutError(
                    f"PostgreSQL delivery poller did not pause for "
                    f"{self.consumer.queue_name}"
                )
            if poller.result() is not None:
                raise RuntimeError("PostgreSQL delivery poller returned a value")
            self._poller = None

    async def stop(self) -> None:
        await self.pause()
        handlers = {task for task in self.consumer._inflight if not task.done()}
        for task in handlers:
            task.cancel()
        if handlers:
            _, pending = await asyncio.wait(handlers, timeout=5.0)
            if pending:
                raise RuntimeError(
                    "PostgreSQL delivery handlers have not surrendered ownership"
                )

    async def _poll(self) -> None:
        while (
            self.consumer._running
            and not self.consumer._draining
            and not self._pause_requested.is_set()
        ):
            try:
                capacity = min(
                    100, self.consumer.prefetch_count - len(self.consumer._inflight)
                )
                if capacity > 0:
                    async with get_db_context() as db:
                        await interrupt_expired_deliveries(
                            db, queue_name=self.consumer.queue_name
                        )
                        await db.commit()
                    await self._recover()
                    async with get_db_context() as db:
                        leases = await claim_deliveries(
                            db,
                            queue_name=self.consumer.queue_name,
                            owner=self.owner,
                            limit=capacity,
                        )
                        await db.commit()
                    for lease in leases:
                        task = asyncio.create_task(
                            self._deliver(PostgresMessage(lease))
                        )
                        self.consumer._inflight.add(task)
                        task.add_done_callback(self.consumer._inflight.discard)
                        task.add_done_callback(self._observe_handler)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed poll never owns work unless its transaction committed;
                # committed orphan claims become visible as interrupted work.
                logger.exception(
                    "PostgreSQL delivery poll failed for %s", self.consumer.queue_name
                )
            try:
                await asyncio.wait_for(
                    self._pause_requested.wait(), timeout=POLL_SECONDS
                )
            except TimeoutError:
                continue

    async def _recover(self) -> None:
        async with get_db_context() as db:
            interrupted = list(
                (
                    await db.execute(
                        select(WorkDelivery.id)
                        .where(
                            WorkDelivery.queue_name == self.consumer.queue_name,
                            WorkDelivery.status == "interrupted",
                            WorkDelivery.available_at <= func.clock_timestamp(),
                        )
                        .order_by(WorkDelivery.available_at, WorkDelivery.id)
                        .limit(100)
                    )
                ).scalars()
            )
        for delivery_id in interrupted:
            async with get_db_context() as db:
                recovered = await recover_interrupted_delivery(db, delivery_id)
                if not recovered:
                    # Malformed or otherwise unowned rows return before the
                    # domain-aware helper can lock the delivery. Back them
                    # off conditionally so one poison envelope cannot occupy
                    # the oldest batch forever; keep it interrupted for
                    # operator inspection and never make it runnable here.
                    await db.execute(
                        update(WorkDelivery)
                        .where(
                            WorkDelivery.id == delivery_id,
                            WorkDelivery.status == "interrupted",
                        )
                        .values(
                            available_at=func.clock_timestamp() + timedelta(seconds=30),
                        )
                    )
                await db.commit()

    @staticmethod
    def _observe_handler(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error(
                "PostgreSQL delivery handler failed: %s",
                type(task.exception()).__name__,
            )

    async def _deliver(self, message: PostgresMessage) -> None:
        handler = asyncio.current_task()
        assert handler is not None
        heartbeat = asyncio.create_task(self._heartbeat(message, handler))
        scope = current_delivery.set(message.lease)
        try:
            if self.consumer._draining:
                await message.nack(requeue=True)
                return
            async with get_db_context() as db:
                await require_delivery_ownership(db)
                await db.commit()
            # The shared policy consumes precisely the fields/ack methods above.
            # Topology and Rabbit channel methods never cross this boundary.
            await self.consumer._process_message_with_ack(
                cast(IncomingMessage, message)
            )
        finally:
            current_delivery.reset(scope)
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, message: PostgresMessage, handler: asyncio.Task) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                async with get_db_context() as db:
                    owned = await renew_delivery(db, message.lease)
                    await db.commit()
                if owned:
                    continue
            except Exception:
                logger.exception("Cannot renew PostgreSQL delivery ownership")
            # Stop on uncertain ownership. The cancellation path records an
            # interruption, never an automatic retry of a possible external effect.
            handler.cancel()
            return

    @staticmethod
    def message(message: IncomingMessage) -> PostgresMessage:
        if not isinstance(message, PostgresMessage):
            raise TypeError("PostgreSQL consumer received a different transport")
        return message
