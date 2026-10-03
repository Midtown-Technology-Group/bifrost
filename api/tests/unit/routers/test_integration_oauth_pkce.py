"""Regression tests for PKCE in third-party integration OAuth flows.

Covers the explicit ``provider_metadata.use_pkce`` opt-in across:

- integration-level authorize (``oauth_connections.authorize_connection``)
- integration-level authorize (``integrations.get_oauth_authorization_url``)
- per-mapping authorize (``integrations.authorize_mapping``)
- the shared callback (``oauth_connections.oauth_callback``)
- the token-exchange client (``OAuthProviderClient``)

Behavioral contract under test:

- PKCE providers get ``code_challenge`` + ``code_challenge_method=S256``
  on the authorize URL and ``code_verifier`` on the token exchange.
- Non-PKCE providers get neither (existing confidential-client behavior
  is unchanged).
- A consumed/missing verifier fails closed on PKCE providers and is
  ignored on non-PKCE providers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.oauth import (
    CreateOAuthConnectionRequest,
    OAuthCallbackRequest,
)
from src.routers import integrations, oauth_connections
from src.services import oauth_pkce
from src.services.oauth_pkce import (
    build_pkce_authorize_params,
    code_challenge_for,
    generate_code_verifier,
    provider_uses_pkce,
    validate_pkce_binding,
)

PROVIDER_ID = UUID("33333333-3333-3333-3333-333333333333")
MAPPING_ID = UUID("22222222-2222-2222-2222-222222222222")
INTEGRATION_ID = UUID("11111111-1111-1111-1111-111111111111")
REDIRECT_URI = "https://app.example.test/oauth/callback"


# =============================================================================
# Flag + helpers
# =============================================================================


def test_provider_uses_pkce_requires_explicit_true():
    assert provider_uses_pkce({"use_pkce": True}) is True
    assert provider_uses_pkce({"use_pkce": False}) is False
    assert provider_uses_pkce({}) is False
    assert provider_uses_pkce(None) is False
    assert provider_uses_pkce({"use_pkce": "true"}) is False
    assert provider_uses_pkce(SimpleNamespace(provider_metadata={"use_pkce": True})) is True
    assert provider_uses_pkce(SimpleNamespace(provider_metadata={})) is False
    assert provider_uses_pkce(SimpleNamespace()) is False


def test_code_challenge_is_s256_without_padding():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    challenge = code_challenge_for(verifier)
    assert "=" not in challenge
    assert challenge != verifier
    params = build_pkce_authorize_params(verifier)
    assert params == {
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }


def test_validate_pkce_binding_rejects_cross_flow_states():
    payload = {
        "code_verifier": "verifier",
        "redirect_uri": REDIRECT_URI,
        "provider_id": str(PROVIDER_ID),
    }
    # Matching round trip passes.
    validate_pkce_binding(payload, provider_id=PROVIDER_ID, redirect_uri=REDIRECT_URI)
    # State swapped across providers or redirect URIs fails.
    with pytest.raises(ValueError, match="not issued for this provider"):
        validate_pkce_binding(payload, provider_id=uuid4(), redirect_uri=REDIRECT_URI)
    with pytest.raises(ValueError, match="redirect URI"):
        validate_pkce_binding(
            payload, provider_id=PROVIDER_ID, redirect_uri="https://evil.example.test/x"
        )


def test_provider_metadata_rejects_non_boolean_use_pkce():
    with pytest.raises(ValueError, match="use_pkce must be a boolean"):
        CreateOAuthConnectionRequest(
            integration_id=str(INTEGRATION_ID),
            oauth_flow_type="authorization_code",
            client_id="client-123",
            authorization_url="https://auth.example.test/authorize",
            token_url="https://auth.example.test/token",
            provider_metadata={"use_pkce": "yes"},
        )


def test_provider_metadata_accepts_use_pkce_flag():
    request = CreateOAuthConnectionRequest(
        integration_id=str(INTEGRATION_ID),
        oauth_flow_type="authorization_code",
        client_id="client-123",
        authorization_url="https://auth.example.test/authorize",
        token_url="https://auth.example.test/token",
        provider_metadata={"use_pkce": True},
    )
    assert request.provider_metadata == {"use_pkce": True}


# =============================================================================
# Store / consume round trip (fake Redis)
# =============================================================================


class _FakeRedis:
    """Test double exposing only the atomic operations the implementation uses.

    There is deliberately no separate ``get``/``delete``: if
    ``consume_pkce_verifier`` ever regresses to GET-then-DELETE, every test
    using this double fails loudly instead of silently passing.
    """

    def __init__(self):
        self.values: dict[str, str] = {}
        self.calls: list[str] = []

    async def setex(self, key, ttl, value):
        self.calls.append("setex")
        self.values[key] = value

    async def getdel(self, key):
        self.calls.append("getdel")
        return self.values.pop(key, None)


@pytest.mark.asyncio
async def test_store_consume_round_trip_is_single_use():
    redis = _FakeRedis()
    with patch.object(oauth_pkce, "get_shared_redis", new=AsyncMock(return_value=redis)):
        await oauth_pkce.store_pkce_verifier(
            state="state-1",
            code_verifier="verifier-1",
            redirect_uri=REDIRECT_URI,
            provider_id=PROVIDER_ID,
            mapping_id=MAPPING_ID,
        )
        first = await oauth_pkce.consume_pkce_verifier("state-1")
        assert first is not None
        assert first["code_verifier"] == "verifier-1"
        assert first["redirect_uri"] == REDIRECT_URI
        assert first["provider_id"] == str(PROVIDER_ID)
        assert first["mapping_id"] == str(MAPPING_ID)
        # Replay finds nothing: the verifier was consumed exactly once.
        assert await oauth_pkce.consume_pkce_verifier("state-1") is None
        # Unknown states also read as absent.
        assert await oauth_pkce.consume_pkce_verifier("never-stored") is None


@pytest.mark.asyncio
async def test_concurrent_consume_has_exactly_one_winner():
    """Concurrent racers for one state resolve to a single payload holder.

    The double's ``getdel`` is one indivisible step, mirroring Redis
    GETDEL: whatever interleaving the event loop chooses, exactly one
    caller observes the payload. (True server-side atomicity is proven by
    the e2e concurrent-callback test against stack Redis.)
    """
    redis = _FakeRedis()
    with patch.object(oauth_pkce, "get_shared_redis", new=AsyncMock(return_value=redis)):
        await oauth_pkce.store_pkce_verifier(
            state="state-race",
            code_verifier="verifier-race",
            redirect_uri=REDIRECT_URI,
            provider_id=PROVIDER_ID,
        )
        results = await asyncio.gather(
            *(oauth_pkce.consume_pkce_verifier("state-race") for _ in range(8))
        )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0]["code_verifier"] == "verifier-race"
    assert redis.calls.count("getdel") == 8


# =============================================================================
# Authorize endpoints
# =============================================================================


def _ctx(*, org_id=None):
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    return SimpleNamespace(
        org_id=org_id,
        db=db,
        user=SimpleNamespace(
            email="admin@example.com",
            user_id=uuid4(),
            name="Admin User",
        ),
    )


def _connection_provider(**overrides):
    data = {
        "id": PROVIDER_ID,
        "provider_name": "goto",
        "display_name": "GoTo",
        "oauth_flow_type": "authorization_code",
        "client_id": "client-123",
        "encrypted_client_secret": b"encrypted-secret",
        "authorization_url": "https://auth.example.test/oauth/authorize",
        "token_url": "https://auth.example.test/oauth/token",
        "scopes": ["read"],
        "audience": None,
        "provider_metadata": {},
        "status": "not_connected",
        "status_message": None,
        "integration_id": INTEGRATION_ID,
        "organization_id": None,
        "entity_id_source": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _repo(**methods):
    repo = MagicMock()
    for name, value in methods.items():
        setattr(repo, name, AsyncMock(return_value=value))
    return repo


@pytest.mark.asyncio
async def test_connection_authorize_pkce_adds_challenge_and_stores_verifier():
    provider = _connection_provider(provider_metadata={"use_pkce": True})
    repo = _repo(get_by_connection_name=provider, update_status=None)

    with (
        patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo),
        patch.object(oauth_connections.secrets, "token_urlsafe", return_value="state-token"),
        patch.object(
            oauth_connections,
            "get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            oauth_connections,
            "generate_code_verifier",
            return_value="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
        ),
        patch.object(
            oauth_connections, "store_pkce_verifier", new=AsyncMock()
        ) as store,
    ):
        result = await oauth_connections.authorize_connection(
            "goto", _ctx(), MagicMock(), redirect_uri=REDIRECT_URI
        )

    assert result.state == "state-token"
    expected_challenge = code_challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
    assert f"code_challenge={expected_challenge}" in result.authorization_url
    assert "code_challenge_method=S256" in result.authorization_url
    assert "code_verifier=" not in result.authorization_url
    store.assert_awaited_once()
    assert store.await_args.kwargs["state"] == "state-token"
    assert store.await_args.kwargs["redirect_uri"] == REDIRECT_URI
    assert store.await_args.kwargs["provider_id"] == PROVIDER_ID
    assert (
        store.await_args.kwargs["code_verifier"]
        == "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    )


@pytest.mark.asyncio
async def test_connection_authorize_without_pkce_sends_no_challenge():
    provider = _connection_provider()
    repo = _repo(get_by_connection_name=provider, update_status=None)

    with (
        patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo),
        patch.object(oauth_connections.secrets, "token_urlsafe", return_value="state-token"),
        patch.object(
            oauth_connections,
            "get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            oauth_connections, "store_pkce_verifier", new=AsyncMock()
        ) as store,
    ):
        result = await oauth_connections.authorize_connection(
            "goto", _ctx(), MagicMock(), redirect_uri=REDIRECT_URI
        )

    assert "code_challenge" not in result.authorization_url
    assert "code_verifier" not in result.authorization_url
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_integration_authorize_url_pkce_adds_challenge():
    provider = _connection_provider(provider_metadata={"use_pkce": True})
    repo = _repo(get_oauth_provider=provider)

    with (
        patch.object(integrations, "IntegrationsRepository", return_value=repo),
        patch.object(integrations.secrets, "token_urlsafe", return_value="state-token"),
        patch.object(
            integrations, "store_pkce_verifier", new=AsyncMock()
        ) as store,
    ):
        result = await integrations.get_oauth_authorization_url(
            INTEGRATION_ID, _ctx(), MagicMock(), redirect_uri=REDIRECT_URI
        )

    assert result.state == "state-token"
    assert "code_challenge=" in result.authorization_url
    assert "code_challenge_method=S256" in result.authorization_url
    assert "code_verifier=" not in result.authorization_url
    store.assert_awaited_once()
    assert store.await_args.kwargs["state"] == "state-token"


@pytest.mark.asyncio
async def test_mapping_authorize_pkce_binds_mapping_context():
    from src.models import MappingAuthorizeRequest

    provider = _connection_provider(provider_metadata={"use_pkce": True})
    integration = SimpleNamespace(id=INTEGRATION_ID, oauth_provider=provider)
    mapping = SimpleNamespace(id=MAPPING_ID, integration_id=INTEGRATION_ID)
    repo = _repo(get_integration_by_id=integration, get_mapping_by_id=mapping)

    async def _remember(nonce):
        return None

    with (
        patch.object(integrations, "IntegrationsRepository", return_value=repo),
        patch.object(
            integrations,
            "get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            integrations,
            "encode_state",
            return_value=("signed-state", "nonce-1"),
        ),
        patch.object(integrations, "remember_nonce", side_effect=_remember),
        patch.object(
            integrations, "store_pkce_verifier", new=AsyncMock()
        ) as store,
    ):
        response = await integrations.authorize_mapping(
            INTEGRATION_ID,
            MAPPING_ID,
            MappingAuthorizeRequest(redirect_uri=REDIRECT_URI),
            _ctx(),
            MagicMock(),
        )

    assert "code_challenge=" in response.authorization_url
    assert "code_challenge_method=S256" in response.authorization_url
    assert "code_verifier=" not in response.authorization_url
    store.assert_awaited_once()
    assert store.await_args.kwargs["state"] == "signed-state"
    assert store.await_args.kwargs["mapping_id"] == MAPPING_ID
    assert store.await_args.kwargs["provider_id"] == PROVIDER_ID


@pytest.mark.asyncio
async def test_mapping_authorize_without_pkce_sends_no_challenge():
    from src.models import MappingAuthorizeRequest

    provider = _connection_provider()
    integration = SimpleNamespace(id=INTEGRATION_ID, oauth_provider=provider)
    mapping = SimpleNamespace(id=MAPPING_ID, integration_id=INTEGRATION_ID)
    repo = _repo(get_integration_by_id=integration, get_mapping_by_id=mapping)

    with (
        patch.object(integrations, "IntegrationsRepository", return_value=repo),
        patch.object(
            integrations,
            "get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            integrations,
            "encode_state",
            return_value=("signed-state", "nonce-1"),
        ),
        patch.object(
            integrations, "remember_nonce", new=AsyncMock(return_value=None)
        ),
        patch.object(
            integrations, "store_pkce_verifier", new=AsyncMock()
        ) as store,
    ):
        response = await integrations.authorize_mapping(
            INTEGRATION_ID,
            MAPPING_ID,
            MappingAuthorizeRequest(redirect_uri=REDIRECT_URI),
            _ctx(),
            MagicMock(),
        )

    assert "code_challenge" not in response.authorization_url
    store.assert_not_awaited()


# =============================================================================
# Callback
# =============================================================================


def _token_result(**overrides):
    result = {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "scope": "read",
    }
    result.update(overrides)
    return result


def _callback_request(**overrides):
    data = {
        "code": "auth-code-123",
        "state": "state-token",
        "redirect_uri": REDIRECT_URI,
        "organization_id": None,
    }
    data.update(overrides)
    return OAuthCallbackRequest(**data)


@pytest.mark.asyncio
async def test_callback_pkce_sends_stored_verifier():
    provider = _connection_provider(provider_metadata={"use_pkce": True})
    stored = {
        "code_verifier": "verifier-abc",
        "redirect_uri": REDIRECT_URI,
        "provider_id": str(PROVIDER_ID),
        "mapping_id": None,
    }

    with (
        patch(
            "src.repositories.oauth.OAuthProviderRepository.get_by_connection_name",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "src.repositories.oauth.OAuthProviderRepository.store_token",
            new=AsyncMock(),
        ),
        patch(
            "src.routers.oauth_connections.get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.routers.oauth_connections.resolve_url_template",
            return_value=provider.token_url,
        ),
        patch(
            "src.services.oauth_provider.OAuthProviderClient.exchange_code_for_token",
            new=AsyncMock(return_value=(True, _token_result())),
        ) as mock_exchange,
        patch("src.core.security.decrypt_secret", return_value="decrypted-secret"),
        patch(
            "src.routers.oauth_connections.consume_pkce_verifier",
            new=AsyncMock(return_value=dict(stored)),
        ) as consume,
        patch("src.routers.oauth_connections.CACHE_INVALIDATION_AVAILABLE", False),
    ):
        result = await oauth_connections.oauth_callback(
            connection_name="goto",
            request=_callback_request(),
            ctx=_ctx(),
            user=MagicMock(),
        )

    assert result.success is True
    consume.assert_awaited_once_with("state-token")
    assert mock_exchange.await_args.kwargs["code_verifier"] == "verifier-abc"
    # Confidential-client behavior is preserved: the secret still goes out.
    assert mock_exchange.await_args.kwargs["client_secret"] == "decrypted-secret"


@pytest.mark.asyncio
async def test_callback_pkce_replay_is_rejected():
    provider = _connection_provider(provider_metadata={"use_pkce": True})

    with (
        patch(
            "src.repositories.oauth.OAuthProviderRepository.get_by_connection_name",
            new=AsyncMock(return_value=provider),
        ),
        patch("src.core.security.decrypt_secret", return_value="decrypted-secret"),
        patch(
            "src.routers.oauth_connections.consume_pkce_verifier",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.services.oauth_provider.OAuthProviderClient.exchange_code_for_token",
            new=AsyncMock(),
        ) as mock_exchange,
        patch("src.routers.oauth_connections.CACHE_INVALIDATION_AVAILABLE", False),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await oauth_connections.oauth_callback(
                connection_name="goto",
                request=_callback_request(),
                ctx=_ctx(),
                user=MagicMock(),
            )

    assert exc_info.value.status_code == 400
    mock_exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_pkce_binding_mismatch_is_rejected():
    provider = _connection_provider(provider_metadata={"use_pkce": True})
    stored = {
        "code_verifier": "verifier-abc",
        "redirect_uri": "https://other.example.test/callback",
        "provider_id": str(PROVIDER_ID),
        "mapping_id": None,
    }

    with (
        patch(
            "src.repositories.oauth.OAuthProviderRepository.get_by_connection_name",
            new=AsyncMock(return_value=provider),
        ),
        patch("src.core.security.decrypt_secret", return_value="decrypted-secret"),
        patch(
            "src.routers.oauth_connections.consume_pkce_verifier",
            new=AsyncMock(return_value=dict(stored)),
        ),
        patch(
            "src.services.oauth_provider.OAuthProviderClient.exchange_code_for_token",
            new=AsyncMock(),
        ) as mock_exchange,
        patch("src.routers.oauth_connections.CACHE_INVALIDATION_AVAILABLE", False),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await oauth_connections.oauth_callback(
                connection_name="goto",
                request=_callback_request(),
                ctx=_ctx(),
                user=MagicMock(),
            )

    assert exc_info.value.status_code == 400
    mock_exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_without_pkce_ignores_stray_verifier():
    provider = _connection_provider()

    with (
        patch(
            "src.repositories.oauth.OAuthProviderRepository.get_by_connection_name",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "src.repositories.oauth.OAuthProviderRepository.store_token",
            new=AsyncMock(),
        ),
        patch(
            "src.routers.oauth_connections.get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.routers.oauth_connections.resolve_url_template",
            return_value=provider.token_url,
        ),
        patch(
            "src.services.oauth_provider.OAuthProviderClient.exchange_code_for_token",
            new=AsyncMock(return_value=(True, _token_result())),
        ) as mock_exchange,
        patch("src.core.security.decrypt_secret", return_value="decrypted-secret"),
        # Even if a verifier payload exists, a non-PKCE provider must not send it.
        patch(
            "src.routers.oauth_connections.consume_pkce_verifier",
            new=AsyncMock(
                return_value={
                    "code_verifier": "stray-verifier",
                    "redirect_uri": REDIRECT_URI,
                    "provider_id": str(PROVIDER_ID),
                    "mapping_id": None,
                }
            ),
        ),
        patch("src.routers.oauth_connections.CACHE_INVALIDATION_AVAILABLE", False),
    ):
        result = await oauth_connections.oauth_callback(
            connection_name="goto",
            request=_callback_request(),
            ctx=_ctx(),
            user=MagicMock(),
        )

    assert result.success is True
    assert mock_exchange.await_args.kwargs["code_verifier"] is None


@pytest.mark.asyncio
async def test_callback_per_mapping_pkce_links_token_to_mapping():
    from src.services.events import builtins as event_builtins

    provider = _connection_provider(provider_metadata={"use_pkce": True})
    mapping_row = SimpleNamespace(
        id=MAPPING_ID,
        integration_id=INTEGRATION_ID,
        organization_id=None,
        entity_id="",
        entity_name="",
        oauth_token_id=None,
        organization=None,
    )
    stored_token = SimpleNamespace(id=uuid4())
    stored = {
        "code_verifier": "verifier-abc",
        "redirect_uri": REDIRECT_URI,
        "provider_id": str(PROVIDER_ID),
        "mapping_id": str(MAPPING_ID),
    }

    ctx = _ctx()
    ctx.db.get = AsyncMock(return_value=mapping_row)

    with (
        patch(
            "src.repositories.oauth.OAuthProviderRepository.get_by_connection_name",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "src.repositories.oauth.OAuthProviderRepository.store_token",
            new=AsyncMock(),
        ),
        patch(
            "src.repositories.oauth.OAuthProviderRepository.get_token",
            new=AsyncMock(return_value=stored_token),
        ),
        patch(
            "src.routers.oauth_connections.decode_state",
            return_value={
                "provider_id": str(PROVIDER_ID),
                "mapping_id": str(MAPPING_ID),
                "nonce": "nonce-1",
            },
        ),
        patch(
            "src.routers.oauth_connections.consume_nonce",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "src.routers.oauth_connections.get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.routers.oauth_connections.resolve_url_template",
            return_value=provider.token_url,
        ),
        patch(
            "src.services.oauth_provider.OAuthProviderClient.exchange_code_for_token",
            new=AsyncMock(return_value=(True, _token_result())),
        ) as mock_exchange,
        patch("src.core.security.decrypt_secret", return_value="decrypted-secret"),
        patch(
            "src.routers.oauth_connections.consume_pkce_verifier",
            new=AsyncMock(return_value=dict(stored)),
        ),
        patch.object(
            event_builtins, "emit_integration_connected", new=AsyncMock()
        ),
        patch("src.routers.oauth_connections.CACHE_INVALIDATION_AVAILABLE", False),
    ):
        result = await oauth_connections.oauth_callback(
            connection_name="goto",
            request=_callback_request(state="signed-state"),
            ctx=ctx,
            user=MagicMock(),
        )

    assert result.success is True
    assert result.triggering_mapping_id == MAPPING_ID
    assert mock_exchange.await_args.kwargs["code_verifier"] == "verifier-abc"
    # The freshly-stored token was linked to the mapping from the state.
    assert mapping_row.oauth_token_id == stored_token.id


@pytest.mark.asyncio
async def test_callback_mapping_nonce_replay_rejected_before_storage():
    """A replayed per-mapping state is rejected before exchange or storage."""
    provider = _connection_provider()
    mapping_row = SimpleNamespace(id=MAPPING_ID, organization_id=None)

    ctx = _ctx()
    ctx.db.get = AsyncMock(return_value=mapping_row)

    with (
        patch(
            "src.repositories.oauth.OAuthProviderRepository.get_by_connection_name",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "src.repositories.oauth.OAuthProviderRepository.store_token",
            new=AsyncMock(),
        ) as mock_store,
        patch(
            "src.routers.oauth_connections.decode_state",
            return_value={
                "provider_id": str(PROVIDER_ID),
                "mapping_id": str(MAPPING_ID),
                "nonce": "nonce-1",
            },
        ),
        # The nonce was already consumed by the first use.
        patch(
            "src.routers.oauth_connections.consume_nonce",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "src.services.oauth_provider.OAuthProviderClient.exchange_code_for_token",
            new=AsyncMock(),
        ) as mock_exchange,
        patch(
            "src.routers.oauth_connections.consume_pkce_verifier",
            new=AsyncMock(return_value=None),
        ),
        patch("src.routers.oauth_connections.CACHE_INVALIDATION_AVAILABLE", False),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await oauth_connections.oauth_callback(
                connection_name="goto",
                request=_callback_request(state="signed-state"),
                ctx=ctx,
                user=MagicMock(),
            )

    assert exc_info.value.status_code == 400
    mock_exchange.assert_not_awaited()
    mock_store.assert_not_awaited()


# =============================================================================
# Token client payload behavior
# =============================================================================


@pytest.mark.asyncio
async def test_exchange_sends_verifier_only_when_present():
    from src.services.oauth_provider import OAuthProviderClient

    client = OAuthProviderClient(max_retries=1)

    with patch.object(
        client, "_make_token_request", new=AsyncMock(return_value=(True, {}))
    ) as mock_request:
        await client.exchange_code_for_token(
            token_url="https://auth.example.test/token",
            code="code-1",
            client_id="client-123",
            client_secret="secret-1",
            redirect_uri=REDIRECT_URI,
            code_verifier="verifier-1",
        )
    payload = mock_request.await_args.args[1]
    assert payload["code_verifier"] == "verifier-1"
    assert payload["client_secret"] == "secret-1"

    with patch.object(
        client, "_make_token_request", new=AsyncMock(return_value=(True, {}))
    ) as mock_request:
        await client.exchange_code_for_token(
            token_url="https://auth.example.test/token",
            code="code-1",
            client_id="client-123",
            client_secret="secret-1",
            redirect_uri=REDIRECT_URI,
        )
    payload = mock_request.await_args.args[1]
    assert "code_verifier" not in payload
    assert payload["client_secret"] == "secret-1"


@pytest.mark.asyncio
async def test_refresh_keeps_secret_conditional_behavior():
    from src.services.oauth_provider import OAuthProviderClient

    client = OAuthProviderClient(max_retries=1)

    with patch.object(
        client, "_make_token_request", new=AsyncMock(return_value=(True, {}))
    ) as mock_request:
        await client.refresh_access_token(
            token_url="https://auth.example.test/token",
            refresh_token="refresh-1",
            client_id="client-123",
            client_secret="secret-1",
        )
    assert mock_request.await_args.args[1]["client_secret"] == "secret-1"

    with patch.object(
        client, "_make_token_request", new=AsyncMock(return_value=(True, {}))
    ) as mock_request:
        await client.refresh_access_token(
            token_url="https://auth.example.test/token",
            refresh_token="refresh-1",
            client_id="client-123",
            client_secret=None,
        )
    assert "client_secret" not in mock_request.await_args.args[1]
