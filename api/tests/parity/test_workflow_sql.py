"""Real SQL characterization before claiming Rust persistence parity."""

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.repositories.executions import ExecutionRepository
from tests.parity.workflow_domain_harness import WorkflowCohort, load_cases

pytestmark = pytest.mark.e2e


async def test_queued_cancel_emitted_update_order(async_engine, record_property):
    """Record actual ORM flush order; assignment order is not SQL evidence."""
    case = next(
        case for case in load_cases() if case["case_id"] == "cancel-Pending-claimed"
    )
    cohort = WorkflowCohort(async_engine, case)
    cohort.sessions = async_sessionmaker(
        async_engine, autoflush=False, expire_on_commit=False
    )
    labels = []

    def observe(_connection, _cursor, _statement, _parameters, context, _many):
        # Inspect compiled statement metadata only. Never retain SQL or binds.
        compiled = context.compiled
        if compiled is None:
            return
        statement = compiled.statement
        if not getattr(statement, "is_update", False):
            return
        table = getattr(getattr(statement, "table", None), "name", None)
        if table in {"executions", "workflow_execution_attempts"}:
            labels.append(table)

    try:
        await cohort.seed()
        assert cohort.principal is not None
        event.listen(async_engine.sync_engine, "after_cursor_execute", observe)
        try:
            async with cohort.sessions() as db:
                _, error = await ExecutionRepository(db).cancel_execution(
                    cohort.ids["execution"], cohort.principal
                )
                assert error is None
        finally:
            event.remove(async_engine.sync_engine, "after_cursor_execute", observe)
        # Both genuine updates must execute exactly once. Order is retained for
        # architectural review, not guessed here or declared Rust-equivalent.
        assert sorted(labels) == ["executions", "workflow_execution_attempts"]
        record_property("queued_cancel_update_order", ",".join(labels))
        async with cohort.sessions() as db:
            execution = await db.get(Execution, cohort.ids["execution"])
            attempt = await db.scalar(
                select(WorkflowExecutionAttempt).where(
                    WorkflowExecutionAttempt.execution_id == cohort.ids["execution"]
                )
            )
            assert execution is not None and attempt is not None
            assert execution.status == ExecutionStatus.CANCELLED
            assert attempt.status == "cancelled"
            assert execution.completed_at is not None
            assert (
                execution.completed_at == attempt.completed_at == attempt.heartbeat_at
            )
    finally:
        await cohort.close()
