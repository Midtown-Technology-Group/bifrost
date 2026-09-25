"""Regression tests for exact webhook event-to-delivery queueing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from src.routers.hooks import receive_webhook
from src.services.webhooks.protocol import Deliver


@pytest.mark.asyncio
async def test_webhook_queues_the_exact_event_returned_by_the_processor():
    """Concurrent webhooks must not re-query and queue the newest sibling event."""
    source_id = uuid4()
    exact_event_id = uuid4()
    event_source = SimpleNamespace(id=source_id, is_active=True)
    webhook_source = SimpleNamespace(
        rate_limit_enabled=False,
        rate_limit_per_minute=None,
        rate_limit_window_seconds=60,
    )
    request = MagicMock()
    request.method = "POST"
    request.headers = {}
    request.query_params = {}
    request.client = None
    request.body = AsyncMock(return_value=b"{}")
    db = AsyncMock()

    with (
        patch(
            "src.routers.hooks.resolve_webhook_source",
            return_value=(event_source, webhook_source),
        ),
        patch("src.routers.hooks.EventProcessor") as processor_class,
    ):
        processor = processor_class.return_value
        processor.process_webhook = AsyncMock(
            return_value=Deliver(
                data={"alert": "one of two simultaneous requests"},
                event_type="alert",
                event_id=exact_event_id,
            )
        )
        processor.queue_event_deliveries = AsyncMock(return_value=1)

        response = await receive_webhook(str(source_id), request, db)

    assert response.status_code == 202
    processor.queue_event_deliveries.assert_awaited_once_with(exact_event_id)
    db.execute.assert_not_awaited()
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_verified_teams_direct_run_skips_legacy_worker_delivery():
    source_id = uuid4()
    event_id = uuid4()
    event_source = SimpleNamespace(id=source_id, is_active=True)
    webhook_source = SimpleNamespace(
        adapter_name="microsoft_bot_framework",
        rate_limit_enabled=False,
        rate_limit_per_minute=None,
        rate_limit_window_seconds=60,
    )
    request = MagicMock()
    request.method = "POST"
    request.headers = {}
    request.query_params = {}
    request.client = None
    request.body = AsyncMock(return_value=b"{}")
    event = SimpleNamespace(event_type="microsoft_teams.message", data={"activity_id": "inbound"})
    db = AsyncMock()
    db.get.return_value = event

    with (
        patch("src.routers.hooks.resolve_webhook_source", return_value=(event_source, webhook_source)),
        patch("src.routers.hooks.EventProcessor") as processor_class,
        patch("src.routers.hooks.send_fast_teams_receipt", new_callable=AsyncMock, return_value="owner") as receipt,
        patch("src.routers.hooks.validate_teams_chat_event", new_callable=AsyncMock),
        patch("src.routers.hooks.submit_teams_chat_event", new_callable=AsyncMock) as submit,
    ):
        processor = processor_class.return_value
        processor.process_webhook = AsyncMock(return_value=Deliver(data={}, event_type="microsoft_teams.message", event_id=event_id))
        processor.queue_event_deliveries = AsyncMock()
        response = await receive_webhook(str(source_id), request, db)

    assert response.status_code == 202
    receipt.assert_awaited_once_with(db, event_id, webhook_source)
    submit.assert_awaited_once_with(db, event_id)
    processor.queue_event_deliveries.assert_not_awaited()
    assert event.data["teams_direct_enqueued"] is True
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_teams_direct_failure_falls_back_to_legacy_queue():
    source_id = uuid4()
    event_id = uuid4()
    event_source = SimpleNamespace(id=source_id, is_active=True)
    webhook_source = SimpleNamespace(
        adapter_name="microsoft_bot_framework",
        rate_limit_enabled=False,
        rate_limit_per_minute=None,
        rate_limit_window_seconds=60,
    )
    request = MagicMock()
    request.method = "POST"
    request.headers = {}
    request.query_params = {}
    request.client = None
    request.body = AsyncMock(return_value=b"{}")
    event = SimpleNamespace(event_type="microsoft_teams.message", data={"activity_id": "inbound"})
    db = AsyncMock()
    db.get.return_value = event

    with (
        patch("src.routers.hooks.resolve_webhook_source", return_value=(event_source, webhook_source)),
        patch("src.routers.hooks.EventProcessor") as processor_class,
        patch("src.routers.hooks.send_fast_teams_receipt", new_callable=AsyncMock, return_value="owner"),
        patch("src.routers.hooks.validate_teams_chat_event", new_callable=AsyncMock),
        patch("src.routers.hooks.submit_teams_chat_event", new_callable=AsyncMock, side_effect=RuntimeError("queue unavailable")),
    ):
        processor = processor_class.return_value
        processor.process_webhook = AsyncMock(return_value=Deliver(data={}, event_type="microsoft_teams.message", event_id=event_id))
        processor.queue_event_deliveries = AsyncMock(return_value=1)
        response = await receive_webhook(str(source_id), request, db)

    assert response.status_code == 202
    processor.queue_event_deliveries.assert_awaited_once_with(event_id)
    assert event.data["teams_direct_enqueued"] is False


@pytest.mark.asyncio
async def test_duplicate_teams_event_skips_run_and_legacy_delivery():
    source_id = uuid4()
    event_id = uuid4()
    event_source = SimpleNamespace(id=source_id, is_active=True)
    webhook_source = SimpleNamespace(
        adapter_name="microsoft_bot_framework", rate_limit_enabled=False,
        rate_limit_per_minute=None, rate_limit_window_seconds=60,
    )
    request = MagicMock()
    request.method = "POST"
    request.headers = {}
    request.query_params = {}
    request.client = None
    request.body = AsyncMock(return_value=b"{}")
    event = SimpleNamespace(event_type="microsoft_teams.message", data={"activity_id": "inbound"})
    db = AsyncMock()
    db.get.return_value = event

    with (
        patch("src.routers.hooks.resolve_webhook_source", return_value=(event_source, webhook_source)),
        patch("src.routers.hooks.EventProcessor") as processor_class,
        patch("src.routers.hooks.validate_teams_chat_event", new_callable=AsyncMock),
        patch("src.routers.hooks.send_fast_teams_receipt", new_callable=AsyncMock, return_value="duplicate"),
        patch("src.routers.hooks.submit_teams_chat_event", new_callable=AsyncMock) as submit,
    ):
        processor = processor_class.return_value
        processor.process_webhook = AsyncMock(return_value=Deliver(data={}, event_type="microsoft_teams.message", event_id=event_id))
        processor.queue_event_deliveries = AsyncMock()
        response = await receive_webhook(str(source_id), request, db)

    assert response.status_code == 202
    submit.assert_not_awaited()
    processor.queue_event_deliveries.assert_not_awaited()
    assert event.data.get("teams_direct_enqueued") is None


@pytest.mark.asyncio
async def test_unlinked_teams_sender_gets_one_signin_card_and_no_legacy_route():
    source_id = uuid4()
    event_id = uuid4()
    event_source = SimpleNamespace(id=source_id, is_active=True)
    webhook_source = SimpleNamespace(
        adapter_name="microsoft_bot_framework", rate_limit_enabled=False,
        rate_limit_per_minute=None, rate_limit_window_seconds=60,
    )
    request = MagicMock()
    request.method = "POST"
    request.headers = {}
    request.query_params = {}
    request.client = None
    request.body = AsyncMock(return_value=b"{}")
    event = SimpleNamespace(event_type="microsoft_teams.message", data={"activity_id": "inbound"})
    db = AsyncMock()
    db.get.return_value = event

    with (
        patch("src.routers.hooks.resolve_webhook_source", return_value=(event_source, webhook_source)),
        patch("src.routers.hooks.EventProcessor") as processor_class,
        patch("src.routers.hooks.validate_teams_chat_event", new_callable=AsyncMock, side_effect=HTTPException(403, "Teams sender has no linked Bifrost user in this organization")),
        patch("src.routers.hooks.send_fast_teams_receipt", new_callable=AsyncMock) as receipt,
    ):
        processor = processor_class.return_value
        processor.process_webhook = AsyncMock(return_value=Deliver(data={}, event_type="microsoft_teams.message", event_id=event_id))
        processor.queue_event_deliveries = AsyncMock()
        response = await receive_webhook(str(source_id), request, db)

    assert response.status_code == 202
    receipt.assert_awaited_once()
    assert "Sign in to Bifrost" in receipt.await_args.kwargs["message"]
    assert receipt.await_args.kwargs["sent_status"] == "guidance"
    processor.queue_event_deliveries.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmapped_teams_tenant_gets_no_outbound_card():
    source_id = uuid4()
    event_id = uuid4()
    event_source = SimpleNamespace(id=source_id, is_active=True)
    webhook_source = SimpleNamespace(
        adapter_name="microsoft_bot_framework", rate_limit_enabled=False,
        rate_limit_per_minute=None, rate_limit_window_seconds=60,
    )
    request = MagicMock()
    request.method = "POST"
    request.headers = {}
    request.query_params = {}
    request.client = None
    request.body = AsyncMock(return_value=b"{}")
    event = SimpleNamespace(event_type="microsoft_teams.message", data={"activity_id": "inbound"})
    db = AsyncMock()
    db.get.return_value = event

    with (
        patch("src.routers.hooks.resolve_webhook_source", return_value=(event_source, webhook_source)),
        patch("src.routers.hooks.EventProcessor") as processor_class,
        patch("src.routers.hooks.validate_teams_chat_event", new_callable=AsyncMock, side_effect=HTTPException(403, "Teams tenant has no unique Bifrost organization")),
        patch("src.routers.hooks.send_fast_teams_receipt", new_callable=AsyncMock) as receipt,
    ):
        processor = processor_class.return_value
        processor.process_webhook = AsyncMock(return_value=Deliver(data={}, event_type="microsoft_teams.message", event_id=event_id))
        processor.queue_event_deliveries = AsyncMock()
        response = await receive_webhook(str(source_id), request, db)

    assert response.status_code == 202
    receipt.assert_not_awaited()
    processor.queue_event_deliveries.assert_not_awaited()
