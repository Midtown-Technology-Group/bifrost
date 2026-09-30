"""Atomic owner and runtime-pointer movement for a reviewed Live handoff."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.contracts.solution_deployments import (
    WorkspaceLiveHandoffCommitRequest,
    WorkspaceLiveHandoffCommitResponse,
)
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import (
    sha256_digest,
    validate_runtime_closure,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.live_handoff_preflight import (
    WorkspaceLiveHandoffPreflightConflict,
    WorkspaceLiveHandoffPreflightError,
    WorkspaceLiveHandoffPreflightService,
    _require_empty_solution_install,
    _require_live_identity,
    _require_workflow_binding,
    _verify_source_archive,
)
from src.services.solutions.live_handoff_source import (
    LiveHandoffSourceError,
    source_closure,
)
from src.services.solutions.shared_table_bindings import SharedTableBindingError, require_shared_tables
from src.services.workspace_release_files import WorkspaceReleaseFileView
from src.services.workspace_release_projection import acquire_workspace_release_lock
from src.services.workspace_release_runtime import (
    WorkspaceReleaseRuntimeError,
    active_workspace_release,
)

_HANDOFF_MARKER_SCHEMA = "bifrost.workspace-live-handoff/v1"


class WorkspaceLiveHandoffCommitService:
    """Complete one new Solution install in the caller's database transaction."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def activate(
        self,
        solution_id: UUID,
        deployment_id: UUID,
        request: WorkspaceLiveHandoffCommitRequest,
    ) -> WorkspaceLiveHandoffCommitResponse:
        # Release activation and Workspace registration writers take this same
        # advisory lock. The per-Solution Redis write lock is held by the route.
        await acquire_workspace_release_lock(self.db, None)
        solution = await self.db.scalar(
            select(Solution).where(Solution.id == solution_id).with_for_update()
        )
        if solution is None or solution.status != "active":
            raise WorkspaceLiveHandoffPreflightError("Solution is not active")
        if (
            solution.active_deployment_id is not None
            or request.expected_active_deployment_id is not None
            or solution.execution_runtime_mode != "repo-v1"
        ):
            raise WorkspaceLiveHandoffPreflightConflict(
                "handoff activation requires a fresh Solution pointer"
            )
        await _require_empty_solution_install(self.db, solution_id)
        inspection = await WorkspaceLiveHandoffPreflightService(self.db).inspect(
            solution_id, deployment_id, request, lock_selected=True
        )
        if inspection.evidence_id != request.expected_evidence_id:
            raise WorkspaceLiveHandoffPreflightConflict(
                "handoff evidence changed after review"
            )
        repository = SolutionDeploymentRepository(self.db)
        marker = {
            "schema_version": _HANDOFF_MARKER_SCHEMA,
            "preflight_evidence_id": inspection.evidence_id,
            "release_row_id": str(inspection.release_row_id),
            "release_id": inspection.release_id,
            "workflow_ids": [str(item) for item in inspection.workflow_ids],
            "verified_source_paths": inspection.verified_source_paths,
        }
        await repository.transition(
            deployment_id,
            solution.organization_id,
            expected_state="ready",
            new_state="activating",
            validation_result=marker,
        )
        if not await repository.compare_and_set_active_deployment(
            solution_id,
            solution.organization_id,
            expected_active_deployment_id=None,
            new_active_deployment_id=deployment_id,
        ):
            raise WorkspaceLiveHandoffPreflightConflict(
                "Solution active deployment changed during handoff"
            )
        moved = set(
            (
                await self.db.scalars(
                    update(Workflow)
                    .where(
                        Workflow.id.in_(request.workflow_ids),
                        Workflow.solution_id.is_(None),
                        Workflow.is_active.is_(True),
                    )
                    .values(solution_id=solution_id)
                    .returning(Workflow.id)
                )
            ).all()
        )
        if moved != set(request.workflow_ids):
            raise WorkspaceLiveHandoffPreflightConflict(
                "workflow ownership changed during handoff"
            )
        await repository.transition(
            deployment_id,
            solution.organization_id,
            expected_state="activating",
            new_state="active",
            activated_at=datetime.now(UTC),
        )
        return WorkspaceLiveHandoffCommitResponse(
            solution_id=solution_id,
            deployment_id=deployment_id,
            release_row_id=inspection.release_row_id,
            release_id=inspection.release_id,
            workflow_ids=inspection.workflow_ids,
            evidence_id=inspection.evidence_id,
            state="active",
        )

    async def rollback(
        self,
        solution_id: UUID,
        deployment_id: UUID,
        request: WorkspaceLiveHandoffCommitRequest,
    ) -> WorkspaceLiveHandoffCommitResponse:
        await acquire_workspace_release_lock(self.db, None)
        solution = await self.db.scalar(
            select(Solution).where(Solution.id == solution_id).with_for_update()
        )
        if solution is None or solution.status != "active":
            raise WorkspaceLiveHandoffPreflightError("Solution is not active")
        if (
            solution.active_deployment_id != deployment_id
            or request.expected_active_deployment_id != deployment_id
            or solution.execution_runtime_mode != "deployment-v1"
        ):
            raise WorkspaceLiveHandoffPreflightConflict(
                "Solution pointer changed before handoff rollback"
            )
        release = _require_live_identity(
            await active_workspace_release(self.db, None), request
        )
        repository = SolutionDeploymentRepository(self.db)
        deployment = await repository.get_runtime_closure(
            deployment_id, solution.organization_id, solution_id
        )
        if deployment is None or deployment.state != "active":
            raise WorkspaceLiveHandoffPreflightConflict(
                "handoff deployment is no longer active"
            )
        marker = deployment.validation_result or {}
        if (
            marker.get("schema_version") != _HANDOFF_MARKER_SCHEMA
            or marker.get("preflight_evidence_id") != request.expected_evidence_id
            or marker.get("release_row_id") != str(release.release_row_id)
            or marker.get("release_id") != release.release_id
            or set(marker.get("workflow_ids", []))
            != {str(item) for item in request.workflow_ids}
        ):
            raise WorkspaceLiveHandoffPreflightConflict(
                "handoff activation evidence changed before rollback"
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
                "handoff deployment closure is invalid"
            ) from exc
        if manifest.shared_tables != request.shared_tables:
            raise WorkspaceLiveHandoffPreflightConflict("handoff resource bindings changed")
        try:
            await require_shared_tables(self.db, manifest.shared_tables)
        except SharedTableBindingError as exc:
            raise WorkspaceLiveHandoffPreflightError(str(exc)) from exc
        selected = (
            (
                await self.db.execute(
                    select(Workflow)
                    .options(selectinload(Workflow.roles))
                    .where(Workflow.id.in_(request.workflow_ids))
                    .with_for_update(of=Workflow)
                )
            )
            .scalars()
            .all()
        )
        if {workflow.id for workflow in selected} != set(request.workflow_ids):
            raise WorkspaceLiveHandoffPreflightConflict(
                "handoff workflow registrations changed before rollback"
            )
        installed_ids = set(
            (
                await self.db.scalars(
                    select(Workflow.id).where(Workflow.solution_id == solution_id)
                )
            ).all()
        )
        if installed_ids != set(request.workflow_ids):
            raise WorkspaceLiveHandoffPreflightConflict(
                "Solution gained or lost workflows after handoff activation"
            )
        await _require_empty_solution_install(
            self.db, solution_id, allow_workflows=True
        )
        for workflow in selected:
            _require_workflow_binding(
                workflow,
                solution,
                release,
                resolution,
                expected_owner_id=solution_id,
            )
        if {
            item.resolved_id for item in resolution.workflows.values()
        } != installed_ids:
            raise WorkspaceLiveHandoffPreflightError(
                "handoff deployment workflow set changed"
            )
        try:
            live_bytes = await WorkspaceReleaseFileView.from_release(release).read_many(
                list(release.governed_paths)
            )
            source_bytes = source_closure(
                live_bytes,
                {workflow.path.replace("\\", "/").lstrip("/") for workflow in selected},
                has_table_bindings=bool(manifest.shared_tables),
            )
        except (
            FileNotFoundError,
            WorkspaceReleaseRuntimeError,
            LiveHandoffSourceError,
        ) as exc:
            raise WorkspaceLiveHandoffPreflightError(
                "Live source cannot be verified for rollback"
            ) from exc
        if set(resolution.sources) != set(source_bytes) or marker.get(
            "verified_source_paths"
        ) != sorted(source_bytes):
            raise WorkspaceLiveHandoffPreflightError(
                "handoff source closure changed before rollback"
            )
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        if (
            deployment.source_artifact_key != storage.source_artifact_key
            or deployment.runtime_storage_prefix != storage.runtime_prefix
            or manifest.source.artifact_key != storage.source_artifact_key
            or manifest.source.runtime_prefix != storage.runtime_prefix
            or await storage.read_compiled_manifest() != manifest.canonical_bytes()
        ):
            raise WorkspaceLiveHandoffPreflightError(
                "handoff deployment manifest differs from reviewed bytes"
            )
        read_slots = asyncio.Semaphore(16)

        async def verify_runtime(path: str) -> None:
            source = resolution.sources[path]
            if (
                source.object_key != f"{storage.runtime_prefix}{path}"
                or source.content_hash != sha256_digest(source_bytes[path])
            ):
                raise WorkspaceLiveHandoffPreflightError(
                    f"handoff runtime differs from Live: {path}"
                )
            async with read_slots:
                runtime_bytes = await storage.read_runtime_file(path)
            if runtime_bytes != source_bytes[path]:
                raise WorkspaceLiveHandoffPreflightError(
                    f"handoff runtime differs from Live: {path}"
                )

        await asyncio.gather(*(verify_runtime(path) for path in sorted(source_bytes)))
        _verify_source_archive(await storage.read_source_artifact(), source_bytes)
        _require_live_identity(await active_workspace_release(self.db, None), request)
        if not await repository.compare_and_set_active_deployment(
            solution_id,
            solution.organization_id,
            expected_active_deployment_id=deployment_id,
            new_active_deployment_id=None,
        ):
            raise WorkspaceLiveHandoffPreflightConflict(
                "Solution pointer changed during handoff rollback"
            )
        restored = set(
            (
                await self.db.scalars(
                    update(Workflow)
                    .where(
                        Workflow.id.in_(request.workflow_ids),
                        Workflow.solution_id == solution_id,
                        Workflow.is_active.is_(True),
                    )
                    .values(solution_id=None)
                    .returning(Workflow.id)
                )
            ).all()
        )
        if restored != set(request.workflow_ids):
            raise WorkspaceLiveHandoffPreflightConflict(
                "workflow ownership changed during handoff rollback"
            )
        await repository.transition(
            deployment_id,
            solution.organization_id,
            expected_state="active",
            new_state="superseded",
            superseded_at=datetime.now(UTC),
        )
        return WorkspaceLiveHandoffCommitResponse(
            solution_id=solution_id,
            deployment_id=deployment_id,
            release_row_id=release.release_row_id,
            release_id=release.release_id,
            workflow_ids=sorted(request.workflow_ids, key=str),
            evidence_id=request.expected_evidence_id,
            state="rolled_back_to_live",
        )
