from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException

from src.models.contracts.solution_deployments import SolutionDeploymentCapabilities
from src.routers.solution_deployments import inspect_active_deployment, router


@pytest.mark.asyncio
async def test_github_delivery_disabled_or_untrusted_producer_never_writes(monkeypatch):
    from fastapi.security import HTTPAuthorizationCredentials
    from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
    from src.routers import solution_deployments as module
    from src.services.solutions.github_delivery_source import GitDeliverySourceError

    request = SolutionGitSourceDeliveryRequest(source_commit_sha="a" * 40, ci_run_id=1,
        ci_run_attempt=1, artifact_digest="sha256:" + "b" * 64)
    db = SimpleNamespace(rollback=AsyncMock())
    service = AsyncMock()
    monkeypatch.setattr(module, "GitSourceDeliveryService", service)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(solution_git_delivery_policy=None))
    with pytest.raises(HTTPException) as disabled:
        await module.deliver_github_source(uuid4(), request, cast(Any, db), None, "job-token")
    assert disabled.value.status_code == 503 and not service.called
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(solution_git_delivery_policy=object()))
    monkeypatch.setattr(module, "authenticate_git_delivery", AsyncMock(side_effect=GitDeliverySourceError("untrusted")))
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="source-declaration-token")
    with pytest.raises(HTTPException) as untrusted:
        await module.deliver_github_source(uuid4(), request, cast(Any, db), credentials, "job-token")
    assert untrusted.value.status_code == 401 and not service.called


@pytest.mark.asyncio
async def test_github_identity_cannot_read_admin_pointer_or_supply_uploaded_bytes():
    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from src.core.database import get_db

    app = FastAPI()
    app.include_router(router)

    async def db():
        yield SimpleNamespace()
    app.dependency_overrides[get_db] = db
    token = jwt.encode({"iss": "https://token.actions.githubusercontent.com", "sub": "repo:fixture"},
        rsa.generate_private_key(public_exponent=65537, key_size=2048), algorithm="RS256")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        prefix = f"/api/solutions/{uuid4()}/deployments"
        response = await client.get(prefix + "/active", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 401
        response = await client.post(prefix + "/github-source", headers={
            "Authorization": "Bearer " + token, "X-GitHub-Job-Token": "ephemeral-job-token"}, json={
            "source_commit_sha": "a" * 40, "ci_run_id": 1, "ci_run_attempt": 1,
            "artifact_digest": "sha256:" + "b" * 64, "files": [{"path": "forged.py", "content_base64": "eA=="}]})
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "network"])
async def test_github_transport_failure_rolls_back_and_requires_receipt_readback(monkeypatch, failure):
    import httpx
    from fastapi.security import HTTPAuthorizationCredentials
    from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
    from src.routers import solution_deployments as module

    request = SolutionGitSourceDeliveryRequest(source_commit_sha="a" * 40, ci_run_id=1,
        ci_run_attempt=1, artifact_digest="sha256:" + "b" * 64)
    db = SimpleNamespace(rollback=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(solution_git_delivery_policy=object()))
    monkeypatch.setattr(module, "authenticate_git_delivery", AsyncMock(return_value=object()))
    service = AsyncMock()
    service.deliver.side_effect = httpx.ReadTimeout("unavailable") if failure == "timeout" else httpx.ConnectError("unavailable")
    monkeypatch.setattr(module, "GitSourceDeliveryService", lambda *_: service)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="scoped-producer")
    with pytest.raises(HTTPException) as caught:
        await module.deliver_github_source(uuid4(), request, cast(Any, db), credentials, "job-token")
    assert caught.value.status_code == 503
    assert "pointer and receipt" in caught.value.detail
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    assert service.deliver.await_count == 1


def test_solution_deployment_openapi_exposes_minimal_cs_surface():
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    paths = schema["paths"]

    base = "/api/solutions/{solution_id}/deployments"
    item = f"{base}/{{deployment_id}}"
    assert set(paths[base]) >= {"post"}
    assert set(paths[f"{base}/capabilities"]) >= {"get"}
    assert set(paths[f"{base}/active"]) >= {"get"}
    assert set(paths[item]) >= {"get"}
    assert set(paths[f"{item}/activate"]) >= {"post"}
    assert set(paths[f"{item}/rollback"]) >= {"post"}
    create_schema = paths[base]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert create_schema["$ref"].endswith("SolutionDeploymentCreate")
    assert "multipart/form-data" not in paths[base]["post"]["requestBody"]["content"]


@pytest.mark.asyncio
async def test_active_deployment_readback_uses_current_solution_row():
    solution_id, deployment_id = uuid4(), uuid4()

    class DB:
        async def get(self, _model, key):
            assert key == solution_id
            return SimpleNamespace(
                id=solution_id,
                active_deployment_id=deployment_id,
                execution_runtime_mode="deployment-v1",
            )

    result = await inspect_active_deployment(
        solution_id,
        cast(Any, SimpleNamespace(db=DB())),
        cast(Any, object()),
    )
    assert result.active_deployment_id == deployment_id
    assert result.execution_runtime_mode == "deployment-v1"


def test_capabilities_fail_closed_for_unconfigured_end_to_end_deployment():
    capabilities = SolutionDeploymentCapabilities()

    assert capabilities.registration is True
    assert capabilities.inspection is True
    assert capabilities.artifact_upload is False
    assert capabilities.server_side_compilation is False
    assert capabilities.activation_configured is False
    assert capabilities.safe_for_end_to_end_cs_deploy is False


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stage", "activate"])
async def test_source_revision_transition_conflict_rolls_back_and_returns_409(monkeypatch, operation):
    from src.routers import solution_deployments as module
    from src.repositories.solution_deployments import InvalidDeploymentTransition

    @asynccontextmanager
    async def lock(_solution_id):
        yield

    service = SimpleNamespace(**{operation: AsyncMock(side_effect=InvalidDeploymentTransition("state changed"))})
    monkeypatch.setattr(module, "solution_write_lock", lock)
    monkeypatch.setattr(module, "SolutionSourceRevisionService", lambda _db: service)
    db = AsyncMock()
    endpoint = module.stage_source_revision if operation == "stage" else module.activate_source_revision
    with pytest.raises(HTTPException) as caught:
        await endpoint(uuid4(), uuid4(), cast(Any, object()), cast(Any, SimpleNamespace(db=db)), cast(Any, SimpleNamespace(user_id=uuid4())))
    assert caught.value.status_code == 409
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
