"""Safe recovery for client-identified workflow submissions."""

from __future__ import annotations

import hashlib
import secrets
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cache.keys import TTL_EMBED_EXECUTION, embed_execution_key
from src.core.cache.redis_client import get_redis
from src.models.contracts.executions import WorkflowExecutionResponse
from src.models.orm.executions import Execution


class ExecutionSubmissionConflictError(ValueError):
    """Raised when a submission identity is reused for different work."""


class EmbedExecutionRecoveryDeniedError(PermissionError):
    """Raised when an embed session tries to recover another session's work."""


EMBED_SESSION_BINDING_SCHEMA = "sha256"


def embed_session_binding(session_jti: str) -> str:
    """Return the one-way durable identity for an app-embed session."""

    digest = hashlib.sha256(session_jti.encode("utf-8")).hexdigest()
    return f"{EMBED_SESSION_BINDING_SCHEMA}:{digest}"


def persisted_embed_session_binding(execution: Execution) -> str | None:
    """Read the durable embed owner recorded during execution admission."""

    binding = getattr(execution, "embed_session_binding", None)
    return binding if isinstance(binding, str) else None


def assert_persisted_embed_session_binding(
    execution: Execution,
    expected_binding: str | None,
) -> None:
    """Reject admission reuse unless its durable embed owner is identical."""

    persisted_binding = persisted_embed_session_binding(execution)
    if persisted_binding is None and expected_binding is None:
        return
    if (
        persisted_binding is None
        or expected_binding is None
        or not secrets.compare_digest(persisted_binding, expected_binding)
    ):
        raise EmbedExecutionRecoveryDeniedError("Access denied to this execution")


def assert_execution_submission_identity(
    execution: Execution,
    *,
    workflow_id: UUID,
    parameters: dict,
    executed_by: UUID,
    organization_id: UUID | None,
    form_id: UUID | None,
) -> None:
    """Reject reuse of an execution UUID for different persisted work."""

    expected = (
        workflow_id,
        parameters,
        executed_by,
        organization_id,
        form_id,
    )
    actual = (
        execution.workflow_id,
        execution.parameters or {},
        execution.executed_by,
        execution.organization_id,
        execution.form_id,
    )
    if actual != expected:
        raise ExecutionSubmissionConflictError(
            "execution submission identity is already bound to different work"
        )


async def bind_execution_to_embed_session(
    session_jti: str,
    execution_id: UUID | str,
) -> None:
    """Record the embed session that may read or recover an execution."""

    async with get_redis() as redis:
        await redis.setex(
            embed_execution_key(session_jti, str(execution_id)),
            TTL_EMBED_EXECUTION,
            "1",
        )


async def assert_execution_bound_to_embed_session(
    session_jti: str,
    execution_id: UUID | str,
) -> None:
    """Reject recovery unless this exact embed session created the execution."""

    async with get_redis() as redis:
        bound = await redis.exists(embed_execution_key(session_jti, str(execution_id)))
    if not bound:
        raise EmbedExecutionRecoveryDeniedError("Access denied to this execution")


async def recover_execution_submission(
    db: AsyncSession,
    *,
    execution_id: UUID,
    workflow_id: UUID,
    parameters: dict,
    executed_by: UUID,
    organization_id: UUID | None,
    form_id: UUID | None,
    embed_session_jti: str | None = None,
) -> WorkflowExecutionResponse | None:
    """Return a matching persisted execution or reject identity reuse.

    The caller-generated execution UUID is both the submission identity and the
    durable execution primary key.  That lets a client recover with a read-only
    ``GET /api/executions/{id}`` after losing the POST response, without
    replaying the mutation.
    """

    existing = await db.get(Execution, execution_id)
    if existing is None:
        return None

    assert_execution_submission_identity(
        existing,
        workflow_id=workflow_id,
        parameters=parameters,
        executed_by=executed_by,
        organization_id=organization_id,
        form_id=form_id,
    )
    expected_binding = (
        embed_session_binding(embed_session_jti)
        if embed_session_jti is not None
        else None
    )
    assert_persisted_embed_session_binding(existing, expected_binding)
    if embed_session_jti is not None:
        # Redis is a revocable read capability, not the ownership authority.
        # Re-mint it from the durable binding after a dropped response or cache
        # write failure without allowing a different session to claim the row.
        await bind_execution_to_embed_session(embed_session_jti, existing.id)

    return WorkflowExecutionResponse(
        execution_id=str(existing.id),
        workflow_id=str(existing.workflow_id) if existing.workflow_id else None,
        workflow_name=existing.workflow_name,
        status=existing.status,
        result=existing.result,
        error=existing.error_message,
        duration_ms=existing.duration_ms,
        started_at=existing.started_at,
        completed_at=existing.completed_at,
        scheduled_at=existing.scheduled_at,
    )
