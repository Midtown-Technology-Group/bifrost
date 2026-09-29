"""Stage an immutable Solution candidate from verified Workspace Live bytes."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.contracts.solution_deployments import (
    SolutionDeploymentCreate,
    WorkspaceLiveHandoffPreflightRequest,
    WorkspaceLiveHandoffPreflightResponse,
)
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.solutions.deployment_api import SolutionDeploymentAPIService
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentGitProvenance,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeEntityDefinition,
    RuntimeSourceResolution,
    canonical_json,
    sha256_digest,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.live_handoff_preflight import (
    WorkspaceLiveHandoffPreflightError,
    WorkspaceLiveHandoffPreflightService,
    _require_live_identity,
    live_workflow_timeout,
    require_live_workflow,
)
from src.services.solutions.live_handoff_source import (
    LiveHandoffSourceError,
    source_archive,
    source_closure,
)
from src.services.workspace_release_files import WorkspaceReleaseFileView
from src.services.workspace_release_runtime import (
    WorkspaceReleaseRuntimeError,
    active_workspace_release,
)


class WorkspaceLiveHandoffCandidateService:
    """Build a reviewable candidate without changing a runtime pointer or owner."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build(
        self,
        solution_id: UUID,
        deployment_id: UUID,
        created_by: UUID,
        request: WorkspaceLiveHandoffPreflightRequest,
    ) -> WorkspaceLiveHandoffPreflightResponse:
        release = _require_live_identity(
            await active_workspace_release(self.db, None), request
        )
        solution = await self.db.get(Solution, solution_id)
        if solution is None or solution.status != "active":
            raise WorkspaceLiveHandoffPreflightError("Solution is not active")
        if (
            not solution.setup_complete
            or solution.allow_outbound_access
            or solution.git_connected
            or solution.active_deployment_id is not None
            or request.expected_active_deployment_id is not None
        ):
            raise WorkspaceLiveHandoffPreflightError(
                "candidate builder requires a new, configured, disconnected Solution"
            )
        installed = await self.db.scalar(
            select(Workflow.id).where(Workflow.solution_id == solution_id).limit(1)
        )
        if installed is not None:
            raise WorkspaceLiveHandoffPreflightError(
                "candidate builder requires a Solution without installed workflows"
            )
        selected = (
            (
                await self.db.execute(
                    select(Workflow)
                    .options(selectinload(Workflow.roles))
                    .where(Workflow.id.in_(request.workflow_ids))
                )
            )
            .scalars()
            .all()
        )
        if {workflow.id for workflow in selected} != set(request.workflow_ids):
            raise WorkspaceLiveHandoffPreflightError(
                "a requested workflow registration is missing"
            )
        registrations = {
            workflow.id: require_live_workflow(workflow, solution, release)
            for workflow in selected
        }
        try:
            live_bytes = await WorkspaceReleaseFileView.from_release(release).read_many(
                list(release.governed_paths)
            )
        except (FileNotFoundError, WorkspaceReleaseRuntimeError) as exc:
            raise WorkspaceLiveHandoffPreflightError(
                "Live source bytes differ from the active descriptor"
            ) from exc
        try:
            files = source_closure(
                live_bytes, {path for path, _ in registrations.values()}
            )
            archive = source_archive(files)
        except LiveHandoffSourceError as exc:
            raise WorkspaceLiveHandoffPreflightError(str(exc)) from exc
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        sources = {
            path: RuntimeSourceResolution(
                object_key=f"{storage.runtime_prefix}{path}",
                content_hash=sha256_digest(content),
            )
            for path, content in files.items()
        }
        entities: dict[str, RuntimeEntityDefinition] = {}
        for workflow in selected:
            path, registration = registrations[workflow.id]
            bounds = registration["runtime_bounds"]
            portable_ref = f"{path}::{workflow.function_name}"
            entities[portable_ref] = RuntimeEntityDefinition(
                portable_ref=portable_ref,
                resolved_id=workflow.id,
                source_ref=path,
                source_hash=sources[path].content_hash,
                definition={
                    "path": path,
                    "function_name": workflow.function_name,
                    "name": workflow.name,
                    "type": workflow.type,
                    "organization_id": (
                        str(workflow.organization_id)
                        if workflow.organization_id
                        else None
                    ),
                    "timeout_seconds": live_workflow_timeout(workflow, registration),
                    "execution_mode": workflow.execution_mode,
                    "time_saved": workflow.time_saved or 0,
                    "value": float(workflow.value or 0),
                    "cache_ttl_seconds": workflow.cache_ttl_seconds or 0,
                    "runtime_bounds": bounds,
                },
            )
        resolution = DeploymentResolutionMap(workflows=entities, sources=sources)
        manifest = CompiledDeploymentManifest(
            solution_id=solution_id,
            deployment_id=deployment_id,
            bundle_hash=sha256_digest(
                canonical_json(
                    {
                        "release_id": release.release_id,
                        "workflow_ids": sorted(
                            str(item) for item in request.workflow_ids
                        ),
                        "source_hashes": {
                            path: source.content_hash
                            for path, source in sources.items()
                        },
                    }
                )
            ),
            resolution_map_hash=sha256_digest(canonical_json(resolution)),
            source=DeploymentSource(
                artifact_key=storage.source_artifact_key,
                runtime_prefix=storage.runtime_prefix,
            ),
            workflows=entities,
            git=DeploymentGitProvenance(commit_sha=release.source_commit_sha),
        )
        await storage.write_source_artifact(archive, idempotent=True)
        upload_slots = asyncio.Semaphore(16)

        async def write_runtime(path: str, content: bytes) -> None:
            async with upload_slots:
                await storage.write_runtime_file(path, content, idempotent=True)

        await asyncio.gather(
            *(write_runtime(path, content) for path, content in files.items())
        )
        await SolutionDeploymentAPIService(self.db).create_ready_draft(
            solution_id,
            created_by,
            SolutionDeploymentCreate(
                compiled_manifest=manifest,
                resolution_map=resolution,
                git_commit_sha=release.source_commit_sha,
            ),
        )
        _require_live_identity(await active_workspace_release(self.db, None), request)
        return await WorkspaceLiveHandoffPreflightService(self.db).inspect(
            solution_id, deployment_id, request
        )
