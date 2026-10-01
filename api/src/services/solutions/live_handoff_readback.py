"""Prove a retained Live binding was handed to an immutable Solution runtime.

This readback never moves ownership or edits either artifact. Its cache lives
for one preview/activation only; activation holds the native Solution row lock.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bifrost.solution_delivery_review import WORKFLOW_REVISION_MARKER
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import (
    canonical_json, sha256_digest, validate_runtime_closure,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.live_handoff_preflight import handoff_preflight_evidence_id
from src.services.solutions.root_file_bindings import require_root_workspace_files
from src.services.solutions.shared_table_bindings import require_shared_tables
from src.services.solutions.source_revision import (
    _archive_files, _require_registration, _workflow_snapshot,
)
from src.services.workspace_release_runtime import WorkspaceReleaseDescriptor

HANDOFF_MARKER = "bifrost.workspace-live-handoff/v1"
SOURCE_MARKER = "bifrost.solution-source-revision/v1"


class UnprovenLiveHandoff(ValueError):
    """A missing loose registration has no verified replacement runtime."""


class LiveHandoffReadback:
    def __init__(self, db: AsyncSession, release: WorkspaceReleaseDescriptor, *, lock: bool = False):
        self.db = db
        self.release = release
        self.lock = lock
        self.repository = SolutionDeploymentRepository(db)
        self.solutions: dict[UUID, tuple[Any, Any, Any, dict[UUID, Workflow]]] = {}
        self.deployments: dict[UUID, tuple[Any, Any, Any]] = {}

    async def require(self, inherited: dict[str, Any]) -> None:
        """Only the exact UUID with certified lineage may leave the loose cohort."""
        workflow_id = UUID(inherited["workflow_id"])
        workflow = await self.db.scalar(
            select(Workflow).options(selectinload(Workflow.roles))
            .where(Workflow.id == workflow_id).execution_options(populate_existing=True)
        )
        scope = UUID(inherited["organization_id"]) if inherited.get("organization_id") else None
        if (workflow is None or workflow.solution_id is None or not workflow.is_active
                or workflow.organization_id != scope
                or workflow.path.replace("\\", "/").lstrip("/") != inherited["path"]
                or workflow.function_name != inherited["function"]):
            raise UnprovenLiveHandoff("Inherited workflow has no exact Solution owner")
        solution_id = workflow.solution_id
        if solution_id not in self.solutions:
            self.solutions[solution_id] = await self._solution(solution_id, scope)
        active_manifest, active_resolution, origin_resolution, rows = self.solutions[solution_id]
        if workflow_id not in rows:
            raise UnprovenLiveHandoff("Inherited workflow is absent from the active runtime")
        origin = origin_resolution.resolve_workflow_id(workflow_id)
        if (origin.source_ref != inherited["path"]
                or origin.source_hash != f"sha256:{inherited['source_sha256']}"
                or any(origin.definition.get(field) != expected for field, expected in {
                    "path": inherited["path"], "function_name": inherited["function"],
                    "name": inherited["name"], "type": inherited["type"],
                    "organization_id": inherited.get("organization_id"),
                    "runtime_bounds": inherited["runtime_bounds"],
                }.items())):
            raise UnprovenLiveHandoff("Handoff origin differs from the inherited Live binding")
        # Initial handoffs predate complete control definitions. Retain the Live
        # exposure baseline until a reviewed workflow revision defines controls.
        current = rows[workflow_id]
        definition = active_resolution.resolve_workflow_id(workflow_id).definition
        snapshot = _workflow_snapshot(current)
        for field in ("endpoint_enabled", "public_endpoint", "api_key_enabled", "access_level", "role_ids"):
            expected = definition[field] if field in definition else inherited.get(field)
            if field not in definition and field not in inherited:
                raise UnprovenLiveHandoff("Handoff has no immutable exposure baseline")
            if snapshot[field] != expected:
                raise UnprovenLiveHandoff("Solution registration exposure differs from its reviewed baseline")
        # Cache never substitutes for a fresh ownership/scope observation.
        if current.solution_id != solution_id or current.organization_id != scope:
            raise UnprovenLiveHandoff("Solution workflow owner changed during readback")

    async def _deployment(self, deployment_id: UUID, solution_id: UUID, scope: UUID | None):
        if deployment_id in self.deployments:
            deployment, manifest, resolution = self.deployments[deployment_id]
            if deployment.solution_id != solution_id or deployment.organization_id != scope:
                raise UnprovenLiveHandoff("Deployment is outside the owner scope")
            return deployment, manifest, resolution
        deployment = await self.db.scalar(
            select(SolutionDeployment).options(selectinload(SolutionDeployment.dependencies))
            .where(SolutionDeployment.id == deployment_id,
                   SolutionDeployment.solution_id == solution_id,
                   SolutionDeployment.organization_id == scope)
            .execution_options(populate_existing=True)
        )
        if (deployment is None or deployment.state not in {"active", "superseded"}
                or deployment.activated_at is None):
            raise UnprovenLiveHandoff("Deployment is not an activated immutable revision")
        manifest, resolution = validate_runtime_closure(
            deployment.compiled_manifest, deployment.resolution_map, deployment.dependencies,
            expected_manifest_hash=deployment.compiled_manifest_hash,
            expected_resolution_hash=deployment.resolution_map_hash,
        )
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        if (manifest.solution_id != solution_id or manifest.deployment_id != deployment_id
                or manifest.bundle_hash != deployment.bundle_hash
                or manifest.source.artifact_key != storage.source_artifact_key
                or manifest.source.runtime_prefix != storage.runtime_prefix
                or deployment.source_artifact_key != storage.source_artifact_key
                or deployment.runtime_storage_prefix != storage.runtime_prefix):
            raise UnprovenLiveHandoff("Deployment storage identity differs from its immutable closure")
        self.deployments[deployment_id] = deployment, manifest, resolution
        return deployment, manifest, resolution

    async def _runtime_bytes(self, deployment, manifest, resolution) -> None:
        storage = SolutionDeploymentStorage(deployment.solution_id, deployment.id)
        files = _archive_files(await storage.read_source_artifact(), set(resolution.sources))
        slots = asyncio.Semaphore(16)

        async def verify(path, source):
            if (source.object_key != f"{storage.runtime_prefix}{path}"
                    or sha256_digest(files[path]) != source.content_hash):
                raise UnprovenLiveHandoff("Immutable source archive or object identity changed")
            async with slots:
                if await storage.read_runtime_file(path) != files[path]:
                    raise UnprovenLiveHandoff("Active runtime source bytes differ from the immutable archive")

        await asyncio.gather(*(verify(path, source) for path, source in resolution.sources.items()))
        async def verify_resource(path, resource):
            async with slots:
                content = await storage.read_runtime_file(f"_resources/{path}")
            if len(content) != resource.size_bytes or sha256_digest(content) != resource.content_hash:
                raise UnprovenLiveHandoff("Active runtime resource bytes differ from the immutable closure")
        await asyncio.gather(*(verify_resource(path, resource) for path, resource in resolution.resources.items()))
        await require_shared_tables(self.db, manifest.shared_tables)
        await require_root_workspace_files(self.db, manifest.root_file_bindings)

    async def _dependencies(self, deployment, visiting: set[UUID], verified: set[UUID]) -> None:
        if deployment.id in visiting:
            raise UnprovenLiveHandoff("Immutable dependency cycle")
        if deployment.id in verified:
            return
        visiting.add(deployment.id)
        await self.repository.validate_dependencies(deployment)
        for edge in deployment.dependencies:
            dependency_scope = await self.db.scalar(
                select(SolutionDeployment.organization_id).where(SolutionDeployment.id == edge.dependency_deployment_id)
            )
            dependency, manifest, resolution = await self._deployment(
                edge.dependency_deployment_id, edge.dependency_solution_id, dependency_scope
            )
            await self._runtime_bytes(dependency, manifest, resolution)
            await self._dependencies(dependency, visiting, verified)
        visiting.remove(deployment.id)
        verified.add(deployment.id)

    async def _solution(self, solution_id: UUID, scope: UUID | None):
        query = select(Solution).where(Solution.id == solution_id, Solution.organization_id == scope)
        if self.lock:
            query = query.with_for_update()
        solution = await self.db.scalar(query.execution_options(populate_existing=True))
        if (solution is None or solution.status != "active" or solution.execution_runtime_mode != "deployment-v1"
                or solution.active_deployment_id is None):
            raise UnprovenLiveHandoff("Solution has no active immutable runtime")
        active, active_manifest, active_resolution = await self._deployment(solution.active_deployment_id, solution_id, scope)
        if active.state != "active":
            raise UnprovenLiveHandoff("Solution pointer is not active")
        rows = (await self.db.scalars(
            select(Workflow).options(selectinload(Workflow.roles))
            .where(Workflow.solution_id == solution_id, Workflow.is_active.is_(True))
            .execution_options(populate_existing=True)
        )).all()
        if {row.id for row in rows} != {item.resolved_id for item in active_resolution.workflows.values()}:
            raise UnprovenLiveHandoff("Solution registration set differs from its active closure")
        for row in rows:
            if row.organization_id != scope:
                raise UnprovenLiveHandoff("Solution registration is outside its owner scope")
            _require_registration(row, active_resolution.resolve_workflow_id(row.id))
        await self._runtime_bytes(active, active_manifest, active_resolution)
        await self._dependencies(active, set(), set())
        visited: set[UUID] = set()
        origin, manifest, resolution = active, active_manifest, active_resolution
        while True:
            if origin.id in visited:
                raise UnprovenLiveHandoff("Reviewed handoff lineage cycle")
            visited.add(origin.id)
            marker = origin.validation_result or {}
            schema = marker.get("schema_version")
            if schema == HANDOFF_MARKER:
                break
            if (schema not in {SOURCE_MARKER, WORKFLOW_REVISION_MARKER}
                    or not str(marker.get("preflight_evidence_id", "")).startswith("sha256:")
                    or marker.get("workflow_ids") != sorted(str(item.resolved_id) for item in resolution.workflows.values())
                    or marker.get("source_hashes") != {
                        path: source.content_hash for path, source in {**resolution.sources, **resolution.resources}.items()
                    }
                    or origin.parent_deployment_id is None
                    or origin.parent_deployment_id != origin.base_deployment_id):
                raise UnprovenLiveHandoff("Solution revision has no reviewed handoff lineage")
            origin, manifest, resolution = await self._deployment(origin.parent_deployment_id, solution_id, scope)
        source_paths = sorted(resolution.sources)
        workflow_ids = sorted((item.resolved_id for item in resolution.workflows.values()), key=str)
        expected_bundle = sha256_digest(canonical_json({
            "release_id": self.release.release_id,
            "workflow_ids": [str(item) for item in workflow_ids],
            "shared_tables": {name: binding.model_dump(mode="json") for name, binding in manifest.shared_tables.items()},
            "source_hashes": {path: source.content_hash for path, source in resolution.sources.items()},
        }))
        if (origin.parent_deployment_id is not None or origin.base_deployment_id is not None
                or resolution.dependencies or not source_paths
                or not set(source_paths).issubset(self.release.governed_paths)
                or any(source.content_hash != f"sha256:{self.release.source_hashes[path]}"
                       for path, source in resolution.sources.items())
                or manifest.bundle_hash != expected_bundle
                or manifest.git.commit_sha != self.release.source_commit_sha
                or marker.get("release_row_id") != str(self.release.release_row_id)
                or marker.get("release_id") != self.release.release_id
                or marker.get("workflow_ids") != [str(item) for item in workflow_ids]
                or marker.get("verified_source_paths") != source_paths
                or marker.get("preflight_evidence_id") != handoff_preflight_evidence_id(
                    self.release, manifest, resolution, workflow_ids=workflow_ids)):
            raise UnprovenLiveHandoff("Retained handoff receipt does not prove the current Live binding")
        if origin.id != active.id:
            await self._runtime_bytes(origin, manifest, resolution)
        return active_manifest, active_resolution, resolution, {row.id: row for row in rows}
