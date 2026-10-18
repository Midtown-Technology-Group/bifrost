"""Fork compatibility checks for strict execution-history query parsing."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request

from shared import sdk_execution_reads
from src.routers import executions


async def _list(ctx, query):
    return await executions.list_executions(
        ctx,
        Request({"type": "http", "query_string": urlencode(query).encode()}),
        scope=None, workflowName=None, workflowId=None,
        status_filter=None, startDate=None, endDate=None,
        excludeLocal=True, limit=25, continuationToken=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        {alias: value}
        for aliases, values in [
            (("workflow_id", "workflowId"), ("", "not-a-uuid")),
            (("start_date", "startDate", "end_date", "endDate"), ("", "invalid")),
            (("continuation_token", "continuationToken"), ("", "-1", "not-a-cursor", "hist1:bad")),
            (("exclude_local", "excludeLocal"), ("", "maybe")),
        ]
        for alias in aliases
        for value in values
    ] + [{"workflow": "unsupported"}],
)
async def test_invalid_filters_never_query_execution_history(query):
    ctx = SimpleNamespace(db=object(), user=object())
    with patch.object(sdk_execution_reads, "list_sdk_executions", AsyncMock()) as reader:
        with pytest.raises(HTTPException) as exc:
            await _list(ctx, query)

    assert exc.value.status_code == 422
    reader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("snake_case", [False, True])
async def test_aliases_forward_the_same_validated_filters(snake_case):
    workflow_id = uuid4()
    cursor_id = uuid4()
    timeline = datetime(2026, 9, 8, tzinfo=UTC)
    token = sdk_execution_reads.encode_history_cursor(timeline, cursor_id)
    query = {
        "workflowName": "Sync Tickets", "workflowId": str(workflow_id),
        "startDate": "2026-09-01T00:00:00Z", "endDate": "2026-09-08T00:00:00Z",
        "excludeLocal": "false", "continuationToken": token,
    }
    if snake_case:
        query = {
            {"workflowName": "workflow_name", "workflowId": "workflow_id",
             "startDate": "start_date", "endDate": "end_date",
             "excludeLocal": "exclude_local", "continuationToken": "continuation_token"}[key]: value
            for key, value in query.items()
        }
    ctx = SimpleNamespace(db=object(), user=object())
    with patch.object(
        sdk_execution_reads, "list_sdk_executions", AsyncMock(return_value=([], "next")),
    ) as reader:
        response = await _list(ctx, query)

    assert response.executions == []
    assert response.continuation_token == "next"
    reader.assert_awaited_once_with(
        ctx.db, ctx.user, scope=None,
        workflow_name="Sync Tickets", workflow_id=workflow_id,
        status_filter=None, start_date="2026-09-01T00:00:00Z", end_date="2026-09-08T00:00:00Z",
        exclude_local=False, limit=25, offset=0, cursor=(timeline, cursor_id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value, expected", [("TRUE", True), ("1", True), ("off", False), ("0", False)])
async def test_boolean_aliases_keep_supported_values(value, expected):
    ctx = SimpleNamespace(db=object(), user=object())
    with patch.object(
        sdk_execution_reads, "list_sdk_executions", AsyncMock(return_value=([], None)),
    ) as reader:
        await _list(ctx, {"exclude_local": value})

    assert reader.await_args.kwargs["exclude_local"] is expected


@pytest.mark.asyncio
async def test_legacy_offset_alias_is_forwarded_without_a_keyset_cursor():
    ctx = SimpleNamespace(db=object(), user=object())
    with patch.object(
        sdk_execution_reads, "list_sdk_executions", AsyncMock(return_value=([], None)),
    ) as reader:
        await _list(ctx, {"continuation_token": "50"})

    assert reader.await_args.kwargs["offset"] == 50
    assert reader.await_args.kwargs["cursor"] is None
