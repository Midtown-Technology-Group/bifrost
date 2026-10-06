"""Guarded first immutable install from an operator-reviewed workflow recipe."""

from __future__ import annotations

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
    DeploymentResolutionMap,
    RuntimeEntityDefinition,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.live_handoff_preflight import (
    WorkspaceLiveHandoffPreflightConflict,
    _require_empty_solution_install,
)
from src.services.solutions.reviewed_workflow_artifact import (
    build_reviewed_artifact,
    compile_reviewed_workflows,
    inspect_reviewed_artifact,
    write_reviewed_artifact,
)
from src.services.solutions.shared_table_bindings import (
    SharedTableBindingError,
    require_shared_tables,
)
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict,
    SolutionSourceRevisionError,
)
from src.services.solutions.workflow_revision import project_workflow_registrations
from src.services.solutions.workflow_revision_recipe import (
    ReviewedWorkflowRecipe,
)


_INITIAL_MARKER = "bifrost.initial-reviewed-workflow-install/v1"


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
        manifest, resolution = build_reviewed_artifact(
            solution_id, deployment_id, body.reviewed_recipe, files, resources,
            entities, body.source_commit_sha, _INITIAL_MARKER,
        )
        await write_reviewed_artifact(storage, files, resources)
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
        recipe = request.reviewed_recipe
        manifest, resolution = await inspect_reviewed_artifact(
            solution_id, deployment_id, deployment, recipe, WorkflowIndexer(self.db),
            _INITIAL_MARKER,
        )
        desired = resolution.workflows
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
            "schema_version": _INITIAL_MARKER,
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
            "schema_version": _INITIAL_MARKER,
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
        desired = compile_reviewed_workflows(recipe, files, resources, WorkflowIndexer(self.db))
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
