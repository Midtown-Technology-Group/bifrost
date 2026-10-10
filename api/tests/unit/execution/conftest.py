"""Committed execution claims for real worker SDK transport proofs."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import delete

from src.models.orm.executions import Execution, WorkflowExecutionAttempt


@pytest_asyncio.fixture
async def claimed_sdk_execution(async_session_factory):
    created_executions = []
    created_attempts = []

    async def claim(execution_id=None):
        execution_id = UUID(str(execution_id)) if execution_id else uuid4()
        claim_token = uuid4()
        now = datetime.now(timezone.utc)
        async with async_session_factory() as db:
            execution = await db.get(Execution, execution_id)
            if execution is None:
                execution = Execution(
                    id=execution_id, workflow_name="worker-sdk-fork-proof",
                    executed_by_name="Engine",
                )
                db.add(execution)
                created_executions.append(execution_id)
            execution.attempt_tracking_version = "v1"
            await db.flush()
            attempt = WorkflowExecutionAttempt(
                execution_id=execution_id, attempt_number=1,
                claim_token=claim_token, status="claimed", phase="claim",
                published_at=now, claimed_at=now,
            )
            db.add(attempt)
            await db.commit()
            created_attempts.append(attempt.id)
        return str(execution_id), str(claim_token)

    yield claim

    async with async_session_factory() as db:
        await db.execute(delete(WorkflowExecutionAttempt).where(
            WorkflowExecutionAttempt.id.in_(created_attempts)
        ))
        await db.execute(delete(Execution).where(Execution.id.in_(created_executions)))
        await db.commit()
