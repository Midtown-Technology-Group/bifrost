"""Resolve a provider-authenticated actor without trusting event payload fields."""

from dataclasses import asdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.orm.external_identities import ExternalIdentity
from src.models.orm.agent_runs import AgentRun
from src.models.orm.events import Event, EventDelivery
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.services.webhooks.protocol import AuthenticatedExternalActor


class ExternalActorResolutionError(ValueError):
    """A verified provider actor has no safe, unique Bifrost principal."""


def actor_record(actor: AuthenticatedExternalActor) -> dict[str, str | None]:
    """Persist only non-secret provider IDs; never persist a bearer token."""
    return {**asdict(actor), "integration_id": str(actor.integration_id)}


def actor_from_record(record: dict) -> AuthenticatedExternalActor:
    return AuthenticatedExternalActor(
        provider=record["provider"],
        external_scope_id=record["external_scope_id"],
        external_user_id=record["external_user_id"],
        integration_id=UUID(record["integration_id"]),
        external_event_id=record.get("external_event_id"),
        conversation_id=record.get("conversation_id"),
        team_id=record.get("team_id"),
        reply_to_id=record.get("reply_to_id"),
    )


async def resolve_external_actor(
    db: AsyncSession,
    actor: AuthenticatedExternalActor,
    *,
    source_org_id: UUID | None = None,
) -> tuple[UserPrincipal, UUID]:
    if not actor.provider or not actor.external_scope_id or not actor.external_user_id:
        raise ExternalActorResolutionError("External actor identity is incomplete")

    mapping_rows = (
        await db.execute(
            select(IntegrationMapping, Organization)
            .join(Integration, Integration.id == IntegrationMapping.integration_id)
            .outerjoin(
                Organization, Organization.id == IntegrationMapping.organization_id
            )
            .where(
                IntegrationMapping.integration_id == actor.integration_id,
                IntegrationMapping.entity_id == actor.external_scope_id,
                Integration.is_deleted.is_(False),
            )
        )
    ).all()
    if len(mapping_rows) != 1:
        raise ExternalActorResolutionError(
            "External tenant has no unique organization mapping"
        )
    _, org = mapping_rows[0]
    source_org = (
        await db.get(Organization, source_org_id)
        if org is not None and source_org_id is not None and org.id != source_org_id
        else None
    )
    if (
        org is None
        or not org.is_active
        or (source_org_id is not None and org.id != source_org_id and (
            source_org is None or not source_org.is_active or not source_org.is_provider
        ))
    ):
        raise ExternalActorResolutionError(
            "External tenant mapping is inactive or out of scope"
        )

    identities = (
        await db.execute(
            select(ExternalIdentity, User)
            .join(User, User.id == ExternalIdentity.user_id)
            .where(
                ExternalIdentity.provider == actor.provider,
                ExternalIdentity.external_scope_id == actor.external_scope_id,
                ExternalIdentity.external_user_id == actor.external_user_id,
            )
        )
    ).all()
    if len(identities) != 1:
        raise ExternalActorResolutionError(
            "External user has no unique Bifrost identity"
        )
    identity, user = identities[0]
    home_org = await db.get(Organization, user.organization_id)
    provider_granted = (
        home_org is not None
        and home_org.is_active
        and home_org.is_provider
        and identity.authorized_organization_id == org.id
    )
    if (
        not identity.is_active
        or not user.is_active
        or user.is_system
        or home_org is None
        or not home_org.is_active
        or (user.organization_id != org.id and not provider_granted)
    ):
        raise ExternalActorResolutionError(
            "External identity is inactive or outside the tenant organization"
        )

    role_names = (
        (
            await db.execute(
                select(Role.name)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    principal = UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=org.id,
        name=user.name or "",
        is_active=user.is_active,
        # A provider grant selects one customer scope; it never transports
        # the provider user's platform-wide administrator authority.
        is_superuser=user.is_superuser and user.organization_id == org.id,
        is_verified=user.is_verified,
        is_external=user.is_external,
        is_provider_org=home_org.is_provider,
        roles=list(role_names),
    )
    return principal, identity.id


async def resolve_run_external_actor(
    db: AsyncSession, run_id: UUID, caller_user_id: UUID | None,
) -> tuple[UserPrincipal, UUID, dict] | None:
    """Revalidate a run's durable verified-event link before delegated work."""
    run = await db.get(AgentRun, run_id)
    if run is None or run.event_delivery_id is None:
        return None
    delivery = await db.get(EventDelivery, run.event_delivery_id)
    event = await db.get(Event, delivery.event_id) if delivery else None
    if event is None:
        raise ExternalActorResolutionError("Originating event is unavailable")
    if event.authenticated_actor is None:
        return None
    if caller_user_id is None:
        raise ExternalActorResolutionError("Verified external run has no human caller")
    principal, identity_id = await resolve_external_actor(
        db, actor_from_record(event.authenticated_actor),
        source_org_id=event.organization_id,
    )
    if (
        principal.user_id != caller_user_id
        or identity_id != event.external_identity_id
        or principal.organization_id != run.org_id
    ):
        raise ExternalActorResolutionError("External actor grant changed after queueing")
    return principal, identity_id, event.authenticated_actor
