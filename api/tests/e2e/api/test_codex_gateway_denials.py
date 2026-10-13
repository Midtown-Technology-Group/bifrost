"""Invalid-key HTTP denials must survive the real audit storage constraints."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import database
from src.core.db_deps import DbSession
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


@pytest.mark.parametrize("credential", ["missing", "malformed", "unknown"])
async def test_invalid_key_http_denial_survives_failed_audit_and_request_commit(
    db_session, monkeypatch, credential, caplog
):
    """A real failed INSERT must not poison the denial request's final commit."""
    request_id = f"denial-collision-{uuid4().hex}"
    repository = CodexGatewayRepository(db_session)
    await repository.create_request_log(
        request_id=request_id,
        endpoint="/v1/responses",
        status_code=401,
        policy_decision="deny",
    )
    connection = await db_session.connection()
    request_session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    commit = AsyncMock(wraps=request_session.commit)
    monkeypatch.setattr(request_session, "commit", commit)
    monkeypatch.setattr(
        database, "get_session_factory", lambda: lambda: request_session
    )
    create_log = CodexGatewayRepository.create_request_log

    async def insert_duplicate_request_id(self, **kwargs):
        kwargs["request_id"] = request_id
        return await create_log(self, **kwargs)

    monkeypatch.setattr(
        CodexGatewayRepository, "create_request_log", insert_duplicate_request_id
    )
    upstream = AsyncMock()

    async def runtime_dependency(db: DbSession):
        return CodexGatewayRuntime(
            repository=CodexGatewayRepository(db), upstream_client=upstream
        )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_codex_gateway_runtime] = runtime_dependency
    headers = {}
    if credential == "malformed":
        headers["Authorization"] = "Bearer not-a-bifrost-key"
    elif credential == "unknown":
        headers["Authorization"] = f"Bearer bfck_{uuid4().hex}{uuid4().hex[:11]}"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/responses",
            headers=headers,
            json={"model": "gpt-test", "input": "do not retain this prompt"},
        )

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "invalid_gateway_key"
    commit.assert_awaited_once()
    # get_db committed successfully; the enclosing fixture transaction and its
    # existing row survive. Its rollback removes every test-owned record.
    logs = (
        (
            await db_session.execute(
                select(CodexGatewayRequestLog).where(
                    CodexGatewayRequestLog.request_id == request_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert "error_type=IntegrityError" in caplog.text
    assert "do not retain this prompt" not in caplog.text
    upstream.create_response.assert_not_awaited()
