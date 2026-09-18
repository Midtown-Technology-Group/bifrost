"""Unit tests for _apply_callback_to_integration — integration-level entity_id capture.

Companion to test_oauth_per_mapping_callback.py, which covers the per-mapping
path. These tests exercise the helper directly so they avoid the full HTTP
stack and the external token endpoint.
"""

from __future__ import annotations

import base64
import json

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import OAuthProvider
from src.models.orm.integrations import Integration


async def _make_integration(db: AsyncSession, entity_id: str | None = None) -> Integration:
    integration = Integration(
        id=uuid4(),
        name=f"test-integ-{uuid4().hex[:8]}",
        entity_id=entity_id,
    )
    db.add(integration)
    await db.flush()
    return integration


async def _make_provider(
    db: AsyncSession, integration: Integration, entity_id_source: dict | None = None
) -> OAuthProvider:
    provider = OAuthProvider(
        id=uuid4(),
        provider_name=f"test-provider-{uuid4().hex[:8]}",
        client_id="test-client-id",
        encrypted_client_secret=b"encrypted",
        entity_id_source=entity_id_source,
        integration_id=integration.id,
    )
    db.add(provider)
    await db.flush()
    return provider


@pytest.mark.asyncio
async def test_integration_callback_captures_entity_id(db_session: AsyncSession):
    """A url_param entity_id source lands on the integration."""
    from src.routers.oauth_connections import _apply_callback_to_integration

    integration = await _make_integration(db_session)
    provider = await _make_provider(
        db_session, integration, entity_id_source={"type": "url_param", "key": "realmId"}
    )

    captured = await _apply_callback_to_integration(
        db=db_session,
        provider=provider,
        callback_url_params={"realmId": "9130350000000000"},
        token_response={"access_token": "x"},
    )

    await db_session.refresh(integration)
    assert captured == "9130350000000000"
    assert integration.entity_id == "9130350000000000"


@pytest.mark.asyncio
async def test_integration_callback_captures_token_response_field(db_session: AsyncSession):
    """A token_response_field entity_id source lands on the integration."""
    from src.routers.oauth_connections import _apply_callback_to_integration

    integration = await _make_integration(db_session)
    provider = await _make_provider(
        db_session,
        integration,
        entity_id_source={"type": "token_response_field", "key": "realm_id"},
    )

    captured = await _apply_callback_to_integration(
        db=db_session,
        provider=provider,
        callback_url_params={},
        token_response={"access_token": "x", "realm_id": "5555"},
    )

    await db_session.refresh(integration)
    assert captured == "5555"
    assert integration.entity_id == "5555"


@pytest.mark.asyncio
async def test_integration_callback_captures_id_token_claim(db_session: AsyncSession):
    """An id_token_claim entity_id source lands on the integration."""
    from src.routers.oauth_connections import _apply_callback_to_integration

    integration = await _make_integration(db_session)
    provider = await _make_provider(
        db_session, integration, entity_id_source={"type": "id_token_claim", "key": "sub"}
    )
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "claim-123"}).encode()).decode().rstrip("=")
    id_token = f"header.{payload}.signature"

    captured = await _apply_callback_to_integration(
        db=db_session,
        provider=provider,
        callback_url_params={},
        token_response={"access_token": "x", "id_token": id_token},
    )

    await db_session.refresh(integration)
    assert captured == "claim-123"
    assert integration.entity_id == "claim-123"


@pytest.mark.asyncio
async def test_integration_callback_preserves_manual_override(db_session: AsyncSession):
    """A non-empty integration.entity_id is never overwritten."""
    from src.routers.oauth_connections import _apply_callback_to_integration

    integration = await _make_integration(db_session, entity_id="manual-override")
    provider = await _make_provider(
        db_session, integration, entity_id_source={"type": "url_param", "key": "realmId"}
    )

    captured = await _apply_callback_to_integration(
        db=db_session,
        provider=provider,
        callback_url_params={"realmId": "9130350000000000"},
        token_response={"access_token": "x"},
    )

    await db_session.refresh(integration)
    assert captured is None
    assert integration.entity_id == "manual-override"


@pytest.mark.asyncio
async def test_integration_callback_without_source_is_noop(db_session: AsyncSession):
    """No entity_id_source means no capture and no write."""
    from src.routers.oauth_connections import _apply_callback_to_integration

    integration = await _make_integration(db_session)
    provider = await _make_provider(db_session, integration, entity_id_source=None)

    captured = await _apply_callback_to_integration(
        db=db_session,
        provider=provider,
        callback_url_params={"realmId": "9130350000000000"},
        token_response={"access_token": "x"},
    )

    await db_session.refresh(integration)
    assert captured is None
    assert not integration.entity_id


@pytest.mark.asyncio
async def test_integration_callback_extraction_miss_is_noop(db_session: AsyncSession):
    """A configured source whose key is absent leaves the integration unchanged."""
    from src.routers.oauth_connections import _apply_callback_to_integration

    integration = await _make_integration(db_session)
    provider = await _make_provider(
        db_session, integration, entity_id_source={"type": "url_param", "key": "tenant"}
    )

    captured = await _apply_callback_to_integration(
        db=db_session,
        provider=provider,
        callback_url_params={"realmId": "9130350000000000"},
        token_response={"access_token": "x"},
    )

    await db_session.refresh(integration)
    assert captured is None
    assert not integration.entity_id
