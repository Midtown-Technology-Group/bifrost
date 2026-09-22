"""PostgreSQL delivery transactions. Callers own commit/rollback.

No connection or transaction remains open while a handler runs. An expired
claim is interrupted, not permission to repeat an unknown external effect.
Domain recovery decides whether a fresh attempt is safe.
"""

import json
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import decrypt_secret, encrypt_secret
from src.models.orm.agent_runs import AgentRun
from src.models.orm.work_deliveries import WorkDelivery
from src.services.runtime_maintenance import (
    reject_if_runtime_maintenance_sealed,
    runtime_claims_sealed,
)

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


class DeliveryOwnershipLost(RuntimeError):
    """The current handler may no longer admit or settle domain work."""


current_delivery: ContextVar[DeliveryLease | None] = ContextVar(
    "current_delivery", default=None
)


async def require_delivery_ownership(db: AsyncSession) -> None:
    """Fence domain admission inside its own transaction; Rabbit is unchanged.

    The row lock serializes with lease expiry/recovery. Call after acquiring the
    domain identity lock and before changing its state or starting effects.
    """
    lease = current_delivery.get()
    if lease is None:
        return
    owned = (
        await db.execute(
            select(WorkDelivery.id).where(*_owned(lease)).with_for_update()
        )
    ).scalar_one_or_none()
    if owned is None:
        raise DeliveryOwnershipLost(str(lease.id))
    await db.execute(
        update(WorkDelivery)
        .where(WorkDelivery.id == lease.id)
        .values(
            started_at=func.coalesce(WorkDelivery.started_at, func.clock_timestamp()),
        )
    )


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
    await reject_if_runtime_maintenance_sealed(db)
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


async def retire_workflow_delivery_for_retry(
    db: AsyncSession, execution_id: str
) -> None:
    """Fence the old dispatch inside an already-authorized domain retry.

    The caller holds the execution advisory lock and commits this retirement,
    replacement enqueue and domain/attempt transition together. A late ack for
    the retired row cannot consume the replacement, even if the child failed
    before its original delivery handler acknowledged dispatch.
    """
    await db.execute(
        update(WorkDelivery)
        .where(
            WorkDelivery.queue_name == "workflow-executions",
            WorkDelivery.message_id == execution_id,
            WorkDelivery.status.in_(["queued", "claimed", "interrupted"]),
        )
        .values(
            status="completed",
            settled_at=func.clock_timestamp(),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
    )


async def claim_deliveries(
    db: AsyncSession,
    *,
    queue_name: str,
    owner: str,
    limit: int = 1,
) -> list[DeliveryLease]:
    if not owner or len(owner) > 255 or not 1 <= limit <= MAX_CLAIM_BATCH:
        raise ValueError("claim requires an owner and a batch size between 1 and 100")
    if await runtime_claims_sealed(db):
        return []
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
        values["started_at"] = None
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
    db: AsyncSession, *, limit: int = 100, queue_name: str | None = None
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
    if queue_name is not None:
        expired = expired.where(WorkDelivery.queue_name == queue_name)
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


async def interrupt_delivery(db: AsyncSession, lease: DeliveryLease) -> bool:
    """Surrender this exact owner, including an already expired lease."""
    result = await db.execute(
        update(WorkDelivery)
        .where(
            WorkDelivery.id == lease.id,
            WorkDelivery.status == "claimed",
            WorkDelivery.lease_token == lease.token,
        )
        .values(
            status="interrupted",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
        .returning(WorkDelivery.id)
    )
    return result.scalar_one_or_none() is not None


def _bounded_recovery_retry(
    envelope: dict[str, Any],
) -> tuple[bool, dict[str, Any], int, int]:
    """Advance the existing broker retry header without creating a new identity."""
    from src.jobs.rabbitmq import DEFAULT_RETRY_DELAYS_SECONDS

    headers = dict(envelope.get("headers") or {})
    try:
        retry_count = int(headers.get("x-retry-count") or 0)
    except (TypeError, ValueError):
        retry_count = 0
    retry_count = max(0, retry_count)
    enqueued_at = headers.get("x-enqueued-at")
    if enqueued_at:
        try:
            elapsed = (
                datetime.now(UTC) - datetime.fromisoformat(str(enqueued_at))
            ).total_seconds()
        except (TypeError, ValueError):
            elapsed = 0
        if elapsed >= 3600:
            return False, envelope, 0, retry_count
    if retry_count >= len(DEFAULT_RETRY_DELAYS_SECONDS):
        return False, envelope, 0, retry_count
    next_retry = retry_count + 1
    delay = DEFAULT_RETRY_DELAYS_SECONDS[next_retry - 1]
    headers["x-retry-count"] = next_retry
    headers["x-last-error"] = "delivery lease expired before durable outcome"
    headers["x-retry-delay-seconds"] = delay
    return True, {**envelope, "headers": headers}, delay, retry_count


async def _settle_interrupted(
    db: AsyncSession,
    row: WorkDelivery,
    *,
    status: Literal["queued", "completed", "poison"],
    envelope: dict[str, Any] | None = None,
    delay_seconds: int = 0,
) -> None:
    values: dict[str, Any] = {
        "status": status,
        "lease_owner": None,
        "lease_token": None,
        "lease_expires_at": None,
        "settled_at": None if status == "queued" else func.clock_timestamp(),
        "available_at": (
            func.clock_timestamp() + timedelta(seconds=delay_seconds)
            if status == "queued"
            else func.clock_timestamp()
        ),
    }
    if status == "queued":
        values["started_at"] = None
    if envelope is not None:
        values["encrypted_envelope"] = _encrypted(envelope)
    await db.execute(
        update(WorkDelivery).where(WorkDelivery.id == row.id).values(**values)
    )


async def _summary_backfill_accounted(
    db: AsyncSession, envelope: dict[str, Any], run_id: UUID, queue: str
) -> bool:
    if queue != "agent-summarization-backfill":
        return True
    raw_job_id = envelope["body"].get("backfill_job_id")
    if not raw_job_id:
        return True
    try:
        job_id = UUID(str(raw_job_id))
    except ValueError:
        return False
    from src.models.orm.summary_backfill_job import SummaryBackfillJob

    processed = await db.scalar(
        select(SummaryBackfillJob.processed_run_ids).where(
            SummaryBackfillJob.id == job_id
        )
    )
    # A deleted parent is already outside the accounting contract; do not
    # hold a delivery open forever for a row that cannot be updated.
    return processed is None or str(run_id) in set(processed or [])


async def recover_interrupted_delivery(db: AsyncSession, delivery_id: UUID) -> bool:
    """Recover an interrupted delivery under its domain's lock contract.

    Domain locks are acquired before the exact delivery row lock. Derived LLM
    queues may retry the same encrypted envelope only within the existing
    retry-header budget. An exhausted summary is durably failed; a backfill
    summary is requeued once more so its idempotent parent counter is recorded.
    Agent execution loss is terminalized as worker_lost without replaying tools.
    """
    identity = (
        await db.execute(
            select(
                WorkDelivery.queue_name,
                WorkDelivery.message_id,
                WorkDelivery.encrypted_envelope,
            ).where(
                WorkDelivery.id == delivery_id, WorkDelivery.status == "interrupted"
            )
        )
    ).one_or_none()
    if identity is None:
        return False
    queue, message_id, encrypted_envelope = identity
    derived_queues = {
        "agent-summarization",
        "agent-summarization-backfill",
        "agent-tuning-chat",
    }
    envelope: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    if queue in derived_queues:
        try:
            envelope = json.loads(decrypt_secret(encrypted_envelope))
            body = envelope.get("body") if isinstance(envelope, dict) else None
            run_id = UUID(str(body.get("run_id"))) if isinstance(body, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if run_id is None:
            return False
        locked = (
            await db.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:identity))"),
                {"identity": f"bifrost:agent-run:{run_id}"},
            )
        ).scalar_one()
        if not locked:
            return False
        domain = await db.get(AgentRun, run_id, with_for_update={"of": AgentRun})
        if domain is None:
            return False
    else:
        domain = None
        run_id = None

    active = None
    if queue in {"workflow-executions", "agent-runs"}:
        try:
            domain_id = UUID(message_id)
        except ValueError:
            return False
        namespace = (
            "workflow-execution" if queue == "workflow-executions" else "agent-run"
        )
        locked = (
            await db.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:identity))"),
                {"identity": f"bifrost:{namespace}:{domain_id}"},
            )
        ).scalar_one()
        if not locked:
            return False
        if queue == "workflow-executions":
            from src.models.orm.executions import Execution, WorkflowExecutionAttempt

            domain = await db.get(Execution, domain_id, with_for_update=True)
            active = (
                await db.execute(
                    select(WorkflowExecutionAttempt.id)
                    .where(
                        WorkflowExecutionAttempt.execution_id == domain_id,
                        WorkflowExecutionAttempt.completed_at.is_(None),
                        (
                            WorkflowExecutionAttempt.claim_token.is_not(None)
                            | WorkflowExecutionAttempt.status.not_in(
                                ["dispatching", "published"]
                            )
                        ),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        else:
            from src.models.orm.execution_attempts import ExecutionAttempt

            domain = await db.get(AgentRun, domain_id, with_for_update={"of": AgentRun})
            active = (
                await db.execute(
                    select(ExecutionAttempt.id)
                    .where(
                        ExecutionAttempt.logical_job_type == "agent_run",
                        ExecutionAttempt.logical_job_id == domain_id,
                        ExecutionAttempt.completed_at.is_(None),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()

    conversation = None
    if queue == "agent-tuning-chat":
        from src.models.orm.agent_run_flag_conversations import (
            AgentRunFlagConversation,
        )

        conversation = await db.scalar(
            select(AgentRunFlagConversation)
            .where(AgentRunFlagConversation.run_id == run_id)
            .with_for_update()
        )

    row = (
        await db.execute(
            select(WorkDelivery)
            .where(WorkDelivery.id == delivery_id, WorkDelivery.status == "interrupted")
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return False

    disposition: Literal["queued", "completed", "poison"] | None = (
        "queued" if row.started_at is None else None
    )
    next_envelope = envelope
    delay_seconds = 0
    domain_loss_terminalized = False

    if queue == "workflow-executions" and domain is not None:
        status = domain.status
        if status == "Pending":
            disposition = "queued" if active is None else None
        elif status in {
            "Success",
            "Failed",
            "Timeout",
            "CompletedWithErrors",
            "Cancelled",
        }:
            disposition = "completed"
        else:
            disposition = None
    elif queue == "agent-runs" and isinstance(domain, AgentRun):
        status = domain.status
        if status in {"Pending", "queued"}:
            disposition = "queued" if active is None else None
        elif status in {"running", "cancelling"}:
            from src.services.execution_attempts import (
                transition_execution_attempt,
            )

            reason = "agent delivery lease expired; worker_lost"
            await transition_execution_attempt(
                db,
                logical_job_type="agent_run",
                logical_job_id=domain.id,
                status="worker_lost",
                failure_code="worker_lost",
                failure_message=reason,
            )
            domain.status = "failed"
            domain.error = reason
            domain.completed_at = await db.scalar(select(func.clock_timestamp()))
            disposition = None
            domain_loss_terminalized = True
        elif status in {
            "Success",
            "Failed",
            "Timeout",
            "CompletedWithErrors",
            "Cancelled",
            "completed",
            "failed",
            "cancelled",
            "timeout",
        }:
            disposition = "completed"
        else:
            disposition = None
    elif queue in {"agent-summarization", "agent-summarization-backfill"}:
        assert envelope is not None and body is not None and run_id is not None
        if not isinstance(domain, AgentRun):
            return False
        previous_id = domain.summary_delivery_id
        previous_owner_blocks = False
        if previous_id is not None and previous_id != row.id:
            previous_status = await db.scalar(
                select(WorkDelivery.status).where(WorkDelivery.id == previous_id)
            )
            if previous_status in {
                "queued",
                "claimed",
                "interrupted",
            } or domain.summary_status not in {"completed", "failed", "skipped"}:
                previous_owner_blocks = True
        if not previous_owner_blocks:
            accounted = await _summary_backfill_accounted(db, envelope, run_id, queue)
            if domain.summary_status in {"completed", "failed", "skipped"}:
                disposition = "completed" if accounted else "queued"
            elif row.started_at is None:
                disposition = "queued"
            else:
                allowed, next_envelope, delay_seconds, _ = _bounded_recovery_retry(
                    envelope
                )
                if allowed:
                    disposition = "queued"
                else:
                    domain.summary_status = "failed"
                    domain.summary_delivery_id = row.id
                    domain.summary_error = (
                        "summary delivery recovery exhausted before a durable "
                        "LLM outcome was recorded"
                    )
                    # A backfill must run its idempotent parent accounting path
                    # even after the summary itself is durably failed.
                    disposition = "queued" if not accounted else "poison"
                    delay_seconds = 0
    elif queue == "agent-tuning-chat":
        assert envelope is not None and body is not None and run_id is not None
        if not isinstance(domain, AgentRun):
            return False
        delivery_key = str(row.id)
        assistant_done = bool(
            conversation is not None
            and any(
                isinstance(message, dict)
                and message.get("kind") == "assistant"
                and message.get("delivery_id") == delivery_key
                for message in (conversation.messages or [])
            )
        )
        if assistant_done:
            disposition = "completed"
        elif row.started_at is None:
            disposition = "queued"
        else:
            allowed, next_envelope, delay_seconds, _ = _bounded_recovery_retry(envelope)
            disposition = "queued" if allowed else "poison"

    if disposition is None:
        # Running agent loss remains visible as interrupted until a later
        # pass observes the now-terminal domain and settles the transport row.
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id == row.id)
            .values(
                available_at=func.clock_timestamp() + timedelta(seconds=30),
            )
        )
        return domain_loss_terminalized
    if disposition == "poison" and next_envelope is not None:
        headers = dict(next_envelope.get("headers") or {})
        headers["x-poison-reason"] = "delivery recovery retry budget exhausted"
        next_envelope = {**next_envelope, "headers": headers}
    await _settle_interrupted(
        db,
        row,
        status=disposition,
        envelope=next_envelope,
        delay_seconds=delay_seconds,
    )
    return True
