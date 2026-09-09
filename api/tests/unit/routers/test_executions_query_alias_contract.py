"""Fork compatibility checks for strict execution-history query parsing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException, Request

from src.core.org_filter import OrgFilterType
from src.routers import executions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        {"workflow_id": ""},
        {"workflowId": ""},
        {"start_date": ""},
        {"end_date": "invalid"},
        {"continuation_token": ""},
        {"continuationToken": "-1"},
        {"continuation_token": "not-a-cursor"},
        {"exclude_local": ""},
        {"exclude_local": "maybe"},
        {"workflow": "unsupported"},
    ],
)
async def test_invalid_filters_never_query_execution_history(query):
    request = Request({"type": "http", "query_string": urlencode(query).encode()})
    ctx = SimpleNamespace(db=object(), user=object())
    with (
        patch.object(executions, "resolve_org_filter", return_value=(OrgFilterType.ALL, None)),
        patch.object(executions, "ExecutionRepository") as repo_class,
    ):
        repo_class.return_value.list_executions = AsyncMock(return_value=([], None))
        with pytest.raises(HTTPException) as exc:
            await executions.list_executions(
                ctx, request, scope=None, workflowName=None, workflowId=None,
                status_filter=None, startDate=None, endDate=None,
                excludeLocal=True, limit=25, continuationToken=None,
            )
        assert exc.value.status_code == 422
        repo_class.return_value.list_executions.assert_not_awaited()
