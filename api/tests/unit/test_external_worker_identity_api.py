"""Optional broker API authorization, bounded DTO and private errors."""
import base64
import secrets
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from shared.models import ExternalWorkerIdentityRequest
from src.core.database import get_db
from src.core.request_body_limit import RouteBodyLimitMiddleware
from src.routers.platform.external_workers import router

PATH = "/api/platform/external-workers/identity"


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router)
    async def database():
        yield SimpleNamespace()
    app.dependency_overrides[get_db] = database
    app.add_middleware(RouteBodyLimitMiddleware, limits={("POST", PATH): 4 << 20},
                       messages={("POST", PATH): "Identity request exceeds body limit."})
    return app


@pytest.mark.asyncio
async def test_disabled_and_rejected_identity_never_acquire(api):
    settings = SimpleNamespace(external_worker_scaling_enabled=True, external_worker_identity_enabled=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        with patch("src.routers.platform.external_workers.get_settings", return_value=settings), patch(
            "src.services.external_worker_ownership.acquire", new=AsyncMock()
        ) as acquire:
            response = await client.post(PATH, json={"operation": "acquire", "replica": "test--replica", "attempt": secrets.token_hex(32)})
            assert response.status_code == 404
            settings.external_worker_identity_enabled = True
            with patch("src.routers.platform.external_workers.validate_settings"), patch(
                "src.routers.platform.external_workers.verify_identity", new=AsyncMock(side_effect=ValueError("private-upstream"))
            ):
                response = await client.post(PATH, headers={"Authorization": "Bearer synthetic"},
                                             json={"operation": "acquire", "replica": "test--replica", "attempt": secrets.token_hex(32)})
                assert response.status_code == 403
                assert "private-upstream" not in response.text
            acquire.assert_not_called()


@pytest.mark.asyncio
async def test_authenticated_operation_is_private_and_extra_fields_are_rejected(api):
    token = secrets.token_urlsafe(32)
    settings = SimpleNamespace(external_worker_scaling_enabled=True, external_worker_identity_enabled=True)
    azure = SimpleNamespace(close=AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        with patch("src.routers.platform.external_workers.get_settings", return_value=settings), patch(
            "src.routers.platform.external_workers.validate_settings"
        ), patch("src.routers.platform.external_workers.verify_identity", new=AsyncMock()) as verify, patch(
            "src.routers.platform.external_workers.Azure", return_value=azure
        ), patch("src.services.external_worker_ownership.mutate", new=AsyncMock(return_value={"ok": True, "token": token})) as mutate:
            response = await client.post(PATH, headers={"Authorization": "Bearer synthetic"},
                                         json={"operation": "renew", "replica": "test--replica", "token": token})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.json() == {"ok": True, "token": token}
            verify.assert_awaited_once_with("synthetic", settings)
            assert mutate.call_args.args[3:] == (token, "renew")
            response = await client.post(PATH, headers={"Authorization": "Bearer synthetic"},
                                         json={"operation": "renew", "replica": "test--replica", "token": token, "attempt": secrets.token_hex(32)})
            assert response.status_code == 403
            assert mutate.await_count == 1
            azure.close.assert_awaited()


@pytest.mark.asyncio
async def test_oversize_body_rejected_before_parsing_or_acquisition(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.post(PATH, content=b"x" * ((4 << 20) + 1))
        assert response.status_code == 413
        assert response.json() == {"detail": "Identity request exceeds body limit."}


def test_checkpoint_and_token_are_hidden_from_model_repr():
    token = secrets.token_urlsafe(32)
    value = ExternalWorkerIdentityRequest(operation="checkpoint", replica="test--replica", token=token,
                                          state=base64.b64encode(b"synthetic-private-state"))
    assert token not in repr(value)
    assert "synthetic-private-state" not in repr(value)
    assert value.state == b"synthetic-private-state"


@pytest.mark.asyncio
async def test_uncertain_backend_error_is_closed_and_provider_is_joined(api):
    token = secrets.token_urlsafe(32)
    private_error = secrets.token_urlsafe(32)
    settings = SimpleNamespace(external_worker_scaling_enabled=True, external_worker_identity_enabled=True)
    azure = SimpleNamespace(close=AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        with patch("src.routers.platform.external_workers.get_settings", return_value=settings), patch(
            "src.routers.platform.external_workers.validate_settings"
        ), patch("src.routers.platform.external_workers.verify_identity", new=AsyncMock()), patch(
            "src.routers.platform.external_workers.Azure", return_value=azure
        ), patch("src.services.external_worker_ownership.mutate", new=AsyncMock(side_effect=RuntimeError(private_error))):
            response = await client.post(PATH, headers={"Authorization": "Bearer synthetic"},
                                         json={"operation": "renew", "replica": "test--replica", "token": token})
            assert response.status_code == 503
            assert token not in response.text and private_error not in response.text
            azure.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_blocked_mutation_is_cancelled_within_client_ceiling(api):
    import asyncio

    settings = SimpleNamespace(external_worker_scaling_enabled=True, external_worker_identity_enabled=True)
    azure = SimpleNamespace(close=AsyncMock())
    cancelled = asyncio.Event()
    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        with patch("src.routers.platform.external_workers.get_settings", return_value=settings), patch(
            "src.routers.platform.external_workers.validate_settings"
        ), patch("src.routers.platform.external_workers.verify_identity", new=AsyncMock()), patch(
            "src.routers.platform.external_workers.Azure", return_value=azure
        ), patch("src.services.external_worker_ownership.mutate", side_effect=blocked):
            async with asyncio.timeout(10):
                response = await client.post(PATH, headers={"Authorization": "Bearer synthetic"},
                                             json={"operation": "renew", "replica": "test--replica", "token": secrets.token_urlsafe(32)})
            assert response.status_code == 503
            assert cancelled.is_set()
            azure.close.assert_awaited_once()
