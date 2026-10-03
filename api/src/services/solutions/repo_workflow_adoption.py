"""Adopt a populated legacy workflow install without projecting its entities."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from bifrost.solution_delivery_review import (
    WorkflowRecipeError, compile_workflow_parameters, require_adoption_parameters,
)
from bifrost.workspace_release import canonical_digest
from sqlalchemy import exists, inspect as orm_inspect, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.contracts.solution_deployments import (
    InitialWorkflowInstallInspectRequest,
    InitialWorkflowInstallRequest,
    RepoWorkflowAdoptionInspectResponse,
    SolutionDeploymentCreate,
)
from src.models.enums import ExecutionStatus
from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.events import EventSource, EventSubscription, ScheduleSource, WebhookSource
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.executions import Execution
from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.models.orm.forms import Form
from src.models.orm.pending_capture import PendingCaptureORM
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.services import ServiceDefinition
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.deployment_api import SolutionDeploymentAPIService
from src.services.solutions.deployment_manifest import sha256_digest
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.live_handoff_source import MAX_ARCHIVE_BYTES
from src.services.solutions.reviewed_workflow_artifact import (
    build_reviewed_artifact, compile_reviewed_workflows,
    inspect_reviewed_artifact, write_reviewed_artifact,
)
from src.services.solutions.root_file_bindings import require_root_workspace_files
from src.services.solutions.shared_table_bindings import require_shared_tables
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict, SolutionSourceRevisionError, _require_registration, _workflow_snapshot,
)
from src.services.solutions.storage import SolutionStorage
from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe
from src.services.workspace_release_projection import acquire_workspace_release_lock

ADOPTION_MARKER = "bifrost.repo-workflow-adoption/v1"
_ACCEPTED = (ExecutionStatus.SCHEDULED, ExecutionStatus.PENDING, ExecutionStatus.RUNNING,
             ExecutionStatus.CANCELLING, ExecutionStatus.STUCK)
_UNSUPPORTED = (Agent, Application, Form, ServiceDefinition, PendingCaptureORM)
_PRESERVED = (CustomClaim, EventSource, EventSubscription, FileMetadata, FilePolicy,
              PolicyRule, SolutionConfigSchema, SolutionConnectionSchema,
              SolutionFileLocation, Table)
_MAX_ROWS = 1024


def _digest_row(row) -> str:
    """Hash all persisted columns without retaining credential or config values."""
    def value(item):
        if isinstance(item, bytes):
            return {"sha256": sha256_digest(item)}
        if isinstance(item, (UUID, Decimal)):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        return item
    return canonical_digest({attr.key: value(getattr(row, attr.key))
                             for attr in orm_inspect(type(row)).column_attrs})


class RepoWorkflowAdoptionService:
    """Change runtime selection only after immutable and mutable readback agree."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = SolutionDeploymentRepository(db)

    async def _rows(self, model, predicate, *, lock: bool):
        query = select(model).where(predicate).order_by(*orm_inspect(model).primary_key).limit(_MAX_ROWS + 1)
        if lock:
            query = query.with_for_update(of=model)
        rows = list((await self.db.scalars(query.execution_options(populate_existing=True))).all())
        if len(rows) > _MAX_ROWS:
            raise SolutionSourceRevisionError("legacy entity inventory exceeds its review bound")
        return rows

    async def _installed(self, solution_id: UUID, *, lock: bool):
        query = select(Solution).where(Solution.id == solution_id).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update(of=Solution)
        solution = await self.db.scalar(query)
        if solution is None or solution.status != "active":
            raise SolutionSourceRevisionError("Solution is not active")
        if (not solution.setup_complete or solution.allow_outbound_access or solution.git_connected
                or solution.active_deployment_id is not None or solution.execution_runtime_mode != "repo-v1"):
            raise SolutionSourceRevisionConflict("adoption requires a sealed disconnected repo-v1 install with a null pointer")
        for model in _UNSUPPORTED:
            if await self.db.scalar(select(model.id).where(model.solution_id == solution_id).limit(1)) is not None:
                raise SolutionSourceRevisionConflict(f"legacy {model.__name__} requires its reviewed runtime adapter")
        query = select(Workflow).where(Workflow.solution_id == solution_id).order_by(Workflow.id).options(
            selectinload(Workflow.roles)).execution_options(populate_existing=True).limit(_MAX_ROWS + 1)
        if lock:
            query = query.with_for_update(of=Workflow)
        workflows = list((await self.db.scalars(query)).all())
        if not workflows or len(workflows) > _MAX_ROWS:
            raise SolutionSourceRevisionError("legacy workflow inventory is empty or exceeds its review bound")
        if any(row.type not in {"workflow", "tool", "data_provider"} for row in workflows):
            raise SolutionSourceRevisionError("legacy registration type requires its reviewed runtime adapter")
        snapshots = {"Solution": [_digest_row(solution)], "Workflow": [_digest_row(row) for row in workflows]}
        snapshots["WorkflowRole"] = [_digest_row(row) for row in await self._rows(
            WorkflowRole, WorkflowRole.workflow_id.in_([row.id for row in workflows]), lock=lock)]
        owned_tables = False
        source_ids = []
        for model in _PRESERVED:
            rows = await self._rows(model, model.solution_id == solution_id, lock=lock)
            snapshots[model.__name__] = [_digest_row(row) for row in rows]
            if model is Table:
                owned_tables = bool(rows)
            if model is EventSource:
                source_ids = [row.id for row in rows]
        # Include Root-owned subscriptions targeting these UUIDs as well.
        subscriptions = await self._rows(EventSubscription,
            or_(EventSubscription.solution_id == solution_id,
                EventSubscription.workflow_id.in_([row.id for row in workflows])), lock=lock)
        snapshots["EventSubscription"] = [_digest_row(row) for row in subscriptions]
        source_ids = sorted(set(source_ids) | {row.event_source_id for row in subscriptions}, key=str)
        for model in (EventSource, ScheduleSource, WebhookSource):
            key = model.id if model is EventSource else model.event_source_id
            rows = await self._rows(model, key.in_(source_ids), lock=lock)
            snapshots[f"trigger:{model.__name__}"] = [_digest_row(row) for row in rows]
        return solution, workflows, canonical_digest(snapshots), owned_tables

    async def _legacy_hashes(self, solution_id: UUID, deployment_id: UUID, recipe: ReviewedWorkflowRecipe):
        storage = SolutionStorage(solution_id)
        deployment_ids = {str(value) for value in (await self.db.scalars(select(SolutionDeployment.id).where(
            SolutionDeployment.solution_id == solution_id))).all()} | {str(deployment_id)}
        paths = await storage.list()
        python_paths = {path for path in paths if path.endswith(".py") and path.split("/", 1)[0] not in deployment_ids}
        if len(python_paths) > 256 or not {item.path for item in recipe.workflows} <= python_paths:
            raise SolutionSourceRevisionError("legacy Python inventory is missing an entrypoint or exceeds its bound")
        # Existing sidecars are part of the mutable baseline; new reviewed resources
        # may be introduced without pretending they already exist in legacy storage.
        selected = python_paths | (set(recipe.resources) & set(paths))
        slots = asyncio.Semaphore(16)
        async def read(path):
            async with slots:
                return path, await storage.read(path)
        files = dict(await asyncio.gather(*(read(path) for path in sorted(selected))))
        if sum(len(raw) for raw in files.values()) > MAX_ARCHIVE_BYTES:
            raise SolutionSourceRevisionError("legacy source exceeds its byte bound")
        return files, {path: sha256_digest(raw) for path, raw in files.items()}

    async def _registrations(self, solution, rows, entities, legacy_files):
        active = {row.id: row for row in rows if row.is_active}
        if set(active) != {item.resolved_id for item in entities.values()}:
            raise SolutionSourceRevisionConflict("reviewed recipe must preserve exactly the active workflow identities")
        indexer = WorkflowIndexer(self.db)
        for entity in entities.values():
            row = active[entity.resolved_id]
            if row.organization_id != solution.organization_id:
                raise SolutionSourceRevisionError("legacy registration scope differs from its install")
            try:
                schema = compile_workflow_parameters(legacy_files[row.path], row.function_name,
                    path=row.path, indexer=indexer)
            except WorkflowRecipeError as exc:
                raise SolutionSourceRevisionError(str(exc)) from exc
            definition = dict(entity.definition)
            try:
                require_adoption_parameters(schema, definition["parameters_schema"],
                    attested_legacy_list=isinstance(row.parameters_schema, list))
            except WorkflowRecipeError as exc:
                raise SolutionSourceRevisionConflict(str(exc)) from exc
            # Legacy list schemas retain their original DB representation. The
            # current Python signature above supplies the complete runtime schema.
            _require_registration(row, entity)

    async def stage(self, solution_id: UUID, deployment_id: UUID, created_by: UUID,
                    body: InitialWorkflowInstallRequest, files: dict[str, bytes], resources: dict[str, bytes]):
        solution, rows, controls, has_owned_tables = await self._installed(solution_id, lock=True)
        legacy_files, hashes = await self._legacy_hashes(solution_id, deployment_id, body.reviewed_recipe)
        entities = compile_reviewed_workflows(body.reviewed_recipe, files, resources,
            WorkflowIndexer(self.db), has_owned_tables=has_owned_tables,
            legacy_parameter_hashes={row.id: canonical_digest(row.parameters_schema)
                                     for row in rows if row.is_active and isinstance(row.parameters_schema, list)},
            legacy_descriptor_snapshots={row.id: _workflow_snapshot(row) for row in rows if row.is_active})
        await self._registrations(solution, rows, entities, legacy_files)
        manifest, resolution = build_reviewed_artifact(solution_id, deployment_id, body.reviewed_recipe,
            files, resources, entities, body.source_commit_sha, ADOPTION_MARKER)
        await write_reviewed_artifact(SolutionDeploymentStorage(solution_id, deployment_id), files, resources)
        deployment = await SolutionDeploymentAPIService(self.db).create_ready_draft(solution_id, created_by,
            SolutionDeploymentCreate(compiled_manifest=manifest, resolution_map=resolution,
                                     git_commit_sha=body.source_commit_sha))
        baseline = {"schema_version": ADOPTION_MARKER,
                    "installed_control_digest": controls, "legacy_source_hashes": hashes}
        if deployment.validation_result and deployment.validation_result != baseline:
            raise SolutionSourceRevisionConflict("candidate already retains another legacy baseline; use a new candidate ID")
        if not deployment.validation_result:
            result = await self.db.execute(update(SolutionDeployment).where(
                SolutionDeployment.id == deployment_id,
                SolutionDeployment.solution_id == solution_id,
                SolutionDeployment.state == "ready",
                or_(SolutionDeployment.validation_result.is_(None),
                    SolutionDeployment.validation_result == {}),
            ).values(validation_result=baseline).returning(SolutionDeployment.id))
            if result.scalar_one_or_none() != deployment_id:
                raise SolutionSourceRevisionConflict("candidate state changed before baseline persistence")
            await self.db.refresh(deployment, attribute_names=["validation_result"])
        return await self.inspect(solution_id, deployment_id,
            InitialWorkflowInstallInspectRequest(reviewed_recipe=body.reviewed_recipe))

    async def inspect(self, solution_id: UUID, deployment_id: UUID,
                      request: InitialWorkflowInstallInspectRequest, *, lock: bool = False):
        solution, rows, controls, has_owned_tables = await self._installed(solution_id, lock=lock)
        deployment = await self.repository.get_runtime_closure(deployment_id, solution.organization_id, solution_id)
        if (deployment is None or deployment.state != "ready" or deployment.parent_deployment_id is not None
                or deployment.base_deployment_id is not None):
            raise SolutionSourceRevisionConflict("legacy adoption candidate is not an initial ready deployment")
        await self.db.refresh(deployment, attribute_names=["validation_result"])
        marker = deployment.validation_result or {}
        legacy_files, hashes = await self._legacy_hashes(solution_id, deployment_id, request.reviewed_recipe)
        if (marker.get("schema_version") != ADOPTION_MARKER or marker.get("installed_control_digest") != controls
                or marker.get("legacy_source_hashes") != hashes):
            raise SolutionSourceRevisionConflict("legacy installed source or controls changed after staging")
        manifest, resolution = await inspect_reviewed_artifact(solution_id, deployment_id, deployment,
            request.reviewed_recipe, WorkflowIndexer(self.db), ADOPTION_MARKER, has_owned_tables=has_owned_tables,
            legacy_parameter_hashes={row.id: canonical_digest(row.parameters_schema)
                                     for row in rows if row.is_active and isinstance(row.parameters_schema, list)},
            legacy_descriptor_snapshots={row.id: _workflow_snapshot(row) for row in rows if row.is_active})
        await self._registrations(solution, rows, resolution.workflows, legacy_files)
        await require_shared_tables(self.db, request.reviewed_recipe.shared_tables,
                                   solution_organization_id=solution.organization_id)
        await require_root_workspace_files(self.db, manifest.root_file_bindings)
        unfinished = exists(select(ExecutionAttempt.id).where(ExecutionAttempt.logical_job_type == "workflow",
            ExecutionAttempt.logical_job_id == Execution.id, ExecutionAttempt.completed_at.is_(None)))
        accepted_ids = list((await self.db.scalars(select(Execution.id).where(
            Execution.workflow_id.in_([row.id for row in rows]),
            or_(Execution.status.in_(_ACCEPTED), unfinished)).order_by(Execution.id).limit(101))).all())
        evidence = {"schema_version": ADOPTION_MARKER, "solution_id": str(solution_id),
            "deployment_id": str(deployment_id), "compiled_manifest_hash": deployment.compiled_manifest_hash,
            "resolution_map_hash": deployment.resolution_map_hash, "installed_control_digest": controls,
            "legacy_source_hashes": hashes, "reviewed_recipe": request.reviewed_recipe.model_dump(mode="json")}
        return RepoWorkflowAdoptionInspectResponse(solution_id=solution_id, deployment_id=deployment_id,
            organization_id=solution.organization_id, source_commit_sha=manifest.git.commit_sha,
            workflow_ids=sorted((item.resolved_id for item in resolution.workflows.values()), key=str),
            source_hashes={path: item.content_hash for path, item in
                {**resolution.sources, **resolution.resources}.items()},
            legacy_source_hashes=hashes, installed_control_digest=controls,
            retained_inactive_workflow_ids=sorted((row.id for row in rows if not row.is_active), key=str),
            accepted_execution_ids=accepted_ids[:100], accepted_work_exceeds_limit=len(accepted_ids) > 100,
            evidence_id=canonical_digest(evidence), state="ready")

    async def activate(self, solution_id: UUID, deployment_id: UUID,
                       request: InitialWorkflowInstallInspectRequest, expected_evidence_id: str):
        # Lock ordering matches runtime admission/accounting: global fence, then
        # Solution and workflow rows. The router holds the install writer lock.
        await acquire_workspace_release_lock(self.db, None)
        inspected = await self.inspect(solution_id, deployment_id, request, lock=True)
        if inspected.evidence_id != expected_evidence_id:
            raise SolutionSourceRevisionConflict("legacy adoption evidence changed")
        if inspected.accepted_execution_ids or inspected.accepted_work_exceeds_limit:
            raise SolutionSourceRevisionConflict("accepted legacy work must finish before adoption; do not cancel or repin it")
        await self.repository.transition(deployment_id, inspected.organization_id,
                                         expected_state="ready", new_state="activating")
        if not await self.repository.compare_and_set_active_deployment(solution_id, inspected.organization_id,
            expected_active_deployment_id=None, new_active_deployment_id=deployment_id):
            raise SolutionSourceRevisionConflict("legacy Solution pointer changed during adoption")
        await self.repository.transition(deployment_id, inspected.organization_id,
            expected_state="activating", new_state="active", activated_at=datetime.now(UTC))
        return inspected.model_copy(update={"state": "active"})
