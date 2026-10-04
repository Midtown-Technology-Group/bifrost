"""Route-level contracts for protected Workspace Source accounting."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import (
    HTTPException,
)
from fastapi.security import HTTPAuthorizationCredentials

from src.models.contracts.workspace_promotions import (
    WorkspaceSourceReleaseDeclareRequest,
)
from src.routers import workspace_promotions
from src.services.github_actions_oidc import (
    GitHubActionsOIDCError,
    WorkspaceSourceReleaseProducer,
)


def _source_declaration(commit_sha: str) -> WorkspaceSourceReleaseDeclareRequest:
    return WorkspaceSourceReleaseDeclareRequest(
        source_commit_sha=commit_sha,
        source_tree_sha="b" * 40,
        paths={"workflows/example.py": "c" * 64},
        disposition="pending",
    )


@pytest.mark.asyncio
async def test_github_source_release_dependency_rejects_missing_bearer() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await workspace_promotions._github_source_release_producer(None)

    assert exc_info.value.status_code == 401
    assert "bearer token is required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_github_source_release_dependency_rejects_partial_configuration(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        workspace_promotions,
        "get_settings",
        lambda: SimpleNamespace(
            workspace_source_release_oidc_repository=("MTG-Thomas/bifrost-workspace")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_promotions._github_source_release_producer(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        )

    assert exc_info.value.status_code == 503
    assert "not configured" in exc_info.value.detail


@pytest.mark.asyncio
async def test_github_source_release_dependency_rejects_invalid_oidc_token(
    monkeypatch,
) -> None:
    settings = SimpleNamespace(
        workspace_source_release_oidc_repository="MTG-Thomas/bifrost-workspace",
        workspace_source_release_oidc_repository_id=1197464564,
        workspace_source_release_oidc_repository_owner_id=87775189,
        workspace_source_release_oidc_workflow_ref=(
            "MTG-Thomas/bifrost-workspace/.github/workflows/"
            "declare-workspace-source-release.yml@refs/heads/main"
        ),
        workspace_source_release_oidc_organization_id=str(uuid4()),
    )
    monkeypatch.setattr(workspace_promotions, "get_settings", lambda: settings)
    authenticate = AsyncMock(
        side_effect=GitHubActionsOIDCError("token rejected by pinned policy")
    )
    monkeypatch.setattr(
        workspace_promotions,
        "authenticate_workspace_source_release_producer",
        authenticate,
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_promotions._github_source_release_producer(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "token rejected by pinned policy"
    authenticate.assert_awaited_once_with("token", settings=settings)


@pytest.mark.asyncio
async def test_github_declaration_requires_body_sha_to_match_oidc_sha(
    monkeypatch,
) -> None:
    service = MagicMock(side_effect=AssertionError("mismatch must not be recorded"))
    monkeypatch.setattr(workspace_promotions, "WorkspaceSourceReleaseService", service)
    producer = WorkspaceSourceReleaseProducer(
        organization_id=uuid4(),
        source_commit_sha="a" * 40,
        oidc_commit_sha="b" * 40,
        repository="MTG-Thomas/bifrost-workspace",
        workflow_ref="trusted",
        run_id="123",
        event_name="workflow_run",
        triggering_workflow_run_id="456",
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_promotions.declare_workspace_source_release_from_github(
            _source_declaration("d" * 40),
            SimpleNamespace(),
            producer,
        )

    assert exc_info.value.status_code == 403
    service.assert_not_called()


@pytest.mark.asyncio
async def test_github_declaration_uses_pinned_organization_and_system_actor(
    monkeypatch,
) -> None:
    organization_id = uuid4()
    response = SimpleNamespace(id=uuid4())
    captured: dict[str, object] = {}

    class Service:
        def __init__(self, db, org_id):
            captured["db"] = db
            captured["organization_id"] = org_id

        async def declare(self, request, *, created_by, producer):
            captured["request"] = request
            captured["created_by"] = created_by
            captured["producer"] = producer
            return response

    monkeypatch.setattr(workspace_promotions, "WorkspaceSourceReleaseService", Service)
    producer = WorkspaceSourceReleaseProducer(
        organization_id=organization_id,
        source_commit_sha="a" * 40,
        oidc_commit_sha="b" * 40,
        repository="MTG-Thomas/bifrost-workspace",
        workflow_ref="trusted",
        run_id="123",
        event_name="workflow_run",
        triggering_workflow_run_id="456",
    )
    request = _source_declaration("a" * 40)
    db = SimpleNamespace()

    result = await workspace_promotions.declare_workspace_source_release_from_github(
        request,
        db,
        producer,
    )

    assert result is response
    assert captured["organization_id"] == organization_id
    assert captured["request"] is request
    assert captured["created_by"] == workspace_promotions.SYSTEM_USER_UUID
    assert captured["producer"] is producer
