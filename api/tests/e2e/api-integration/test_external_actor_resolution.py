"""Resolve a verified actor against real tenant, user, and role rows."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.external_identities import ExternalIdentity
from src.models.orm.agents import Agent
from src.models.enums import AgentAccessLevel
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.services.events.external_actors import (
    ExternalActorResolutionError,
    actor_record,
    resolve_external_actor,
)
from src.services.events.processor import EventProcessor
from src.services.webhooks.protocol import AuthenticatedExternalActor


@pytest.mark.asyncio
async def test_tenant_sender_and_role_resolve_without_guessing(db_session: AsyncSession):
    org = Organization(name=f"Customer {uuid4()}", created_by="test")
    other_org = Organization(name=f"MTG {uuid4()}", created_by="test", is_provider=True)
    integration = Integration(name=f"Teams Bot {uuid4()}")
    user = User(email=f"jane-{uuid4()}@example.com", name="Jane", organization=org)
    role = Role(name=f"Tier 2 {uuid4()}", created_by="test")
    db_session.add_all([org, other_org, integration, user, role])
    await db_session.flush()

    tenant_id = str(uuid4())
    sender_id = str(uuid4())
    mapping = IntegrationMapping(
        integration_id=integration.id, organization_id=org.id, entity_id=tenant_id,
    )
    identity = ExternalIdentity(
        provider="microsoft_teams", external_scope_id=tenant_id,
        external_user_id=sender_id, user_id=user.id,
    )
    db_session.add_all([
        mapping, identity,
        UserRole(user_id=user.id, role_id=role.id, assigned_by="test"),
    ])
    await db_session.flush()

    actor = AuthenticatedExternalActor(
        provider="microsoft_teams", external_scope_id=tenant_id,
        external_user_id=sender_id, integration_id=integration.id,
        external_event_id="activity-A",
    )
    principal, identity_id = await resolve_external_actor(
        db_session, actor, source_org_id=other_org.id,
    )
    assert identity_id == identity.id
    assert principal.user_id == user.id
    assert principal.organization_id == org.id
    assert role.name in principal.roles

    user.organization_id = other_org.id
    with pytest.raises(ExternalActorResolutionError, match="outside the tenant"):
        await resolve_external_actor(db_session, actor)

    identity.authorized_organization_id = org.id
    principal, _ = await resolve_external_actor(db_session, actor)
    assert principal.organization_id == org.id
    assert principal.is_provider_org

    agent = Agent(
        name=f"Endpoint {uuid4()}", system_prompt="Investigate devices",
        organization_id=org.id, access_level=AgentAccessLevel.AUTHENTICATED,
        created_by="test", is_active=True,
    )
    db_session.add(agent)
    await db_session.flush()
    event = SimpleNamespace(
        id=uuid4(), event_type="microsoft_teams.message",
        data={"activity": {"text": "Investigate PC123"}},
        headers={}, received_at=datetime.now(UTC), source_ip=None,
        organization_id=org.id, external_identity_id=identity.id,
        authenticated_actor=actor_record(actor),
    )
    delivery = SimpleNamespace(
        id=uuid4(), subscription=SimpleNamespace(agent=agent, input_mapping=None),
    )
    with (
        patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=str(uuid4())),
        ) as enqueue,
        patch("src.services.events.processor.emit_audit", new=AsyncMock()) as audit,
    ):
        await EventProcessor(db_session)._queue_agent_run(delivery, event)
    assert enqueue.await_args.kwargs["caller_user_id"] == str(user.id)
    assert enqueue.await_args.kwargs["caller_roles"] == [role.name]
    assert enqueue.await_args.kwargs["org_id"] == str(org.id)
    assert enqueue.await_args.kwargs["event_delivery_id"] == str(delivery.id)
    assert audit.await_args.kwargs["strict"] is True
