"""The all-history receipt guard must seek candidates without losing unknowns."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_teams_receipt_index_preserves_all_history_unknown_receipts(
    db_session: AsyncSession,
) -> None:
    index = (
        await db_session.execute(text("""
            SELECT i.indisvalid, i.indisready, pg_get_expr(i.indpred, i.indrelid) AS predicate
            FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'ix_executions_teams_action_receipt'
        """))
    ).mappings().one()
    assert index["indisvalid"] is True
    assert index["indisready"] is True
    assert index["predicate"] == "(execution_context ? 'teams_action_completion'::text)"

    # Use ordinary test-DB rows and the default planner, never enable_seqscan=off.
    # Nothing commits: background workers cannot observe these synthetic rows.
    await db_session.execute(text("""
        INSERT INTO executions
            (id, workflow_name, status, parameters, executed_by_name, execution_context, created_at)
        SELECT gen_random_uuid(), 'teams-receipt-index-proof', 'Success', '{}', 'test',
               jsonb_build_object('unrelated', repeat(md5(n::text), 16)), '2001-01-01'
        FROM generate_series(1, 20000) AS n
    """))
    terminal = (
        ExecutionStatus.SUCCESS, ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT,
        ExecutionStatus.CANCELLED, ExecutionStatus.COMPLETED_WITH_ERRORS, ExecutionStatus.STUCK,
    )
    cases = [(status, {"teams_action_completion": {}}) for status in terminal]
    cases.extend(
        (ExecutionStatus.SUCCESS, {"teams_action_completion": value})
        for value in (
            None, {}, [], "malformed", {"emitted_at": None}, {"emitted_at": ""},
            {"emitted_at": False}, {"emitted_at": 0}, {"emitted_at": []},
        )
    )
    unknown_count = len(cases)
    cases.extend([
        (ExecutionStatus.SUCCESS, {"teams_action_completion": {"emitted_at": "recorded"}}),
        (ExecutionStatus.SUCCESS, {}),
        (ExecutionStatus.SUCCESS, None),
        (ExecutionStatus.PENDING, {"teams_action_completion": {}}),
    ])
    ids = [uuid4() for _ in cases]
    await db_session.execute(insert(Execution), [
        {"id": execution_id, "workflow_name": "teams-receipt-index-proof", "status": status,
         "parameters": {}, "executed_by_name": "test", "execution_context": context,
         "created_at": datetime(2001, 1, 1, tzinfo=timezone.utc)}
        for execution_id, (status, context) in zip(ids, cases, strict=True)
    ])
    await db_session.execute(text("ANALYZE executions"))
    plan = await db_session.scalar(text(
        "EXPLAIN (FORMAT JSON) SELECT id FROM executions "
        "WHERE execution_context ? :receipt_key"
    ), {"receipt_key": "teams_action_completion"})
    nodes = [plan[0]["Plan"]]
    indexes = set()
    while nodes:
        node = nodes.pop()
        indexes.add(node.get("Index Name"))
        nodes.extend(node.get("Plans", []))
    assert "ix_executions_teams_action_receipt" in indexes

    # This is the existing protected guard's full predicate, with no age cutoff.
    marker = Execution.execution_context["teams_action_completion"]["emitted_at"]
    statement = select(func.count()).select_from(Execution).where(
        Execution.id.in_(ids), Execution.status.in_(terminal),
        Execution.execution_context.has_key("teams_action_completion"),
        or_(marker.astext.is_(None), marker.astext == "", func.jsonb_typeof(marker) != "string"),
    )
    assert await db_session.scalar(statement) == unknown_count
    await db_session.execute(update(Execution).where(Execution.id == ids[-1]).values(
        status=ExecutionStatus.SUCCESS,
    ))
    assert await db_session.scalar(statement) == unknown_count + 1
    await db_session.execute(update(Execution).where(Execution.id == ids[0]).values(
        execution_context={"teams_action_completion": {"emitted_at": "recorded"}},
    ))
    assert await db_session.scalar(statement) == unknown_count
