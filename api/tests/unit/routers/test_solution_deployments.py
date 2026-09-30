from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException

from src.models.contracts.solution_deployments import SolutionDeploymentCapabilities
from src.routers.solution_deployments import inspect_active_deployment, router


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
