"""Finite result-wait enforcement for API-key workflow endpoints."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from src.routers import endpoints


@pytest.mark.asyncio
async def test_zero_workflow_timeout_uses_finite_endpoint_result_wait(monkeypatch):
    redis = SimpleNamespace(
        set_pending_execution=AsyncMock(),
        wait_for_result=AsyncMock(return_value=None),
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(endpoints, "get_redis_client", lambda: redis)
    monkeypatch.setattr(
        "src.services.execution.async_executor.enqueue_workflow_execution",
        enqueue,
    )
    context = SimpleNamespace(
        org_id=None,
        user_id="system",
        name="API Key",
        email="system@example.invalid",
        is_platform_admin=False,
    )

    response = await endpoints._execute_sync(
        context=context,
        workflow_id="workflow-id",
        workflow_name="workflow-name",
        input_data={},
        timeout_seconds=0,
    )

    redis.wait_for_result.assert_awaited_once_with(
        execution_id=ANY,
        timeout_seconds=endpoints.ENDPOINT_RESULT_WAIT_MAX_SECONDS,
    )
    assert response.status == "Timeout"
    assert response.error == (
        "Workflow endpoint result wait timed out after "
        f"{endpoints.ENDPOINT_RESULT_WAIT_MAX_SECONDS} seconds"
    )


@pytest.mark.parametrize(
    ("workflow_timeout", "result_wait"),
    [(30, 30), (86400, 86400), (86401, 86400), (-1, 86400)],
)
def test_endpoint_result_wait_is_always_positive_and_bounded(
    workflow_timeout,
    result_wait,
):
    assert endpoints._endpoint_result_wait_timeout(workflow_timeout) == result_wait
