"""Read-only proof that a Solution candidate can replace selected Live workflows."""

from __future__ import annotations

from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from bifrost.workspace_release import canonical_digest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.contracts.solution_deployments import (
    WorkspaceLiveHandoffPreflightRequest,
    WorkspaceLiveHandoffPreflightResponse,
)
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import (
    DeploymentResolutionMap,
    sha256_digest,
    validate_runtime_closure,
)
from src.services.solutions.deployment_storage import (
    SolutionDeploymentStorage,
    deployment_runtime_prefix,
    deployment_source_artifact_key,
)
from src.services.solutions.live_handoff_source import (
    MAX_ARCHIVE_BYTES,
    LiveHandoffSourceError,
    source_closure,
)
from src.services.workspace_release_files import WorkspaceReleaseFileView
from src.services.workspace_release_runtime import (
    WorkspaceReleaseDescriptor,
    WorkspaceReleaseRuntimeError,
    _registration_binding,
    active_workspace_release,
)


class WorkspaceLiveHandoffPreflightError(ValueError):
    """A candidate cannot take ownership of the requested Live workflows."""


class WorkspaceLiveHandoffPreflightConflict(WorkspaceLiveHandoffPreflightError):
    """The release or Solution pointer differs from the reviewed expectation."""


def _require_live_identity(
    release: WorkspaceReleaseDescriptor | None,
    request: WorkspaceLiveHandoffPreflightRequest,
) -> WorkspaceReleaseDescriptor:
    if release is None or (
        release.release_row_id != request.expected_release_row_id
        or release.release_id != request.expected_release_id
        or release.artifact_id != request.expected_artifact_id
        or release.governed_manifest_id != request.expected_governed_manifest_id
        or release.registration_state_fingerprint
        != request.expected_registration_state_fingerprint
    ):
        raise WorkspaceLiveHandoffPreflightConflict("Live release identity changed")
    return release


def _require_workflow_binding(
    workflow: Workflow,
    solution: Solution,
    release: WorkspaceReleaseDescriptor,
    resolution: DeploymentResolutionMap,
) -> None:
    path, registration = require_live_workflow(workflow, solution, release)
    runtime_bounds = registration["runtime_bounds"]
    try:
        entity = resolution.resolve_workflow_id(workflow.id)
    except KeyError as exc:
        raise WorkspaceLiveHandoffPreflightError(
            f"candidate lacks workflow {workflow.id}"
        ) from exc
    definition = entity.definition
    expected = {
        "path": path,
        "function_name": workflow.function_name,
        "name": workflow.name,
        "type": workflow.type,
        "organization_id": (
            str(workflow.organization_id) if workflow.organization_id else None
        ),
        "timeout_seconds": min(
            workflow.timeout_seconds or 1800,
            runtime_bounds["max_duration_seconds"],
        ),
        "execution_mode": workflow.execution_mode,
        "time_saved": workflow.time_saved or 0,
        "value": float(workflow.value or 0),
        "cache_ttl_seconds": workflow.cache_ttl_seconds or 0,
        "runtime_bounds": runtime_bounds,
    }
    if any(definition.get(key) != value for key, value in expected.items()):
        raise WorkspaceLiveHandoffPreflightError(
            f"candidate metadata differs from workflow {workflow.id}"
        )
    if entity.source_ref != path or entity.source_hash != (
        f"sha256:{release.source_hashes.get(path)}"
    ):
        raise WorkspaceLiveHandoffPreflightError(
            f"candidate source differs from Live workflow {workflow.id}"
        )


def require_live_workflow(
    workflow: Workflow,
    solution: Solution,
    release: WorkspaceReleaseDescriptor,
) -> tuple[str, dict]:
    """Prove that a loose UUID still has the reviewed Live owner and scope."""
    if workflow.solution_id is not None:
        raise WorkspaceLiveHandoffPreflightError(
            f"workflow {workflow.id} already has a Solution owner"
        )
    if workflow.organization_id != solution.organization_id:
        raise WorkspaceLiveHandoffPreflightError(
            f"workflow {workflow.id} does not match the Solution scope"
        )
    if (
        not workflow.is_active
        or _registration_binding(workflow, release).status != "bound"
    ):
        raise WorkspaceLiveHandoffPreflightError(
            f"workflow {workflow.id} is not bound to the expected Live release"
        )
    path = workflow.path.replace("\\", "/").lstrip("/")
    registration = release.effective_registrations[f"{path}::{workflow.function_name}"]
    return path, registration


def _verify_source_archive(archive: bytes, source_bytes: dict[str, bytes]) -> None:
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise WorkspaceLiveHandoffPreflightError(
            "candidate source archive is too large"
        )
    try:
        with ZipFile(BytesIO(archive)) as source_zip:
            files = [item for item in source_zip.infolist() if not item.is_dir()]
            names = [item.filename for item in files]
            if (
                len(names) != len(set(names))
                or set(names) != set(source_bytes)
                or sum(item.file_size for item in files) > MAX_ARCHIVE_BYTES
            ):
                raise WorkspaceLiveHandoffPreflightError(
                    "candidate archive does not match the reviewed source closure"
                )
            for item in files:
                if source_zip.read(item) != source_bytes[item.filename]:
                    raise WorkspaceLiveHandoffPreflightError(
                        f"candidate archive bytes differ from Live: {item.filename}"
                    )
    except (BadZipFile, RuntimeError) as exc:
        raise WorkspaceLiveHandoffPreflightError(
            "candidate source archive is invalid"
        ) from exc


class WorkspaceLiveHandoffPreflightService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def inspect(
        self,
        solution_id: UUID,
        deployment_id: UUID,
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
        ):
            raise WorkspaceLiveHandoffPreflightError(
                "Solution must be configured, disconnected, and sealed from mutable Workspace source"
            )
        repository = SolutionDeploymentRepository(self.db)
        deployment = await repository.get_runtime_closure(
            deployment_id, solution.organization_id, solution_id
        )
        if deployment is None or deployment.state != "ready":
            raise WorkspaceLiveHandoffPreflightError(
                "candidate deployment is not ready"
            )
        if (
            solution.active_deployment_id != request.expected_active_deployment_id
            or deployment.base_deployment_id != request.expected_active_deployment_id
        ):
            raise WorkspaceLiveHandoffPreflightConflict(
                "Solution active deployment changed"
            )
        try:
            manifest, resolution = validate_runtime_closure(
                deployment.compiled_manifest,
                deployment.resolution_map,
                deployment.dependencies,
                expected_manifest_hash=deployment.compiled_manifest_hash,
                expected_resolution_hash=deployment.resolution_map_hash,
            )
        except ValueError as exc:
            raise WorkspaceLiveHandoffPreflightError(
                "candidate deployment closure is invalid"
            ) from exc
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        if (
            manifest.source.artifact_key
            != deployment_source_artifact_key(solution_id, deployment_id)
            or deployment.source_artifact_key != storage.source_artifact_key
            or manifest.source.runtime_prefix
            != deployment_runtime_prefix(solution_id, deployment_id)
            or deployment.runtime_storage_prefix != storage.runtime_prefix
        ):
            raise WorkspaceLiveHandoffPreflightError(
                "candidate storage references are not canonical"
            )
        if await storage.read_compiled_manifest() != manifest.canonical_bytes():
            raise WorkspaceLiveHandoffPreflightError(
                "stored candidate manifest differs from its reviewed closure"
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
        if {row.id for row in selected} != set(request.workflow_ids):
            raise WorkspaceLiveHandoffPreflightError(
                "a requested workflow registration is missing"
            )
        for workflow in selected:
            _require_workflow_binding(workflow, solution, release, resolution)
        installed_ids = set(
            (
                await self.db.scalars(
                    select(Workflow.id).where(Workflow.solution_id == solution_id)
                )
            ).all()
        )
        candidate_ids = {item.resolved_id for item in resolution.workflows.values()}
        if candidate_ids != installed_ids | set(request.workflow_ids):
            raise WorkspaceLiveHandoffPreflightError(
                "candidate workflow IDs differ from the reviewed handoff set"
            )
        source_paths = sorted(resolution.sources)
        if not source_paths or any(
            path not in release.governed_paths for path in source_paths
        ):
            raise WorkspaceLiveHandoffPreflightError(
                "candidate source closure is not entirely governed by Live"
            )
        try:
            live_bytes = await WorkspaceReleaseFileView.from_release(
                release
            ).read_many(list(release.governed_paths))
        except (FileNotFoundError, WorkspaceReleaseRuntimeError) as exc:
            raise WorkspaceLiveHandoffPreflightError(
                "Live source bytes differ from the active descriptor"
            ) from exc
        entry_paths = {
            workflow.path.replace("\\", "/").lstrip("/") for workflow in selected
        }
        if installed_ids:
            entry_paths.update(
                (
                    await self.db.scalars(
                        select(Workflow.path).where(Workflow.id.in_(installed_ids))
                    )
                ).all()
            )
        try:
            source_bytes = source_closure(live_bytes, entry_paths)
        except LiveHandoffSourceError as exc:
            raise WorkspaceLiveHandoffPreflightError(str(exc)) from exc
        if set(source_paths) != set(source_bytes):
            raise WorkspaceLiveHandoffPreflightError(
                "candidate source paths differ from the complete Live dependency closure"
            )
        for path in source_paths:
            source = resolution.sources[path]
            if (
                source.object_key != f"{storage.runtime_prefix}{path}"
                or source.content_hash != sha256_digest(source_bytes[path])
                or await storage.read_runtime_file(path) != source_bytes[path]
            ):
                raise WorkspaceLiveHandoffPreflightError(
                    f"candidate runtime bytes differ from Live: {path}"
                )
        _verify_source_archive(await storage.read_source_artifact(), source_bytes)
        _require_live_identity(await active_workspace_release(self.db, None), request)
        workflow_ids = sorted(request.workflow_ids, key=str)
        evidence = {
            "schema_version": "bifrost.workspace-live-handoff-preflight/v1",
            "release_row_id": str(release.release_row_id),
            "release_id": release.release_id,
            "artifact_id": str(release.artifact_id),
            "governed_manifest_id": release.governed_manifest_id,
            "registration_state_fingerprint": release.registration_state_fingerprint,
            "solution_id": str(solution_id),
            "deployment_id": str(deployment_id),
            "compiled_manifest_hash": deployment.compiled_manifest_hash,
            "resolution_map_hash": deployment.resolution_map_hash,
            "expected_active_deployment_id": (
                str(request.expected_active_deployment_id)
                if request.expected_active_deployment_id
                else None
            ),
            "workflow_ids": [str(item) for item in workflow_ids],
            "source_hashes": {
                path: release.source_hashes[path] for path in source_paths
            },
        }
        return WorkspaceLiveHandoffPreflightResponse(
            solution_id=solution_id,
            deployment_id=deployment_id,
            release_row_id=release.release_row_id,
            release_id=release.release_id,
            governed_manifest_id=release.governed_manifest_id,
            registration_state_fingerprint=release.registration_state_fingerprint,
            workflow_ids=workflow_ids,
            verified_source_paths=source_paths,
            expected_active_deployment_id=request.expected_active_deployment_id,
            evidence_id=canonical_digest(evidence),
        )
