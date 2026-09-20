"""Atomic persistence and fencing for audited worker controls."""

import json
import logging
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.core.redis_client import get_redis_client
from src.models.orm.worker_control_commands import WorkerControlCommand

ALLOWED_ACTIONS = {"recycle_process", "recycle_all", "package_install"}
logger = logging.getLogger(__name__)
PACKAGE_COMMAND_TIMEOUT = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _snapshot_worker_incarnations() -> dict[str, UUID]:
    """Read the live worker set once before creating a package fanout."""
    redis = await get_redis_client()._get_redis()
    cursor = 0
    workers: dict[str, UUID] = {}
    while True:
        cursor, keys = await redis.scan(cursor, match="bifrost:pool:*", count=100)
        for key in keys:
            if key.count(":") != 2:
                continue
            worker_id = key.split(":", 2)[2]
            raw_incarnation = await cast(
                Awaitable[str | None], redis.hget(key, "worker_incarnation_id")
            )
            try:
                workers[worker_id] = UUID(str(raw_incarnation))
            except (TypeError, ValueError):
                # A worker without an incarnation cannot safely receive a
                # fenced command; its next startup will converge requirements.
                continue
        if cursor == 0:
            return workers


async def enqueue_package_installation_commands(
    message: dict[str, Any],
    *,
    requested_by_user_id: UUID,
) -> list[WorkerControlCommand]:
    """Persist one fenced package command for each worker in a target snapshot."""
    run_id = UUID(str(message["run_id"]))
    commands: list[WorkerControlCommand] = []
    async with get_db_context() as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:operation))"),
            {"operation": f"bifrost:package-install:{run_id}"},
        )
        existing_rows = list(
            (
                await db.execute(
                    select(WorkerControlCommand).where(
                        WorkerControlCommand.operation_id == run_id,
                        WorkerControlCommand.action == "package_install",
                    )
                )
            )
            .scalars()
            .all()
        )
        if existing_rows:
            workers = {
                row.worker_id: row.target_incarnation_id
                for row in existing_rows
                if row.target_incarnation_id is not None
            }
        else:
            workers = await _snapshot_worker_incarnations()
        if not workers:
            raise RuntimeError(
                "No registered execution workers can accept package installation"
            )
        for worker_id, incarnation_id in workers.items():
            existing = next(
                (row for row in existing_rows if row.worker_id == worker_id), None
            )
            if existing is not None:
                commands.append(existing)
                continue
            command = await create_worker_control_command(
                db,
                worker_id=worker_id,
                action="package_install",
                requested_by_user_id=requested_by_user_id,
                reason="package installation fanout",
                operation_id=run_id,
                target_incarnation_id=incarnation_id,
                payload={
                    **message,
                    "target_worker_incarnation_id": str(incarnation_id),
                },
            )
            commands.append(command)
        if existing_rows:
            commands = existing_rows
        await db.commit()

    # Notifications are only hints. Durable polling remains the recovery path.
    for command in commands:
        try:
            redis = await get_redis_client()._get_redis()
            await redis.publish(
                f"bifrost:pool:{command.worker_id}:commands",
                json.dumps(
                    {
                        "command_id": str(command.id),
                        "action": command.action,
                        "payload": command.payload,
                    }
                ),
            )
        except Exception as exc:  # noqa: BLE001 - polling recovers lost hints
            logger.warning("Package command wake hint failed: %s", exc)
    return commands


async def fail_stale_worker_control_commands(
    db: AsyncSession,
    *,
    worker_id: str,
    worker_incarnation_id: UUID,
) -> list[WorkerControlCommand]:
    """Fail commands fenced to an incarnation replaced by this worker."""
    result = await db.execute(
        select(WorkerControlCommand)
        .where(
            WorkerControlCommand.worker_id == worker_id,
            WorkerControlCommand.target_incarnation_id.is_not(None),
            WorkerControlCommand.target_incarnation_id != worker_incarnation_id,
            WorkerControlCommand.status.in_(("pending", "running")),
        )
        .with_for_update()
    )
    commands = list(result.scalars().all())
    for command in commands:
        command.status = "failed"
        command.failure_message = "target worker incarnation is no longer available"
        command.completed_at = _now()
    await db.flush()
    return commands


async def expire_package_commands(db: AsyncSession) -> list[UUID]:
    """Record unresolved targets within the existing five-minute cleanup cycle.

    Replacement pods may have different worker IDs. A target that has not
    converged after ten minutes fails explicitly; startup requirements sync
    still converges replacement workers. Never infer success from disappearance.
    """
    overdue = (
        select(WorkerControlCommand.id)
        .where(
            WorkerControlCommand.action == "package_install",
            WorkerControlCommand.status.in_(["pending", "running"]),
            WorkerControlCommand.requested_at
            <= func.clock_timestamp() - PACKAGE_COMMAND_TIMEOUT,
        )
        .order_by(WorkerControlCommand.requested_at)
        .limit(100)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(
        update(WorkerControlCommand)
        .where(
            WorkerControlCommand.id.in_(overdue),
        )
        .values(
            status="failed",
            completed_at=func.clock_timestamp(),
            failure_message="Target worker did not confirm package convergence within ten minutes",
        )
        .returning(WorkerControlCommand.operation_id)
    )
    return [operation for operation in set(result.scalars()) if operation is not None]


async def create_worker_control_command(
    db: AsyncSession,
    *,
    worker_id: str,
    action: str,
    requested_by_user_id: UUID,
    reason: str,
    process_id: int | None = None,
    operation_id: UUID | None = None,
    target_incarnation_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> WorkerControlCommand:
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported worker control action: {action}")
    command = WorkerControlCommand(
        worker_id=worker_id[:255],
        action=action,
        process_id=process_id,
        operation_id=operation_id,
        target_incarnation_id=target_incarnation_id,
        payload=payload,
        requested_by_user_id=requested_by_user_id,
        reason=(reason.strip() or "operator request")[:2000],
    )
    db.add(command)
    await db.flush()
    return command


async def claim_worker_control_command(
    db: AsyncSession,
    *,
    command_id: UUID,
    worker_id: str,
    worker_incarnation_id: UUID | None = None,
) -> WorkerControlCommand | None:
    command = (
        await db.execute(
            select(WorkerControlCommand)
            .where(
                WorkerControlCommand.id == command_id,
                WorkerControlCommand.worker_id == worker_id,
                or_(
                    WorkerControlCommand.action != "package_install",
                    WorkerControlCommand.requested_at
                    > func.clock_timestamp() - PACKAGE_COMMAND_TIMEOUT,
                ),
                (
                    WorkerControlCommand.target_incarnation_id.is_(None)
                    | (
                        WorkerControlCommand.target_incarnation_id
                        == worker_incarnation_id
                    )
                ),
                or_(
                    WorkerControlCommand.status == "pending",
                    (
                        (WorkerControlCommand.status == "running")
                        & (
                            WorkerControlCommand.claimed_at
                            < _now() - timedelta(minutes=15)
                        )
                    ),
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command is None:
        return None
    command.status = "running"
    command.claimed_at = _now()
    command.claim_token = uuid4()
    await db.flush()
    return command


async def finish_worker_control_command(
    db: AsyncSession,
    *,
    command_id: UUID,
    worker_id: str,
    worker_incarnation_id: UUID | None = None,
    claim_token: UUID | None = None,
    succeeded: bool,
    failure_message: str | None = None,
) -> WorkerControlCommand | None:
    command = (
        await db.execute(
            select(WorkerControlCommand)
            .where(
                WorkerControlCommand.id == command_id,
                WorkerControlCommand.worker_id == worker_id,
                (
                    WorkerControlCommand.target_incarnation_id.is_(None)
                    | (
                        WorkerControlCommand.target_incarnation_id
                        == worker_incarnation_id
                    )
                ),
                WorkerControlCommand.status == "running",
                WorkerControlCommand.claim_token == claim_token,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command is None:
        return None
    command.status = "succeeded" if succeeded else "failed"
    command.failure_message = (
        failure_message.strip()[:4000] if failure_message else None
    )
    command.completed_at = _now()
    await db.flush()
    return command


async def get_pending_worker_control_command(
    db: AsyncSession,
    *,
    worker_id: str,
    worker_incarnation_id: UUID | None = None,
) -> WorkerControlCommand | None:
    """Find the oldest desired command for polling after a lost pub/sub hint."""

    return (
        await db.execute(
            select(WorkerControlCommand)
            .where(
                WorkerControlCommand.worker_id == worker_id,
                (
                    WorkerControlCommand.target_incarnation_id.is_(None)
                    | (
                        WorkerControlCommand.target_incarnation_id
                        == worker_incarnation_id
                    )
                ),
                or_(
                    WorkerControlCommand.action != "package_install",
                    WorkerControlCommand.requested_at
                    > func.clock_timestamp() - PACKAGE_COMMAND_TIMEOUT,
                ),
                or_(
                    WorkerControlCommand.status == "pending",
                    (
                        (WorkerControlCommand.status == "running")
                        & (
                            WorkerControlCommand.claimed_at
                            < _now() - timedelta(minutes=15)
                        )
                    ),
                ),
            )
            .order_by(WorkerControlCommand.requested_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
