"""Guarded first immutable install from an operator-reviewed workflow recipe."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from bifrost.workspace_release import canonical_digest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solution_deployments import (
    InitialWorkflowInstallInspectRequest,
    InitialWorkflowInstallInspectResponse,
    InitialWorkflowInstallRequest,
    SolutionDeploymentCreate,
)
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.users import Role
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.deployment_api import SolutionDeploymentAPIService
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentGitProvenance,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeEntityDefinition,
    RuntimeResourceResolution,
    RuntimeSourceResolution,
    canonical_json,
    sha256_digest,
    validate_runtime_closure,
)
from src.services.solutions.deployment_storage import (
    SolutionDeploymentStorage,
    deployment_runtime_prefix,
    deployment_source_artifact_key,
)
from src.services.solutions.live_handoff_preflight import (
    WorkspaceLiveHandoffPreflightConflict,
    _require_empty_solution_install,
)
from src.services.solutions.live_handoff_source import (
    LiveHandoffSourceError,
    source_archive,
    source_closure,
)
from src.services.solutions.resource_delivery import (
    read_deployment_resources,
    validate_resource_files,
)
from src.services.solutions.shared_table_bindings import (
    SharedTableBindingError,
    require_shared_tables,
)
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict,
    SolutionSourceRevisionError,
    _archive_files,
)
from src.services.solutions.workflow_revision import project_workflow_registrations
from src.services.solutions.workflow_revision_recipe import (
    ReviewedWorkflowRecipe,
    WorkflowRecipeError,
    compile_workflow_registrations,
)


class InitialWorkflowInstallService:
    """Stage and activate a first workflow-only deployment for a new install."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = SolutionDeploymentRepository(db)

    async def stage(
        self, solution_id: UUID, deployment_id: UUID, created_by: UUID,
        body: InitialWorkflowInstallRequest, files: dict[str, bytes],
        resources: dict[str, bytes],
    ) -> InitialWorkflowInstallInspectResponse:
        solution = await self._require_empty_solution(solution_id, lock=True)
        entities = await self._compile(solution, body.reviewed_recipe, files, resources)
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        sources = {
            path: RuntimeSourceResolution(
                object_key=f"{storage.runtime_prefix}{path}", content_hash=sha256_digest(content)
            ) for path, content in files.items()
        }
        resource_map = {
            path: RuntimeResourceResolution(
                object_key=f"{storage.runtime_prefix}_resources/{path}",
                content_hash=sha256_digest(content), size_bytes=len(content),
            ) for path, content in resources.items()
        }
        resolution = DeploymentResolutionMap(
            workflows=entities, sources=sources,
            shared_tables=body.reviewed_recipe.shared_tables, root_file_bindings=body.reviewed_recipe.root_file_bindings, resources=resource_map,
        )
        hashes = {path: item.content_hash for path, item in {**sources, **resource_map}.items()}
        manifest = CompiledDeploymentManifest(
            solution_id=solution_id, deployment_id=deployment_id,
            bundle_hash=sha256_digest(canonical_json({
                "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
                "reviewed_recipe": body.reviewed_recipe.model_dump(mode="json"),
                "source_commit_sha": body.source_commit_sha, "source_hashes": hashes,
            })),
            resolution_map_hash=sha256_digest(canonical_json(resolution)),
            source=DeploymentSource(
                artifact_key=storage.source_artifact_key,
                runtime_prefix=storage.runtime_prefix,
            ), workflows=entities, shared_tables=resolution.shared_tables, root_file_bindings=resolution.root_file_bindings,
            resources=resource_map,
            git=DeploymentGitProvenance(commit_sha=body.source_commit_sha),
        )
        await storage.write_source_artifact(source_archive(files), idempotent=True)
        slots = asyncio.Semaphore(16)

        async def upload(path: str, content: bytes) -> None:
            async with slots:
                await storage.write_runtime_file(path, content, idempotent=True)

        await asyncio.gather(*(upload(path, content) for path, content in files.items()))
        if resources:
            await storage.write_resources_artifact(source_archive(resources), idempotent=True)
            await asyncio.gather(*(
                upload("_resources/" + path, content) for path, content in resources.items()
            ))
        await SolutionDeploymentAPIService(self.db).create_ready_draft(
            solution_id, created_by,
            SolutionDeploymentCreate(
                compiled_manifest=manifest, resolution_map=resolution,
                git_commit_sha=body.source_commit_sha,
            ),
        )
        return await self.inspect(
            solution_id, deployment_id,
            InitialWorkflowInstallInspectRequest(reviewed_recipe=body.reviewed_recipe),
        )

    async def inspect(
        self, solution_id: UUID, deployment_id: UUID,
        request: InitialWorkflowInstallInspectRequest, *, lock: bool = False,
    ) -> InitialWorkflowInstallInspectResponse:
        solution = await self._require_empty_solution(solution_id, lock=lock, candidate_id=deployment_id)
        deployment = await self.repository.get_runtime_closure(
            deployment_id, solution.organization_id, solution_id,
        )
        if deployment is None or deployment.state != "ready":
            raise SolutionSourceRevisionError("initial workflow candidate is not ready")
        if deployment.base_deployment_id is not None or deployment.parent_deployment_id is not None:
            raise SolutionSourceRevisionError("initial workflow candidate must have no base or parent")
        try:
            manifest, resolution = validate_runtime_closure(
                deployment.compiled_manifest, deployment.resolution_map,
                deployment.dependencies,
                expected_manifest_hash=deployment.compiled_manifest_hash,
                expected_resolution_hash=deployment.resolution_map_hash,
            )
        except ValueError as exc:
            raise SolutionSourceRevisionError("initial workflow candidate closure is invalid") from exc
        recipe = request.reviewed_recipe
        if recipe.solution_id != solution_id:
            raise SolutionSourceRevisionError("workflow recipe belongs to another install")
        if (manifest.solution_id != solution_id or manifest.deployment_id != deployment_id
                or manifest.source.artifact_key != deployment_source_artifact_key(solution_id, deployment_id)
                or manifest.source.runtime_prefix != deployment_runtime_prefix(solution_id, deployment_id)
                or deployment.source_artifact_key != manifest.source.artifact_key
                or deployment.runtime_storage_prefix != manifest.source.runtime_prefix
                or manifest.agents or manifest.forms or manifest.events or manifest.applications
                or manifest.tables or manifest.file_locations or manifest.config_requirements
                or manifest.connections or manifest.dependencies):
            raise SolutionSourceRevisionError("candidate is not a workflow-only initial deployment")
        if manifest.git.commit_sha is None or deployment.git_commit_sha != manifest.git.commit_sha:
            raise SolutionSourceRevisionError("initial candidate has no reviewed source commit")
        files = _archive_files(
            await SolutionDeploymentStorage(solution_id, deployment_id).read_source_artifact(),
            set(resolution.sources),
        )
        resources = await read_deployment_resources(solution_id, deployment_id, resolution)
        validate_resource_files(recipe, resources, files)
        try:
            closure = source_closure(
                files, {item.path for item in recipe.workflows},
                has_table_bindings=bool(recipe.shared_tables),
                has_root_file_bindings=bool(recipe.root_file_bindings),
                has_resource_bindings=bool(recipe.resources),
            )
            desired = compile_workflow_registrations(recipe, files, WorkflowIndexer(self.db))
        except (WorkflowRecipeError, LiveHandoffSourceError) as exc:
            raise SolutionSourceRevisionError(str(exc)) from exc
        if set(closure) != set(files):
            raise SolutionSourceRevisionError("recipe differs from complete source dependency closure")
        if (desired != resolution.workflows or recipe.shared_tables != resolution.shared_tables
                or recipe.root_file_bindings != resolution.root_file_bindings
                or manifest.workflows != resolution.workflows
                or manifest.shared_tables != resolution.shared_tables):
            raise SolutionSourceRevisionConflict("candidate differs from reviewed recipe")
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        if await storage.read_compiled_manifest() != manifest.canonical_bytes():
            raise SolutionSourceRevisionError("stored initial manifest differs from candidate")
        for path, content in files.items():
            source = resolution.sources[path]
            if (source.object_key != f"{storage.runtime_prefix}{path}"
                    or sha256_digest(content) != source.content_hash
                    or await storage.read_runtime_file(path) != content):
                raise SolutionSourceRevisionError("source archive or runtime bytes differ from reviewed evidence")
        for path, resource in resolution.resources.items():
            if resource.object_key != f"{storage.runtime_prefix}_resources/{path}":
                raise SolutionSourceRevisionError("resource storage reference is not canonical")
        if manifest.bundle_hash != sha256_digest(canonical_json({
            "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
            "reviewed_recipe": recipe.model_dump(mode="json"),
            "source_commit_sha": manifest.git.commit_sha,
            "source_hashes": {path: item.content_hash for path, item in {
                **resolution.sources, **resolution.resources,
            }.items()},
        })):
            raise SolutionSourceRevisionError("candidate bundle hash differs from reviewed recipe")
        await require_shared_tables(
            self.db, recipe.shared_tables, solution_organization_id=solution.organization_id,
        )
        from src.services.solutions.root_file_bindings import (
            RootFileBindingError, require_root_workspace_files,
        )
        try:
            await require_root_workspace_files(self.db, manifest.root_file_bindings)
        except RootFileBindingError as exc:
            raise SolutionSourceRevisionError(str(exc)) from exc
        candidate_ids = {item.resolved_id for item in desired.values()}
        if not candidate_ids:
            raise SolutionSourceRevisionError("initial recipe must register at least one workflow")
        occupied = (await self.db.scalars(select(Workflow).where(Workflow.id.in_(candidate_ids)))).all()
        if occupied:
            raise SolutionSourceRevisionConflict("a reviewed workflow UUID is already registered")
        for item in recipe.workflows:
            if (item.organization_id != solution.organization_id
                    or item.controls.endpoint_enabled or item.controls.public_endpoint
                    or item.controls.access_level != "role_based"):
                raise SolutionSourceRevisionError(
                    "initial workflows require install scope, role-based access, and disabled endpoints"
                )
            role_ids = set(item.controls.role_ids)
            roles = (await self.db.scalars(select(Role).where(Role.id.in_(role_ids)))).all() if role_ids else []
            if {role.id for role in roles} != role_ids:
                raise SolutionSourceRevisionError("a reviewed workflow role is missing")
        hashes = {path: item.content_hash for path, item in {**resolution.sources, **resolution.resources}.items()}
        evidence = {
            "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
            "solution_id": str(solution_id), "deployment_id": str(deployment_id),
            "organization_id": str(solution.organization_id) if solution.organization_id else None,
            "pointer": None, "runtime_mode": solution.execution_runtime_mode,
            "manifest_hash": deployment.compiled_manifest_hash,
            "resolution_hash": deployment.resolution_map_hash,
            "reviewed_recipe": recipe.model_dump(mode="json"),
            "workflow_ids": sorted(str(value) for value in candidate_ids),
            "source_hashes": hashes,
        }
        return InitialWorkflowInstallInspectResponse(
            solution_id=solution_id, deployment_id=deployment_id,
            organization_id=solution.organization_id,
            source_commit_sha=manifest.git.commit_sha,
            workflow_ids=sorted(candidate_ids, key=str), source_hashes=hashes,
            evidence_id=canonical_digest(evidence), state="ready",
        )

    async def activate(
        self, solution_id: UUID, deployment_id: UUID,
        request: InitialWorkflowInstallInspectRequest,
        expected_evidence_id: str,
    ) -> InitialWorkflowInstallInspectResponse:
        inspected = await self.inspect(solution_id, deployment_id, request, lock=True)
        if inspected.evidence_id != expected_evidence_id:
            raise SolutionSourceRevisionConflict("initial workflow preflight evidence changed")
        solution = await self._require_empty_solution(solution_id, lock=True, candidate_id=deployment_id)
        candidate = await self.repository.get_runtime_closure(
            deployment_id, solution.organization_id, solution_id,
        )
        if candidate is None:
            raise SolutionSourceRevisionConflict("initial workflow candidate disappeared")
        resolution = DeploymentResolutionMap.model_validate(candidate.resolution_map)
        await project_workflow_registrations(self.db, solution_id, resolution.workflows, set())
        marker = {
            "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
            "preflight_evidence_id": inspected.evidence_id,
            "workflow_ids": [str(value) for value in inspected.workflow_ids],
            "source_hashes": inspected.source_hashes,
        }
        await self.repository.transition(
            deployment_id, solution.organization_id,
            expected_state="ready", new_state="activating", validation_result=marker,
        )
        if not await self.repository.compare_and_set_active_deployment(
            solution_id, solution.organization_id,
            expected_active_deployment_id=None, new_active_deployment_id=deployment_id,
        ):
            raise SolutionSourceRevisionConflict("empty Solution pointer changed during activation")
        await self.repository.transition(
            deployment_id, solution.organization_id,
            expected_state="activating", new_state="active", activated_at=datetime.now(UTC),
        )
        return inspected.model_copy(update={"state": "active"})

    async def _require_empty_solution(
        self, solution_id: UUID, *, lock: bool, candidate_id: UUID | None = None,
    ) -> Solution:
        query = select(Solution).where(Solution.id == solution_id)
        if lock:
            query = query.with_for_update()
        solution = await self.db.scalar(query)
        if solution is None or solution.status != "active":
            raise SolutionSourceRevisionError("Solution is not active")
        if (not solution.setup_complete or solution.allow_outbound_access or solution.git_connected
                or solution.active_deployment_id is not None or solution.execution_runtime_mode != "repo-v1"):
            raise SolutionSourceRevisionConflict(
                "initial install requires a configured disconnected repo-v1 Solution with a null pointer"
            )
        try:
            await _require_empty_solution_install(self.db, solution_id)
        except WorkspaceLiveHandoffPreflightConflict as exc:
            raise SolutionSourceRevisionConflict(str(exc)) from exc
        # Default allow_workflows=False includes every registration, active or inactive.
        deployment_query = select(SolutionDeployment.id).where(
            SolutionDeployment.solution_id == solution_id,
        )
        if candidate_id is not None:
            deployment_query = deployment_query.where(SolutionDeployment.id != candidate_id)
        if (await self.db.scalar(deployment_query.limit(1))) is not None:
            raise SolutionSourceRevisionConflict("Solution already has another deployment history")
        return solution

    async def _compile(
        self, solution: Solution, recipe: ReviewedWorkflowRecipe,
        files: dict[str, bytes], resources: dict[str, bytes],
    ) -> dict[str, RuntimeEntityDefinition]:
        if recipe.solution_id != solution.id:
            raise SolutionSourceRevisionError("workflow recipe belongs to another install")
        validate_resource_files(recipe, resources, files)
        try:
            closure = source_closure(
                files, {item.path for item in recipe.workflows},
                has_table_bindings=bool(recipe.shared_tables),
                has_root_file_bindings=bool(recipe.root_file_bindings),
                has_resource_bindings=bool(recipe.resources),
            )
            desired = compile_workflow_registrations(recipe, files, WorkflowIndexer(self.db))
        except (WorkflowRecipeError, LiveHandoffSourceError) as exc:
            raise SolutionSourceRevisionError(str(exc)) from exc
        if set(closure) != set(files):
            raise SolutionSourceRevisionError("recipe differs from complete source dependency closure")
        if not desired:
            raise SolutionSourceRevisionError("initial recipe must register at least one workflow")
        if any(item.definition.get("type") != "workflow" for item in desired.values()):
            raise SolutionSourceRevisionError("initial recipe accepts workflow registrations only")
        occupied = (await self.db.scalars(select(Workflow).where(
            Workflow.id.in_({item.resolved_id for item in desired.values()}),
        ))).all()
        if occupied:
            raise SolutionSourceRevisionConflict("a reviewed workflow UUID is already registered")
        for item in recipe.workflows:
            if (item.organization_id != solution.organization_id
                    or item.controls.endpoint_enabled or item.controls.public_endpoint
                    or item.controls.access_level != "role_based"):
                raise SolutionSourceRevisionError(
                    "initial workflows require install scope, role-based access, and disabled endpoints"
                )
            role_ids = set(item.controls.role_ids)
            roles = (await self.db.scalars(select(Role).where(Role.id.in_(role_ids)))).all() if role_ids else []
            if {role.id for role in roles} != role_ids:
                raise SolutionSourceRevisionError("a reviewed workflow role is missing")
        try:
            await require_shared_tables(
                self.db, recipe.shared_tables, solution_organization_id=solution.organization_id,
            )
        except SharedTableBindingError as exc:
            raise SolutionSourceRevisionError(str(exc)) from exc
        return desired
