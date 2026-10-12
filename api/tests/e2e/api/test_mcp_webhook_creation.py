"""MCP subscription setup must expose a durable callback before provider setup."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.events import EventSource, EventSubscription, WebhookSource
from src.models.orm.workflows import Workflow
from src.services.events.processor import resolve_webhook_source
from src.services.mcp_server.tools.events import create_event_source
from src.services.webhooks.protocol import SubscribeResult


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "provider", "finalize", "cleanup"])
async def test_mcp_subscription_callback_is_durable_and_failure_cleans_up(
    async_session_factory, e2e_client, monkeypatch, failure,
):
    name = f"mcp-callback-{uuid4().hex}"
    workflow_id = uuid4()
    callback_ids = []
    removed_subscriptions = []

    async def subscribe(*, callback_url, config, integration):
        source_id = UUID(urlsplit(callback_url).path.rsplit("/", 1)[1])
        callback_ids.append(source_id)
        # A provider's validation request uses a different transaction.
        async with async_session_factory() as callback_db:
            resolved = await resolve_webhook_source(callback_db, str(source_id))
            assert resolved is not None
            assert resolved[0].name == name
            assert resolved[1].adapter_name == "microsoft_graph"
            assert await callback_db.scalar(
                select(EventSubscription).where(
                    EventSubscription.event_source_id == source_id,
                    EventSubscription.workflow_id == workflow_id,
                )
            ) is not None
        # Exercise the actual unauthenticated callback receiver as Graph does.
        response = e2e_client.post(
            f"/api/hooks/{source_id}", params={"validationToken": "mcp-handshake"},
        )
        assert response.status_code == 200, response.text
        assert response.text == "mcp-handshake"
        if failure == "provider":
            raise ValueError("provider rejected subscription")
        return SubscribeResult(external_id="test-subscription", state={"client_state": "test"})

    async def unsubscribe(*, external_id, state, integration):
        removed_subscriptions.append(external_id)
        if failure == "cleanup":
            raise ValueError("provider cleanup unavailable")

    # No real provider or credentials: retain the real Graph callback parser.
    adapter = SimpleNamespace(
        requires_integration=None, subscribe=subscribe, unsubscribe=unsubscribe,
    )
    registry = MagicMock()
    registry.get.return_value = adapter
    monkeypatch.setattr(
        "src.services.webhooks.registry.get_adapter_registry", lambda: registry,
    )

    try:
        async with async_session_factory() as setup:
            setup.add(Workflow(
                id=workflow_id, name=name, function_name="test_callback",
                path=f"{name}.py", type="workflow",
            ))
            await setup.commit()
        async with async_session_factory() as db:
            if failure in {"finalize", "cleanup"}:
                commit = db.commit
                commit_count = 0

                async def fail_metadata_commit():
                    nonlocal commit_count
                    commit_count += 1
                    if commit_count == 2:
                        raise RuntimeError("metadata commit failed")
                    await commit()

                monkeypatch.setattr(db, "commit", fail_metadata_commit)
            context = SimpleNamespace(
                is_platform_admin=True, session=db, user_email="mcp-test@example.com",
            )
            result = await create_event_source(
                context, name=name, source_type="webhook", adapter_name="microsoft_graph",
                workflow_id=str(workflow_id),
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
            if failure in {"provider", "finalize"}:
                expected = (
                    "provider rejected subscription" if failure == "provider"
                    else "metadata commit failed"
                )
                assert data["error"] == f"Failed to create provider subscription: {expected}"
                assert removed_subscriptions == (
                    ["test-subscription"] if failure == "finalize" else []
                )
                assert source is None
                assert webhook is None
            elif failure == "cleanup":
                assert "cleanup required" in data["error"]
                assert source is not None and not source.is_active
                assert source.error_message == "Provider subscription cleanup required"
                assert webhook is not None
                assert webhook.external_id == "test-subscription"
                assert webhook.state == {"client_state": "test"}
                assert removed_subscriptions == ["test-subscription"]
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
            await cleanup.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await cleanup.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_name", ["generic", "microsoft_graph"])
async def test_missing_workflow_leaves_no_callback_or_provider_subscription(
    async_session_factory, monkeypatch, adapter_name,
):
    from unittest.mock import AsyncMock

    name = f"mcp-missing-workflow-{uuid4().hex}"
    adapter = SimpleNamespace(requires_integration=None, subscribe=AsyncMock())
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
            missing_workflow = str(uuid4())
            result = await create_event_source(
                context, name=name, source_type="webhook", adapter_name=adapter_name,
                workflow_id=missing_workflow,
            )
            await db.commit()
        assert result.structured_content["error"] == f"Workflow not found: {missing_workflow}"
        adapter.subscribe.assert_not_awaited()
        async with async_session_factory() as readback:
            assert await readback.scalar(
                select(EventSource).where(EventSource.name == name)
            ) is None
            assert await readback.scalar(
                select(WebhookSource).join(EventSource).where(EventSource.name == name)
            ) is None
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(EventSource).where(EventSource.name == name))
            await cleanup.commit()
