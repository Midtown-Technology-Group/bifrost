"""Fast, at-most-once Teams receipt after verified webhook admission."""

from __future__ import annotations

import logging
from urllib.parse import quote, urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import Event, WebhookSource
from src.repositories.integrations import IntegrationsRepository
from src.services.operation_receipts import (
    OperationReceiptDisposition,
    canonical_operation_scope_key,
    claim_operation_receipt,
    complete_operation_receipt_error,
    complete_operation_receipt_success,
    record_operation_receipt_handle,
)
from src.models.orm.operation_receipts import OperationReceipt

logger = logging.getLogger(__name__)
_NAMESPACE = "teams.ingress.receipt"
_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_ALLOWED_HOSTS = (
    "smba.trafficmanager.net",
    "smba.ng.msg.teams.microsoft.com",
    "svc.msg.teams.microsoft.com",
)


def _receipt_scope(event: Event) -> str | None:
    actor = event.authenticated_actor or {}
    data = event.data or {}
    if (
        event.event_type != "microsoft_teams.message"
        or actor.get("provider") != "microsoft_teams"
        or not event.external_identity_id
        or data.get("channel_id") != "msteams"
    ):
        return None
    activity_id = str(data.get("activity_id") or "").strip()
    tenant_id = str(actor.get("external_scope_id") or "").strip()
    conversation_id = str(data.get("conversation_id") or "").strip()
    activity = data.get("activity") or {}
    if not all((activity_id, tenant_id, conversation_id)) or not isinstance(activity, dict):
        return None
    if not str(activity.get("text") or "").strip():
        return None
    return canonical_operation_scope_key(
        [str(event.event_source_id), tenant_id, conversation_id, activity_id]
    )


async def resolve_canonical_teams_event_id(db: AsyncSession, event_id: UUID) -> UUID:
    """Map a retried Teams delivery to the one receipt-owning Event."""
    event = await db.get(Event, event_id)
    if event is None:
        return event_id
    scope = _receipt_scope(event)
    if scope is None:
        return event_id
    receipt = await db.scalar(
        select(OperationReceipt).where(
            OperationReceipt.namespace == _NAMESPACE,
            OperationReceipt.scope_key == scope,
        )
    )
    handle = receipt.durable_handle if receipt else None
    if isinstance(handle, dict) and handle.get("kind") == "teams-event":
        try:
            return UUID(handle["id"])
        except (KeyError, TypeError, ValueError):
            pass
    return event_id


def _service_url(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or parsed.port not in (None, 443)
        or not any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_HOSTS)
    ):
        raise ValueError("Untrusted Bot Framework service URL")
    return value.rstrip("/")


async def _send_receipt(
    *, app_id: str, client_secret: str, service_url: str,
    conversation_id: str, inbound_activity_id: str,
) -> str:
    base = _service_url(service_url)
    path = (
        f"/v3/conversations/{quote(conversation_id, safe='')}/activities/"
        f"{quote(inbound_activity_id, safe='')}"
    )
    card = {
        "$schema": "https://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "Bifrost", "weight": "Bolder"},
            {"type": "TextBlock", "text": "Received. Working on it…", "wrap": True},
        ],
    }
    activity = {
        "type": "message",
        "summary": "Bifrost received your message",
        "replyToId": inbound_activity_id,
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": card,
        }],
    }
    async with httpx.AsyncClient(timeout=3.0) as client:
        token_response = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials", "client_id": app_id,
                "client_secret": client_secret,
                "scope": "https://api.botframework.com/.default",
            },
        )
        token_response.raise_for_status()
        token = token_response.json()["access_token"]
        response = await client.post(
            f"{base}{path}",
            json=activity,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code not in (200, 201, 202):
            raise ValueError(f"Bot Framework receipt returned {response.status_code}")
        reply_id = response.json().get("id")
        if not isinstance(reply_id, str) or not reply_id:
            raise ValueError("Bot Framework receipt had no activity ID")
        return reply_id


async def send_fast_teams_receipt(
    db: AsyncSession, event_id: UUID, webhook_source: WebhookSource,
) -> None:
    """Send before worker admission; failure never rejects a verified webhook."""
    event = await db.get(Event, event_id)
    if event is None or webhook_source.adapter_name != "microsoft_bot_framework":
        return
    scope = _receipt_scope(event)
    if scope is None:
        return
    claim = await claim_operation_receipt(
        namespace=_NAMESPACE, scope_key=scope, request_fingerprint=scope,
    )
    if claim.disposition != OperationReceiptDisposition.OWNER:
        return
    assert claim.owner_token is not None
    await record_operation_receipt_handle(
        claim.receipt_id, claim.owner_token,
        {"kind": "teams-event", "id": str(event_id)},
    )
    data = event.data or {}
    receipt: dict[str, str] = {
        "status": "failed",
        "service_url": str(data.get("service_url") or ""),
        "conversation_id": str(data.get("conversation_id") or ""),
        "inbound_activity_id": str(data.get("activity_id") or ""),
    }
    try:
        if webhook_source.integration_id is None:
            raise ValueError("Teams integration is missing")
        config = await IntegrationsRepository(db).get_integration_defaults(
            webhook_source.integration_id, external=False,
        )
        app_id = str(config.get("app_id") or "").strip()
        client_secret = str(config.get("client_secret") or "")
        if not app_id or not client_secret or app_id != str((webhook_source.config or {}).get("app_id") or "").strip():
            raise ValueError("Teams bot credentials are missing")
        reply_id = await _send_receipt(
            app_id=app_id, client_secret=client_secret,
            service_url=receipt["service_url"],
            conversation_id=receipt["conversation_id"],
            inbound_activity_id=receipt["inbound_activity_id"],
        )
        receipt.update(status="sent", reply_activity_id=reply_id)
        await complete_operation_receipt_success(
            claim.receipt_id, claim.owner_token,
            {"canonical_event_id": str(event_id), "reply_activity_id": reply_id},
        )
    except Exception as exc:
        # Connector accepts can be ambiguous after a timeout; at-most-once avoids
        # an accidental second card. Completion may send its own final reply.
        logger.warning("Teams fast receipt failed event=%s error=%s", event_id, type(exc).__name__)
        await complete_operation_receipt_error(
            claim.receipt_id, claim.owner_token,
            {"canonical_event_id": str(event_id), "code": type(exc).__name__},
        )
    event.data = {**data, "teams_receipt": receipt}
    await db.commit()
