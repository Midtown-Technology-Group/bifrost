"""Authorized execution attempt evidence shared by HTTP and engine SDK reads."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared import access_checks

from src.core.principal import UserPrincipal
from src.models.contracts.executions import ExecutionAttemptHistory, ExecutionAttemptPublic
from src.models.orm.executions import WorkflowExecutionAttempt as ExecutionAttempt


async def load_attempt_history(
    db: AsyncSession,
    execution_id: UUID,
    user: UserPrincipal,
    *,
    tracking_enabled: bool,
    organization_id: UUID | None,
) -> ExecutionAttemptHistory:
    """Load bounded attempt evidence after execution authorization succeeds."""

    if user.is_superuser:
        access_checks.note_power(
            "executions.read", organization_id, subject=f"execution:{execution_id}"
        )

    rows = (
        await db.execute(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.execution_id == execution_id)
            .order_by(ExecutionAttempt.attempt_number)
        )
    ).scalars().all()
    if not rows:
        return ExecutionAttemptHistory(
            coverage="recorded" if tracking_enabled else "legacy_unavailable",
            attempts=[],
        )
    return ExecutionAttemptHistory(
        coverage="recorded",
        attempts=[
            ExecutionAttemptPublic(
                attempt_id=row.id,
                attempt_number=row.attempt_number,
                status=row.status,
                phase=row.phase,
                failure_phase=row.failure_phase,
                failure_code=row.failure_code,
                worker_id=row.worker_id if user.is_superuser else None,
                worker_incarnation_id=(
                    row.worker_incarnation_id if user.is_superuser else None
                ),
                process_id=row.process_id if user.is_superuser else None,
                runtime_mode=row.runtime_mode,
                runtime_evidence_hash=(
                    row.runtime_evidence_hash if user.is_superuser else None
                ),
                dispatch_evidence_hash=(
                    row.dispatch_evidence_hash if user.is_superuser else None
                ),
                policy_digest=(row.policy_digest if user.is_superuser else None),
                policy_version=row.policy_version,
                created_at=row.created_at,
                published_at=row.published_at,
                claimed_at=row.claimed_at,
                started_at=row.started_at,
                heartbeat_at=row.heartbeat_at,
                completed_at=row.completed_at,
                duration_ms=row.duration_ms,
                peak_memory_bytes=(row.peak_memory_bytes if user.is_superuser else None),
                cpu_total_seconds=(row.cpu_total_seconds if user.is_superuser else None),
            )
            for row in rows
        ],
    )
