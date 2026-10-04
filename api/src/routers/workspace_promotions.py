"""Workspace source accounting and retained release evidence."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials

from src.config import get_settings
from src.core.auth import Context, CurrentSuperuser, bearer_scheme
from src.core.constants import SYSTEM_USER_UUID
from src.core.db_deps import DbSession
from src.models.contracts.workspace_promotions import (
    SolutionDeployObligationListResponse,
    SolutionDeployObligationResponse,
    WorkspaceLiveRetirementInventory,
    WorkspaceLiveStatusResponse,
    WorkspacePromotionArtifactResponse,
    WorkspaceReleaseStatusResponse,
    WorkspaceSourceReleaseDeclareRequest,
    WorkspaceSourceReleaseDispositionRequest,
    WorkspaceSourceReleaseListResponse,
    WorkspaceSourceReleaseResponse,
)
from src.services.github_actions_oidc import (
    GitHubActionsOIDCError,
    WorkspaceSourceReleaseProducer,
    authenticate_workspace_source_release_producer,
    workspace_source_release_producer_configured,
    workspace_source_release_tracking_expected,
)
from src.services.solution_deploy_obligations import (
    SolutionDeployObligationConflict,
    SolutionDeployObligationService,
)
from src.services.workspace_promotions import (
    WorkspacePromotionPreviewService,
)
from src.services.workspace_release_activation import (
    WorkspaceReleaseActivationService,
)
from src.services.workspace_release_retirement import (
    WorkspaceReleaseRetirementError,
    WorkspaceReleaseRetirementService,
)
from src.services.workspace_source_releases import (
    WorkspaceSourceReleaseConflict,
    WorkspaceSourceReleaseService,
)

router = APIRouter(
    prefix="/api/workspace-promotions",
    tags=["Workspace Source evidence"],
)


async def _github_source_release_producer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> WorkspaceSourceReleaseProducer:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="GitHub Actions OIDC bearer token is required",
        )
    settings = get_settings()
    if not workspace_source_release_producer_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace source-release OIDC producer is not configured",
        )
    try:
        return await authenticate_workspace_source_release_producer(
            credentials.credentials,
            settings=settings,
        )
    except GitHubActionsOIDCError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


@router.get(
    "/artifacts/{artifact_id}", response_model=WorkspacePromotionArtifactResponse
)
async def get_workspace_promotion_artifact(
    artifact_id: UUID,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await WorkspacePromotionPreviewService(db, ctx.org_id).get_artifact(
            artifact_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace release artifact not found",
        ) from exc


@router.get("/live/retirement-inventory", response_model=WorkspaceLiveRetirementInventory)
async def inspect_workspace_release_retirement(
    ctx: Context, db: DbSession, user: CurrentSuperuser,
) -> WorkspaceLiveRetirementInventory:
    """Inventory the guard's complete Root cohort, including inactive audit rows."""
    if ctx.org_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="an organization context is required")
    try:
        return await WorkspaceReleaseRetirementService(db, ctx.org_id).inspect()
    except WorkspaceReleaseRetirementError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/live", response_model=WorkspaceLiveStatusResponse)
async def get_live_workspace_release(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> WorkspaceLiveStatusResponse:
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    return await WorkspaceReleaseActivationService(db, ctx.org_id).get_live()


@router.get("/releases/{release_id}", response_model=WorkspaceReleaseStatusResponse)
async def get_workspace_release_status(
    release_id: UUID,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> WorkspaceReleaseStatusResponse:
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await WorkspaceReleaseActivationService(db, ctx.org_id).get_release(
            release_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace release not found",
        ) from exc


@router.post(
    "/source-releases",
    response_model=WorkspaceSourceReleaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def declare_workspace_source_release(
    request: WorkspaceSourceReleaseDeclareRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> WorkspaceSourceReleaseResponse:
    """Record the release disposition owed by one protected source commit."""
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await WorkspaceSourceReleaseService(db, ctx.org_id).declare(
            request,
            created_by=user.user_id,
        )
    except (WorkspaceSourceReleaseConflict, SolutionDeployObligationConflict) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post(
    "/source-releases/github",
    response_model=WorkspaceSourceReleaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def declare_workspace_source_release_from_github(
    request: WorkspaceSourceReleaseDeclareRequest,
    db: DbSession,
    producer: Annotated[
        WorkspaceSourceReleaseProducer, Depends(_github_source_release_producer)
    ],
) -> WorkspaceSourceReleaseResponse:
    """Record one protected-main push using a narrowly pinned Actions identity."""
    if producer.source_commit_sha != request.source_commit_sha:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OIDC source commit SHA does not match the declaration",
        )
    try:
        return await WorkspaceSourceReleaseService(
            db, producer.organization_id
        ).declare(
            request,
            created_by=SYSTEM_USER_UUID,
            producer=producer,
        )
    except (WorkspaceSourceReleaseConflict, SolutionDeployObligationConflict) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get(
    "/source-releases",
    response_model=WorkspaceSourceReleaseListResponse,
)
async def list_workspace_source_releases(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> WorkspaceSourceReleaseListResponse:
    """List pending, completed, and attention-required source releases."""
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    return await WorkspaceSourceReleaseService(db, ctx.org_id).list(
        limit=limit,
        tracking_expected=workspace_source_release_tracking_expected(get_settings()),
    )


@router.get(
    "/solution-deploy-obligations",
    response_model=SolutionDeployObligationListResponse,
)
async def list_solution_deploy_obligations(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SolutionDeployObligationListResponse:
    """List reviewed Solution source that requires an explicit deployment."""
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    return await SolutionDeployObligationService(db, ctx.org_id).list(limit=limit)


@router.get(
    "/solution-deploy-obligations/{obligation_id}",
    response_model=SolutionDeployObligationResponse,
)
async def get_solution_deploy_obligation(
    obligation_id: UUID,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> SolutionDeployObligationResponse:
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await SolutionDeployObligationService(db, ctx.org_id).get(obligation_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Solution deploy obligation not found",
        ) from exc


@router.get(
    "/source-releases/{source_release_id}",
    response_model=WorkspaceSourceReleaseResponse,
)
async def get_workspace_source_release(
    source_release_id: UUID,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> WorkspaceSourceReleaseResponse:
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await WorkspaceSourceReleaseService(db, ctx.org_id).get(
            source_release_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace source release not found",
        ) from exc


@router.post(
    "/source-releases/{source_release_id}/disposition",
    response_model=WorkspaceSourceReleaseResponse,
)
async def set_workspace_source_release_disposition(
    source_release_id: UUID,
    request: WorkspaceSourceReleaseDispositionRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> WorkspaceSourceReleaseResponse:
    """Record a deferred, non-production, or evidenced superseded decision."""
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await WorkspaceSourceReleaseService(
            db, ctx.org_id
        ).set_manual_disposition(
            source_release_id,
            disposition=request.disposition,
            reason=request.reason,
            supersession_evidence=request.supersession_evidence,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace source release not found",
        ) from exc
    except WorkspaceSourceReleaseConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
