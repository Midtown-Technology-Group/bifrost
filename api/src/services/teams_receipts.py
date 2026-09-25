"""Fast, at-most-once Teams receipt after verified webhook admission."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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
        or parsed.port not in (None, 443)
        or not any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_HOSTS)
    ):
        raise ValueError("Untrusted Bot Framework service URL")
    return value.rstrip("/")


async def _send_receipt(
    *, app_id: str, client_secret: str, service_url: str,
    conversation_id: str, inbound_activity_id: str,
    update_activity_id: str | None = None,
    message: str = "Received. Working on it…",
) -> str | None:
    base = _service_url(service_url)
    path = f"/v3/conversations/{quote(conversation_id, safe='')}/activities/"
    path += quote(update_activity_id or inbound_activity_id, safe="")
    card = {
        "$schema": "https://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "Bifrost", "weight": "Bolder"},
            {"type": "TextBlock", "text": message, "wrap": True},
        ],
    }
    activity = {
        "type": "message",
        "summary": message,
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
        response = await client.request(
            "PUT" if update_activity_id else "POST", f"{base}{path}",
            json=activity,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code not in (200, 201, 202):
            raise ValueError(f"Bot Framework receipt returned {response.status_code}")
        try:
            reply_id = response.json().get("id") if response.content else update_activity_id
        except (ValueError, AttributeError):
            reply_id = update_activity_id
        return reply_id if isinstance(reply_id, str) and reply_id else None


async def send_fast_teams_receipt(
    db: AsyncSession, event_id: UUID, webhook_source: WebhookSource,
    *, message: str = "Received. Working on it…", sent_status: str = "sent",
) -> str | None:
    """Send before worker admission; failure never rejects a verified webhook."""
    event = await db.get(Event, event_id)
    if event is None or webhook_source.adapter_name != "microsoft_bot_framework":
        return None
    scope = _receipt_scope(event)
    if scope is None:
        return None
    claim = await claim_operation_receipt(
        namespace=_NAMESPACE, scope_key=scope, request_fingerprint=scope,
    )
    if claim.disposition != OperationReceiptDisposition.OWNER:
        return "duplicate"
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
            message=message,
        )
        receipt["sent_at"] = datetime.now(timezone.utc).isoformat()
        receipt["status"] = sent_status if reply_id else "accepted_unaddressable"
        if reply_id:
            receipt["reply_activity_id"] = reply_id
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
    return "owner"


async def finish_rejected_teams_receipt(
    db: AsyncSession, event_id: UUID, webhook_source: WebhookSource,
) -> bool:
    """Replace a visible receipt with a safe terminal failure on identity/config rejection."""
    event = await db.get(Event, event_id)
    if event is None:
        return False
    receipt = (event.data or {}).get("teams_receipt") or {}
    if receipt.get("status") != "sent" or not receipt.get("reply_activity_id"):
        return False
    if webhook_source.integration_id is None:
        return False
    config = await IntegrationsRepository(db).get_integration_defaults(
        webhook_source.integration_id, external=False,
    )
    try:
        await _send_receipt(
            app_id=str(config.get("app_id") or ""),
            client_secret=str(config.get("client_secret") or ""),
            service_url=receipt["service_url"],
            conversation_id=receipt["conversation_id"],
            inbound_activity_id=receipt["inbound_activity_id"],
            update_activity_id=receipt["reply_activity_id"],
            message="I couldn't start that request. Check your Bifrost access or bot setup.",
        )
    except Exception as exc:
        logger.warning("Teams rejection card update failed event=%s error=%s", event_id, type(exc).__name__)
        return False
    event.data = {
        **event.data,
        "teams_receipt": {**receipt, "status": "rejected"},
    }
    await db.commit()
    return True
