"""Execution History ordering and keyset pagination regressions."""

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from shared.sdk_execution_reads import decode_history_cursor, list_sdk_executions
from src.core.constants import PROVIDER_ORG_ID
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.users import User
from src.routers.executions import ExecutionRepository

pytestmark = pytest.mark.e2e


def _legacy_v1_cursor(row_id: UUID, started_at: datetime | None = None) -> str:
    payload = {
        "v": 1,
        "s": started_at.isoformat() if started_at else None,
        "i": str(row_id),
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _execution(
    *,
    name: str,
    status: ExecutionStatus,
    created_at: datetime,
    executed_by: UUID,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    scheduled_at: datetime | None = None,
) -> Execution:
    return Execution(
        id=uuid4(),
        workflow_name=name,
        status=status,
        parameters={},
        executed_by=executed_by,
        executed_by_name="History pagination test",
        created_at=created_at,
        started_at=started_at,
        completed_at=completed_at,
        scheduled_at=scheduled_at,
    )


@pytest.mark.asyncio
async def test_history_pages_use_the_display_timeline_without_gaps(db_session):
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    user_id = uuid4()
    workflow_name = f"history-pagination-{uuid4().hex}"
    db_session.add(
        User(
            id=user_id,
            email=f"history-pagination-{user_id}@example.com",
            name="History pagination test",
            organization_id=PROVIDER_ORG_ID,
        )
    )
    recent = [
        _execution(
            name=workflow_name,
            status=ExecutionStatus.SUCCESS,
            created_at=now - timedelta(minutes=index, seconds=5),
            executed_by=user_id,
            started_at=now - timedelta(minutes=index),
            completed_at=now - timedelta(minutes=index) + timedelta(seconds=2),
        )
        for index in range(30)
    ]
    pending = _execution(
        name=workflow_name,
        status=ExecutionStatus.PENDING,
        created_at=now - timedelta(seconds=30),
        executed_by=user_id,
    )
    future_scheduled = _execution(
        name=workflow_name,
        status=ExecutionStatus.SCHEDULED,
        created_at=now,
        executed_by=user_id,
        scheduled_at=now + timedelta(days=1),
    )
    stale_cancelled = _execution(
        name=workflow_name,
        status=ExecutionStatus.CANCELLED,
        created_at=now - timedelta(days=77, minutes=5),
        executed_by=user_id,
        scheduled_at=now - timedelta(days=77),
    )
    rows = [*recent, pending, future_scheduled, stale_cancelled]
    db_session.add_all(rows)
    await db_session.flush()

    # Superuser: scope resolves to ALL (no org filter) and the owner
    # filter is off. Filter by this test's unique workflow name so prior
    # E2E executions cannot enter the 33-row pagination universe. An org-user
    # principal would now resolve scope to its own org (the HTTP rule),
    # which would exclude these global rows; owner-only visibility is
    # pinned by the shared-service unit tests instead.
    principal = UserPrincipal(
        user_id=user_id,
        email="history-admin@example.com",
        organization_id=PROVIDER_ORG_ID,
        is_superuser=True,
    )
    first_page, token = await list_sdk_executions(
        db_session,
        principal,
        workflow_name=workflow_name,
        limit=25,
    )
    assert token is not None
    assert len(first_page) == 25
    assert first_page[0].execution_id == str(future_scheduled.id)
    assert first_page[-1].execution_id != str(stale_cancelled.id)

    second_page, final_token = await list_sdk_executions(
        db_session,
        principal,
        workflow_name=workflow_name,
        limit=25,
        cursor=decode_history_cursor(token),
    )

    all_ids = [row.execution_id for row in [*first_page, *second_page]]
    assert len(all_ids) == len(rows)
    assert len(set(all_ids)) == len(rows)
    assert all_ids[-1] == str(stale_cancelled.id)
    assert final_token is None


@pytest.mark.asyncio
async def test_legacy_v1_cursor_restarts_instead_of_returning_an_empty_page(
    db_session,
):
    now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    user_id = uuid4()
    workflow_name = f"legacy-history-cursor-{uuid4().hex}"
    db_session.add(
        User(
            id=user_id,
            email=f"legacy-history-{user_id}@example.com",
            name="Legacy history cursor test",
            organization_id=PROVIDER_ORG_ID,
        )
    )
    newest = _execution(
        name=workflow_name,
        status=ExecutionStatus.PENDING,
        created_at=now,
        executed_by=user_id,
    )
    older = _execution(
        name=workflow_name,
        status=ExecutionStatus.PENDING,
        created_at=now - timedelta(minutes=1),
        executed_by=user_id,
    )
    db_session.add_all([newest, older])
    await db_session.flush()

    principal = UserPrincipal(
        user_id=user_id,
        email="legacy-history-admin@example.com",
        organization_id=PROVIDER_ORG_ID,
        is_superuser=True,
    )
    cursor = decode_history_cursor(_legacy_v1_cursor(older.id))
    assert cursor == (None, older.id)

    shared_page, shared_token = await list_sdk_executions(
        db_session,
        principal,
        workflow_name=workflow_name,
        limit=1,
        cursor=cursor,
    )
    router_page, router_token = await ExecutionRepository(db_session).list_executions(
        principal,
        None,
        workflow_name=workflow_name,
        limit=1,
        cursor=cursor,
    )

    assert [row.execution_id for row in shared_page] == [str(newest.id)]
    assert [row.execution_id for row in router_page] == [str(newest.id)]
    assert shared_token is not None
    assert router_token is not None
    assert json.loads(base64.urlsafe_b64decode(shared_token.encode()))["v"] == 2
    assert json.loads(base64.urlsafe_b64decode(router_token.encode()))["v"] == 2
