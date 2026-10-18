"""Microsoft Graph notification validation at the public webhook seam."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.routers import hooks
from src.services.events import processor as processor_module
from src.services.webhooks.adapters.microsoft_graph import MicrosoftGraphAdapter


@pytest.fixture
def graph_ingress(monkeypatch):
    adapter = MicrosoftGraphAdapter()
    db = AsyncMock()
    webhook_source = SimpleNamespace(
        adapter_name="microsoft_graph",
        integration_id=None,
        config={"resource": "/users/user-1/messages/"},
        state={"client_state": "fixture-state"},
        rate_limit_enabled=False,
    )
    event_source = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        hooks,
        "resolve_webhook_source",
        AsyncMock(return_value=(event_source, webhook_source)),
    )
    monkeypatch.setattr(processor_module, "get_adapter", lambda _name: adapter)
    persisted = AsyncMock()
    monkeypatch.setattr(
        processor_module.EventProcessor,
        "_process_delivery",
        persisted,
    )

    async def receive(payload, *, query=None, method="POST"):
        request = SimpleNamespace(
            method=method,
            headers={},
            query_params=query or {},
            client=None,
            body=AsyncMock(return_value=json.dumps(payload).encode()),
        )
        response = await hooks.receive_webhook(str(event_source.id), request, db)
        return response, db, persisted

    return receive, webhook_source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        4,
        {"value": 4},
        {"value": [None]},
        {
            "value": [
                {
                    "clientState": "fixture-state",
                    "changeType": "created",
                    "resource": "Users/user-1/Messages/01LONGMESSAGEID",
                },
                None,
            ]
        },
    ],
    ids=["outer-scalar", "value-scalar", "item-null", "mixed-validity-batch"],
)
async def test_graph_shape_rejection_at_real_ingress(graph_ingress, payload):
    receive, _webhook_source = graph_ingress

    response, db, persisted = await receive(payload)

    assert response.status_code == 400
    persisted.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_graph_handshake_precedes_body_and_state_checks(graph_ingress, method):
    receive, webhook_source = graph_ingress
    webhook_source.state = {}

    response, db, persisted = await receive(
        4,
        query={"validationToken": "fixture token"},
        method=method,
    )

    assert response.status_code == 200
    assert bytes(response.body) == b"fixture token"
    assert response.headers["content-type"].startswith("text/plain")
    persisted.assert_not_awaited()
    db.commit.assert_not_awaited()
