"""Teams events must become chat runs only for the linked user in the mapped org."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from src.services import teams_chat_bridge as bridge

EVENT_ID = UUID("92f93bcd-03c3-4948-8c0d-f309edbc87f8")
ORG_ID = UUID("00000000-0000-0000-0000-000000000002")
USER_ID = UUID("e7f3cdad-1c8b-45d8-a813-7e33bd9590f9")
AGENT_ID = UUID("83fe1e44-7c4e-43be-aaab-7684ebc23810")


def _event_row(*, adapter="microsoft_bot_framework", sender_id="aad-user"):
    activity = {
        "text": "<at>Bifrost</at> ping",
        "recipient": {"id": "bot-id"},
        "entities": [
            {
                "type": "mention",
                "text": "<at>Bifrost</at>",
                "mentioned": {"id": "bot-id"},
            }
        ],
    }
    event = SimpleNamespace(
        id=EVENT_ID,
        event_type="microsoft_teams.message",
        data={
            "tenant_id": "tenant-id",
            "conversation_id": "teams-conversation",
            "channel_id": "msteams",
            "sender": {"aadObjectId": sender_id},
            "activity": activity,
        },
    )
    source = SimpleNamespace(is_active=True)
    webhook = SimpleNamespace(adapter_name=adapter)
    return event, source, webhook


class _Result:
    def __init__(self, value):
        self.value = value

    def one_or_none(self):
        return self.value

    def all(self):
        return self.value


@pytest.mark.asyncio
async def test_rejects_event_without_verified_teams_adapter():
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(_event_row(adapter="generic")))
    )

    with pytest.raises(HTTPException) as exc:
        await bridge.submit_teams_chat_event(db, EVENT_ID)

    assert exc.value.status_code == 400
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_sender_must_be_linked_to_the_mapped_organization():
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(_event_row())),
        scalar=AsyncMock(
            side_effect=[
                SimpleNamespace(id=UUID("4ad2cf59-29b5-472f-9ddb-f42b6453caf8")),
                SimpleNamespace(
                    organization_id=UUID("c149aeb9-b2f4-4325-93db-99c92f82bb7c"),
                    is_active=True,
                    is_verified=True,
                ),
            ]
        ),
        scalars=AsyncMock(return_value=_Result([ORG_ID])),
    )

    with pytest.raises(HTTPException) as exc:
        await bridge.submit_teams_chat_event(db, EVENT_ID)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_linked_sender_runs_chat_with_their_own_principal(monkeypatch):
    user = SimpleNamespace(
        id=USER_ID,
        email="thomas@example.com",
        name="Thomas",
        organization_id=ORG_ID,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[_Result(_event_row()), _Result([(UUID(int=1), "operator")])]
        ),
        scalar=AsyncMock(side_effect=[SimpleNamespace(id=UUID(int=2)), user]),
        scalars=AsyncMock(return_value=_Result([ORG_ID])),
        get=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        bridge,
        "IntegrationsRepository",
        lambda _db: SimpleNamespace(
            get_integration_defaults=AsyncMock(return_value={"agent_id": str(AGENT_ID)})
        ),
    )
    monkeypatch.setattr(bridge, "resolve_external_claim", AsyncMock(return_value=False))
    monkeypatch.setattr(
        bridge, "resolve_provider_org_claim", AsyncMock(return_value=True)
    )
    submitted = SimpleNamespace(
        run_id=UUID(int=3),
        conversation=SimpleNamespace(id=UUID(int=4)),
        status="queued",
        idempotent=False,
    )
    create = AsyncMock(return_value=submitted)
    monkeypatch.setattr(bridge, "create_chat_run", create)

    result = await bridge.submit_teams_chat_event(db, EVENT_ID)

    principal = create.await_args.args[1]
    request = create.await_args.args[2]
    assert principal.user_id == USER_ID
    assert principal.organization_id == ORG_ID
    assert principal.roles == ["operator"]
    assert request.agent_id == AGENT_ID
    assert request.content == "ping"
    assert create.await_args.kwargs["channel"] == "teams"
    assert result["run_id"] == str(UUID(int=3))


@pytest.mark.asyncio
async def test_conversation_race_rechecks_binding_after_rollback(monkeypatch):
    user = SimpleNamespace(
        id=USER_ID,
        email="thomas@example.com",
        name="Thomas",
        organization_id=ORG_ID,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Result(_event_row()), _Result([])]),
        scalar=AsyncMock(side_effect=[SimpleNamespace(id=UUID(int=2)), user]),
        scalars=AsyncMock(return_value=_Result([ORG_ID])),
        get=AsyncMock(
            side_effect=[
                None,
                SimpleNamespace(user_id=UUID(int=99), channel="chat", extra_data={}),
            ]
        ),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(
        bridge,
        "IntegrationsRepository",
        lambda _db: SimpleNamespace(
            get_integration_defaults=AsyncMock(return_value={"agent_id": str(AGENT_ID)})
        ),
    )
    monkeypatch.setattr(bridge, "resolve_external_claim", AsyncMock(return_value=False))
    monkeypatch.setattr(
        bridge, "resolve_provider_org_claim", AsyncMock(return_value=True)
    )
    create = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("race")))
    monkeypatch.setattr(bridge, "create_chat_run", create)

    with pytest.raises(HTTPException) as exc:
        await bridge.submit_teams_chat_event(db, EVENT_ID)

    assert exc.value.status_code == 409
    db.rollback.assert_awaited_once()
    create.assert_awaited_once()
