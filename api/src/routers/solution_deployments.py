"""Admin API for immutable Solution deployment registration and pointer movement."""

import base64
import binascii
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from src.config import get_settings
from src.core.auth import Context, CurrentSuperuser, bearer_scheme
from src.core.db_deps import DbSession
from src.models.contracts.solution_deployments import (
    DeploymentActivationPublic,
    DeploymentPointerRequest,
    InitialWorkflowInstallCommitRequest,
    InitialWorkflowInstallInspectRequest,
    InitialWorkflowInstallInspectResponse,
    InitialWorkflowInstallRequest,
    RepoWorkflowAdoptionInspectResponse,
    SharedTableBindingPreviewRequest,
    SolutionDeploymentCapabilities,
    SolutionDeploymentCreate,
    SolutionDeploymentPublic,
    SolutionDeploymentRuntimeState,
    SolutionGitSourceDeliveryRequest,
    SolutionGitSourceDeliveryResponse,
    SolutionSourceRevisionCommitRequest,
    SolutionSourceRevisionInspectRequest,
    SolutionSourceRevisionInspectResponse,
    SolutionSourceRevisionRequest,
    SolutionWorkflowRevisionCommitRequest,
    SolutionWorkflowRevisionInspectRequest,
    SolutionWorkflowRevisionRequest,
    WorkspaceLiveHandoffCommitRequest,
    WorkspaceLiveHandoffCommitResponse,
    WorkspaceLiveHandoffPreflightRequest,
    WorkspaceLiveHandoffPreflightResponse,
)
from src.models.orm.solutions import Solution
from src.models.contracts.platform_jobs import PlatformJobPublic
from src.repositories.solution_deployments import (
    InvalidDeploymentTransition,
    SolutionDeploymentRepository,
)
from src.services.solutions.deployment_activation import (
    ActivationResult,
    SolutionDeploymentActivationService,
)
from src.services.solutions.deployment_api import (
    DeploymentRegistrationConflict,
    SolutionDeploymentAPIService,
)
from src.services.solutions.deployment_manifest import (
    MAX_DEPLOYMENT_RESOURCE_BYTES,
    MAX_DEPLOYMENT_RESOURCES_BYTES,
    SharedRootTableBinding,
)
from src.services.solutions.deployment_storage import DeploymentArtifactIntegrityError
from src.services.solutions.github_delivery_source import (
    GitDeliverySourceError,
    ProtectedGitReader,
    authenticate_git_delivery,
)
from src.services.solutions.github_source_delivery import GitSourceDeliveryService
from src.services.solutions.initial_workflow_install import (
    InitialWorkflowInstallService,
)
from src.services.solutions.repo_workflow_adoption import RepoWorkflowAdoptionService
from src.services.solutions.live_handoff_candidate import (
    WorkspaceLiveHandoffCandidateService,
)
from src.services.solutions.live_handoff_commit import WorkspaceLiveHandoffCommitService
from src.services.solutions.live_handoff_preflight import (
    WorkspaceLiveHandoffPreflightConflict,
    WorkspaceLiveHandoffPreflightError,
    WorkspaceLiveHandoffPreflightService,
)
from src.services.solutions.resource_delivery import validate_resource_files
from src.services.solutions.shared_table_bindings import (
    SharedTableBindingError,
    table_metadata_hash,
)
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict,
    SolutionSourceRevisionError,
    SolutionSourceRevisionService,
    _decode_files,
    _decode_source_files,
)
from src.services.solutions.workflow_revision import SolutionWorkflowRevisionService
from src.services.solutions.write_lock import (
    SolutionWriteLockHeld,
    SolutionWriteLockLost,
    solution_write_lock,
)

router = APIRouter(
    prefix="/api/solutions/{solution_id}/deployments", tags=["Solution Deployments"]
)


async def _authenticate_package(solution_id, body, credentials):
    from src.services.solutions.package_git_source import authenticate_package_git_delivery
    policy = get_settings().solution_package_git_delivery_policy
    if policy is None:
        raise HTTPException(status_code=503, detail="Protected complete package delivery is not configured")
    if credentials is None:
        raise HTTPException(status_code=401, detail="GitHub Actions package OIDC token is required")
    try:
        await authenticate_package_git_delivery(credentials.credentials, policy=policy,
            solution_id=solution_id, commit_sha=body.source_commit_sha, ci_run_id=body.ci_run_id,
            ci_run_attempt=body.ci_run_attempt, artifact_digest=body.artifact_digest)
    except GitDeliverySourceError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return policy


@router.post("/github-package", response_model=PlatformJobPublic)
async def deliver_github_package(
    solution_id: UUID, body: SolutionGitSourceDeliveryRequest, db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    github_token: Annotated[str, Header(alias="X-GitHub-Job-Token", min_length=1, max_length=4096)],
):
    """Capture protected complete source, then use the shared durable publisher."""
    from src.services.solutions.package_git_source import read_package_git_source
    from src.services.solutions.package_admission import admit_package
    from src.services.platform_jobs import platform_job_to_public
    policy = await _authenticate_package(solution_id, body, credentials)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            source = await read_package_git_source(ProtectedGitReader(policy, github_token, client),
                policy=policy, solution_id=solution_id, commit_sha=body.source_commit_sha,
                ci_run_id=body.ci_run_id, ci_run_attempt=body.ci_run_attempt, artifact_digest=body.artifact_digest)
        return platform_job_to_public(await admit_package(db, policy, solution_id, source, github_token))
    except HTTPException:
        await db.rollback()
        raise
    except (GitDeliverySourceError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (SolutionWriteLockHeld, SolutionWriteLockLost, httpx.HTTPError) as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Inspect the original package job before retrying") from exc


@router.post("/github-package/status", response_model=PlatformJobPublic)
async def inspect_github_package(
    solution_id: UUID, body: SolutionGitSourceDeliveryRequest, db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
):
    """Same source-scoped identity, independently verified original-job result."""
    from src.services.solutions.package_admission import inspect_package_job, read_package_accounting
    from src.services.platform_jobs import platform_job_to_public
    await _authenticate_package(solution_id, body, credentials)
    try:
        job = await inspect_package_job(db, solution_id, body.artifact_digest)
        public = platform_job_to_public(job)
        if job.status == "succeeded":
            public.result = {**(public.result or {}), "accounting_readback": await read_package_accounting(db, job)}
        return public
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/github-source", response_model=SolutionGitSourceDeliveryResponse)
async def deliver_github_source(
    solution_id: UUID, body: SolutionGitSourceDeliveryRequest, db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    github_token: Annotated[str, Header(alias="X-GitHub-Job-Token", min_length=1, max_length=4096)],
):
    """A source-scoped producer can deliver only the configured protected recipe."""
    policy = get_settings().solution_git_delivery_policy
    if policy is None:
        raise HTTPException(status_code=503, detail="Protected Git Solution delivery is not configured")
    if credentials is None:
        raise HTTPException(status_code=401, detail="GitHub Actions delivery OIDC token is required")
    try:
        producer = await authenticate_git_delivery(credentials.credentials, policy=policy,
            solution_id=solution_id, commit_sha=body.source_commit_sha, ci_run_id=body.ci_run_id,
            ci_run_attempt=body.ci_run_attempt, artifact_digest=body.artifact_digest)
    except GitDeliverySourceError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            reader = ProtectedGitReader(policy, github_token, client)
            return await GitSourceDeliveryService(db, policy, reader).deliver(solution_id, body, producer)
    except SolutionWriteLockHeld as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Solution write lock held") from exc
    except (SolutionWriteLockLost, DeploymentArtifactIntegrityError, httpx.HTTPError) as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Inspect the installed pointer and receipt before a fresh job attempt") from exc
    except (SolutionSourceRevisionConflict, DeploymentRegistrationConflict, InvalidDeploymentTransition) as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (GitDeliverySourceError, SolutionSourceRevisionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


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
    organization_id = await _scope(ctx, solution_id)
    result: dict[str, SharedRootTableBinding] = {}
    for table_id in sorted(set(body.table_ids), key=str):
        table = await ctx.db.get(Table, table_id, populate_existing=True)
        if table is None:
            raise HTTPException(status_code=404, detail="Table not found")
        if (
            table.organization_id is not None
            and organization_id is not None
            and table.organization_id != organization_id
        ):
            raise HTTPException(status_code=422, detail="Shared table organization differs from the Solution installation")
        try:
            metadata_hash = table_metadata_hash(table, organization_id=table.organization_id)
        except SharedTableBindingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        grant = SharedRootTableBinding(
            table_id=table.id, metadata_hash=metadata_hash, organization_id=table.organization_id,
        )
        existing = result.get(table.name)
        if existing is None:
            result[table.name] = grant
        else:
            from bifrost.solution_delivery_review import SharedRootTableGrant
            try:
                result[table.name] = existing.with_scope(SharedRootTableGrant(**grant.model_dump()))
            except ValueError as exc:
                raise HTTPException(
                    status_code=422, detail="Shared table names are ambiguous within one scope",
                ) from exc
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


def _decode_workflow_revision_resources(body: SolutionWorkflowRevisionRequest) -> dict[str, bytes]:
    resources: dict[str, bytes] = {}
    total = 0
    for item in body.resources:
        if item.path in resources:
            raise SolutionSourceRevisionError("Duplicate resource upload")
        if len(item.content_base64) > 4 * ((MAX_DEPLOYMENT_RESOURCE_BYTES + 2) // 3):
            raise SolutionSourceRevisionError("Resource upload exceeds its byte bound")
        try:
            content = base64.b64decode(item.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SolutionSourceRevisionError("Invalid resource upload encoding") from exc
        total += len(content)
        if not 1 <= len(content) <= MAX_DEPLOYMENT_RESOURCE_BYTES or total > MAX_DEPLOYMENT_RESOURCES_BYTES:
            raise SolutionSourceRevisionError("Resource upload exceeds its byte bound")
        resources[item.path] = content
    return resources


def _decode_initial_workflow_resources(body: InitialWorkflowInstallRequest) -> dict[str, bytes]:
    resources: dict[str, bytes] = {}
    total = 0
    for item in body.resources:
        if item.path in resources:
            raise SolutionSourceRevisionError("Duplicate resource upload")
        if len(item.content_base64) > 4 * ((MAX_DEPLOYMENT_RESOURCE_BYTES + 2) // 3):
            raise SolutionSourceRevisionError("Resource upload exceeds its byte bound")
        try:
            content = base64.b64decode(item.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SolutionSourceRevisionError("Invalid resource upload encoding") from exc
        total += len(content)
        if not 1 <= len(content) <= MAX_DEPLOYMENT_RESOURCE_BYTES or total > MAX_DEPLOYMENT_RESOURCES_BYTES:
            raise SolutionSourceRevisionError("Resource upload exceeds its byte bound")
        resources[item.path] = content
    return resources


async def _write_initial_workflow_install(
    ctx: Context, solution_id: UUID,
    operation: Callable[[], Awaitable[InitialWorkflowInstallInspectResponse]],
) -> InitialWorkflowInstallInspectResponse:
    try:
        async with solution_write_lock(solution_id):
            result = await operation()
            await ctx.db.commit()
            return result
    except SolutionWriteLockHeld as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail="Solution write lock held") from exc
    except SolutionWriteLockLost as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=503, detail="Inspect the candidate before retrying") from exc
    except (SolutionSourceRevisionConflict, DeploymentRegistrationConflict, InvalidDeploymentTransition) as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SolutionSourceRevisionError, DeploymentArtifactIntegrityError, ValueError) as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        await ctx.db.rollback()
        raise


@router.post("/{deployment_id}/initial-workflow/candidate", response_model=InitialWorkflowInstallInspectResponse)
async def stage_initial_workflow_install(
    solution_id: UUID, deployment_id: UUID, body: InitialWorkflowInstallRequest,
    ctx: Context, user: CurrentSuperuser,
):
    """Stage the first immutable workflow-only closure for a disconnected Solution."""
    async def stage():
        files = _decode_source_files(body.files)
        resources = _decode_initial_workflow_resources(body)
        return await InitialWorkflowInstallService(ctx.db).stage(
            solution_id, deployment_id, user.user_id, body, files, resources,
        )
    return await _write_initial_workflow_install(ctx, solution_id, stage)


@router.post("/{deployment_id}/initial-workflow/preflight", response_model=InitialWorkflowInstallInspectResponse)
async def inspect_initial_workflow_install(
    solution_id: UUID, deployment_id: UUID, body: InitialWorkflowInstallInspectRequest,
    ctx: Context, user: CurrentSuperuser,
):
    del user
    try:
        return await InitialWorkflowInstallService(ctx.db).inspect(solution_id, deployment_id, body)
    except SolutionSourceRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SolutionSourceRevisionError, DeploymentArtifactIntegrityError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{deployment_id}/initial-workflow/activate", response_model=InitialWorkflowInstallInspectResponse)
async def activate_initial_workflow_install(
    solution_id: UUID, deployment_id: UUID, body: InitialWorkflowInstallCommitRequest,
    ctx: Context, user: CurrentSuperuser,
):
    del user
    return await _write_initial_workflow_install(ctx, solution_id, partial(
        InitialWorkflowInstallService(ctx.db).activate,
        solution_id, deployment_id,
        InitialWorkflowInstallInspectRequest(reviewed_recipe=body.reviewed_recipe),
        body.expected_evidence_id,
    ))


@router.post("/{deployment_id}/repo-workflow-adoption/candidate", response_model=RepoWorkflowAdoptionInspectResponse)
async def stage_repo_workflow_adoption(
    solution_id: UUID, deployment_id: UUID, body: InitialWorkflowInstallRequest,
    ctx: Context, user: CurrentSuperuser,
):
    """Stage reviewed source for a populated legacy install, preserving entities."""
    async def stage():
        return await RepoWorkflowAdoptionService(ctx.db).stage(
            solution_id, deployment_id, user.user_id, body,
            _decode_source_files(body.files), _decode_initial_workflow_resources(body),
        )
    return await _write_initial_workflow_install(ctx, solution_id, stage)


@router.post("/{deployment_id}/repo-workflow-adoption/preflight", response_model=RepoWorkflowAdoptionInspectResponse)
async def inspect_repo_workflow_adoption(
    solution_id: UUID, deployment_id: UUID, body: InitialWorkflowInstallInspectRequest,
    ctx: Context, user: CurrentSuperuser,
):
    del user
    try:
        return await RepoWorkflowAdoptionService(ctx.db).inspect(solution_id, deployment_id, body)
    except SolutionSourceRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SolutionSourceRevisionError, DeploymentArtifactIntegrityError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{deployment_id}/repo-workflow-adoption/activate", response_model=RepoWorkflowAdoptionInspectResponse)
async def activate_repo_workflow_adoption(
    solution_id: UUID, deployment_id: UUID, body: InitialWorkflowInstallCommitRequest,
    ctx: Context, user: CurrentSuperuser,
):
    del user
    return await _write_initial_workflow_install(ctx, solution_id, partial(
        RepoWorkflowAdoptionService(ctx.db).activate,
        solution_id, deployment_id,
        InitialWorkflowInstallInspectRequest(reviewed_recipe=body.reviewed_recipe),
        body.expected_evidence_id,
    ))


async def _write_workflow_revision(
    ctx: Context, solution_id: UUID,
    operation: Callable[[], Awaitable[SolutionSourceRevisionInspectResponse]],
) -> SolutionSourceRevisionInspectResponse:
    try:
        async with solution_write_lock(solution_id):
            result = await operation()
            await ctx.db.commit()
            return result
    except SolutionWriteLockHeld as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail="Solution write lock held") from exc
    except SolutionWriteLockLost as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=503, detail="Inspect the pointer and candidate before retrying") from exc
    except (SolutionSourceRevisionConflict, DeploymentRegistrationConflict, InvalidDeploymentTransition) as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SolutionSourceRevisionError, DeploymentArtifactIntegrityError, ValueError) as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        await ctx.db.rollback()
        raise


@router.post("/{deployment_id}/workflow-revision/candidate", response_model=SolutionSourceRevisionInspectResponse)
async def stage_workflow_revision(
    solution_id: UUID, deployment_id: UUID, body: SolutionWorkflowRevisionRequest,
    ctx: Context, user: CurrentSuperuser,
):
    """Stage a reviewed complete successor without changing registrations or pointer."""
    async def stage():
        files = _decode_files(body)
        resources = _decode_workflow_revision_resources(body)
        validate_resource_files(body.reviewed_recipe, resources, files)
        expected = SolutionSourceRevisionInspectRequest(
            expected_active_deployment_id=body.expected_active_deployment_id,
            expected_active_manifest_hash=body.expected_active_manifest_hash,
        )
        return await SolutionWorkflowRevisionService(ctx.db).stage_workflows(
            solution_id, deployment_id, user.user_id, expected, body.reviewed_recipe,
            files, body.source_commit_sha, resources,
        )
    return await _write_workflow_revision(ctx, solution_id, stage)


@router.post("/{deployment_id}/workflow-revision/preflight", response_model=SolutionSourceRevisionInspectResponse)
async def inspect_workflow_revision(
    solution_id: UUID, deployment_id: UUID, body: SolutionWorkflowRevisionInspectRequest,
    ctx: Context, user: CurrentSuperuser,
):
    """Read immutable bytes, registrations, triggers and the exact reviewed recipe."""
    del user
    try:
        return await SolutionWorkflowRevisionService(ctx.db).inspect_workflows(
            solution_id, deployment_id, body, body.reviewed_recipe,
        )
    except SolutionSourceRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SolutionSourceRevisionError, DeploymentArtifactIntegrityError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{deployment_id}/workflow-revision/activate", response_model=SolutionSourceRevisionInspectResponse)
async def activate_workflow_revision(
    solution_id: UUID, deployment_id: UUID, body: SolutionWorkflowRevisionCommitRequest,
    ctx: Context, user: CurrentSuperuser,
):
    """Atomically activate the exact preflight evidence and compatible registrations."""
    del user
    return await _write_workflow_revision(ctx, solution_id, partial(
        SolutionWorkflowRevisionService(ctx.db).activate_workflows,
        solution_id, deployment_id, body, body.reviewed_recipe,
    ))


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
