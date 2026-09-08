"""Execution pagination retains the fork's public class and positional API."""

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_execution_alias_and_positional_filters(monkeypatch):
    module = importlib.import_module("bifrost.executions")
    assert module.executions is module.Executions
    response = MagicMock()
    response.json.return_value = {"executions": [], "continuation_token": "next"}
    client = MagicMock(get=AsyncMock(return_value=response))
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    rows = await module.Executions.list("workflow", "Success", "start", "end", 12)

    assert isinstance(rows, module.ExecutionList)
    assert rows == []
    assert rows.continuation_token == "next"
    client.get.assert_awaited_once_with(
        "/api/executions",
        params={
            "workflow_name": "workflow", "status": "Success",
            "start_date": "start", "end_date": "end", "limit": 12,
        },
    )
