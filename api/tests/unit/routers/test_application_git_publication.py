"""The HTTP adapter admits no ordinary API identity or uploaded source controls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from src.core.database import get_db
from src.routers import applications
from tests.unit.services.test_application_git_source import SID, app_policy


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,expected", [
    ("not_configured", 503), ("missing_oidc", 401), ("other_app", 401),
    ("uploaded_source", 422), ("controls", 422), ("wrong_commit", 422),
])
async def test_source_http_adapter_fails_closed_without_enqueuing_or_transport(monkeypatch, fault, expected):
    api = FastAPI()
    api.include_router(applications.router)
    db = SimpleNamespace(rollback=AsyncMock(), commit=AsyncMock())

    async def database():
        yield db

    api.dependency_overrides[get_db] = database
    monkeypatch.setattr(applications, "get_settings", lambda: SimpleNamespace(
        inline_app_git_delivery_policy=None if fault == "not_configured" else app_policy()))
    enqueue = AsyncMock(side_effect=AssertionError("No failed admission may enqueue"))
    monkeypatch.setattr(applications, "enqueue_app_git_publication", enqueue)
    body = {"source_commit_sha": "a" * 40, "ci_run_id": 123, "ci_run_attempt": 2,
        "artifact_digest": "sha256:" + "c" * 64}
    if fault == "uploaded_source":
        body["files"] = {"pages/index.tsx": "attacker code"}
    elif fault == "controls":
        body["access_level"] = "public"
    elif fault == "wrong_commit":
        body["source_commit_sha"] = "not-an-exact-commit"
    app_id = UUID(int=20) if fault == "other_app" else SID
    headers = {"X-GitHub-Job-Token": "never-used"}
    if fault != "missing_oidc":
        headers["Authorization"] = "Bearer not-an-api-or-producer-token"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.post(f"/api/applications/{app_id}/github-source", headers=headers, json=body)
    assert response.status_code == expected, response.text
    enqueue.assert_not_awaited()
    db.commit.assert_not_awaited()
