"""External identity resolution never guesses a tenant or a human caller."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from src.services.events.external_actors import (
    ExternalActorResolutionError,
    actor_from_record,
    actor_record,
    resolve_external_actor,
    resolve_run_external_actor,
)
from src.services.webhooks.protocol import AuthenticatedExternalActor


def _result(rows):
    result = MagicMock()
    result.all.return_value = rows
    result.scalars.return_value.all.return_value = rows
    return result


@pytest.fixture
def actor():
    return AuthenticatedExternalActor(
        provider="microsoft_teams",
        external_scope_id="tenant-A",
        external_user_id="jane-object-id",
        integration_id=uuid4(),
        external_event_id="activity-A",
        conversation_id="thread-A",
    )


def test_provenance_round_trip_contains_no_token(actor):
    record = actor_record(actor)
    assert actor_from_record(record) == actor
    assert "token" not in str(record).lower()


@pytest.mark.asyncio
async def test_resolves_active_tenant_scoped_user_and_roles(actor):
    org_id, user_id, identity_id = uuid4(), uuid4(), uuid4()
    org = SimpleNamespace(id=org_id, is_active=True, is_provider=False)
    identity = SimpleNamespace(id=identity_id, is_active=True, authorized_organization_id=None)
    user = SimpleNamespace(
        id=user_id,
        organization_id=org_id,
        is_active=True,
        is_system=False,
        is_superuser=False,
        is_verified=True,
        is_external=False,
        email="jane@example.com",
        name="Jane",
    )
    db = AsyncMock()
    db.get.return_value = org
    db.execute.side_effect = [
        _result([(SimpleNamespace(organization_id=org_id), org)]),
        _result([(identity, user)]),
        _result(["Tier 2"]),
    ]
    principal, resolved_id = await resolve_external_actor(db, actor)
    assert resolved_id == identity_id
    assert principal.user_id == user_id
    assert principal.organization_id == org_id
    assert principal.roles == ["Tier 2"]
    assert not principal.is_superuser


@pytest.mark.asyncio
@pytest.mark.parametrize("mapping_count", [0, 2])
async def test_unknown_or_ambiguous_tenant_fails_closed(actor, mapping_count):
    db = AsyncMock()
    db.execute.return_value = _result(
        [
            (SimpleNamespace(), SimpleNamespace(is_active=True))
            for _ in range(mapping_count)
        ]
    )
    with pytest.raises(ExternalActorResolutionError, match="unique organization"):
        await resolve_external_actor(db, actor)
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_sender_fails_closed(actor):
    org = SimpleNamespace(id=uuid4(), is_active=True)
    db = AsyncMock()
    db.execute.side_effect = [_result([(SimpleNamespace(), org)]), _result([])]
    with pytest.raises(ExternalActorResolutionError, match="unique Bifrost identity"):
        await resolve_external_actor(db, actor)


@pytest.mark.asyncio
async def test_sender_mapped_to_another_org_fails_closed(actor):
    org = SimpleNamespace(id=uuid4(), is_active=True)
    identity = SimpleNamespace(id=uuid4(), is_active=True, authorized_organization_id=None)
    user = SimpleNamespace(is_active=True, is_system=False, organization_id=uuid4())
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(is_active=True, is_provider=False)
    db.execute.side_effect = [
        _result([(SimpleNamespace(), org)]),
        _result([(identity, user)]),
    ]
    with pytest.raises(ExternalActorResolutionError, match="outside the tenant"):
        await resolve_external_actor(db, actor)


@pytest.mark.asyncio
async def test_source_org_mismatch_fails_closed(actor):
    org = SimpleNamespace(id=uuid4(), is_active=True)
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(is_active=True, is_provider=False)
    db.execute.return_value = _result([(SimpleNamespace(), org)])
    with pytest.raises(ExternalActorResolutionError, match="out of scope"):
        await resolve_external_actor(db, actor, source_org_id=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("granted", [False, True])
async def test_provider_user_requires_explicit_customer_grant(actor, granted):
    customer_id, provider_id, user_id = uuid4(), uuid4(), uuid4()
    customer = SimpleNamespace(id=customer_id, is_active=True, is_provider=False)
    provider = SimpleNamespace(id=provider_id, is_active=True, is_provider=True)
    identity = SimpleNamespace(
        id=uuid4(), is_active=True,
        authorized_organization_id=customer_id if granted else None,
    )
    user = SimpleNamespace(
        id=user_id, organization_id=provider_id, is_active=True,
        is_system=False, is_superuser=True, is_verified=True,
        is_external=False, email="jane@mtg.example", name="Jane",
    )
    db = AsyncMock()
    db.get.return_value = provider
    db.execute.side_effect = [
        _result([(SimpleNamespace(), customer)]),
        _result([(identity, user)]),
        _result(["Tier 2"]),
    ]
    if granted:
        principal, _ = await resolve_external_actor(db, actor)
        assert principal.user_id == user_id
        assert principal.organization_id == customer_id
        assert principal.is_provider_org
        assert not principal.is_superuser
    else:
        with pytest.raises(ExternalActorResolutionError, match="outside the tenant"):
            await resolve_external_actor(db, actor)


@pytest.mark.asyncio
async def test_run_revalidates_durable_verified_event_link(actor):
    run_id, delivery_id, event_id = uuid4(), uuid4(), uuid4()
    user_id, org_id, identity_id = uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(event_delivery_id=delivery_id, org_id=org_id)
    delivery = SimpleNamespace(event_id=event_id)
    event = SimpleNamespace(
        authenticated_actor=actor_record(actor), external_identity_id=identity_id,
        organization_id=org_id,
    )
    principal = SimpleNamespace(user_id=user_id, organization_id=org_id)
    db = AsyncMock()
    db.get.side_effect = [run, delivery, event]
    with patch(
        "src.services.events.external_actors.resolve_external_actor",
        new=AsyncMock(return_value=(principal, identity_id)),
    ):
        resolved = await resolve_run_external_actor(db, run_id, user_id)
    assert resolved == (principal, identity_id, event.authenticated_actor)

    db.get.side_effect = [run, delivery, event]
    with patch(
        "src.services.events.external_actors.resolve_external_actor",
        new=AsyncMock(return_value=(principal, identity_id)),
    ):
        with pytest.raises(ExternalActorResolutionError, match="grant changed"):
            await resolve_run_external_actor(db, run_id, uuid4())
