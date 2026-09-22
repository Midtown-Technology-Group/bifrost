"""Durable, generation-fenced runtime maintenance state.

The state lives in the existing global ``system_configs`` table. PostgreSQL
advisory transaction locks serialize the maintenance transition with delivery
publication and claiming without holding a lock while work executes. Entering
``draining`` closes external API and scheduler admissions while workers and
platform handlers may continue claiming already accepted PostgreSQL work.
After that work reaches zero, ``sealed`` also closes claims for restart.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.config import SystemConfig
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.scheduler_diagnostics import SchedulerTaskRun
from src.models.orm.scheduler_leases import SchedulerLease
from src.models.orm.work_deliveries import WorkDelivery

MAINTENANCE_CATEGORY = "runtime"
MAINTENANCE_KEY = "maintenance"
MAINTENANCE_LOCK = "bifrost:runtime-maintenance"
MAINTENANCE_ROUTE_PREFIX = "/api/platform/runtime-maintenance"
_CACHE_SECONDS = 0.5


class RuntimeMaintenanceError(RuntimeError):
    """The durable maintenance state is invalid or cannot transition safely."""


class RuntimeMaintenanceSealed(RuntimeMaintenanceError):
    """New durable work cannot be admitted after the maintenance seal."""


class RuntimeMaintenanceActive(RuntimeMaintenanceError):
    """A trigger cannot start while runtime maintenance is active."""


@dataclass(frozen=True)
class RuntimeMaintenanceState:
    generation: UUID | None = None
    phase: Literal["open", "draining", "sealed"] = "open"
    requested_at: datetime | None = None
    requested_by: str | None = None
    reason: str | None = None

    @property
    def active(self) -> bool:
        return self.phase != "open"

    @property
    def sealed(self) -> bool:
        return self.phase == "sealed"


@dataclass(frozen=True)
class RuntimeDrainCounts:
    work_deliveries: int
    workflow_attempts: int
    workflow_executions: int
    agent_attempts: int
    agent_runs: int
    platform_jobs: int
    scheduler_task_runs: int
    trigger_leases: int

    @property
    def accepted_work(self) -> int:
        return (
            self.work_deliveries
            + self.workflow_attempts
            + self.workflow_executions
            + self.agent_attempts
            + self.agent_runs
            + self.platform_jobs
            + self.scheduler_task_runs
        )

    @property
    def drained(self) -> bool:
        return self.accepted_work == 0 and self.trigger_leases == 0


@dataclass
class _RuntimeMaintenanceCache:
    state: RuntimeMaintenanceState | None = None
    until: float = 0.0


_cache_lock = asyncio.Lock()
_cache = _RuntimeMaintenanceCache()


async def acquire_runtime_maintenance_lock(db: AsyncSession, *, shared: bool) -> None:
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await db.execute(
        text(f"SELECT {function}(hashtext(:lock_key))"),
        {"lock_key": MAINTENANCE_LOCK},
    )


async def _matching_rows(db: AsyncSession) -> list[SystemConfig]:
    return list(
        (
            await db.execute(
                select(SystemConfig)
                .where(
                    SystemConfig.category == MAINTENANCE_CATEGORY,
                    SystemConfig.key == MAINTENANCE_KEY,
                    SystemConfig.organization_id.is_(None),
                )
                .order_by(SystemConfig.created_at, SystemConfig.id)
            )
        )
        .scalars()
        .all()
    )


def _parse_row(row: SystemConfig) -> RuntimeMaintenanceState:
    value = row.value_json
    if not isinstance(value, dict):
        raise RuntimeMaintenanceError("Runtime maintenance state is not an object")
    try:
        generation = UUID(str(value["generation"]))
        phase = value["phase"]
        requested_at = datetime.fromisoformat(str(value["requested_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeMaintenanceError("Runtime maintenance state is malformed") from exc
    if phase not in {"draining", "sealed"}:
        raise RuntimeMaintenanceError("Runtime maintenance phase is invalid")
    if requested_at.tzinfo is None:
        raise RuntimeMaintenanceError("Runtime maintenance timestamp lacks timezone")
    requested_by = value.get("requested_by")
    reason = value.get("reason")
    if not isinstance(requested_by, str) or not requested_by:
        raise RuntimeMaintenanceError("Runtime maintenance actor is invalid")
    if not isinstance(reason, str) or not reason:
        raise RuntimeMaintenanceError("Runtime maintenance reason is invalid")
    return RuntimeMaintenanceState(
        generation=generation,
        phase=phase,
        requested_at=requested_at,
        requested_by=requested_by,
        reason=reason,
    )


async def read_runtime_maintenance_state(
    db: AsyncSession,
) -> RuntimeMaintenanceState:
    rows = await _matching_rows(db)
    if len(rows) > 1:
        raise RuntimeMaintenanceError("Duplicate global runtime maintenance rows")
    return _parse_row(rows[0]) if rows else RuntimeMaintenanceState()


async def read_cached_runtime_maintenance_state() -> RuntimeMaintenanceState:
    now = time.monotonic()
    if _cache.state is not None and now < _cache.until:
        return _cache.state
    async with _cache_lock:
        now = time.monotonic()
        if _cache.state is not None and now < _cache.until:
            return _cache.state
        async with get_db_context() as db:
            state = await read_runtime_maintenance_state(db)
        _cache.state = state
        _cache.until = now + _CACHE_SECONDS
        return state


def invalidate_runtime_maintenance_cache() -> None:
    _cache.state = None
    _cache.until = 0.0


async def enter_runtime_maintenance(
    db: AsyncSession,
    *,
    requested_by: str,
    reason: str,
    generation: UUID | None = None,
) -> RuntimeMaintenanceState:
    normalized_reason = reason.strip()
    if not requested_by or not normalized_reason or len(normalized_reason) > 500:
        raise ValueError("Maintenance actor and a 1..500 character reason are required")
    await acquire_runtime_maintenance_lock(db, shared=False)
    rows = await _matching_rows(db)
    if len(rows) > 1:
        raise RuntimeMaintenanceError("Duplicate global runtime maintenance rows")
    if rows:
        state = _parse_row(rows[0])
        if (
            generation is not None
            and state.generation == generation
            and state.requested_by == requested_by
            and state.reason == normalized_reason
        ):
            return state
        raise RuntimeMaintenanceActive(
            f"Runtime maintenance generation {state.generation} is already active"
        )
    now = datetime.now(UTC)
    state = RuntimeMaintenanceState(
        generation=generation or uuid4(),
        phase="draining",
        requested_at=now,
        requested_by=requested_by,
        reason=normalized_reason,
    )
    db.add(
        SystemConfig(
            category=MAINTENANCE_CATEGORY,
            key=MAINTENANCE_KEY,
            organization_id=None,
            value_json={
                "generation": str(state.generation),
                "phase": state.phase,
                "requested_at": now.isoformat(),
                "requested_by": requested_by,
                "reason": normalized_reason,
            },
            created_by=requested_by,
            updated_by=requested_by,
        )
    )
    await db.flush()
    return state


async def runtime_drain_counts(db: AsyncSession) -> RuntimeDrainCounts:
    async def count(model, *predicates) -> int:
        return int(
            await db.scalar(select(func.count()).select_from(model).where(*predicates))
            or 0
        )

    return RuntimeDrainCounts(
        work_deliveries=await count(
            WorkDelivery,
            WorkDelivery.status.in_(("queued", "claimed", "interrupted")),
        ),
        workflow_attempts=await count(
            WorkflowExecutionAttempt, WorkflowExecutionAttempt.completed_at.is_(None)
        ),
        workflow_executions=await count(
            Execution,
            Execution.status.in_(
                (
                    ExecutionStatus.PENDING,
                    ExecutionStatus.RUNNING,
                    ExecutionStatus.CANCELLING,
                )
            ),
        ),
        agent_attempts=await count(
            ExecutionAttempt, ExecutionAttempt.completed_at.is_(None)
        ),
        agent_runs=await count(
            AgentRun, AgentRun.status.in_(("queued", "running", "cancelling"))
        ),
        platform_jobs=await count(
            PlatformJob,
            PlatformJob.status.in_(
                ("queued", "running", "waiting", "cancel_requested")
            ),
        ),
        scheduler_task_runs=await count(
            SchedulerTaskRun, SchedulerTaskRun.status == "running"
        ),
        trigger_leases=await count(
            SchedulerLease,
            SchedulerLease.owner_id.is_not(None),
            SchedulerLease.lease_expires_at > func.clock_timestamp(),
        ),
    )


async def seal_runtime_maintenance(
    db: AsyncSession, *, generation: UUID
) -> tuple[RuntimeMaintenanceState, RuntimeDrainCounts]:
    await acquire_runtime_maintenance_lock(db, shared=False)
    rows = await _matching_rows(db)
    if len(rows) != 1:
        raise RuntimeMaintenanceError("Runtime maintenance is missing or duplicated")
    row = rows[0]
    state = _parse_row(row)
    if state.generation != generation:
        raise RuntimeMaintenanceError("Runtime maintenance generation does not match")
    counts = await runtime_drain_counts(db)
    if not counts.drained:
        return state, counts
    if state.phase == "draining":
        value = dict(row.value_json or {})
        value["phase"] = "sealed"
        row.value_json = value
        row.updated_at = datetime.now(UTC)
        row.updated_by = state.requested_by
        await db.flush()
        state = RuntimeMaintenanceState(
            generation=state.generation,
            phase="sealed",
            requested_at=state.requested_at,
            requested_by=state.requested_by,
            reason=state.reason,
        )
    return state, counts


async def exit_runtime_maintenance(
    db: AsyncSession, *, generation: UUID
) -> RuntimeMaintenanceState:
    await acquire_runtime_maintenance_lock(db, shared=False)
    rows = await _matching_rows(db)
    if len(rows) != 1:
        raise RuntimeMaintenanceError("Runtime maintenance is missing or duplicated")
    row = rows[0]
    state = _parse_row(row)
    if state.generation != generation:
        raise RuntimeMaintenanceError("Runtime maintenance generation does not match")
    await db.delete(row)
    await db.flush()
    return RuntimeMaintenanceState()


async def reject_if_runtime_maintenance_sealed(db: AsyncSession) -> None:
    """Fence a durable admission against the maintenance seal."""
    await acquire_runtime_maintenance_lock(db, shared=True)
    if (await read_runtime_maintenance_state(db)).sealed:
        raise RuntimeMaintenanceSealed("Runtime maintenance is sealed")


async def runtime_claims_sealed(db: AsyncSession) -> bool:
    """Fence a claim transaction against a concurrent maintenance seal."""
    await acquire_runtime_maintenance_lock(db, shared=True)
    return (await read_runtime_maintenance_state(db)).sealed


async def reject_if_runtime_maintenance_active(db: AsyncSession) -> None:
    """Fence a scheduled trigger against concurrent maintenance entry."""
    await acquire_runtime_maintenance_lock(db, shared=True)
    if (await read_runtime_maintenance_state(db)).active:
        raise RuntimeMaintenanceActive("Runtime maintenance is active")
