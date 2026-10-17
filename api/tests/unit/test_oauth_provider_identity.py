"""Unit coverage for conservative global OAuth provider reuse."""

from types import SimpleNamespace

import pytest

from src.services.oauth_provider_identity import (
    OAuthProviderIdentityConflict,
    assert_oauth_provider_reusable,
    merged_portable_oauth_values,
)


def test_compatible_shape_fills_blanks_and_preserves_configured_values() -> None:
    provider = SimpleNamespace(
        integration_id=None,
        display_name="Configured display",
        oauth_flow_type="authorization_code",
        authorization_url="https://auth.example/authorize",
        token_url=None,
        audience=None,
        token_url_defaults={},
        entity_id_source=None,
        scopes=["write", "read"],
        redirect_uri=None,
        provider_metadata={"pkce": True},
    )
    desired = {
        "display_name": "Portable display",
        "oauth_flow_type": "authorization_code",
        "authorization_url": "https://auth.example/authorize",
        "token_url": "https://auth.example/token",
        "scopes": ["read", "write"],
        "provider_metadata": {},
    }

    assert_oauth_provider_reusable(
        provider,
        desired,
        provider_name="shared",
        integration_name="Target",
    )
    merged = merged_portable_oauth_values(provider, desired)

    assert merged["display_name"] == "Configured display"
    assert merged["token_url"] == "https://auth.example/token"
    assert merged["provider_metadata"] == {"pkce": True}


def test_bound_provider_is_not_reusable() -> None:
    provider = SimpleNamespace(integration_id="existing")

    with pytest.raises(OAuthProviderIdentityConflict, match="already bound"):
        assert_oauth_provider_reusable(
            provider,
            {},
            provider_name="shared",
            integration_name="Target",
        )


def test_conflicting_structural_identity_is_not_reusable() -> None:
    provider = SimpleNamespace(
        integration_id=None,
        authorization_url="https://configured.example/authorize",
    )

    with pytest.raises(OAuthProviderIdentityConflict, match="authorization_url"):
        assert_oauth_provider_reusable(
            provider,
            {"authorization_url": "https://portable.example/authorize"},
            provider_name="shared",
            integration_name="Target",
        )
