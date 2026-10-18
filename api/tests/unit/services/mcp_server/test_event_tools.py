"""Event tools forward canonical subscription criteria through REST.

REST authorization/personas are exercised in test_mcp_events_external.py and
write-scope tests; these cover the public thin-wrapper input/response contract.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import events


@pytest.fixture
def event_transport(monkeypatch):
    source_id = str(uuid4())
    response = {"id": str(uuid4()), "is_active": True}
    call = AsyncMock(return_value=(200, response))

    @asynccontextmanager
    async def rest_client(_context):
        yield object()

    monkeypatch.setattr(events, "rest_client", rest_client)
    monkeypatch.setattr(events, "call_rest", call)
    monkeypatch.setattr(events, "_resolve_ref", AsyncMock(return_value=source_id))
    return source_id, response, call


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_subscription_criteria_round_trip(event_transport, operation):
    source_id, response, call = event_transport
    criteria = {"version": 1, "root": {
        "kind": "condition", "field": "event.body.priority",
        "operator": "equals", "value": "critical",
    }}
    if operation == "create":
        result = await events.bifrost_event_subscription_create(
            object(), source_id, workflow_id=str(uuid4()), criteria=criteria,
        )
        expected_method = "POST"
    else:
        result = await events.bifrost_event_subscription_update(
            object(), source_id, response["id"], criteria=criteria,
        )
        expected_method = "PATCH"
    args, kwargs = call.await_args
    assert args[1] == expected_method
    assert kwargs["json_body"]["criteria"] == criteria
    assert "filter_expression" not in kwargs["json_body"]
    assert result.structured_content == response


@pytest.mark.asyncio
async def test_update_omits_unset_criteria_and_preserves_false(event_transport):
    source_id, response, call = event_transport
    await events.bifrost_event_subscription_update(
        object(), source_id, response["id"], is_active=False,
    )
    assert call.await_args.kwargs["json_body"] == {"is_active": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [403, 409, 422])
async def test_subscription_errors_preserve_rest_denial(event_transport, status_code):
    source_id, response, call = event_transport
    call.return_value = (status_code, {"detail": "Denied or invalid"})
    result = await events.bifrost_event_subscription_update(
        object(), source_id, response["id"], is_active=False,
    )
    assert "error" in result.structured_content
    assert result.structured_content["status_code"] == status_code
    assert result.structured_content["body"] == {"detail": "Denied or invalid"}


@pytest.mark.asyncio
async def test_no_update_does_not_dispatch(event_transport):
    source_id, response, call = event_transport
    result = await events.bifrost_event_subscription_update(
        object(), source_id, response["id"],
    )
    assert "error" in result.structured_content
    call.assert_not_awaited()
