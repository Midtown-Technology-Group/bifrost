"""Admission uses the SDK's existing scope and external-caller gates."""
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from src.core.principal import UserPrincipal
from src.models.contracts.cli import SDKIntegrationRequestSlotRequest
from src.routers import cli


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [cli.sdk_integration_request_slot_acquire, cli.sdk_integration_request_slot_release])
@pytest.mark.parametrize("scope", ["global", str(uuid4())])
async def test_cross_org_and_global_denied_before_policy_read(monkeypatch, scope, handler):
    monkeypatch.setattr(cli, "_is_provider_org", AsyncMock(return_value=False))
    user = UserPrincipal(user_id=uuid4(), email="user@example.test", organization_id=uuid4())
    request = SDKIntegrationRequestSlotRequest(name="Example", scope=scope, token=uuid4())
    with pytest.raises(HTTPException) as exc:
        await handler(request, user, AsyncMock())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [cli.sdk_integration_request_slot_acquire, cli.sdk_integration_request_slot_release])
async def test_external_caller_cannot_contend_for_integration(handler):
    user = UserPrincipal(
        user_id=uuid4(), email="guest@example.test", organization_id=uuid4(),
        is_external=True, is_engine_token=True, engine_execution_id=uuid4(),
    )
    request = SDKIntegrationRequestSlotRequest(name="Example", token=uuid4())
    with pytest.raises(HTTPException) as exc:
        await handler(request, user, AsyncMock())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [cli.sdk_integration_request_slot_acquire, cli.sdk_integration_request_slot_release])
async def test_ordinary_internal_caller_cannot_reserve_own_scope(handler):
    org_id = uuid4()
    user = UserPrincipal(user_id=uuid4(), email="user@example.test", organization_id=org_id)
    request = SDKIntegrationRequestSlotRequest(name="Example", scope=str(org_id), token=uuid4())
    with pytest.raises(HTTPException) as exc:
        await handler(request, user, AsyncMock())
    assert exc.value.status_code == 403
    assert "execution credentials or platform access" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_declared_missing_integration_preserves_required_connection_error(monkeypatch):
    from src.repositories import integrations
    repo = AsyncMock()
    repo.get_request_slot_policy.return_value = None
    monkeypatch.setattr(integrations, "IntegrationsRepository", lambda db: repo)
    monkeypatch.setattr(cli, "_is_external_user_db", AsyncMock(return_value=False))
    monkeypatch.setattr(cli, "_connection_is_declared", AsyncMock(return_value=True))
    user = UserPrincipal(user_id=uuid4(), email="admin@example.test", organization_id=None, is_superuser=True)
    request = SDKIntegrationRequestSlotRequest(name="Example", token=uuid4(), solution=uuid4())
    with pytest.raises(HTTPException) as exc:
        await cli._request_slot_integration(request, user, AsyncMock())
    assert exc.value.status_code == 424


@pytest.mark.asyncio
async def test_owner_token_is_bound_to_authenticated_principal(monkeypatch):
    from src.repositories import integrations
    repo = AsyncMock()
    repo.get_request_slot_policy.return_value = (uuid4(), 2)
    monkeypatch.setattr(integrations, "IntegrationsRepository", lambda db: repo)
    monkeypatch.setattr(cli, "_is_external_user_db", AsyncMock(return_value=False))
    request = SDKIntegrationRequestSlotRequest(name="Example", token=uuid4())
    user1 = UserPrincipal(user_id=uuid4(), email="one@example.test", organization_id=None, is_superuser=True)
    user2 = UserPrincipal(user_id=uuid4(), email="two@example.test", organization_id=None, is_superuser=True)
    _, _, owner1, principal1 = await cli._request_slot_integration(request, user1, AsyncMock())
    _, _, owner2, principal2 = await cli._request_slot_integration(request, user2, AsyncMock())
    assert owner1 != owner2
    assert principal1 != principal2


@pytest.mark.asyncio
async def test_execution_principal_is_stable_across_tokens(monkeypatch):
    from src.repositories import integrations
    repo = AsyncMock()
    repo.get_request_slot_policy.return_value = (uuid4(), 2)
    monkeypatch.setattr(integrations, "IntegrationsRepository", lambda db: repo)
    monkeypatch.setattr(cli, "_is_external_user_db", AsyncMock(return_value=False))
    execution_id = uuid4()
    user = UserPrincipal(
        user_id=uuid4(), email="engine@example.test", organization_id=uuid4(),
        is_engine_token=True, engine_execution_id=execution_id,
    )
    requests = [SDKIntegrationRequestSlotRequest(name="Example", token=uuid4()) for _ in range(2)]
    results = [await cli._request_slot_integration(request, user, AsyncMock()) for request in requests]
    assert results[0][3] == results[1][3] == str(execution_id)
    assert results[0][2] != results[1][2]
    assert all(result[2].startswith(f"{execution_id}:") for result in results)


@pytest.mark.asyncio
async def test_distinct_executions_have_distinct_principals(monkeypatch):
    from src.repositories import integrations
    repo = AsyncMock()
    repo.get_request_slot_policy.return_value = (uuid4(), 2)
    monkeypatch.setattr(integrations, "IntegrationsRepository", lambda db: repo)
    monkeypatch.setattr(cli, "_is_external_user_db", AsyncMock(return_value=False))
    request = SDKIntegrationRequestSlotRequest(name="Example", token=uuid4())
    users = [
        UserPrincipal(
            user_id=uuid4(), email="engine@example.test", organization_id=uuid4(),
            is_engine_token=True, engine_execution_id=uuid4(),
        )
        for _ in range(2)
    ]
    results = [await cli._request_slot_integration(request, user, AsyncMock()) for user in users]
    assert results[0][3] != results[1][3]
    assert results[0][2] != results[1][2]


@pytest.mark.asyncio
async def test_legacy_engine_uses_signed_execution_principal(monkeypatch):
    from src.repositories import integrations
    repo = AsyncMock()
    repo.get_request_slot_policy.return_value = (uuid4(), 2)
    monkeypatch.setattr(integrations, "IntegrationsRepository", lambda db: repo)
    monkeypatch.setattr(cli, "_is_external_user_db", AsyncMock(return_value=False))
    shared_user_id = uuid4()
    request = SDKIntegrationRequestSlotRequest(name="Example", token=uuid4())
    users = [
        UserPrincipal(
            user_id=shared_user_id,
            email="engine@example.test",
            organization_id=None,
            is_superuser=True,
            is_engine_token=True,
            engine_legacy_execution_id=uuid4(),
        )
        for _ in range(2)
    ]

    results = [
        await cli._request_slot_integration(request, user, AsyncMock())
        for user in users
    ]

    assert results[0][3] != results[1][3]
    assert results[0][3] != str(shared_user_id)
    assert results[1][3] != str(shared_user_id)
