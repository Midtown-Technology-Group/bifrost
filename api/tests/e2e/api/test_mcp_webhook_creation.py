"""MCP subscription setup must expose a durable callback before provider setup."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.events import EventSource, WebhookSource
from src.services.events.processor import resolve_webhook_source
from src.services.mcp_server.tools.events import create_event_source
from src.services.webhooks.protocol import SubscribeResult


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("provider_fails", [False, True])
async def test_mcp_subscription_callback_is_durable_and_failure_cleans_up(
    async_session_factory, e2e_client, monkeypatch, provider_fails,
):
    name = f"mcp-callback-{uuid4().hex}"
    callback_ids = []

    async def subscribe(*, callback_url, config, integration):
        source_id = UUID(urlsplit(callback_url).path.rsplit("/", 1)[1])
        callback_ids.append(source_id)
        # A provider's validation request uses a different transaction.
        async with async_session_factory() as callback_db:
            resolved = await resolve_webhook_source(callback_db, str(source_id))
            assert resolved is not None
            assert resolved[0].name == name
            assert resolved[1].adapter_name == "microsoft_graph"
        # Exercise the actual unauthenticated callback receiver as Graph does.
        response = e2e_client.post(
            f"/api/hooks/{source_id}", params={"validationToken": "mcp-handshake"},
        )
        assert response.status_code == 200, response.text
        assert response.text == "mcp-handshake"
        if provider_fails:
            raise ValueError("provider rejected subscription")
        return SubscribeResult(external_id="test-subscription", state={"client_state": "test"})

    # No real provider or credentials: retain the real Graph callback parser.
    adapter = SimpleNamespace(requires_integration=None, subscribe=subscribe)
    registry = MagicMock()
    registry.get.return_value = adapter
    monkeypatch.setattr(
        "src.services.webhooks.registry.get_adapter_registry", lambda: registry,
    )

    try:
        async with async_session_factory() as db:
            context = SimpleNamespace(
                is_platform_admin=True, session=db, user_email="mcp-test@example.com",
            )
            result = await create_event_source(
                context, name=name, source_type="webhook", adapter_name="microsoft_graph",
            )
            await db.commit()
        assert len(callback_ids) == 1
        data = result.structured_content
        assert data is not None
        async with async_session_factory() as readback:
            source = await readback.get(EventSource, callback_ids[0])
            webhook = await readback.scalar(
                select(WebhookSource).where(WebhookSource.event_source_id == callback_ids[0])
            )
            if provider_fails:
                assert data["error"] == (
                    "Failed to create provider subscription: provider rejected subscription"
                )
                assert source is None
                assert webhook is None
            else:
                assert "error" not in data, data
                assert data["id"] == str(callback_ids[0])
                assert source is not None
                assert webhook is not None
                assert webhook.external_id == "test-subscription"
                assert webhook.state == {"client_state": "test"}
    finally:
        # Own cleanup even if callback validation/assertions fail after commit.
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(EventSource).where(EventSource.name == name))
            await cleanup.commit()
