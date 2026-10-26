"""Real-service coverage for endpoint-cache authorization boundaries."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.redis_client import get_redis_client
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.routers import endpoints


pytestmark = pytest.mark.e2e


class _PostRequest:
    method = "POST"
    query_params: dict[str, str] = {}

    async def json(self) -> dict:
        return {}


def _endpoint_workflow(*, solution_id=None) -> Workflow:
    suffix = uuid4().hex
    return Workflow(
        id=uuid4(),
        name=f"endpoint-cache-{suffix}",
        function_name="main",
        path=f"workflows/endpoint_cache_{suffix}.py",
        type="workflow",
        is_active=True,
        solution_id=solution_id,
        endpoint_enabled=True,
        allowed_methods=["POST"],
        execution_mode="sync",
        timeout_seconds=30,
    )


def _execution_result() -> endpoints.EndpointExecuteResponse:
    return endpoints.EndpointExecuteResponse(
        execution_id=str(uuid4()),
        status="Success",
    )


async def test_warm_cache_stops_authorizing_after_solution_uninstall(
    db_session,
    monkeypatch,
) -> None:
    """A cache warmed while active cannot execute after its install goes dormant."""
    solution = Solution(
        id=uuid4(),
        slug=f"endpoint-cache-{uuid4().hex}",
        name="Endpoint cache lifecycle fixture",
        allow_outbound_access=False,
        status="active",
    )
    workflow = _endpoint_workflow(solution_id=solution.id)
    db_session.add_all([solution, workflow])
    await db_session.flush()

    @asynccontextmanager
    async def db_context():
        yield db_session

    execute_sync = AsyncMock(return_value=_execution_result())
    redis = get_redis_client()
    workflow_id = str(workflow.id)
    monkeypatch.setattr(endpoints, "get_db_context", db_context)
    monkeypatch.setattr(endpoints, "get_redis_client", lambda: redis)
    monkeypatch.setattr(
        endpoints,
        "validate_workflow_key",
        AsyncMock(return_value=(True, "key-id")),
    )
    monkeypatch.setattr(endpoints, "_execute_sync", execute_sync)

    await redis.invalidate_endpoint_workflow_cache(workflow_id)
    try:
        response = await endpoints.execute_endpoint(
            workflow_id,
            _PostRequest(),  # type: ignore[arg-type]
            "fixture-key",
        )
        assert response.status == "Success"
        assert await redis.get_endpoint_workflow_cache(workflow_id) is not None

        solution.status = "inactive"
        await db_session.flush()

        revoked_request = _PostRequest()
        with pytest.raises(HTTPException) as exc_info:
            await endpoints.execute_endpoint(
                workflow_id,
                revoked_request,  # type: ignore[arg-type]
                "fixture-key",
            )

        assert exc_info.value.status_code == 404
        assert execute_sync.await_count == 1
        assert await redis.get_endpoint_workflow_cache(workflow_id) is None
    finally:
        await redis.invalidate_endpoint_workflow_cache(workflow_id)


async def test_standalone_endpoint_remains_servable_with_authoritative_lookup(
    db_session,
    monkeypatch,
) -> None:
    """The Solution lifecycle join must preserve standalone endpoint workflows."""
    workflow = _endpoint_workflow()
    db_session.add(workflow)
    await db_session.flush()

    @asynccontextmanager
    async def db_context():
        yield db_session

    execute_sync = AsyncMock(return_value=_execution_result())
    redis = get_redis_client()
    workflow_id = str(workflow.id)
    monkeypatch.setattr(endpoints, "get_db_context", db_context)
    monkeypatch.setattr(endpoints, "get_redis_client", lambda: redis)
    monkeypatch.setattr(
        endpoints,
        "validate_workflow_key",
        AsyncMock(return_value=(True, "key-id")),
    )
    monkeypatch.setattr(endpoints, "_execute_sync", execute_sync)

    await redis.invalidate_endpoint_workflow_cache(workflow_id)
    try:
        response = await endpoints.execute_endpoint(
            workflow_id,
            _PostRequest(),  # type: ignore[arg-type]
            "fixture-key",
        )

        assert response.status == "Success"
        execute_sync.assert_awaited_once()
        assert await redis.get_endpoint_workflow_cache(workflow_id) is not None
    finally:
        await redis.invalidate_endpoint_workflow_cache(workflow_id)
