"""Bind an authenticated Teams event to its sender's Bifrost chat identity."""

from __future__ import annotations

import re
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from shared.external_access import resolve_external_claim, resolve_provider_org_claim
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.core.principal import UserPrincipal
from src.models.contracts.agents import ChatRunCreateRequest
from src.models.orm import (
    Conversation,
    Event,
    EventSource,
    Integration,
    IntegrationMapping,
    Role,
    User,
    UserOAuthAccount,
    UserRole,
    WebhookSource,
)
from src.repositories.integrations import IntegrationsRepository
from src.services.chat_runs import create_chat_run

INTEGRATION_NAME = "Microsoft Teams Bot"
_LEADING_MENTION = re.compile(r"^\s*<at\b[^>]*>.*?</at>\s*", re.IGNORECASE | re.DOTALL)


def _message_text(activity: dict) -> str:
    text = activity.get("text")
    if not isinstance(text, str):
        return ""
    recipient = activity.get("recipient") or {}
    recipient_id = recipient.get("id") if isinstance(recipient, dict) else None
    entities = activity.get("entities") or []
    removed = False
    for entity in entities if isinstance(entities, list) else []:
        if not isinstance(entity, dict) or entity.get("type") != "mention":
            continue
        mentioned = entity.get("mentioned") or {}
        if not isinstance(mentioned, dict) or mentioned.get("id") != recipient_id:
            continue
        tag = entity.get("text")
        if isinstance(tag, str) and tag:
            text = text.replace(tag, " ", 1)
            removed = True
    if not entities or removed:
        text = _LEADING_MENTION.sub("", text, count=1)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


async def submit_teams_chat_event(db, event_id: UUID) -> dict:
    """Create one user-attributed chat run from a stored, verified Teams event."""
    row = await db.execute(
        select(Event, EventSource, WebhookSource)
        .join(EventSource, Event.event_source_id == EventSource.id)
        .join(WebhookSource, WebhookSource.event_source_id == EventSource.id)
        .where(Event.id == event_id)
    )
    found = row.one_or_none()
    if found is None:
        raise HTTPException(404, "Teams event not found")
    event, source, webhook = found
    data = event.data or {}
    if (
        webhook.adapter_name != "microsoft_bot_framework"
        or not source.is_active
        or event.event_type != "microsoft_teams.message"
        or data.get("channel_id") != "msteams"
    ):
        raise HTTPException(400, "Event is not an authenticated Teams message")

    tenant_id = str(data.get("tenant_id") or "").strip()
    teams_conversation_id = str(data.get("conversation_id") or "").strip()
    sender = data.get("sender") or {}
    aad_object_id = (
        str(sender.get("aadObjectId") or "").strip() if isinstance(sender, dict) else ""
    )
    activity = data.get("activity") or {}
    content = _message_text(activity) if isinstance(activity, dict) else ""
    if not all((tenant_id, teams_conversation_id, aad_object_id, content)):
        raise HTTPException(
            400, "Teams message is missing identity, conversation, or text"
        )

    integration = await db.scalar(
        select(Integration).where(
            Integration.name == INTEGRATION_NAME,
            Integration.is_deleted.is_(False),
        )
    )
    if integration is None:
        raise HTTPException(409, "Teams integration is not configured")
    mapped_orgs = (
        await db.scalars(
            select(IntegrationMapping.organization_id).where(
                IntegrationMapping.integration_id == integration.id,
                IntegrationMapping.entity_id == tenant_id,
                IntegrationMapping.organization_id.is_not(None),
            )
        )
    ).all()
    if len(mapped_orgs) != 1:
        raise HTTPException(403, "Teams tenant has no unique Bifrost organization")

    user = await db.scalar(
        select(User)
        .join(UserOAuthAccount, UserOAuthAccount.user_id == User.id)
        .where(
            UserOAuthAccount.provider_id == "microsoft",
            UserOAuthAccount.provider_user_id == aad_object_id,
        )
    )
    if (
        user is None
        or not user.is_active
        or not user.is_verified
        or user.organization_id != mapped_orgs[0]
    ):
        raise HTTPException(
            403, "Teams sender has no linked Bifrost user in this organization"
        )

    config = await IntegrationsRepository(db).get_integration_defaults(
        integration.id, external=False
    )
    try:
        agent_id = UUID(str(config.get("agent_id") or ""))
    except ValueError:
        raise HTTPException(409, "Teams chat agent is not configured") from None

    roles = (
        await db.execute(
            select(Role.id, Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id)
        )
    ).all()
    principal = UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=True,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=await resolve_external_claim(db, user),
        is_provider_org=await resolve_provider_org_claim(db, user),
        roles=[name for _, name in roles],
        role_ids=[role_id for role_id, _ in roles],
        role_names=[name for _, name in roles],
    )
    linked_user_id = user.id
    linked_org_id = user.organization_id
    conversation_id = uuid5(
        NAMESPACE_URL, f"bifrost-teams:{tenant_id}:{teams_conversation_id}:{user.id}"
    )
    metadata = {
        "tenant_id": tenant_id,
        "teams_conversation_id": teams_conversation_id,
        "aad_object_id": aad_object_id,
    }

    async def validate_binding() -> None:
        existing = await db.get(Conversation, conversation_id)
        if existing is not None and (
            existing.user_id != linked_user_id
            or existing.channel != "teams"
            or existing.extra_data != metadata
        ):
            raise HTTPException(
                409, "Teams conversation binding differs from the stored chat"
            )

    await validate_binding()

    request = ChatRunCreateRequest(
        conversation_id=conversation_id,
        client_run_id=uuid5(NAMESPACE_URL, f"bifrost-teams-event:{event_id}"),
        agent_id=agent_id,
        content=content,
    )
    try:
        submitted = await create_chat_run(
            db, principal, request, channel="teams", conversation_extra_data=metadata
        )
    except IntegrityError:
        # Two first messages may race to create the same Teams conversation.
        await db.rollback()
        await validate_binding()
        submitted = await create_chat_run(
            db, principal, request, channel="teams", conversation_extra_data=metadata
        )
    return {
        "run_id": str(submitted.run_id),
        "conversation_id": str(submitted.conversation.id),
        "organization_id": str(linked_org_id),
        "caller_user_id": str(linked_user_id),
        "status": submitted.status,
        "idempotent": submitted.idempotent,
    }
