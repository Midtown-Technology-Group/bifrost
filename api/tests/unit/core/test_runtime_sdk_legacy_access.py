"""CRED legacy admission must retain current main's execution/bypass gate.

Authentication is replaced only to isolate the already-authenticated principal
boundary. Dedicated credentials, token parsing and real HTTP/DB admission have
their own existing tests; this matrix must not grant ordinary-user fallback.
"""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.core import runtime_sdk_ingress as ingress
from src.core.auth import get_current_engine_or_bypass_user
from src.core.principal import UserPrincipal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dependency", [ingress.sdk_get_principal, ingress.sdk_mapping_principal]
)
@pytest.mark.parametrize(
    ("flags", "allowed"),
    [
        ({}, False),
        ({"is_external": True}, False),
        ({"roles": ["Platform Admin"]}, False),
        ({"embed": True, "engine_execution_id": uuid4()}, False),
        ({"is_superuser": True}, True),
        ({"is_provider_org": True}, True),
        ({"is_engine_token": True, "engine_execution_id": uuid4()}, True),
        ({"service_id": str(uuid4()), "engine_execution_id": uuid4()}, True),
    ],
    ids=[
        "ordinary",
        "external",
        "role-only",
        "embed",
        "admin",
        "provider",
        "engine",
        "service",
    ],
)
async def test_legacy_sdk_preserves_current_main_access_gate(
    monkeypatch, dependency, flags, allowed
):
    principal = UserPrincipal(
        user_id=uuid4(),
        email="synthetic@example.invalid",
        organization_id=uuid4(),
        **flags,
    )
    calls = []

    async def authenticated(_request, _credentials, _db):
        calls.append("authenticated")
        return principal

    async def unexpected_runtime_loader(*_args, **_kwargs):
        pytest.fail("legacy principal entered dedicated credential admission")

    monkeypatch.setattr(ingress, "get_current_user", authenticated)
    monkeypatch.setattr(
        ingress, "load_runtime_sdk_ingress_authority", unexpected_runtime_loader
    )
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    scope = dependency(request, None, None)
    try:
        if allowed:
            assert await get_current_engine_or_bypass_user(principal) is principal
            assert await anext(scope) is principal
        else:
            with pytest.raises(HTTPException) as reference:
                await get_current_engine_or_bypass_user(principal)
            assert reference.value.status_code == 403
            assert (
                reference.value.detail
                == "Execution credentials or platform access required"
            )
            with pytest.raises(HTTPException) as candidate:
                await anext(scope)
            assert candidate.value.status_code == reference.value.status_code
            assert candidate.value.detail == reference.value.detail
            assert candidate.value.headers == reference.value.headers
        assert calls == ["authenticated"]
    finally:
        await scope.aclose()
