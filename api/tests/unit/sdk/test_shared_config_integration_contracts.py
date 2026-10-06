"""Contracts for imported services retained pending fork router adoption.

These call the shared services directly. They complement the canonical-router
tests; they do not claim that the fork's production routes use these services.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from shared import sdk_config, sdk_integrations
from src.core.security import decrypt_secret, encrypt_secret
from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.organizations import Organization


@pytest_asyncio.fixture
async def storage(db_session, monkeypatch):
    """Own committed fixtures and exercise repository fallback with cache down."""
    orgs = [Organization(name=f"contract-{uuid4().hex}", created_by="contract")
            for _ in range(2)]
    integration = Integration(name=f"contract-{uuid4().hex}", default_entity_id="default")
    db_session.add_all([*orgs, integration])
    await db_session.commit()
    org_ids = [org.id for org in orgs]
    integration_id = integration.id
    stem = f"contract-{uuid4().hex}-"
    monkeypatch.setattr(
        "src.core.cache.redis_client.get_shared_redis",
        AsyncMock(side_effect=ConnectionError("contract cache unavailable")),
    )
    monkeypatch.setattr("src.core.cache.upsert_config", AsyncMock())
    monkeypatch.setattr("src.core.cache.invalidate_config", AsyncMock())
    yield db_session, orgs, integration, stem
    await db_session.rollback()
    await db_session.execute(delete(Config).where(Config.key.startswith(stem)))
    await db_session.execute(delete(Integration).where(Integration.id == integration_id))
    await db_session.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db_session.commit()


@pytest.mark.parametrize("value,kind", [
    ({"nested": [1, True]}, "json"), ([1, "two"], "json"),
    (True, "bool"), (False, "bool"), (42, "int"), ("plain", "string"),
])
async def test_config_round_trip_update_and_delete(storage, value, kind):
    db, orgs, _, stem = storage
    key = stem + "value"
    args = dict(key=key, org_id=orgs[0].id, actor_email="operator@contract")
    await sdk_config.set_sdk_config_value(db, value=value, is_secret=False, **args)
    read = await sdk_config.get_sdk_config_dict(db, key=key, org_id=orgs[0].id, external=True)
    assert read == {"key": key, "value": value, "config_type": kind}
    assert (await sdk_config.list_sdk_config_values(db, org_id=orgs[0].id, external=True))[key] == value
    assert await sdk_config.get_sdk_config_dict(db, key=key, org_id=orgs[1].id, external=True) is None
    await sdk_config.set_sdk_config_value(db, value="updated", is_secret=False, **args)
    rows = (await db.execute(select(Config).where(Config.key == key))).scalars().all()
    assert len(rows) == 1
    assert rows[0].updated_by == "operator@contract"
    assert await sdk_config.delete_sdk_config_value(db, **args) is True
    assert await sdk_config.delete_sdk_config_value(db, **args) is False
    assert await sdk_config.list_sdk_config_values(db, org_id=orgs[0].id, external=True) == {}


async def test_secret_encryption_redaction_and_external_global_boundary(storage):
    db, orgs, _, stem = storage
    key = stem + "secret"
    await sdk_config.set_sdk_config_value(
        db, key=key, value="global-secret", is_secret=True,
        org_id=None, actor_email="operator@contract",
    )
    stored = (await db.execute(select(Config).where(Config.key == key))).scalar_one()
    assert stored.value["value"] != "global-secret"
    assert decrypt_secret(stored.value["value"]) == "global-secret"
    assert await sdk_config.get_sdk_config_dict(db, key=key, org_id=orgs[0].id, external=True) is None
    assert key not in await sdk_config.list_sdk_config_values(db, org_id=orgs[0].id, external=True)
    assert (await sdk_config.get_sdk_config_dict(db, key=key, org_id=orgs[0].id, external=False))["value"] == "global-secret"
    assert (await sdk_config.list_sdk_config_values(db, org_id=None, external=False))[key] == "[SECRET]"


@pytest.mark.parametrize("kind,raw,expected", [
    (ConfigType.JSON, '{"n":1}', {"n": 1}), (ConfigType.JSON, "{broken", "{broken"),
    (ConfigType.BOOL, "true", True), (ConfigType.BOOL, "FALSE", False),
    (ConfigType.BOOL, 1, True), (ConfigType.INT, "12", 12),
    (ConfigType.INT, "invalid", "invalid"), (ConfigType.INT, None, None),
    (ConfigType.SECRET, "corrupt-ciphertext", None),
])
async def test_legacy_config_values_have_consistent_read_and_list_semantics(storage, kind, raw, expected):
    db, orgs, _, stem = storage
    key = stem + "legacy"
    db.add(Config(key=key, value={"value": raw}, config_type=kind,
                  organization_id=orgs[0].id, updated_by="contract"))
    await db.flush()
    read = await sdk_config.get_sdk_config_dict(db, key=key, org_id=orgs[0].id, external=True)
    assert read["value"] == expected
    listed = await sdk_config.list_sdk_config_values(db, org_id=orgs[0].id, external=True)
    assert listed[key] == ("[SECRET]" if kind == ConfigType.SECRET else expected)


async def test_integration_defaults_and_org_overrides_do_not_leak_to_external(storage):
    db, orgs, integration, stem = storage
    for org_id, suffix, value in [(None, "global", "global-value"), (orgs[0].id, "own", "own-value")]:
        db.add(Config(key=stem + suffix, value={"value": value}, config_type=ConfigType.STRING,
                      integration_id=integration.id, organization_id=org_id, updated_by="contract"))
    db.add(IntegrationMapping(integration_id=integration.id, organization_id=orgs[0].id,
                              entity_id="own-entity", entity_name="Own"))
    await db.flush()
    normal = await sdk_integrations.get_sdk_integration_dict(db, name=integration.name, org_id=orgs[0].id)
    external = await sdk_integrations.get_sdk_integration_dict(db, name=integration.name, org_id=orgs[0].id, external=True)
    assert normal["entity_id"] == external["entity_id"] == "own-entity"
    assert normal["config"] == {stem + "global": "global-value", stem + "own": "own-value"}
    assert external["config"] == {stem + "own": "own-value"}
    defaults = await sdk_integrations.get_sdk_integration_dict(db, name=integration.name, org_id=orgs[1].id)
    assert defaults["entity_id"] == "default"
    assert defaults["config"] == {stem + "global": "global-value"}
    external_defaults = await sdk_integrations.get_sdk_integration_dict(db, name=integration.name, org_id=orgs[1].id, external=True)
    assert external_defaults["config"] == {}


async def test_mapping_crud_scope_and_missing_entity_contract(storage):
    db, orgs, integration, _ = storage
    args = dict(name=integration.name, scope=None, caller_org_id=orgs[0].id, is_platform_admin=False)
    created = await sdk_integrations.upsert_sdk_integration_mapping(
        db, **args, entity_id="tenant-one", actor_email="operator@contract",
    )
    updated = await sdk_integrations.upsert_sdk_integration_mapping(
        db, **args, entity_id="tenant-updated", entity_name="Renamed", actor_email="operator@contract",
    )
    assert created["id"] == updated["id"]
    own = await sdk_integrations.list_sdk_integration_mappings(db, **args)
    assert [row["entity_id"] for row in own] == ["tenant-updated"]
    assert await sdk_integrations.get_sdk_integration_mapping_dict(db, **args) == updated
    other = {**args, "caller_org_id": orgs[1].id}
    assert await sdk_integrations.get_sdk_integration_mapping_dict(db, **other, entity_id="tenant-updated") is None
    assert await sdk_integrations.list_sdk_integration_mappings(db, **other) == []
    global_args = {**args, "scope": "global", "is_platform_admin": True}
    assert (await sdk_integrations.get_sdk_integration_mapping_dict(db, **global_args, entity_id="tenant-updated"))["id"] == created["id"]
    assert len(await sdk_integrations.list_sdk_integration_mappings(db, **global_args)) == 1
    assert await sdk_integrations.list_sdk_integration_mappings(db, **{**args, "caller_org_id": None}) == []
    with pytest.raises(sdk_config.ScopeResolutionError) as exc:
        await sdk_integrations.delete_sdk_integration_mapping(db, **{**args, "scope": str(orgs[1].id)})
    assert exc.value.status_code == 403
    assert await sdk_integrations.delete_sdk_integration_mapping(db, **global_args) == {"deleted": False}
    assert await sdk_integrations.delete_sdk_integration_mapping(db, **other) == {"deleted": False}
    assert await sdk_integrations.delete_sdk_integration_mapping(db, **args) == {"deleted": True}
    assert await sdk_integrations.delete_sdk_integration_mapping(db, **args) == {"deleted": False}


async def test_mapping_errors_preserve_missing_name_precedence_and_audit_requirement(storage):
    db, orgs, integration, _ = storage
    args = dict(scope="malformed", caller_org_id=orgs[0].id, is_platform_admin=False)
    assert await sdk_integrations.list_sdk_integration_mappings(db, name="missing-contract", **args) is None
    assert await sdk_integrations.get_sdk_integration_mapping_dict(db, name="missing-contract", **args) is None
    assert await sdk_integrations.delete_sdk_integration_mapping(db, name="missing-contract", **args) == {"deleted": False}
    with pytest.raises(sdk_integrations.IntegrationServiceError) as exc:
        await sdk_integrations.upsert_sdk_integration_mapping(db, name="missing-contract", **args, entity_id="x")
    assert exc.value.status_code == 404
    for scope, admin, email, status in [("global", True, "audit@contract", 400), (None, False, None, 500)]:
        with pytest.raises(sdk_integrations.IntegrationServiceError) as exc:
            await sdk_integrations.upsert_sdk_integration_mapping(
                db, name=integration.name, scope=scope, caller_org_id=orgs[0].id,
                is_platform_admin=admin, entity_id="x", actor_email=email,
            )
        assert exc.value.status_code == status
    assert await sdk_integrations.get_sdk_integration_dict(db, name="missing-contract", org_id=None) is None
    for solution in [None, "", "malformed", uuid4()]:
        assert await sdk_integrations.connection_is_declared(db, solution, "missing-contract") is False


@pytest.mark.parametrize("external", [False, True])
async def test_oauth_stored_tokens_are_decrypted_but_external_client_secret_is_withheld(external):
    now = datetime.now(timezone.utc)
    provider = SimpleNamespace(
        provider_name="contract", client_id="client", encrypted_client_secret=encrypt_secret("provider-secret").encode(),
        token_url="https://example.invalid/{entity_id}/token", token_url_defaults={},
        oauth_flow_type="authorization_code", authorization_url="https://example.invalid/auth", scopes=["read"],
    )
    token = SimpleNamespace(encrypted_access_token=encrypt_secret("access").encode(),
                            encrypted_refresh_token=encrypt_secret("refresh"), expires_at=now)
    result = await sdk_integrations.build_oauth_data(
        provider, token, "tenant", lambda **kw: kw["url"].replace("{entity_id}", kw["entity_id"]),
        decrypt_secret, external=external,
    )
    assert result["access_token"] == "access"
    assert result["refresh_token"] == "refresh"
    assert result["expires_at"] == now.isoformat()
    assert result["token_url"] == "https://example.invalid/tenant/token"
    assert result["client_secret"] == (None if external else "provider-secret")


@pytest.mark.parametrize("success", [True, False])
async def test_oauth_client_credentials_use_resolved_audience_without_stored_token_fallback(monkeypatch, success):
    provider = SimpleNamespace(
        provider_name="contract", client_id="client", encrypted_client_secret=encrypt_secret("provider-secret"),
        token_url="https://example.invalid/{entity_id}/token", token_url_defaults={},
        oauth_flow_type="client_credentials", authorization_url=None, scopes=["default"], audience="resource",
    )
    client = AsyncMock()
    client.get_client_credentials_token.return_value = (success, {"access_token": "fresh", "expires_at": "expiry", "error": "denied"})
    monkeypatch.setattr("src.services.oauth_provider.OAuthProviderClient", lambda: client)
    result = await sdk_integrations.build_oauth_data(
        provider, None, "tenant", lambda **kw: kw["url"].replace("{entity_id}", kw["entity_id"]),
        decrypt_secret, oauth_scope="specific",
    )
    client.get_client_credentials_token.assert_awaited_once_with(
        token_url="https://example.invalid/tenant/token", client_id="client", client_secret="provider-secret",
        scopes="specific", audience="resource",
    )
    assert result["access_token"] == ("fresh" if success else None)
    assert result["expires_at"] == ("expiry" if success else None)
