"""Operational CLI for RabbitMQ poison queues.

Run inside the API/worker environment:

    python -m src.jobs.dlq_cli inspect workflow-executions --limit 10
    python -m src.jobs.dlq_cli replay workflow-executions --limit 5 --dry-run
    python -m src.jobs.dlq_cli discard workflow-executions --limit 5 --reason "bad payload"
    python -m src.jobs.dlq_cli reconcile-discard workflow-executions \
        --message-id <id> --execution-id <uuid> --expected-reason <reason> \
        --actor <identity> --reason <operator-reason> --dry-run

When ``BIFROST_WORK_DELIVERY_BACKEND=postgres`` is selected, ``status`` and
``inspect`` read encrypted delivery metadata. ``reconcile`` accepts one exact
interrupted delivery id for domain-owned recovery; ``discard`` only accepts an
exact interrupted id already proven terminal. Generic PostgreSQL replay is
refused because a fresh domain attempt identity is required.
"""

import argparse
import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import aio_pika
from sqlalchemy import func, select

from src.config import get_settings
from src.jobs.execution_policy import broker_execution_policies
from src.jobs.rabbitmq import _message_headers

logger = logging.getLogger(__name__)
MAX_REPLAY_COUNT = 3


def _postgres_row(row: Any) -> dict[str, Any]:
    """Return transport metadata without decrypting the delivery envelope."""
    lease_expires_at = row.lease_expires_at
    return {
        "backend": "postgres",
        "queue": row.queue_name,
        "delivery_id": str(row.id),
        "message_id": row.message_id,
        "status": row.status,
        "claim_count": row.claim_count,
        "created_at": row.created_at,
        "available_at": row.available_at,
        "started_at": row.started_at,
        "settled_at": row.settled_at,
        "lease_owner": row.lease_owner,
        "lease_expires_at": lease_expires_at,
        "lease_expired": bool(
            row.status == "claimed"
            and lease_expires_at is not None
            and lease_expires_at <= datetime.now(UTC)
        ),
    }


async def postgres_status(queue: str | None = None) -> list[dict[str, Any]]:
    """Summarize delivery states, including durable package control commands."""
    from src.core.database import get_db_context
    from src.models.orm.work_deliveries import WorkDelivery
    from src.models.orm.worker_control_commands import WorkerControlCommand

    if queue is not None:
        _validate_queue(queue)
    async with get_db_context() as db:
        statement = select(
            WorkDelivery.queue_name,
            WorkDelivery.status,
            func.count().label("count"),
        ).group_by(WorkDelivery.queue_name, WorkDelivery.status)
        if queue is not None:
            statement = statement.where(WorkDelivery.queue_name == queue)
        result = await db.execute(
            statement.order_by(WorkDelivery.queue_name, WorkDelivery.status)
        )
        package_result = None
        if queue in (None, "package-installations"):
            package_result = await db.execute(
                select(
                    WorkerControlCommand.status,
                    func.count().label("count"),
                )
                .where(WorkerControlCommand.action == "package_install")
                .group_by(WorkerControlCommand.status)
                .order_by(WorkerControlCommand.status)
            )
    rows = [
        {
            "backend": "postgres",
            "queue": row[0],
            "status": row[1],
            "count": row[2],
            "source": "work_deliveries",
        }
        for row in result.all()
    ]
    if package_result is not None:
        rows.extend(
            {
                "backend": "postgres",
                "queue": "package-installations",
                "status": row[0],
                "count": row[1],
                "source": "worker_control_commands",
            }
            for row in package_result.all()
        )
    return rows


async def postgres_inspect(
    queue: str, limit: int, status: str | None = None
) -> list[dict[str, Any]]:
    """Inspect PostgreSQL transport metadata; encrypted bodies stay opaque."""
    from src.core.database import get_db_context
    from src.models.orm.work_deliveries import WorkDelivery

    _validate_queue(queue)
    if not 1 <= limit <= 100:
        raise ValueError("inspect limit must be between 1 and 100")
    allowed_statuses = {"queued", "claimed", "completed", "poison", "interrupted"}
    if status not in {None, "all", *allowed_statuses}:
        raise ValueError(f"unknown PostgreSQL delivery status: {status}")
    async with get_db_context() as db:
        statement = select(WorkDelivery).where(WorkDelivery.queue_name == queue)
        if status is None:
            statement = statement.where(
                WorkDelivery.status.in_(("poison", "interrupted"))
            )
        elif status != "all":
            statement = statement.where(WorkDelivery.status == status)
        rows = list(
            (
                await db.execute(
                    statement.order_by(WorkDelivery.created_at, WorkDelivery.id).limit(
                        limit
                    )
                )
            )
            .scalars()
        )
    return [_postgres_row(row) for row in rows]


async def _postgres_delivery(db: Any, delivery_id: str, queue: str | None):
    from src.models.orm.work_deliveries import WorkDelivery

    try:
        parsed_id = UUID(delivery_id)
    except ValueError as exc:
        raise ValueError("delivery-id must be a UUID") from exc
    # Recovery itself acquires the domain advisory lock before the delivery
    # row lock. Do not pre-lock this row or invert that order here.
    statement = select(WorkDelivery).where(WorkDelivery.id == parsed_id)
    row = (await db.execute(statement)).scalar_one_or_none()
    if row is None:
        raise ValueError(f"PostgreSQL delivery not found: {delivery_id}")
    if queue is not None and row.queue_name != queue:
        raise ValueError(
            f"delivery {delivery_id} belongs to {row.queue_name!r}, not {queue!r}"
        )
    return row


async def _record_postgres_disposition(
    db: Any,
    row: Any,
    *,
    action: str,
    actor: str,
    reason: str,
) -> None:
    """Add the operator receipt to the same transaction as delivery mutation.

    Hash the canonical body privately; never return its decrypted contents to
    the operator command's output.
    """
    from src.core.security import decrypt_secret
    from src.models.orm.poison_message_dispositions import PoisonMessageDisposition

    envelope = json.loads(decrypt_secret(row.encrypted_envelope))
    body = envelope.get("body")
    headers = dict(envelope.get("headers") or {})
    canonical_body = json.dumps(
        body, sort_keys=True, separators=(",", ":"), default=str
    ).encode()

    db.add(
        PoisonMessageDisposition(
            queue_name=row.queue_name,
            message_id=row.message_id,
            idempotency_key=headers.get("x-idempotency-key"),
            action=action,
            actor=actor[:255],
            reason=reason[:2000],
            retry_count=int(headers.get("x-retry-count") or 0),
            replay_count=int(headers.get("x-replayed-count") or 0),
            body_sha256=hashlib.sha256(canonical_body).hexdigest(),
        )
    )
    await db.flush()


async def postgres_reconcile(
    queue: str | None,
    *,
    delivery_id: str,
    dry_run: bool,
    actor: str,
    reason: str,
) -> list[dict[str, Any]]:
    """Apply or preview exact interrupted-delivery recovery.

    Recovery remains domain-owned by ``recover_interrupted_delivery``.  A
    successful apply and its operator receipt commit together; uncertain
    recovery is rolled back and reported instead of being silently requeued.
    """
    from src.core.database import get_db_context
    from src.services.work_delivery_store import recover_interrupted_delivery

    actor, reason = _validate_audit_metadata(actor=actor, reason=reason)
    async with get_db_context() as db:
        row = await _postgres_delivery(db, delivery_id, queue)
        before = _postgres_row(row)
        if row.status != "interrupted":
            raise RuntimeError(
                f"refusing recovery for {delivery_id}: status is {row.status!r}; "
                "only interrupted deliveries have a domain recovery policy"
            )
        recovered = await recover_interrupted_delivery(db, row.id)
        await db.refresh(row)
        after = _postgres_row(row)
        if dry_run or not recovered:
            await db.rollback()
            if not recovered:
                after["recovery"] = "refused"
                after["actionable"] = (
                    "domain state or an advisory lock prevented safe recovery; "
                    "retry after the owning domain transition is terminal"
                )
            else:
                after["recovery"] = "would_apply"
            return [{"before": before, "after": after}]
        await _record_postgres_disposition(
            db,
            row,
            action="reconcile",
            actor=actor,
            reason=reason,
        )
        await db.commit()
        after["recovery"] = "applied"
        after["audit"] = "poison_message_dispositions"
        return [{"before": before, "after": after}]


async def postgres_discard(
    queue: str | None,
    *,
    delivery_id: str,
    dry_run: bool,
    actor: str,
    reason: str,
) -> list[dict[str, Any]]:
    """Discard only an interrupted delivery already proven terminal.

    A poison row cannot be discarded from transport state alone: doing so
    could leave its workflow or agent domain permanently non-terminal.  The
    command therefore refuses poison rows and requires domain recovery to
    prove an interrupted row is already terminal.
    """
    from src.core.database import get_db_context
    from src.services.work_delivery_store import recover_interrupted_delivery

    actor, reason = _validate_audit_metadata(actor=actor, reason=reason)
    async with get_db_context() as db:
        row = await _postgres_delivery(db, delivery_id, queue)
        before = _postgres_row(row)
        if row.status == "poison":
            await db.rollback()
            raise RuntimeError(
                "refusing PostgreSQL poison discard: domain-aware finalization is "
                "required; use the owning workflow/agent recovery path"
            )
        if row.status != "interrupted":
            await db.rollback()
            raise RuntimeError(
                f"refusing discard for {delivery_id}: status is {row.status!r}; "
                "only interrupted deliveries can be domain-reconciled"
            )
        recovered = await recover_interrupted_delivery(db, row.id)
        await db.refresh(row)
        after = _postgres_row(row)
        if not recovered or row.status != "completed":
            await db.rollback()
            raise RuntimeError(
                "refusing PostgreSQL interrupted discard: domain outcome is not "
                "terminal; use reconcile after the domain owner settles it"
            )
        if dry_run:
            await db.rollback()
            after["discard"] = "would_apply"
            return [{"before": before, "after": after}]
        await _record_postgres_disposition(
            db,
            row,
            action="discard",
            actor=actor,
            reason=reason,
        )
        await db.commit()
        after["discard"] = "applied"
        after["audit"] = "poison_message_dispositions"
        return [{"before": before, "after": after}]


async def postgres_replay(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    raise RuntimeError(
        "PostgreSQL replay is unavailable: no generic replay is domain-safe. "
        "Use the owning workflow retry or agent recovery path to create a new "
        "attempt identity; Rabbit replay remains available when Rabbit is selected."
    )


def _validate_audit_metadata(*, actor: str, reason: str) -> tuple[str, str]:
    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise ValueError("actor and reason are required for poison-message mutations")
    return actor, reason


def _validate_queue(queue: str, *, replaying: bool = False) -> None:
    policy = broker_execution_policies().get(queue)
    if policy is None:
        raise ValueError(f"unknown poison queue: {queue}")
    if replaying and not policy.replay_allowed:
        raise ValueError(f"policy forbids replay for queue: {queue}")


async def _record_disposition(
    queue: str,
    message: Any,
    *,
    action: str,
    actor: str,
    reason: str,
    replay_count: int,
) -> None:
    from src.core.database import get_db_context
    from src.models.orm.poison_message_dispositions import PoisonMessageDisposition

    headers = dict(message.headers or {})
    async with get_db_context() as db:
        db.add(
            PoisonMessageDisposition(
                queue_name=queue,
                message_id=message.message_id,
                idempotency_key=headers.get("x-idempotency-key"),
                action=action,
                actor=actor[:255],
                reason=reason[:2000],
                retry_count=int(headers.get("x-retry-count") or 0),
                replay_count=replay_count,
                body_sha256=hashlib.sha256(message.body).hexdigest(),
            )
        )
        await db.commit()


def decode_message(body: bytes) -> dict[str, Any] | str:
    try:
        parsed = json.loads(body.decode())
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode(errors="replace")


async def _connect():
    return await aio_pika.connect_robust(get_settings().rabbitmq_url)


async def _fetch_poison_messages(poison, limit: int) -> list[Any]:
    messages: list[Any] = []
    for _ in range(limit):
        message = await poison.get(fail=False, no_ack=False)
        if message is None:
            break
        messages.append(message)
    return messages


async def _requeue_messages(messages: list[Any]) -> None:
    for message in messages:
        await message.nack(requeue=True)


async def _ensure_main_queue(channel, queue_name: str) -> None:
    await channel.declare_queue(queue_name, passive=True)


async def inspect(queue: str, limit: int) -> list[dict[str, Any]]:
    _validate_queue(queue)
    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        messages = await _fetch_poison_messages(poison, limit)
        rows = [_describe(queue, message) for message in messages]
        await _requeue_messages(messages)
        await channel.close()
    return rows


async def replay(
    queue: str,
    limit: int,
    dry_run: bool,
    *,
    actor: str,
    reason: str,
) -> list[dict[str, Any]]:
    _validate_queue(queue, replaying=True)
    actor, reason = _validate_audit_metadata(actor=actor, reason=reason)
    rows: list[dict[str, Any]] = []
    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        await _ensure_main_queue(channel, queue)
        messages = await _fetch_poison_messages(poison, limit)
        for message in messages:
            row = _describe(queue, message)
            rows.append(row)
            if dry_run:
                continue
            body = decode_message(message.body)
            publish_body = body if isinstance(body, dict) else {"_malformed_body": body}
            replay_count = int((message.headers or {}).get("x-replayed-count") or 0) + 1
            if replay_count > MAX_REPLAY_COUNT:
                row["skipped"] = "replay_count_exhausted"
                await message.nack(requeue=True)
                continue
            await _record_disposition(
                queue,
                message,
                action="replay",
                actor=actor,
                reason=reason,
                replay_count=replay_count,
            )
            headers = _message_headers(
                publish_body,
                queue,
                message_id=message.message_id,
                headers=dict(message.headers or {}),
                retry_count=0,
                replay_count=replay_count,
            )
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(publish_body).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=message.message_id,
                    correlation_id=message.correlation_id,
                    headers=headers,
                ),
                routing_key=queue,
            )
            await message.ack()
            logger.info("Replayed poison message", extra={"queue": queue, **row})
        if dry_run:
            await _requeue_messages(messages)
        await channel.close()
    return rows


async def discard(
    queue: str,
    limit: int,
    reason: str,
    dry_run: bool,
    *,
    actor: str,
) -> list[dict[str, Any]]:
    _validate_queue(queue)
    actor, reason = _validate_audit_metadata(actor=actor, reason=reason)
    rows: list[dict[str, Any]] = []
    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        messages = await _fetch_poison_messages(poison, limit)
        for message in messages:
            row = _describe(queue, message)
            rows.append(row)
            if dry_run:
                continue
            await _record_disposition(
                queue,
                message,
                action="discard",
                actor=actor,
                reason=reason,
                replay_count=int(
                    (message.headers or {}).get("x-replayed-count") or 0
                ),
            )
            await message.ack()
            logger.warning(
                "Discarded poison message",
                extra={"queue": queue, "discard_reason": reason, **row},
            )
        if dry_run:
            await _requeue_messages(messages)
        await channel.close()
    return rows


async def reconcile_discard(
    queue: str,
    *,
    message_id: str,
    execution_id: str,
    expected_reason: str,
    limit: int,
    dry_run: bool,
    actor: str,
    reason: str,
) -> list[dict[str, Any]]:
    """CAS-finalize and discard one exact workflow poison message."""
    if queue != "workflow-executions":
        raise ValueError("reconcile-discard only supports workflow-executions")

    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        messages = await _fetch_poison_messages(poison, limit)
        target = next(
            (message for message in messages if message.message_id == message_id),
            None,
        )
        if target is None:
            await _requeue_messages(messages)
            await channel.close()
            raise RuntimeError(
                f"poison message {message_id!r} was not found in the first {limit} messages"
            )

        row = _describe(queue, target)
        body = row["body"]
        actual_execution_id = body.get("execution_id") if isinstance(body, dict) else None
        actual_reason = row["headers"].get("x-poison-reason")
        if actual_execution_id != execution_id or actual_reason != expected_reason:
            await _requeue_messages(messages)
            await channel.close()
            raise RuntimeError(
                "poison CAS mismatch: execution_id or poison reason changed"
            )

        if dry_run:
            await _requeue_messages(messages)
            await channel.close()
            return [{**row, "reconciliation": "validated_dry_run"}]

        try:
            from src.services.execution.poison import finalize_poisoned_execution

            result = await finalize_poisoned_execution(
                execution_id=execution_id,
                queue=queue,
                reason=expected_reason,
                retry_count=int(row["retry_count"] or 0),
                replay_count=int(row["replay_count"] or 0),
                message_id=message_id,
                sync=bool(body.get("sync")),
                operation="operator_reconcile_discard",
                require_matching_terminal=True,
                require_transient_cleanup=True,
            )
        except Exception:
            await _requeue_messages(messages)
            await channel.close()
            raise

        await _record_disposition(
            queue,
            target,
            action="reconcile_discard",
            actor=actor,
            reason=reason,
            replay_count=int(row["replay_count"] or 0),
        )
        await target.ack()
        await _requeue_messages([message for message in messages if message is not target])
        await channel.close()
        logger.warning(
            "Reconciled and discarded exact poison message",
            extra={
                "queue": queue,
                "message_id": message_id,
                "execution_id": execution_id,
                "poison_reason": expected_reason,
                "disposition": result.disposition,
            },
        )
        return [{**row, "reconciliation": result.__dict__}]


def _describe(queue: str, message) -> dict[str, Any]:
    headers = dict(message.headers or {})
    return {
        "queue": queue,
        "poison_queue": f"{queue}-poison",
        "message_id": message.message_id,
        "correlation_id": message.correlation_id,
        "idempotency_key": headers.get("x-idempotency-key"),
        "retry_count": headers.get("x-retry-count", 0),
        "replay_count": headers.get("x-replayed-count", 0),
        "origin_queue": headers.get("x-origin-queue"),
        "dead_letter": headers.get("x-death"),
        "headers": headers,
        "body": decode_message(message.body),
        "body_sha256": hashlib.sha256(message.body).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect/replay/discard Bifrost delivery poison queues"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "replay", "discard", "reconcile-discard"):
        cmd = sub.add_parser(name)
        cmd.add_argument("queue")
        cmd.add_argument("--limit", type=int, default=10)
        cmd.add_argument("--dry-run", action="store_true")
        if name == "inspect":
            cmd.add_argument(
                "--status",
                choices=("all", "queued", "claimed", "completed", "poison", "interrupted"),
                help="PostgreSQL status filter; defaults to poison and interrupted",
            )
    status_parser = sub.add_parser(
        "status", help="summarize PostgreSQL delivery states (PG backend only)"
    )
    status_parser.add_argument("queue", nargs="?")
    reconcile_parser = sub.add_parser(
        "reconcile",
        help="recover one interrupted PostgreSQL delivery (PG backend only)",
    )
    reconcile_parser.add_argument("queue", nargs="?")
    reconcile_parser.add_argument("--delivery-id", required=True)
    reconcile_parser.add_argument("--actor", required=True)
    reconcile_parser.add_argument("--reason", required=True)
    reconcile_parser.add_argument("--dry-run", action="store_true")
    for name in ("replay", "discard"):
        sub.choices[name].add_argument("--actor", required=True)
        sub.choices[name].add_argument("--reason", required=True)
    reconcile_discard_parser = sub.choices["reconcile-discard"]
    reconcile_discard_parser.add_argument("--message-id", required=True)
    reconcile_discard_parser.add_argument("--execution-id", required=True)
    reconcile_discard_parser.add_argument("--expected-reason", required=True)
    reconcile_discard_parser.add_argument("--actor", required=True)
    reconcile_discard_parser.add_argument("--reason", required=True)
    sub.choices["discard"].add_argument(
        "--delivery-id",
        help="exact PostgreSQL delivery id (required when PostgreSQL is selected)",
    )
    args = parser.parse_args(argv)

    if get_settings().work_delivery_backend == "postgres":
        if args.command == "status":
            rows = asyncio.run(postgres_status(args.queue))
        elif args.command == "inspect":
            rows = asyncio.run(postgres_inspect(args.queue, args.limit, args.status))
        elif args.command == "reconcile":
            rows = asyncio.run(
                postgres_reconcile(
                    args.queue,
                    delivery_id=args.delivery_id,
                    dry_run=args.dry_run,
                    actor=args.actor,
                    reason=args.reason,
                )
            )
        elif args.command == "discard":
            if not args.delivery_id:
                raise ValueError(
                    "PostgreSQL discard requires --delivery-id; bulk transport "
                    "discard is unsafe"
                )
            rows = asyncio.run(
                postgres_discard(
                    args.queue,
                    delivery_id=args.delivery_id,
                    dry_run=args.dry_run,
                    actor=args.actor,
                    reason=args.reason,
                )
            )
        elif args.command == "replay":
            rows = asyncio.run(
                postgres_replay(
                    args.queue,
                    args.limit,
                    args.dry_run,
                    actor=args.actor,
                    reason=args.reason,
                )
            )
        else:
            raise ValueError(
                "reconcile-discard is RabbitMQ-only; use PostgreSQL reconcile "
                "with an exact delivery id"
            )
        print(json.dumps(rows, indent=2, default=str))
        return 0

    if args.command in {"status", "reconcile"}:
        raise ValueError(
            f"{args.command} is only available when BIFROST_WORK_DELIVERY_BACKEND=postgres"
        )
    if getattr(args, "status", None) or getattr(args, "delivery_id", None):
        raise ValueError("--status and --delivery-id require the PostgreSQL backend")
    if args.command == "inspect":
        rows = asyncio.run(inspect(args.queue, args.limit))
    elif args.command == "replay":
        rows = asyncio.run(
            replay(
                args.queue,
                args.limit,
                args.dry_run,
                actor=args.actor,
                reason=args.reason,
            )
        )
    elif args.command == "discard":
        rows = asyncio.run(
            discard(
                args.queue,
                args.limit,
                args.reason,
                args.dry_run,
                actor=args.actor,
            )
        )
    else:
        rows = asyncio.run(
            reconcile_discard(
                args.queue,
                message_id=args.message_id,
                execution_id=args.execution_id,
                expected_reason=args.expected_reason,
                limit=args.limit,
                dry_run=args.dry_run,
                actor=args.actor,
                reason=args.reason,
            )
        )
    print(json.dumps(rows, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
