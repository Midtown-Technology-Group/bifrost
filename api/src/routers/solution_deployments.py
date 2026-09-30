"""Admin API for immutable Solution deployment registration and pointer movement."""

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.solution_deployments import (
    DeploymentActivationPublic,
    DeploymentPointerRequest,
    SolutionDeploymentCapabilities,
    SolutionDeploymentCreate,
    SolutionDeploymentPublic,
    SolutionDeploymentRuntimeState,
    SharedTableBindingPreviewRequest,
    SolutionSourceRevisionCommitRequest,
    SolutionSourceRevisionInspectRequest,
    SolutionSourceRevisionInspectResponse,
    SolutionSourceRevisionRequest,
    WorkspaceLiveHandoffCommitRequest,
    WorkspaceLiveHandoffCommitResponse,
    WorkspaceLiveHandoffPreflightRequest,
    WorkspaceLiveHandoffPreflightResponse,
)
from src.models.orm.solutions import Solution
from src.repositories.solution_deployments import InvalidDeploymentTransition, SolutionDeploymentRepository
from src.services.solutions.deployment_activation import (
    ActivationResult,
    SolutionDeploymentActivationService,
)
from src.services.solutions.deployment_api import (
    DeploymentRegistrationConflict,
    SolutionDeploymentAPIService,
)
from src.services.solutions.deployment_storage import DeploymentArtifactIntegrityError
from src.services.solutions.live_handoff_candidate import (
    WorkspaceLiveHandoffCandidateService,
)
from src.services.solutions.live_handoff_commit import WorkspaceLiveHandoffCommitService
from src.services.solutions.live_handoff_preflight import (
    WorkspaceLiveHandoffPreflightConflict,
    WorkspaceLiveHandoffPreflightError,
    WorkspaceLiveHandoffPreflightService,
)
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict,
    SolutionSourceRevisionError,
    SolutionSourceRevisionService,
)
from src.services.solutions.write_lock import (
    SolutionWriteLockHeld,
    SolutionWriteLockLost,
    solution_write_lock,
)
from src.services.solutions.deployment_manifest import SharedRootTableBinding
from src.services.solutions.shared_table_bindings import SharedTableBindingError, table_metadata_hash

router = APIRouter(
    prefix="/api/solutions/{solution_id}/deployments", tags=["Solution Deployments"]
)


@router.get(
    "/capabilities",
    response_model=SolutionDeploymentCapabilities,
    responses={404: {"description": "Solution not found"}},
)
async def deployment_capabilities(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
):
    del user
    await _scope(ctx, solution_id)
    return SolutionDeploymentCapabilities()


async def _scope(ctx: Context, solution_id: UUID) -> UUID | None:
    solution = await ctx.db.get(Solution, solution_id)
    if solution is None:
        raise HTTPException(status_code=404, detail="Solution not found")
    return solution.organization_id


@router.post("/shared-tables/preview", response_model=dict[str, SharedRootTableBinding])
async def preview_shared_table_bindings(
    solution_id: UUID, body: SharedTableBindingPreviewRequest,
    ctx: Context, user: CurrentSuperuser,
) -> dict[str, SharedRootTableBinding]:
    """Read exact existing Root contracts; document data and ownership stay unchanged."""
    from src.models.orm.tables import Table
    del user
    await _scope(ctx, solution_id)
    result: dict[str, SharedRootTableBinding] = {}
    for table_id in sorted(set(body.table_ids), key=str):
        table = await ctx.db.get(Table, table_id, populate_existing=True)
        if table is None:
            raise HTTPException(status_code=404, detail="Table not found")
        try:
            metadata_hash = table_metadata_hash(table)
        except SharedTableBindingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if table.name in result:
            raise HTTPException(status_code=422, detail="Shared table names are ambiguous")
        result[table.name] = SharedRootTableBinding(table_id=table.id, metadata_hash=metadata_hash)
    return result


@router.get(
    "/active",
    response_model=SolutionDeploymentRuntimeState,
    responses={404: {"description": "Solution not found"}},
)
async def inspect_active_deployment(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> SolutionDeploymentRuntimeState:
    """Read the committed pointer independently of an activation response."""
    del user
    solution = await ctx.db.get(Solution, solution_id)
    if solution is None:
        raise HTTPException(status_code=404, detail="Solution not found")
    return SolutionDeploymentRuntimeState(
        solution_id=solution.id,
        active_deployment_id=solution.active_deployment_id,
        execution_runtime_mode=solution.execution_runtime_mode,
    )


def get_activation_service(ctx: Context) -> SolutionDeploymentActivationService:
    """Dependency seam overridden by the projection/artifact adapter and tests."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Solution deployment activation hooks are not configured",
    )


async def _run_pointer_move(
    ctx: Context,
    solution_id: UUID,
    operation: Callable[[UUID | None], Awaitable[ActivationResult]],
) -> ActivationResult:
    """Serialize one pointer mutation and normalize its transactional errors."""
    organization_id = await _scope(ctx, solution_id)
    try:
        async with solution_write_lock(solution_id):
            result = await operation(organization_id)
    except SolutionWriteLockHeld as exc:
        raise HTTPException(
            status_code=409, detail={"code": "solution_write_lock_held"}
        ) from exc
    except SolutionWriteLockLost as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"code": "solution_write_lock_lost", "retryable": True},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await ctx.db.commit()
    if result.state == "conflicted":
        raise HTTPException(status_code=409, detail=result.conflict)
    return result


@router.post(
    "",
    response_model=SolutionDeploymentPublic,
    status_code=201,
    responses={
        404: {"description": "Solution not found"},
        409: {"description": "Deployment registration conflict"},
        422: {"description": "Invalid deployment closure"},
    },
)
async def create_deployment(
    solution_id: UUID,
    body: SolutionDeploymentCreate,
    ctx: Context,
    user: CurrentSuperuser,
):
    try:
        row = await SolutionDeploymentAPIService(ctx.db).create_ready_draft(
            solution_id, user.user_id, body
        )
        await ctx.db.commit()
        return row
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DeploymentRegistrationConflict as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "deployment_registration_conflict",
                "message": str(exc),
                "reconcile": f"GET /api/solutions/{solution_id}/deployments/{body.compiled_manifest.deployment_id}",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/{deployment_id}",
    response_model=SolutionDeploymentPublic,
    responses={404: {"description": "Solution or deployment not found"}},
)
async def inspect_deployment(
    solution_id: UUID, deployment_id: UUID, ctx: Context, user: CurrentSuperuser
):
    del user
    organization_id = await _scope(ctx, solution_id)
    row = await SolutionDeploymentRepository(ctx.db).get_runtime_closure(
        deployment_id, organization_id
    )
    if row is None or row.solution_id != solution_id:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return row


@router.post(
    "/{deployment_id}/live-handoff/preflight",
    response_model=WorkspaceLiveHandoffPreflightResponse,
    responses={
        404: {"description": "Solution or deployment not found"},
        409: {"description": "Live or Solution state changed"},
        422: {"description": "Candidate cannot own the requested workflows"},
    },
)
async def preflight_live_handoff(
    solution_id: UUID,
    deployment_id: UUID,
    body: WorkspaceLiveHandoffPreflightRequest,
    ctx: Context,
    user: CurrentSuperuser,
):
    """Inspect a staged candidate without changing ownership or execution."""
    del user
    try:
        return await WorkspaceLiveHandoffPreflightService(ctx.db).inspect(
            solution_id, deployment_id, body
        )
    except WorkspaceLiveHandoffPreflightConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceLiveHandoffPreflightError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/{deployment_id}/live-handoff/candidate",
    response_model=WorkspaceLiveHandoffPreflightResponse,
    status_code=201,
    responses={
        409: {"description": "Live state, Solution state, or immutable object changed"},
        422: {"description": "The Live source closure cannot be proven"},
        503: {"description": "Solution write lock was lost"},
    },
)
async def build_live_handoff_candidate(
    solution_id: UUID,
    deployment_id: UUID,
    body: WorkspaceLiveHandoffPreflightRequest,
    ctx: Context,
    user: CurrentSuperuser,
):
    """Copy verified Live bytes into a new immutable Solution candidate."""
    try:
        async with solution_write_lock(solution_id):
            result = await WorkspaceLiveHandoffCandidateService(ctx.db).build(
                solution_id, deployment_id, user.user_id, body
            )
            await ctx.db.commit()
            return result
    except SolutionWriteLockHeld as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=409, detail={"code": "solution_write_lock_held"}
        ) from exc
    except SolutionWriteLockLost as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"code": "solution_write_lock_lost", "retryable": True},
        ) from exc
    except WorkspaceLiveHandoffPreflightConflict as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (DeploymentRegistrationConflict, DeploymentArtifactIntegrityError) as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceLiveHandoffPreflightError as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _commit_live_handoff(
    ctx: Context,
    solution_id: UUID,
    deployment_id: UUID,
    body: WorkspaceLiveHandoffCommitRequest,
    *,
    rollback: bool,
) -> WorkspaceLiveHandoffCommitResponse:
    try:
        async with solution_write_lock(solution_id):
            service = WorkspaceLiveHandoffCommitService(ctx.db)
            result = (
                await service.rollback(solution_id, deployment_id, body)
                if rollback
                else await service.activate(solution_id, deployment_id, body)
            )
            await ctx.db.commit()
            return result
    except SolutionWriteLockHeld as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=409, detail={"code": "solution_write_lock_held"}
        ) from exc
    except SolutionWriteLockLost as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "solution_write_lock_lost",
                "retryable": False,
                "message": "Inspect the Solution pointer and workflow owners before retrying",
            },
        ) from exc
    except WorkspaceLiveHandoffPreflightConflict as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceLiveHandoffPreflightError as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:  # Always release the failed database transaction.
        await ctx.db.rollback()
        raise


@router.post(
    "/{deployment_id}/live-handoff/activate",
    response_model=WorkspaceLiveHandoffCommitResponse,
    responses={
        409: {"description": "Reviewed Live, Solution, or workflow state changed"},
        422: {"description": "Candidate is not safe to activate"},
        503: {"description": "Commit outcome needs readback after write-lock loss"},
    },
)
async def activate_live_handoff(
    solution_id: UUID,
    deployment_id: UUID,
    body: WorkspaceLiveHandoffCommitRequest,
    ctx: Context,
    user: CurrentSuperuser,
):
    del user
    return await _commit_live_handoff(
        ctx, solution_id, deployment_id, body, rollback=False
    )


@router.post(
    "/{deployment_id}/live-handoff/rollback",
    response_model=WorkspaceLiveHandoffCommitResponse,
    responses={
        409: {"description": "Reviewed Live, Solution, or workflow state changed"},
        422: {"description": "Live rollback source is not safe"},
        503: {"description": "Commit outcome needs readback after write-lock loss"},
    },
)
async def rollback_live_handoff(
    solution_id: UUID,
    deployment_id: UUID,
    body: WorkspaceLiveHandoffCommitRequest,
    ctx: Context,
    user: CurrentSuperuser,
):
    del user
    return await _commit_live_handoff(
        ctx, solution_id, deployment_id, body, rollback=True
    )


@router.post(
    "/{deployment_id}/source-revision/candidate",
    response_model=SolutionSourceRevisionInspectResponse,
)
async def stage_source_revision(
    solution_id: UUID,
    deployment_id: UUID,
    body: SolutionSourceRevisionRequest,
    ctx: Context,
    user: CurrentSuperuser,
):
    """Stage exact source bytes while the current immutable pointer stays live."""
    try:
        async with solution_write_lock(solution_id):
            result = await SolutionSourceRevisionService(ctx.db).stage(
                solution_id, deployment_id, user.user_id, body
            )
            await ctx.db.commit()
            return result
    except SolutionWriteLockHeld as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail="Solution write lock held") from exc
    except SolutionWriteLockLost as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Inspect the candidate and Solution pointer before retrying",
        ) from exc
    except SolutionSourceRevisionConflict as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (DeploymentRegistrationConflict, InvalidDeploymentTransition) as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SolutionSourceRevisionError, DeploymentArtifactIntegrityError, ValueError) as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        await ctx.db.rollback()
        raise


@router.post(
    "/{deployment_id}/source-revision/preflight",
    response_model=SolutionSourceRevisionInspectResponse,
)
async def inspect_source_revision(
    solution_id: UUID,
    deployment_id: UUID,
    body: SolutionSourceRevisionInspectRequest,
    ctx: Context,
    user: CurrentSuperuser,
):
    del user
    try:
        return await SolutionSourceRevisionService(ctx.db).inspect(
            solution_id, deployment_id, body
        )
    except SolutionSourceRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SolutionSourceRevisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/{deployment_id}/source-revision/activate",
    response_model=SolutionSourceRevisionInspectResponse,
)
async def activate_source_revision(
    solution_id: UUID,
    deployment_id: UUID,
    body: SolutionSourceRevisionCommitRequest,
    ctx: Context,
    user: CurrentSuperuser,
):
    del user
    try:
        async with solution_write_lock(solution_id):
            result = await SolutionSourceRevisionService(ctx.db).activate(
                solution_id, deployment_id, body
            )
            await ctx.db.commit()
            return result
    except SolutionWriteLockHeld as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail="Solution write lock held") from exc
    except SolutionWriteLockLost as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Inspect the Solution pointer and candidate before retrying",
        ) from exc
    except SolutionSourceRevisionConflict as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SolutionSourceRevisionError as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidDeploymentTransition as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        await ctx.db.rollback()
        raise


@router.post(
    "/{deployment_id}/activate",
    response_model=DeploymentActivationPublic,
    responses={
        404: {"description": "Solution or deployment not found"},
        409: {"description": "Activation conflict or write lock held"},
        422: {"description": "Invalid activation request"},
        503: {"description": "Activation unavailable or write lock lost"},
    },
)
async def activate_deployment(
    solution_id: UUID,
    deployment_id: UUID,
    body: DeploymentPointerRequest,
    ctx: Context,
    user: CurrentSuperuser,
    service: Annotated[
        SolutionDeploymentActivationService, Depends(get_activation_service)
    ],
):
    del user
    return await _run_pointer_move(
        ctx,
        solution_id,
        partial(
            service.activate,
            deployment_id,
            solution_id=solution_id,
            expected_active_deployment_id=body.expected_active_deployment_id,
        ),
    )


@router.post(
    "/{deployment_id}/rollback",
    response_model=DeploymentActivationPublic,
    responses={
        404: {"description": "Solution or deployment not found"},
        409: {"description": "Rollback conflict or write lock held"},
        422: {"description": "Invalid rollback request"},
        503: {"description": "Rollback unavailable or write lock lost"},
    },
)
async def rollback_deployment(
    solution_id: UUID,
    deployment_id: UUID,
    body: DeploymentPointerRequest,
    ctx: Context,
    user: CurrentSuperuser,
    service: Annotated[
        SolutionDeploymentActivationService, Depends(get_activation_service)
    ],
):
    del user
    if body.expected_active_deployment_id is None:
        raise HTTPException(
            status_code=422, detail="rollback requires expected active deployment"
        )
    return await _run_pointer_move(
        ctx,
        solution_id,
        partial(
            service.rollback,
            deployment_id,
            solution_id=solution_id,
            expected_active_deployment_id=body.expected_active_deployment_id,
        ),
    )
