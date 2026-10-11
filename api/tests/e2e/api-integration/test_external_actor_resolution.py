"""Resolve a verified actor against real tenant, user, and role rows."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, AsyncIterator, Mapping
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.audit import AuditLog
from src.models.orm.external_identities import ExternalIdentity
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
async def test_authenticated_agent_dispatch_releases_single_pool_connection(
    async_engine,
):
    """Authorization must release its connection before nested audit storage."""
    engine = create_async_engine(
        async_engine.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.2,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid4()
    integration_id = uuid4()
    user_id = uuid4()
    role_id = uuid4()
    identity_id = uuid4()
    agent_id = uuid4()
    tenant_id = str(uuid4())
    sender_id = str(uuid4())

    try:
        async with sessions() as setup:
            setup.add_all(
                [
                    Organization(
                        id=org_id,
                        name=f"Customer {uuid4()}",
                        created_by="agent-delivery-pool-test",
                    ),
                    Integration(id=integration_id, name=f"Teams Bot {uuid4()}"),
                    User(
                        id=user_id,
                        email=f"jane-{uuid4()}@example.com",
                        name="Jane",
                        organization_id=org_id,
                    ),
                    Role(id=role_id, name=f"Tier 2 {uuid4()}", created_by="test"),
                ]
            )
            await setup.flush()
            setup.add_all(
                [
                    IntegrationMapping(
                        integration_id=integration_id,
                        organization_id=org_id,
                        entity_id=tenant_id,
                    ),
                    ExternalIdentity(
                        id=identity_id,
                        provider="microsoft_teams",
                        external_scope_id=tenant_id,
                        external_user_id=sender_id,
                        user_id=user_id,
                    ),
                    UserRole(user_id=user_id, role_id=role_id, assigned_by="test"),
                    Agent(
                        id=agent_id,
                        name=f"Endpoint {uuid4()}",
                        system_prompt="Investigate devices",
                        organization_id=org_id,
                        access_level=AgentAccessLevel.AUTHENTICATED,
                        created_by="test",
                        is_active=True,
                    ),
                ]
            )
            await setup.commit()

        actor = AuthenticatedExternalActor(
            provider="microsoft_teams",
            external_scope_id=tenant_id,
            external_user_id=sender_id,
            integration_id=integration_id,
            external_event_id="activity-pool",
        )
        event = SimpleNamespace(
            id=uuid4(),
            event_type="microsoft_teams.message",
            data={"activity": {"text": "Investigate PC123"}},
            headers={},
            received_at=datetime.now(UTC),
            source_ip=None,
            organization_id=org_id,
            external_identity_id=identity_id,
            authenticated_actor=actor_record(actor),
        )
        delivery = SimpleNamespace(
            id=uuid4(),
            subscription=SimpleNamespace(
                agent=SimpleNamespace(id=agent_id, organization_id=org_id),
                input_mapping=None,
            ),
        )

        async with sessions() as publisher:
            @asynccontextmanager
            async def constrained_db_context() -> AsyncIterator[AsyncSession]:
                """Open nested storage only after the publisher releases its transaction."""
                assert not publisher.in_transaction()
                async with sessions() as db:
                    try:
                        yield db
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise

            async def enqueue_after_release(**_kwargs: Any) -> str:
                """Model enqueue acquisition after the publisher releases its transaction."""
                assert not publisher.in_transaction()
                return str(uuid4())

            with (
                patch("src.core.database.get_db_context", constrained_db_context),
                patch(
                    "src.services.execution.agent_run_service.enqueue_agent_run",
                    new=AsyncMock(side_effect=enqueue_after_release),
                ),
            ):
                await EventProcessor(publisher)._queue_agent_run(delivery, event)
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                delete(AuditLog).where(AuditLog.organization_id == org_id)
            )
            await cleanup.execute(delete(UserRole).where(UserRole.user_id == user_id))
            await cleanup.execute(
                delete(ExternalIdentity).where(ExternalIdentity.id == identity_id)
            )
            await cleanup.execute(delete(Agent).where(Agent.id == agent_id))
            await cleanup.execute(
                delete(IntegrationMapping).where(
                    IntegrationMapping.integration_id == integration_id
                )
            )
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.execute(delete(Role).where(Role.id == role_id))
            await cleanup.execute(
                delete(Integration).where(Integration.id == integration_id)
            )
            await cleanup.execute(
                delete(Organization).where(Organization.id == org_id)
            )
            await cleanup.commit()
        await engine.dispose()


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
    assert not principal.is_provider_org

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
    enqueue_kwargs: Mapping[str, Any] = {}
    audit_kwargs: Mapping[str, Any] = {}
    try:
        with (
            patch(
                "src.services.execution.agent_run_service.enqueue_agent_run",
                new=AsyncMock(return_value=str(uuid4())),
            ) as enqueue,
            patch("src.services.events.processor.emit_audit", new=AsyncMock()) as audit,
        ):
            await EventProcessor(db_session)._queue_agent_run(delivery, event)
            enqueue_kwargs = enqueue.await_args.kwargs
            audit_kwargs = audit.await_args.kwargs
    finally:
        # _queue_agent_run deliberately commits before audit/enqueue so the
        # rollback-scoped db_session fixture can no longer clean this test's
        # setup. Own every committed row here to preserve later E2E fixtures.
        await db_session.execute(delete(UserRole).where(UserRole.user_id == user.id))
        await db_session.execute(
            delete(ExternalIdentity).where(ExternalIdentity.id == identity.id)
        )
        await db_session.execute(delete(Agent).where(Agent.id == agent.id))
        await db_session.execute(
            delete(IntegrationMapping).where(
                IntegrationMapping.integration_id == integration.id
            )
        )
        await db_session.execute(delete(User).where(User.id == user.id))
        await db_session.execute(delete(Role).where(Role.id == role.id))
        await db_session.execute(
            delete(Integration).where(Integration.id == integration.id)
        )
        await db_session.execute(
            delete(Organization).where(Organization.id.in_([org.id, other_org.id]))
        )
        await db_session.commit()

    assert enqueue_kwargs["caller_user_id"] == str(user.id)
    assert enqueue_kwargs["caller_roles"] == [role.name]
    assert enqueue_kwargs["org_id"] == str(org.id)
    assert enqueue_kwargs["event_delivery_id"] == str(delivery.id)
    assert audit_kwargs["strict"] is True
