"""Authorization checks for cached API-key workflow endpoints."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from src.routers import endpoints


def _cached_metadata(workflow_id: str, *, allowed_methods: list[str]) -> dict:
    return {
        "workflow_id": workflow_id,
        "file_path": "workflows/example.py",
        "execution_mode": "sync",
        "timeout_seconds": 30,
        "allowed_methods": allowed_methods,
    }


def _request(method: str) -> SimpleNamespace:
    return SimpleNamespace(method=method)


@pytest.mark.asyncio
async def test_warm_cache_cannot_outlive_endpoint_disablement(monkeypatch) -> None:
    workflow_id = str(uuid4())
    redis = SimpleNamespace(
        get_endpoint_workflow_cache=AsyncMock(
            return_value=_cached_metadata(workflow_id, allowed_methods=["POST"])
        ),
        invalidate_endpoint_workflow_cache=AsyncMock(),
    )
    repository = SimpleNamespace(
        get_endpoint_workflow_by_id=AsyncMock(return_value=None)
    )

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    monkeypatch.setattr(endpoints, "get_redis_client", lambda: redis)
    monkeypatch.setattr(endpoints, "get_db_context", db_context)
    monkeypatch.setattr(
        endpoints,
        "validate_workflow_key",
        AsyncMock(return_value=(True, "key-id")),
    )
    monkeypatch.setattr(endpoints, "WorkflowRepository", lambda *args, **kwargs: repository)

    request = _request("POST")
    with pytest.raises(HTTPException) as exc_info:
        await endpoints.execute_endpoint(
            workflow_id,
            request,  # type: ignore[arg-type]
            "secret-key",
        )

    assert exc_info.value.status_code == 404
    repository.get_endpoint_workflow_by_id.assert_awaited_once()
    redis.invalidate_endpoint_workflow_cache.assert_awaited_once_with(workflow_id)


@pytest.mark.asyncio
async def test_warm_cache_cannot_preserve_removed_http_method(monkeypatch) -> None:
    workflow_id = str(uuid4())
    redis = SimpleNamespace(
        get_endpoint_workflow_cache=AsyncMock(
            return_value=_cached_metadata(workflow_id, allowed_methods=["POST"])
        ),
        set_endpoint_workflow_cache=AsyncMock(),
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        path="workflows/example.py",
        execution_mode="sync",
        timeout_seconds=30,
        allowed_methods=["GET"],
    )
    repository = SimpleNamespace(
        get_endpoint_workflow_by_id=AsyncMock(return_value=workflow)
    )

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    monkeypatch.setattr(endpoints, "get_redis_client", lambda: redis)
    monkeypatch.setattr(endpoints, "get_db_context", db_context)
    monkeypatch.setattr(
        endpoints,
        "validate_workflow_key",
        AsyncMock(return_value=(True, "key-id")),
    )
    monkeypatch.setattr(endpoints, "WorkflowRepository", lambda *args, **kwargs: repository)

    request = _request("POST")
    with pytest.raises(HTTPException) as exc_info:
        await endpoints.execute_endpoint(
            workflow_id,
            request,  # type: ignore[arg-type]
            "secret-key",
        )

    assert exc_info.value.status_code == 405
    assert "Allowed: GET" in exc_info.value.detail
    redis.set_endpoint_workflow_cache.assert_awaited_once()
