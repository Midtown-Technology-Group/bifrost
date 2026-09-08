"""Log history must not repeat rows when new logs arrive between pages."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core.constants import PROVIDER_ORG_ID
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.users import User
from src.repositories.execution_logs import ExecutionLogRepository, decode_log_cursor

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_log_pages_remain_disjoint_after_inserts_with_timestamp_ties(db_session):
    user_id = uuid4()
    name = f"log-pagination-{uuid4()}"
    db_session.add(User(
        id=user_id, email=f"{user_id}@example.com", name=name,
        organization_id=PROVIDER_ORG_ID,
    ))
    execution = Execution(
        id=uuid4(), workflow_name=name, status=ExecutionStatus.SUCCESS,
        parameters={}, executed_by=user_id, executed_by_name=name,
    )
    db_session.add(execution)
    await db_session.flush()
    repository = ExecutionLogRepository(db_session)
    timestamp = datetime(2026, 9, 8, tzinfo=timezone.utc)
    rows = [await repository.append_log(
        execution.id, "INFO", f"original-{index}", timestamp=timestamp,
    ) for index in range(5)]

    first, token = await repository.list_logs(workflow_name=name, limit=2)
    assert [row["id"] for row in first] == [rows[4].id, rows[3].id]
    assert token is not None

    # Both kinds of new rows must stay ahead of the boundary already returned.
    await repository.append_log(execution.id, "INFO", "same-time", timestamp=timestamp)
    await repository.append_log(
        execution.id, "INFO", "newer", timestamp=timestamp + timedelta(seconds=1),
    )
    second, token = await repository.list_logs(
        workflow_name=name, limit=2, cursor=decode_log_cursor(token),
    )
    assert token is not None
    third, token = await repository.list_logs(
        workflow_name=name, limit=2, cursor=decode_log_cursor(token),
    )
    assert [row["id"] for row in first + second + third] == [row.id for row in reversed(rows)]
    assert token is None

    # Existing numeric pagination callers retain their offset behavior.
    legacy, token = await repository.list_logs(workflow_name=name, limit=2, offset=2)
    assert [row["id"] for row in legacy] == [rows[4].id, rows[3].id]
    assert token is not None
    assert decode_log_cursor(token) == (timestamp, rows[3].id)
