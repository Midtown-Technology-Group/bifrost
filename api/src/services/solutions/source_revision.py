"""Review and activate source-only revisions of an adopted Solution runtime."""

from __future__ import annotations

import asyncio
import ast
import base64
import binascii
import json
import re
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from bifrost.workspace_release import canonical_digest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.contracts.solution_deployments import (
    SolutionDeploymentCreate,
    SolutionSourceRevisionCommitRequest,
    SolutionSourceRevisionInspectRequest,
    SolutionSourceRevisionInspectResponse,
    SolutionSourceRevisionRequest,
)
from src.models.orm.events import EventSource, EventSubscription
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
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
    validate_runtime_closure,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.live_handoff_source import (
    MAX_ARCHIVE_BYTES,
    LiveHandoffSourceError,
    source_archive,
    source_closure,
)
from src.services.solutions.shared_table_bindings import (
    SharedTableBindingError, require_shared_tables,
)

_SOURCE_REVISION_MARKER = "bifrost.solution-source-revision/v1"
_HANDOFF_MARKER = "bifrost.workspace-live-handoff/v1"


class SolutionSourceRevisionError(ValueError):
    """Source or registration state is unsafe for the reviewed revision."""


class SolutionSourceRevisionConflict(SolutionSourceRevisionError):
    """The active base or review evidence changed."""


def _decode_files(request: SolutionSourceRevisionRequest) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    for item in request.files:
        path = item.path
        if (
            not path.endswith(".py")
            or path.startswith("/")
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or path in files
        ):
            raise SolutionSourceRevisionError(f"invalid source path: {path}")
        if len(item.content_base64) > 4 * (MAX_ARCHIVE_BYTES // 3 + 1):
            raise SolutionSourceRevisionError("source file is too large")
        try:
            content = base64.b64decode(item.content_base64, validate=True)
        except binascii.Error as exc:
            raise SolutionSourceRevisionError(
                f"invalid source encoding: {path}"
            ) from exc
        total += len(content)
        if total > MAX_ARCHIVE_BYTES:
            raise SolutionSourceRevisionError("source revision is too large")
        files[path] = content
    return files


def _workflow_snapshot(workflow: Workflow) -> dict:
    return {
        "id": str(workflow.id),
        "path": workflow.path.replace("\\", "/").lstrip("/"),
        "function_name": workflow.function_name,
        "name": workflow.name,
        "type": workflow.type,
        "organization_id": (
            str(workflow.organization_id) if workflow.organization_id else None
        ),
        "is_active": workflow.is_active,
        "endpoint_enabled": workflow.endpoint_enabled,
        "public_endpoint": workflow.public_endpoint,
        "api_key_enabled": workflow.api_key_enabled,
        "access_level": workflow.access_level,
        "role_ids": sorted(str(role.id) for role in workflow.roles),
        "timeout_seconds": workflow.timeout_seconds,
        "execution_mode": workflow.execution_mode,
        "time_saved": workflow.time_saved,
        "value": float(workflow.value or 0),
        "cache_ttl_seconds": workflow.cache_ttl_seconds,
        "parameters_schema": workflow.parameters_schema,
        "display_name": workflow.display_name,
        "description": workflow.description,
        "category": workflow.category,
        "tags": workflow.tags,
        "allowed_methods": sorted(workflow.allowed_methods),
        "disable_global_key": workflow.disable_global_key,
        "retry_policy": workflow.retry_policy,
        "tool_description": workflow.tool_description,
    }


def _require_registration(workflow: Workflow, entity: RuntimeEntityDefinition) -> None:
    definition = json.loads(canonical_json(entity.definition))
    timeout = workflow.timeout_seconds if workflow.timeout_seconds is not None else 1800
    bounds = definition.get("runtime_bounds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise SolutionSourceRevisionError("workflow timeout is invalid")
    if isinstance(bounds, dict):
        duration = bounds.get("max_duration_seconds")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            raise SolutionSourceRevisionError("workflow runtime bound is invalid")
        timeout = min(timeout, duration)
    elif bounds is not None:
        raise SolutionSourceRevisionError("workflow runtime bound is invalid")
    expected = {
        "path": workflow.path.replace("\\", "/").lstrip("/"),
        "function_name": workflow.function_name,
        "name": workflow.name,
        "type": workflow.type,
        "organization_id": (
            str(workflow.organization_id) if workflow.organization_id else None
        ),
        "timeout_seconds": timeout,
        "execution_mode": workflow.execution_mode,
        "time_saved": workflow.time_saved or 0,
        "value": float(workflow.value or 0),
        "cache_ttl_seconds": workflow.cache_ttl_seconds or 0,
    }
    # Older handoff manifests predate a complete registration definition.
    # New workflow revisions must also match every deploy-owned projection.
    snapshot = _workflow_snapshot(workflow)
    for key in (
        "parameters_schema", "display_name", "description", "category", "tags",
        "endpoint_enabled", "public_endpoint", "access_level", "role_ids",
        "allowed_methods", "disable_global_key", "retry_policy", "tool_description",
    ):
        if key in definition:
            expected[key] = snapshot[key]
    if (
        not workflow.is_active
        or workflow.id != entity.resolved_id
        or any(definition.get(key) != value for key, value in expected.items())
    ):
        raise SolutionSourceRevisionError(
            f"workflow registration differs from immutable runtime: {workflow.id}"
        )


def _archive_files(archive: bytes, expected_paths: set[str]) -> dict[str, bytes]:
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise SolutionSourceRevisionError("revision archive is too large")
    try:
        with ZipFile(BytesIO(archive)) as source_zip:
            members = [item for item in source_zip.infolist() if not item.is_dir()]
            paths = [item.filename for item in members]
            if (
                len(paths) != len(set(paths))
                or set(paths) != expected_paths
                or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES
            ):
                raise SolutionSourceRevisionError("revision archive paths differ")
            return {item.filename: source_zip.read(item) for item in members}
    except (BadZipFile, RuntimeError) as exc:
        raise SolutionSourceRevisionError("revision archive is invalid") from exc


def _entrypoint_signature(files: dict[str, bytes], workflow: Workflow) -> str:
    path = workflow.path.replace("\\", "/").lstrip("/")
    try:
        tree = ast.parse(files[path], filename=path)
    except (KeyError, SyntaxError) as exc:
        raise SolutionSourceRevisionError(
            f"workflow entrypoint is missing or invalid: {workflow.id}"
        ) from exc
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == workflow.function_name
    ]
    if len(matches) != 1:
        raise SolutionSourceRevisionError(
            f"workflow function is missing or ambiguous: {workflow.id}"
        )
    node = matches[0]
    return (
        ast.dump(node.args, include_attributes=False)
        + ":"
        + ":".join(
            ast.dump(item, include_attributes=False) for item in node.decorator_list
        )
        + (":async" if isinstance(node, ast.AsyncFunctionDef) else ":sync")
    )


def _require_entrypoint_compatibility(
    base_files: dict[str, bytes], new_files: dict[str, bytes], workflows: list[Workflow]
) -> None:
    for workflow in workflows:
        if _entrypoint_signature(base_files, workflow) != _entrypoint_signature(
            new_files, workflow
        ):
            raise SolutionSourceRevisionError(
                f"workflow signature or decorators changed: {workflow.id}"
            )


class SolutionSourceRevisionService:
    """Keep source updates reviewable without changing workflow identities."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = SolutionDeploymentRepository(db)

    async def verify_current_source(
        self, solution_id: UUID, request: SolutionSourceRevisionInspectRequest
    ) -> None:
        """An idempotent delivery must still validate the current adopted base."""
        _solution, base, resolution = await self._base(solution_id, request)
        await self._registrations(solution_id, resolution, lock=False)
        await self._base_files(solution_id, base.id, resolution)

    async def _base_files(
        self,
        solution_id: UUID,
        deployment_id: UUID,
        resolution: DeploymentResolutionMap,
    ) -> dict[str, bytes]:
        archive = await SolutionDeploymentStorage(
            solution_id, deployment_id
        ).read_source_artifact()
        files = _archive_files(archive, set(resolution.sources))
        if any(
            sha256_digest(content) != resolution.sources[path].content_hash
            for path, content in files.items()
        ):
            raise SolutionSourceRevisionError(
                "active source archive differs from its immutable hashes"
            )
        return files

    async def _base(
        self,
        solution_id: UUID,
        request: SolutionSourceRevisionInspectRequest,
        *,
        lock_solution: bool = False,
        verify_shared_tables: bool = True,
        allow_resources: bool = False,
    ):
        query = select(Solution).where(Solution.id == solution_id)
        if lock_solution:
            query = query.with_for_update()
        solution = await self.db.scalar(query)
        if solution is None or solution.status != "active":
            raise SolutionSourceRevisionError("Solution is not active")
        if (
            solution.active_deployment_id != request.expected_active_deployment_id
            or solution.execution_runtime_mode != "deployment-v1"
        ):
            raise SolutionSourceRevisionConflict("Solution active pointer changed")
        base = await self.repository.get_runtime_closure(
            request.expected_active_deployment_id,
            solution.organization_id,
            solution_id,
        )
        if (
            base is None
            or base.state != "active"
            or base.compiled_manifest_hash != request.expected_active_manifest_hash
        ):
            raise SolutionSourceRevisionConflict("active deployment changed")
        marker = base.validation_result or {}
        if marker.get("schema_version") not in {
            _HANDOFF_MARKER,
            _SOURCE_REVISION_MARKER,
            "bifrost.solution-workflow-revision/v1",
        }:
            raise SolutionSourceRevisionError(
                "active deployment did not use the reviewed handoff path"
            )
        try:
            manifest, resolution = validate_runtime_closure(
                base.compiled_manifest,
                base.resolution_map,
                base.dependencies,
                expected_manifest_hash=base.compiled_manifest_hash,
                expected_resolution_hash=base.resolution_map_hash,
            )
        except ValueError as exc:
            raise SolutionSourceRevisionError(
                "active deployment closure is invalid"
            ) from exc
        if (
            manifest.agents
            or manifest.forms
            or manifest.events
            or manifest.applications
            or manifest.tables
            or manifest.dependencies
            or manifest.file_locations
            or manifest.connections
            or manifest.config_requirements
            or manifest.resources and not allow_resources
            or not resolution.workflows
        ):
            raise SolutionSourceRevisionError(
                "source-only revision requires a workflow-only deployment without immutable resources"
            )
        if verify_shared_tables:
            try:
                await require_shared_tables(self.db, resolution.shared_tables)
            except SharedTableBindingError as exc:
                raise SolutionSourceRevisionError(str(exc)) from exc
        return solution, base, resolution

    async def _registrations(
        self, solution_id: UUID, resolution: DeploymentResolutionMap, *, lock: bool
    ) -> list[Workflow]:
        query = (
            select(Workflow)
            .options(selectinload(Workflow.roles))
            .where(Workflow.solution_id == solution_id, Workflow.is_active.is_(True))
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update(of=Workflow)
        rows = (await self.db.scalars(query)).all()
        if {row.id for row in rows} != {
            item.resolved_id for item in resolution.workflows.values()
        }:
            raise SolutionSourceRevisionConflict("Solution workflow set changed")
        for row in rows:
            _require_registration(row, resolution.resolve_workflow_id(row.id))
        return sorted(rows, key=lambda row: str(row.id))

    async def _subscriptions(
        self, workflow_ids: list[UUID]
    ) -> tuple[list[dict], list[UUID]]:
        query = (
            select(EventSubscription)
            .options(
                selectinload(EventSubscription.event_source).selectinload(
                    EventSource.schedule_source
                ),
                selectinload(EventSubscription.event_source).selectinload(
                    EventSource.webhook_source
                ),
            )
            .where(EventSubscription.workflow_id.in_(workflow_ids))
        )
        rows = (await self.db.scalars(query)).all()
        snapshot = []
        active_ids = []
        for row in sorted(rows, key=lambda item: str(item.id)):
            source = row.event_source
            schedule = source.schedule_source if source else None
            webhook = source.webhook_source if source else None
            if (
                row.is_active
                and source
                and source.is_active
                and (schedule is None or schedule.enabled)
            ):
                active_ids.append(row.id)
            snapshot.append(
                {
                    "id": str(row.id),
                    "workflow_id": str(row.workflow_id),
                    "event_source_id": str(row.event_source_id),
                    "is_active": row.is_active,
                    "event_type": row.event_type,
                    "criteria": row.criteria,
                    "input_mapping": row.input_mapping,
                    "source_active": source.is_active if source else None,
                    "source_type": source.source_type if source else None,
                    "schedule": (
                        {
                            "cron_expression": schedule.cron_expression,
                            "timezone": schedule.timezone,
                            "enabled": schedule.enabled,
                            "overlap_policy": schedule.overlap_policy,
                        }
                        if schedule
                        else None
                    ),
                    "webhook": (
                        {
                            "adapter_name": webhook.adapter_name,
                            "integration_id": (
                                str(webhook.integration_id)
                                if webhook.integration_id
                                else None
                            ),
                            "config": webhook.config,
                        }
                        if webhook
                        else None
                    ),
                }
            )
        return snapshot, active_ids

    async def stage(
        self,
        solution_id: UUID,
        deployment_id: UUID,
        created_by: UUID,
        request: SolutionSourceRevisionRequest,
    ) -> SolutionSourceRevisionInspectResponse:
        expected = SolutionSourceRevisionInspectRequest(
            expected_active_deployment_id=request.expected_active_deployment_id,
            expected_active_manifest_hash=request.expected_active_manifest_hash,
        )
        _solution, base, old_resolution = await self._base(solution_id, expected)
        workflows = await self._registrations(solution_id, old_resolution, lock=False)
        files = _decode_files(request)
        try:
            closure = source_closure(
                files, {row.path.replace("\\", "/").lstrip("/") for row in workflows},
                has_table_bindings=bool(old_resolution.shared_tables),
            )
            archive = source_archive(closure)
        except LiveHandoffSourceError as exc:
            raise SolutionSourceRevisionError(str(exc)) from exc
        if set(closure) != set(files):
            raise SolutionSourceRevisionError(
                "uploaded files differ from the exact workflow dependency closure"
            )
        _require_entrypoint_compatibility(
            await self._base_files(solution_id, base.id, old_resolution),
            closure,
            workflows,
        )
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        sources = {
            path: RuntimeSourceResolution(
                object_key=f"{storage.runtime_prefix}{path}",
                content_hash=sha256_digest(content),
            )
            for path, content in closure.items()
        }
        entities = {}
        for key, entity in old_resolution.workflows.items():
            if entity.source_ref is None or entity.source_ref not in sources:
                raise SolutionSourceRevisionError(
                    f"workflow source is absent from revision: {key}"
                )
            entities[key] = entity.model_copy(
                update={"source_hash": sources[entity.source_ref].content_hash}
            )
        resolution = DeploymentResolutionMap(
            workflows=entities, sources=sources, shared_tables=old_resolution.shared_tables
        )
        manifest = CompiledDeploymentManifest(
            solution_id=solution_id,
            deployment_id=deployment_id,
            bundle_hash=sha256_digest(
                canonical_json(
                    {
                        "base_manifest_hash": base.compiled_manifest_hash,
                        "source_commit_sha": request.source_commit_sha,
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
            shared_tables=old_resolution.shared_tables,
            git=DeploymentGitProvenance(commit_sha=request.source_commit_sha),
        )
        await storage.write_source_artifact(archive, idempotent=True)
        slots = asyncio.Semaphore(16)

        async def upload(path: str, content: bytes) -> None:
            async with slots:
                await storage.write_runtime_file(path, content, idempotent=True)

        await asyncio.gather(
            *(upload(path, content) for path, content in closure.items())
        )
        await SolutionDeploymentAPIService(self.db).create_ready_draft(
            solution_id,
            created_by,
            SolutionDeploymentCreate(
                compiled_manifest=manifest,
                resolution_map=resolution,
                base_deployment_id=base.id,
                parent_deployment_id=base.id,
                git_commit_sha=request.source_commit_sha,
            ),
        )
        return await self.inspect(solution_id, deployment_id, expected)

    async def inspect(
        self,
        solution_id: UUID,
        deployment_id: UUID,
        request: SolutionSourceRevisionInspectRequest,
        *,
        lock: bool = False,
    ) -> SolutionSourceRevisionInspectResponse:
        solution, base, old_resolution = await self._base(
            solution_id, request, lock_solution=lock
        )
        candidate = await self.repository.get_runtime_closure(
            deployment_id, solution.organization_id, solution_id
        )
        if (
            candidate is None
            or candidate.state != "ready"
            or candidate.base_deployment_id != base.id
        ):
            raise SolutionSourceRevisionConflict("revision candidate or base changed")
        try:
            manifest, resolution = validate_runtime_closure(
                candidate.compiled_manifest,
                candidate.resolution_map,
                candidate.dependencies,
                expected_manifest_hash=candidate.compiled_manifest_hash,
                expected_resolution_hash=candidate.resolution_map_hash,
            )
        except ValueError as exc:
            raise SolutionSourceRevisionError("revision closure is invalid") from exc
        if (
            set(resolution.workflows) != set(old_resolution.workflows)
            or resolution.shared_tables != old_resolution.shared_tables
            or manifest.tables or manifest.file_locations or manifest.resources
            or manifest.agents or manifest.forms or manifest.events or manifest.applications
            or manifest.dependencies
            or set(resolution.workflows) != set(manifest.workflows)
            or not manifest.git.commit_sha
            or re.fullmatch(r"[0-9a-f]{40}", manifest.git.commit_sha) is None
            or candidate.git_commit_sha != manifest.git.commit_sha
        ):
            raise SolutionSourceRevisionError("revision changed workflow identities")
        for key, entity in resolution.workflows.items():
            previous = old_resolution.workflows[key]
            if (
                entity.resolved_id != previous.resolved_id
                or entity.definition != previous.definition
                or entity.source_ref != previous.source_ref
                or entity.source_ref is None
                or entity.source_ref not in resolution.sources
                or entity.source_hash
                != resolution.sources[entity.source_ref].content_hash
            ):
                raise SolutionSourceRevisionError(
                    f"revision changed workflow registration: {key}"
                )
        workflows = await self._registrations(solution_id, old_resolution, lock=lock)
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        if (
            candidate.source_artifact_key != storage.source_artifact_key
            or candidate.runtime_storage_prefix != storage.runtime_prefix
            or manifest.source.artifact_key != storage.source_artifact_key
            or manifest.source.runtime_prefix != storage.runtime_prefix
            or await storage.read_compiled_manifest() != manifest.canonical_bytes()
        ):
            raise SolutionSourceRevisionError(
                "revision manifest differs from stored bytes"
            )
        files = _archive_files(
            await storage.read_source_artifact(), set(resolution.sources)
        )
        try:
            closure = source_closure(
                files, {row.path.replace("\\", "/").lstrip("/") for row in workflows},
                has_table_bindings=bool(old_resolution.shared_tables),
            )
        except LiveHandoffSourceError as exc:
            raise SolutionSourceRevisionError(str(exc)) from exc
        if set(closure) != set(files):
            raise SolutionSourceRevisionError(
                "revision source dependency closure changed"
            )
        _require_entrypoint_compatibility(
            await self._base_files(solution_id, base.id, old_resolution),
            closure,
            workflows,
        )
        slots = asyncio.Semaphore(16)

        async def verify(path: str, content: bytes) -> None:
            source = resolution.sources[path]
            if (
                source.object_key != f"{storage.runtime_prefix}{path}"
                or source.content_hash != sha256_digest(content)
            ):
                raise SolutionSourceRevisionError(
                    f"revision source hash changed: {path}"
                )
            async with slots:
                runtime = await storage.read_runtime_file(path)
            if runtime != content:
                raise SolutionSourceRevisionError(f"revision runtime differs: {path}")

        await asyncio.gather(
            *(verify(path, content) for path, content in closure.items())
        )
        subscription_snapshot, active_subscriptions = await self._subscriptions(
            [row.id for row in workflows]
        )
        evidence = {
            "schema_version": _SOURCE_REVISION_MARKER,
            "solution_id": str(solution_id),
            "deployment_id": str(deployment_id),
            "active_deployment_id": str(base.id),
            "active_manifest_hash": base.compiled_manifest_hash,
            "candidate_manifest_hash": candidate.compiled_manifest_hash,
            "candidate_resolution_hash": candidate.resolution_map_hash,
            "source_commit_sha": manifest.git.commit_sha,
            "workflow_registrations": [_workflow_snapshot(row) for row in workflows],
            "event_subscriptions": subscription_snapshot,
            "source_hashes": {
                path: source.content_hash for path, source in resolution.sources.items()
            },
        }
        return SolutionSourceRevisionInspectResponse(
            solution_id=solution_id,
            deployment_id=deployment_id,
            active_deployment_id=base.id,
            source_commit_sha=manifest.git.commit_sha,
            workflow_ids=[row.id for row in workflows],
            active_subscription_ids=active_subscriptions,
            source_hashes=evidence["source_hashes"],
            evidence_id=canonical_digest(evidence),
            state="ready",
        )

    async def activate(
        self,
        solution_id: UUID,
        deployment_id: UUID,
        request: SolutionSourceRevisionCommitRequest,
    ) -> SolutionSourceRevisionInspectResponse:
        inspection = await self.inspect(solution_id, deployment_id, request, lock=True)
        if inspection.evidence_id != request.expected_evidence_id:
            raise SolutionSourceRevisionConflict(
                "revision evidence changed after review"
            )
        solution = await self.db.get(Solution, solution_id)
        assert solution is not None
        candidate = await self.repository.get_runtime_closure(
            deployment_id, solution.organization_id, solution_id
        )
        assert candidate is not None
        marker = {
            "schema_version": _SOURCE_REVISION_MARKER,
            "preflight_evidence_id": inspection.evidence_id,
            "workflow_ids": [str(item) for item in inspection.workflow_ids],
            "source_hashes": inspection.source_hashes,
        }
        await self.repository.transition(
            deployment_id,
            solution.organization_id,
            expected_state="ready",
            new_state="activating",
            validation_result=marker,
        )
        if not await self.repository.compare_and_set_active_deployment(
            solution_id,
            solution.organization_id,
            expected_active_deployment_id=request.expected_active_deployment_id,
            new_active_deployment_id=deployment_id,
        ):
            raise SolutionSourceRevisionConflict("Solution pointer changed")
        await self.repository.transition(
            request.expected_active_deployment_id,
            solution.organization_id,
            expected_state="active",
            new_state="superseded",
            superseded_at=datetime.now(UTC),
        )
        await self.repository.transition(
            deployment_id,
            solution.organization_id,
            expected_state="activating",
            new_state="active",
            activated_at=datetime.now(UTC),
        )
        return inspection.model_copy(update={"state": "active"})
