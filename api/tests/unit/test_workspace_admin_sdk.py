"""HTTP contract tests independent of backend implementation models."""

import ast
import importlib
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from bifrost.executions import Executions
from bifrost.oauth_admin import OAuthAdmin

pytestmark = pytest.mark.asyncio


@pytest.fixture
def client(monkeypatch):
    module = importlib.import_module("bifrost.oauth_admin")
    executions = importlib.import_module("bifrost.executions")
    fake = AsyncMock()
    monkeypatch.setattr(module, "get_client", lambda: fake)
    monkeypatch.setattr(executions, "get_client", lambda: fake)
    return fake


def response(data, status=200):
    return httpx.Response(
        status, json=data, request=httpx.Request("POST", "https://example.test/api")
    )


async def test_diagnostics_http_contract(client):
    client.get.return_value = response({"providers": [], "tokens": []})
    result = await OAuthAdmin.inspect("NinjaOne")
    assert result.providers == []
    client.get.assert_awaited_once_with(
        "/api/oauth/connections/NinjaOne/diagnostics", params={"scope": "global"}
    )


async def test_recovery_http_contract(client):
    data = {
        "connection_name": "NinjaOne",
        "provider_id": str(uuid4()),
        "has_refresh_token": True,
        "expires_at": "2026-09-30T00:00:00Z",
        "scope": "monitoring",
    }
    client.post.return_value = response(data)
    result = await OAuthAdmin.recover(
        "NinjaOne", code="code", redirect_uri="https://example/callback"
    )
    assert result.model_dump() == data
    client.post.assert_awaited_once_with(
        "/api/oauth/connections/NinjaOne/recover",
        json={
            "scope": "global",
            "action": "exchange_code_without_scope",
            "refresh_token": None,
            "code": "code",
            "redirect_uri": "https://example/callback",
        },
    )


async def test_reconcile_http_contract(client):
    client.post.return_value = response(
        {"provider_rows_updated": 1, "token_rows_updated": 2}
    )
    assert (await OAuthAdmin.reconcile("NinjaOne")).token_rows_updated == 2
    client.post.assert_awaited_once_with(
        "/api/oauth/connections/NinjaOne/reconcile", json={"scope": "global"}
    )


async def test_redaction_http_contract_and_error_propagation(client):
    target = str(uuid4())
    org = str(uuid4())
    client.post.return_value = response(
        {"execution_id": target, "rows_updated": 1, "redacted": True}
    )
    assert (await Executions.redact_sensitive_fields(target, scope=org)).redacted
    client.post.assert_awaited_once_with(
        f"/api/executions/{target}/redact-sensitive-fields", json={"scope": org}
    )
    client.post.return_value = response(
        {"detail": "Only terminal executions may be redacted"}, 409
    )
    with pytest.raises(httpx.HTTPStatusError, match="terminal"):
        await Executions.redact_sensitive_fields(target, scope=org)


async def test_new_sdk_surfaces_never_import_backend_modules():
    root = Path(__file__).resolve().parents[2] / "bifrost"
    for name in ("oauth_admin.py", "admin_models.py", "executions.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            assert not any(
                m == "src" or m.startswith(("src.", "sqlalchemy", "fastapi"))
                for m in modules
            )


async def test_recovery_registers_input_secrets_for_execution_scrubbing(
    client, monkeypatch
):
    context_module = importlib.import_module("bifrost._context")
    registered = []
    monkeypatch.setattr(context_module, "register_secret", registered.append)
    client.post.return_value = response(
        {
            "connection_name": "NinjaOne",
            "provider_id": str(uuid4()),
            "has_refresh_token": True,
            "expires_at": "expiry",
            "scope": "monitoring",
        }
    )
    await OAuthAdmin.recover("NinjaOne", refresh_token="submitted-secret")
    assert "submitted-secret" in registered
