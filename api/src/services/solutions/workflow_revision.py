"""Atomic registration additions and compatible revisions of adopted Solutions.

Destructive transitions remain explicit conflicts until the complete live caller
inventory and its admission guards are available. No row or history is deleted.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update

from bifrost.workspace_release import canonical_digest
from src.models.contracts.solution_deployments import (
    SolutionDeploymentCreate, SolutionSourceRevisionCommitRequest,
    SolutionSourceRevisionInspectRequest, SolutionSourceRevisionInspectResponse,
)
from src.models.orm.workflows import Workflow
from src.models.orm.users import Role
from src.models.orm.workflow_roles import WorkflowRole
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.deployment_api import SolutionDeploymentAPIService
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest, DeploymentGitProvenance, DeploymentResolutionMap,
    DeploymentSource, RuntimeSourceResolution,
    canonical_json, sha256_digest, validate_runtime_closure,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.live_handoff_source import LiveHandoffSourceError, source_archive, source_closure
from src.services.solutions.shared_table_bindings import SharedTableBindingError, require_shared_tables
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict, SolutionSourceRevisionError, SolutionSourceRevisionService,
    _archive_files, _workflow_snapshot,
)
from src.services.solutions.workflow_revision_recipe import (
    WORKFLOW_REVISION_MARKER, ReviewedWorkflowRecipe, WorkflowRecipeError, compile_workflow_registrations,
)


def _without_presentation(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: (
            {name: _without_presentation(schema) for name, schema in item.items()}
            if key == "properties" else item if key in {"enum", "const"} else _without_presentation(item)
        ) for key, item in value.items() if key not in {"title", "default"}}
    if isinstance(value, list | tuple):
        return [_without_presentation(item) for item in value]
    return value


def require_compatible_parameters(old: dict, new: dict) -> None:
    """Existing callers keep their accepted keyword set and type constraints."""
    old_properties, new_properties = old.get("properties", {}), new.get("properties", {})
    if (not set(old_properties).issubset(new_properties)
            or not set(new.get("required", [])).issubset(old.get("required", []))
            or old.get("additionalProperties") is True and new.get("additionalProperties") is not True
            or any(_without_presentation(schema) != _without_presentation(new_properties[name])
                   for name, schema in old_properties.items())):
        raise SolutionSourceRevisionError("Breaking parameter changes require verified live caller reconciliation")


class SolutionWorkflowRevisionService(SolutionSourceRevisionService):
    async def _desired(self, solution_id: UUID, expected: SolutionSourceRevisionInspectRequest,
                       recipe: ReviewedWorkflowRecipe, files: dict[str, bytes], *, lock: bool):
        if recipe.solution_id != solution_id:
            raise SolutionSourceRevisionError("Workflow recipe belongs to another install")
        # A new reviewed binding may repair a drifted contract. Validate the new
        # contract below instead of requiring the obsolete hash to remain live.
        solution, base, previous = await self._base(solution_id, expected,
            lock_solution=lock, verify_shared_tables=False)
        rows = await self._registrations(solution_id, previous, lock=lock)
        indexer = WorkflowIndexer(self.db)
        try:
            desired = compile_workflow_registrations(recipe, files, indexer)
            closure = source_closure(files, {item.path for item in recipe.workflows},
                has_table_bindings=bool(recipe.shared_tables))
        except (WorkflowRecipeError, LiveHandoffSourceError) as exc:
            raise SolutionSourceRevisionError(str(exc)) from exc
        if set(closure) != set(files):
            raise SolutionSourceRevisionError("Recipe differs from the complete dependency closure")
        base_files = await self._base_files(solution_id, base.id, previous)
        old_ids = {row.id for row in rows}
        desired_by_id = {item.resolved_id: item for item in desired.values()}
        if not old_ids.issubset(desired_by_id):
            raise SolutionSourceRevisionError("Workflow removal requires verified live caller and trigger reconciliation")
        for row in rows:
            definition = json.loads(canonical_json(desired_by_id[row.id].definition))
            snapshot = _workflow_snapshot(row)
            # These changes can invalidate string callers, auth decisions, endpoint
            # routing, service lifecycles or retry envelopes. This first adapter
            # exposes them as unsupported instead of silently projecting them.
            protected = ("path", "function_name", "name", "type", "organization_id",
                "endpoint_enabled", "public_endpoint", "access_level", "role_ids",
                "allowed_methods", "disable_global_key", "execution_mode", "retry_policy", "cache_ttl_seconds")
            if any(definition.get(key) != snapshot[key] for key in protected):
                raise SolutionSourceRevisionError("Rename, scope, type, access, endpoint, mode, cache or retry changes require a reviewed caller/control-plane adapter")
            old_schema = indexer.extract_parameters_from_source(base_files[snapshot["path"]], row.function_name, path=row.path)
            if old_schema is None:
                raise SolutionSourceRevisionError("Installed parameter contract cannot be inferred")
            require_compatible_parameters(old_schema, definition["parameters_schema"])
        # Global UUID lookup is intentional in this deploy writer: a recipe must
        # never claim an existing Root registration or another Solution's UUID.
        occupied = (await self.db.scalars(select(Workflow).where(Workflow.id.in_(desired_by_id)))).all()
        if any(row.solution_id != solution_id or row.id not in old_ids for row in occupied):
            raise SolutionSourceRevisionConflict("New workflow UUID is already registered")
        for item in recipe.workflows:
            if item.id not in old_ids:
                if (item.organization_id != solution.organization_id or item.controls.endpoint_enabled
                        or item.controls.public_endpoint or item.controls.access_level != "role_based"):
                    raise SolutionSourceRevisionError("New workflows require install scope, role-based access and disabled endpoints")
                role_ids = set(item.controls.role_ids)
                roles = (await self.db.scalars(select(Role).where(Role.id.in_(role_ids)))).all() if role_ids else []
                # Roles are an instance-global catalog; org scope lives on the
                # workflow. The reviewed recipe pins existing role identities.
                if {role.id for role in roles} != role_ids:
                    raise SolutionSourceRevisionError("New workflow roles are missing")
        try:
            await require_shared_tables(self.db, recipe.shared_tables)
        except SharedTableBindingError as exc:
            raise SolutionSourceRevisionError(str(exc)) from exc
        return solution, base, rows, desired

    async def stage_workflows(self, solution_id: UUID, deployment_id: UUID, created_by: UUID,
                              expected: SolutionSourceRevisionInspectRequest, recipe: ReviewedWorkflowRecipe,
                              files: dict[str, bytes], commit_sha: str) -> SolutionSourceRevisionInspectResponse:
        _solution, base, _rows, entities = await self._desired(solution_id, expected, recipe, files, lock=False)
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        sources = {path: RuntimeSourceResolution(object_key=f"{storage.runtime_prefix}{path}",
            content_hash=sha256_digest(content)) for path, content in files.items()}
        resolution = DeploymentResolutionMap(workflows=entities, sources=sources, shared_tables=recipe.shared_tables)
        manifest = CompiledDeploymentManifest(solution_id=solution_id, deployment_id=deployment_id,
            bundle_hash=sha256_digest(canonical_json({"base_manifest_hash": base.compiled_manifest_hash,
                "source_commit_sha": commit_sha, "reviewed_recipe": recipe.model_dump(mode="json"),
                "source_hashes": {path: item.content_hash for path, item in sources.items()}})),
            resolution_map_hash=sha256_digest(canonical_json(resolution)),
            source=DeploymentSource(artifact_key=storage.source_artifact_key, runtime_prefix=storage.runtime_prefix),
            workflows=entities, shared_tables=recipe.shared_tables, git=DeploymentGitProvenance(commit_sha=commit_sha))
        await storage.write_source_artifact(source_archive(files), idempotent=True)
        slots = asyncio.Semaphore(16)
        async def upload(path: str, content: bytes) -> None:
            async with slots:
                await storage.write_runtime_file(path, content, idempotent=True)
        await asyncio.gather(*(upload(path, content) for path, content in files.items()))
        await SolutionDeploymentAPIService(self.db).create_ready_draft(solution_id, created_by,
            SolutionDeploymentCreate(compiled_manifest=manifest, resolution_map=resolution,
                base_deployment_id=base.id, parent_deployment_id=base.id, git_commit_sha=commit_sha))
        return await self.inspect_workflows(solution_id, deployment_id, expected, recipe)

    async def inspect_workflows(self, solution_id: UUID, deployment_id: UUID,
                                expected: SolutionSourceRevisionInspectRequest, recipe: ReviewedWorkflowRecipe,
                                *, lock: bool = False) -> SolutionSourceRevisionInspectResponse:
        solution, _base, _previous = await self._base(solution_id, expected,
            lock_solution=lock, verify_shared_tables=False)
        candidate = await self.repository.get_runtime_closure(deployment_id, solution.organization_id, solution_id)
        if candidate is None or candidate.state != "ready" or candidate.base_deployment_id != expected.expected_active_deployment_id:
            raise SolutionSourceRevisionConflict("Workflow candidate or base changed")
        try:
            manifest, resolution = validate_runtime_closure(candidate.compiled_manifest, candidate.resolution_map,
                candidate.dependencies, expected_manifest_hash=candidate.compiled_manifest_hash,
                expected_resolution_hash=candidate.resolution_map_hash)
        except ValueError as exc:
            raise SolutionSourceRevisionError("Workflow candidate closure is invalid") from exc
        if (manifest.agents or manifest.forms or manifest.events or manifest.applications or manifest.tables
                or manifest.dependencies or manifest.file_locations or manifest.connections or manifest.config_requirements
                or manifest.resources
                or manifest.git.commit_sha != candidate.git_commit_sha):
            raise SolutionSourceRevisionError("Workflow candidate contains unsupported resources")
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        files = _archive_files(await storage.read_source_artifact(), set(resolution.sources))
        _solution, base, rows, desired = await self._desired(solution_id, expected, recipe, files, lock=lock)
        if desired != resolution.workflows or recipe.shared_tables != resolution.shared_tables:
            raise SolutionSourceRevisionError("Workflow candidate differs from its reviewed recipe")
        slots = asyncio.Semaphore(16)
        async def verify(path: str, content: bytes) -> None:
            source = resolution.sources[path]
            if source.object_key != f"{storage.runtime_prefix}{path}" or source.content_hash != sha256_digest(content):
                raise SolutionSourceRevisionError("Workflow candidate source evidence changed")
            async with slots:
                if await storage.read_runtime_file(path) != content:
                    raise SolutionSourceRevisionError("Workflow candidate runtime bytes changed")
        await asyncio.gather(*(verify(path, content) for path, content in files.items()))
        subscriptions, active = await self._subscriptions([row.id for row in rows])
        hashes = {path: source.content_hash for path, source in resolution.sources.items()}
        evidence = {"schema_version": WORKFLOW_REVISION_MARKER, "solution_id": str(solution_id),
            "deployment_id": str(deployment_id), "active_deployment_id": str(base.id),
            "active_manifest_hash": base.compiled_manifest_hash, "candidate_manifest_hash": candidate.compiled_manifest_hash,
            "candidate_resolution_hash": candidate.resolution_map_hash,
            "source_commit_sha": manifest.git.commit_sha, "workflow_registrations": [_workflow_snapshot(row) for row in rows],
            "reviewed_recipe": recipe.model_dump(mode="json"), "event_subscriptions": subscriptions, "source_hashes": hashes}
        return SolutionSourceRevisionInspectResponse(solution_id=solution_id, deployment_id=deployment_id,
            active_deployment_id=base.id, source_commit_sha=manifest.git.commit_sha,
            workflow_ids=sorted((entity.resolved_id for entity in desired.values()), key=str),
            active_subscription_ids=active, source_hashes=hashes, evidence_id=canonical_digest(evidence), state="ready")

    async def activate_workflows(self, solution_id: UUID, deployment_id: UUID,
                                 request: SolutionSourceRevisionCommitRequest,
                                 recipe: ReviewedWorkflowRecipe) -> SolutionSourceRevisionInspectResponse:
        inspected = await self.inspect_workflows(solution_id, deployment_id, request, recipe, lock=True)
        if inspected.evidence_id != request.expected_evidence_id:
            raise SolutionSourceRevisionConflict("Workflow revision evidence changed")
        # Native row locks, immutable evidence and pointer CAS cover one commit.
        # Core writes are the established sole deploy writer, with scoped IDs;
        # instance-owned credentials and all immutable history remain intact.
        solution, _base, _resolution = await self._base(solution_id, request, lock_solution=True, verify_shared_tables=False)
        candidate = await self.repository.get_runtime_closure(deployment_id, solution.organization_id, solution_id)
        assert candidate is not None
        rows = (await self.db.scalars(select(Workflow).where(Workflow.solution_id == solution_id))).all()
        current_ids = {row.id for row in rows}
        for entity in DeploymentResolutionMap.model_validate(candidate.resolution_map).workflows.values():
            definition: dict[str, Any] = json.loads(canonical_json(entity.definition))
            roles = [UUID(value) for value in definition.pop("role_ids")]
            for key in ("runtime_bounds", "effects", "source_enforced_bounds", "source_requested_bounds",
                        "parameters_schema_contract"):
                definition.pop(key)
            # Immutable nested tuples must become ordinary JSON lists/dicts for
            # the SQL JSONB serializer, matching the canonical contract.
            values = {**definition, "is_active": True,
                "is_orphaned": False, "updated_at": datetime.now(UTC)}
            values["organization_id"] = UUID(values["organization_id"]) if values["organization_id"] else None
            if entity.resolved_id in current_ids:
                result = await self.db.execute(update(Workflow).where(Workflow.id == entity.resolved_id,
                    Workflow.solution_id == solution_id, Workflow.is_active.is_(True)).values(**values))
                if result.rowcount != 1:
                    raise SolutionSourceRevisionConflict("Workflow registration changed")
            else:
                await self.db.execute(insert(Workflow).values(id=entity.resolved_id, solution_id=solution_id, **values))
                if roles:
                    await self.db.execute(insert(WorkflowRole), [{"workflow_id": entity.resolved_id, "role_id": role} for role in roles])
        marker = {"schema_version": WORKFLOW_REVISION_MARKER, "preflight_evidence_id": inspected.evidence_id,
            "workflow_ids": [str(value) for value in inspected.workflow_ids], "source_hashes": inspected.source_hashes}
        await self.repository.transition(deployment_id, solution.organization_id,
            expected_state="ready", new_state="activating", validation_result=marker)
        if not await self.repository.compare_and_set_active_deployment(solution_id, solution.organization_id,
            expected_active_deployment_id=request.expected_active_deployment_id, new_active_deployment_id=deployment_id):
            raise SolutionSourceRevisionConflict("Solution pointer changed")
        await self.repository.transition(request.expected_active_deployment_id, solution.organization_id,
            expected_state="active", new_state="superseded", superseded_at=datetime.now(UTC))
        await self.repository.transition(deployment_id, solution.organization_id,
            expected_state="activating", new_state="active", activated_at=datetime.now(UTC))
        return inspected.model_copy(update={"state": "active"})
