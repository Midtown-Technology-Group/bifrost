"""Regression tests: admin OAuth callback URL ignores spoofed Host headers."""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.routers import oauth_config


def _settings(public_url: str):
    return SimpleNamespace(public_url=public_url)


def test_get_callback_url_uses_public_url(monkeypatch):
    monkeypatch.setattr(
        oauth_config,
        "get_settings",
        lambda: _settings("https://bifrost.example.com/"),
    )

    assert (
        oauth_config._get_callback_url()
        == "https://bifrost.example.com/auth/oauth/callback"
    )


@pytest.mark.asyncio
async def test_list_oauth_configs_ignores_spoofed_host_headers(monkeypatch):
    """Spoofed Host / X-Forwarded-Host must not leak into the callback URL."""
    monkeypatch.setattr(
        oauth_config,
        "get_settings",
        lambda: _settings("https://bifrost.example.com"),
    )
    service = MagicMock()
    service.get_all_provider_configs = AsyncMock(return_value=[])
    service.get_login_preference = AsyncMock(return_value=None)
    with patch.object(oauth_config, "OAuthConfigService", return_value=service):
        result = await oauth_config.list_oauth_configs(
            ctx=SimpleNamespace(),
            user=SimpleNamespace(email="admin@example.com"),
            db=AsyncMock(),
        )

    assert result.callback_url == "https://bifrost.example.com/auth/oauth/callback"
    assert "evil" not in result.callback_url


@pytest.mark.parametrize(
    "func_name", ["_get_callback_url", "list_oauth_configs"]
)
def test_callback_derivation_takes_no_request_input(func_name):
    """Guard: no Request/header parameter exists for spoofed hosts to flow through."""
    params = inspect.signature(getattr(oauth_config, func_name)).parameters
    assert "request" not in params
