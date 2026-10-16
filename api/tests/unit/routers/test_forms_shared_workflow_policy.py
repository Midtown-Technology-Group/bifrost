"""Shared-workflow policy parity for form submission and startup."""

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def test_startup_resolution_passes_solution_shared_workflow_policy():
    source = Path("src/routers/forms.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    startup = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "execute_startup_workflow"
    )
    resolve_calls = [
        node
        for node in ast.walk(startup)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "resolve"
    ]

    assert len(resolve_calls) == 1
    keywords = {item.arg: item.value for item in resolve_calls[0].keywords}
    assert isinstance(keywords["allow_shared_fallback"], ast.Name)
    assert keywords["allow_shared_fallback"].id == "allow_shared_workflow"
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "solution_allows_global"
        for node in ast.walk(startup)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "solution_scoped,policy_enabled,resolves",
    [
        (True, False, False),
        (True, True, True),
        (False, False, True),
    ],
)
async def test_startup_enforces_solution_shared_workflow_policy(
    solution_scoped,
    policy_enabled,
    resolves,
):
    try:
        from src.routers import forms
    except ModuleNotFoundError as exc:
        if exc.name == "pydantic_ai":
            pytest.skip("application dependencies are unavailable")
        raise

    form_id = uuid4()
    org_id = uuid4()
    solution_id = uuid4() if solution_scoped else None
    workflow_id = uuid4()
    form = SimpleNamespace(
        id=form_id,
        is_active=True,
        fields=[],
        allowed_query_params=[],
        launch_workflow_id="workflows/shared.py::startup",
        solution_id=solution_id,
        organization_id=org_id,
        default_launch_params={},
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(form))
    ctx = SimpleNamespace(
        org_id=uuid4(),
        user=SimpleNamespace(
            user_id=uuid4(),
            name="Form user",
            email="form-user@example.test",
            is_superuser=False,
            is_provider_org=False,
            is_external=False,
            verified_context=None,
        ),
    )
    resolve = AsyncMock(
        return_value=(SimpleNamespace(id=workflow_id) if resolves else None)
    )
    repository = SimpleNamespace(resolve=resolve)
    allows_global = AsyncMock(return_value=policy_enabled)
    run_workflow = AsyncMock(return_value=SimpleNamespace(result={"ready": True}))

    with (
        patch.object(forms, "_authorize_form_runtime", AsyncMock()),
        patch.object(forms, "_limit_embed_action", AsyncMock()),
        patch.object(forms, "WorkflowRepository", return_value=repository),
        patch(
            "src.services.solution_scope.solution_allows_global",
            allows_global,
        ),
        patch(
            "src.services.execution.service.run_workflow",
            run_workflow,
        ),
        patch.object(
            forms,
            "store_startup_result",
            AsyncMock(
                return_value=(
                    "startup-handle",
                    datetime(2026, 10, 13, tzinfo=timezone.utc),
                )
            ),
        ),
    ):
        if not resolves:
            with pytest.raises(HTTPException) as exc:
                await forms.execute_startup_workflow(
                    form_id,
                    SimpleNamespace(),
                    ctx,
                    ctx.user,
                    db,
                    {},
                )
            assert exc.value.status_code == 404
        else:
            response = await forms.execute_startup_workflow(
                form_id,
                SimpleNamespace(),
                ctx,
                ctx.user,
                db,
                {},
            )
            assert response.result == {"ready": True}

    resolve.assert_awaited_once_with(
        form.launch_workflow_id,
        solution_scope=solution_id,
        allow_shared_fallback=(policy_enabled if solution_scoped else True),
    )
    if solution_scoped:
        allows_global.assert_awaited_once_with(db, solution_id)
    else:
        allows_global.assert_not_awaited()
    if resolves:
        run_workflow.assert_awaited_once()
        call = run_workflow.await_args.kwargs
        assert call["workflow_id"] == str(workflow_id)
        assert call["context"].scope == str(org_id)
        assert str(call["context"].organization.id) == str(org_id)
    else:
        run_workflow.assert_not_awaited()
