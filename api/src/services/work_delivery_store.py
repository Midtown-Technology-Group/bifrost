"""PostgreSQL delivery transactions. Callers own commit/rollback.

No connection or transaction remains open while a handler runs. An expired
claim is interrupted, not permission to repeat an unknown external effect.
Domain recovery decides whether a fresh attempt is safe.
"""

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import decrypt_secret, encrypt_secret
from src.models.orm.work_deliveries import WorkDelivery

LEASE_SECONDS = 90
MAX_CLAIM_BATCH = 100


@dataclass(frozen=True)
class DeliveryLease:
    id: UUID
    token: UUID
    queue_name: str
    message_id: str
    envelope: dict[str, Any]
    claim_count: int


def _encrypted(envelope: dict[str, Any]) -> str:
    return encrypt_secret(json.dumps(envelope))


async def enqueue_delivery(
    db: AsyncSession,
    *,
    queue_name: str,
    message_id: str,
    envelope: dict[str, Any],
) -> UUID:
    """Persist alongside domain admission in the caller's transaction.

    An active identity reuses the existing delivery, including interrupted work.
    A deliberate new admission after settlement may use that identity again;
    durable domain idempotency remains the publisher's responsibility.
    """
    if not queue_name or len(queue_name) > 100:
        raise ValueError("queue_name must contain 1..100 characters")
    if not message_id or len(message_id) > 255:
        raise ValueError("message_id must contain 1..255 characters")
    delivery_id = uuid4()
    result = await db.execute(
        insert(WorkDelivery)
        .values(
            id=delivery_id,
            queue_name=queue_name,
            message_id=message_id,
            encrypted_envelope=_encrypted(envelope),
        )
        .on_conflict_do_update(
            index_elements=[WorkDelivery.queue_name, WorkDelivery.message_id],
            index_where=text("status IN ('queued', 'claimed', 'interrupted')"),
            set_={"message_id": WorkDelivery.message_id},
        )
        .returning(WorkDelivery.id)
    )
    return result.scalar_one()


async def claim_deliveries(
    db: AsyncSession,
    *,
    queue_name: str,
    owner: str,
    limit: int = 1,
) -> list[DeliveryLease]:
    if not owner or len(owner) > 255 or not 1 <= limit <= MAX_CLAIM_BATCH:
        raise ValueError("claim requires an owner and a batch size between 1 and 100")
    rows = (
        (
            await db.execute(
                select(WorkDelivery)
                .where(
                    WorkDelivery.queue_name == queue_name,
                    WorkDelivery.status == "queued",
                    WorkDelivery.available_at <= func.clock_timestamp(),
                )
                .order_by(WorkDelivery.available_at, WorkDelivery.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    leases = []
    for row in rows:
        token = uuid4()
        claim_count = row.claim_count + 1
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id == row.id)
            .values(
                status="claimed",
                lease_owner=owner,
                lease_token=token,
                lease_expires_at=func.clock_timestamp()
                + timedelta(seconds=LEASE_SECONDS),
                claim_count=claim_count,
            )
            .execution_options(synchronize_session=False)
        )
        envelope = json.loads(decrypt_secret(row.encrypted_envelope))
        if not isinstance(envelope, dict):
            raise TypeError("delivery envelope must be an object")
        leases.append(
            DeliveryLease(
                row.id,
                token,
                row.queue_name,
                row.message_id,
                envelope,
                claim_count,
            )
        )
    await db.flush()
    return leases


def _owned(lease: DeliveryLease):
    return (
        WorkDelivery.id == lease.id,
        WorkDelivery.status == "claimed",
        WorkDelivery.lease_token == lease.token,
        WorkDelivery.lease_expires_at > func.clock_timestamp(),
    )


async def renew_delivery(db: AsyncSession, lease: DeliveryLease) -> bool:
    result = await db.execute(
        update(WorkDelivery)
        .where(*_owned(lease))
        .values(
            lease_expires_at=func.clock_timestamp() + timedelta(seconds=LEASE_SECONDS),
        )
        .returning(WorkDelivery.id)
    )
    return result.scalar_one_or_none() is not None


async def settle_delivery(
    db: AsyncSession,
    lease: DeliveryLease,
    *,
    status: Literal["completed", "poison", "queued"],
    envelope: dict[str, Any] | None = None,
    delay_seconds: int = 0,
) -> bool:
    """Atomically acknowledge, poison, or schedule a delayed retry.

    False means ownership was lost: the caller must not claim settlement.
    """
    if status not in ("completed", "poison", "queued") or delay_seconds < 0:
        raise ValueError("invalid delivery settlement")
    values: dict[str, Any] = {
        "status": status,
        "lease_owner": None,
        "lease_token": None,
        "lease_expires_at": None,
        "settled_at": None if status == "queued" else func.clock_timestamp(),
    }
    if status == "queued":
        values["available_at"] = func.clock_timestamp() + timedelta(
            seconds=delay_seconds
        )
    if envelope is not None:
        values["encrypted_envelope"] = _encrypted(envelope)
    result = await db.execute(
        update(WorkDelivery)
        .where(*_owned(lease))
        .values(
            **values,
        )
        .returning(WorkDelivery.id)
    )
    return result.scalar_one_or_none() is not None


async def interrupt_expired_deliveries(
    db: AsyncSession, *, limit: int = 100
) -> list[UUID]:
    """Expose abandoned ownership for domain-aware recovery, in bounded batches."""
    if not 1 <= limit <= MAX_CLAIM_BATCH:
        raise ValueError("recovery batch size must be between 1 and 100")
    expired = (
        select(WorkDelivery.id)
        .where(
            WorkDelivery.status == "claimed",
            WorkDelivery.lease_expires_at <= func.clock_timestamp(),
        )
        .order_by(WorkDelivery.lease_expires_at, WorkDelivery.id)
        .limit(limit)
        .with_for_update(
            skip_locked=True,
        )
    )
    result = await db.execute(
        update(WorkDelivery)
        .where(
            WorkDelivery.id.in_(expired),
        )
        .values(
            status="interrupted",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
        .returning(WorkDelivery.id)
    )
    return list(result.scalars())
