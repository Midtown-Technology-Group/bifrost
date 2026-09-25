"""The ingress receipt is scoped to one verified Teams message."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from src.services import teams_receipts as receipts

EVENT_ID = UUID("d43b6040-94af-4e54-a60c-f454e2fb4283")
SOURCE_ID = UUID("1c14fd9f-537f-4f96-8217-a14262c4f439")


def _event(*, verified=True):
    return SimpleNamespace(
        id=EVENT_ID,
        event_source_id=SOURCE_ID,
        event_type="microsoft_teams.message",
        external_identity_id=EVENT_ID if verified else None,
        authenticated_actor={"provider": "microsoft_teams", "external_scope_id": "tenant"},
        data={
            "channel_id": "msteams", "activity_id": "activity", "conversation_id": "conversation",
            "service_url": "https://amer.ng.msg.teams.microsoft.com/",
            "activity": {"text": "ping"},
        },
    )


def test_only_verified_text_messages_get_receipt_scope():
    assert receipts._receipt_scope(_event())
    assert receipts._receipt_scope(_event(verified=False)) is None
    event = _event()
    event.data["activity"]["text"] = ""
    assert receipts._receipt_scope(event) is None


def test_connector_url_is_host_bounded():
    assert receipts._service_url("https://amer.ng.msg.teams.microsoft.com/") == "https://amer.ng.msg.teams.microsoft.com"
    for url in (
        "http://amer.ng.msg.teams.microsoft.com",
        "https://amer.ng.msg.teams.microsoft.com.evil.test",
        "https://amer.ng.msg.teams.microsoft.com:444",
        "https://amer.ng.msg.teams.microsoft.com/bad/path",
    ):
        with pytest.raises(ValueError):
            receipts._service_url(url)


@pytest.mark.asyncio
async def test_duplicate_event_resolves_to_receipt_owner(monkeypatch):
    event = _event()
    canonical = SimpleNamespace(durable_handle={"kind": "teams-event", "id": str(SOURCE_ID)})
    db = SimpleNamespace(get=AsyncMock(return_value=event), scalar=AsyncMock(return_value=canonical))
    assert await receipts.resolve_canonical_teams_event_id(db, EVENT_ID) == SOURCE_ID
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_receipt_failure_is_recorded_and_does_not_escape(monkeypatch):
    event = _event()
    db = SimpleNamespace(get=AsyncMock(return_value=event), commit=AsyncMock())
    source = SimpleNamespace(adapter_name="microsoft_bot_framework", integration_id=SOURCE_ID, config={"app_id": "bot-id"})
    claim = SimpleNamespace(disposition=receipts.OperationReceiptDisposition.OWNER, receipt_id=SOURCE_ID, owner_token=EVENT_ID)
    monkeypatch.setattr(receipts, "claim_operation_receipt", AsyncMock(return_value=claim))
    monkeypatch.setattr(receipts, "record_operation_receipt_handle", AsyncMock())
    monkeypatch.setattr(receipts, "complete_operation_receipt_error", AsyncMock())

    class _Repo:
        def __init__(self, _db):
            pass

        async def get_integration_defaults(self, *_args, **_kwargs):
            return {"app_id": "bot-id", "client_secret": "secret"}

    monkeypatch.setattr(receipts, "IntegrationsRepository", _Repo)
    monkeypatch.setattr(receipts, "_send_receipt", AsyncMock(side_effect=ValueError("failed")))
    await receipts.send_fast_teams_receipt(db, EVENT_ID, source)
    assert event.data["teams_receipt"]["status"] == "failed"
    db.commit.assert_awaited_once()
    receipts.complete_operation_receipt_error.assert_awaited_once()
