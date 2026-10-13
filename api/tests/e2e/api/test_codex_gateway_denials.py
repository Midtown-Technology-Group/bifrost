"""Invalid-key HTTP denials must survive the real audit storage constraints."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from src.models.orm.codex_gateway import CodexGatewayRequestLog
from src.repositories.codex_gateway import CodexGatewayRepository
from src.routers.codex_gateway import get_codex_gateway_runtime, router
from src.services.codex_gateway.runtime import CodexGatewayRuntime

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize("credential", ["missing", "malformed", "unknown"])
@pytest.mark.parametrize("model_length", [255, 256])
async def test_invalid_key_http_denial_persists_bounded_model(
    db_session, credential, model_length
):
    # Use the real router, runtime and repository; the fixture rolls the database
    # transaction back so each parameter owns its audit row without shared state.
    upstream = AsyncMock()
    runtime = CodexGatewayRuntime(
        repository=CodexGatewayRepository(db_session), upstream_client=upstream
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_codex_gateway_runtime] = lambda: runtime
    gateway_key = None
    headers = {"User-Agent": f"gateway-denial-{uuid4().hex}"}
    if credential == "malformed":
        gateway_key = "not-a-bifrost-key"
    elif credential == "unknown":
        gateway_key = f"bfck_{uuid4().hex}{uuid4().hex[:11]}"
    if gateway_key is not None:
        headers["Authorization"] = f"Bearer {gateway_key}"
    model = "m" * model_length

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/responses",
            headers=headers,
            json={"model": model, "input": "do not retain this prompt"},
        )

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "invalid_gateway_key"
    log = (
        await db_session.execute(
            select(CodexGatewayRequestLog).where(
                CodexGatewayRequestLog.client_user_agent == headers["User-Agent"]
            )
        )
    ).scalar_one()
    assert log.model == model[:255]
    assert log.request_metadata["model"] == model[:255]
    assert log.status_code == 401
    assert log.policy_decision == "deny"
    assert log.denied_reason == "invalid_gateway_key"
    assert log.user_id is None
    assert log.project_id is None
    assert log.gateway_key_id is None
    assert log.oauth_account_id is None
    assert log.captured_prompt is None
    assert log.captured_response is None
    assert "do not retain this prompt" not in repr(log.request_metadata)
    if gateway_key is not None:
        assert gateway_key not in repr(log.request_metadata)
    upstream.create_response.assert_not_awaited()
