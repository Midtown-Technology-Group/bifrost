"""Complete package compilation/publication through existing deployment storage."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import io
import json
import zipfile
from typing import Any
from uuid import UUID

from bifrost.solution_delivery_review import _decorator
from bifrost.workspace_release import canonical_digest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.app_runtime import verify_compiled_app_runtime_pin
from src.services.solutions.deploy import PreparedSolutionDeployment, SolutionDeployer
from src.services.solutions.deployment_activation import SolutionDeploymentActivationService
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest, DeploymentGitProvenance, DeploymentResolutionMap,
    DeploymentSource, RuntimeEntityDefinition, RuntimeSourceResolution,
    canonical_json, sha256_digest, validate_runtime_closure,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.live_handoff_source import source_closure
from src.services.solutions.package_controls import _json_value, capture_package_controls, capture_package_projection
from src.services.solutions.package_git_source import VerifiedSolutionPackageSource
from src.services.solutions.source_revision import _require_registration, _workflow_snapshot
from src.services.solutions.workflow_revision_recipe import (
    ReviewedWorkflowRecipe, WorkflowRecipeError, compile_workflow_registrations,
)

PACKAGE_RUNTIME_SCHEMA = "bifrost.solution-package-runtime/v1"


async def compile_package_runtime(
    db: AsyncSession, source: VerifiedSolutionPackageSource,
    prepared: PreparedSolutionDeployment, deployment_id: UUID,
    *, publication_job_id: UUID | None = None,
) -> tuple[CompiledDeploymentManifest, DeploymentResolutionMap]:
    """Compile registrations; retain all source, not just executable closure."""
    bundle = prepared.bundle
    sid = bundle.solution.id
    evidence = source.evidence()
    if str(sid) != evidence["package"]["solution_id"]:
        raise ValueError("Package runtime target differs from protected source")
    storage = SolutionDeploymentStorage(sid, deployment_id)
    files = source.authored.files
    python = {path: raw.encode() for path, raw in bundle.python_files.items()}
    sources = {path: RuntimeSourceResolution(
        object_key=storage.runtime_prefix + path, content_hash=sha256_digest(raw),
    ) for path, raw in files.items()}
    workflows: dict[str, RuntimeEntityDefinition] = {}
    if bundle.workflows:
        # The ordinary dependency backstop cannot certify immutable SDK table
        # or file capability. Require the public closure analyser as well.
        source_closure(
            python, {item["path"] for item in bundle.workflows},
            has_table_bindings=bool(bundle.tables),
            has_root_file_bindings=bool(bundle.file_locations),
        )
        rows = (await db.scalars(select(Workflow).where(
            Workflow.solution_id == sid,
        ).options(selectinload(Workflow.roles)))).all()
        by_id = {row.id: row for row in rows}
        if set(by_id) != {UUID(item["id"]) for item in bundle.workflows}:
            raise ValueError("Prepared package workflow identities differ")
        declarations = []
        snapshots: dict[UUID, dict[str, Any]] = {}
        for item in bundle.workflows:
            row = by_id[UUID(item["id"])]
            tree = ast.parse(python[item["path"]], filename=item["path"])
            nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                     and node.name == item["function_name"]]
            if len(nodes) != 1:
                raise WorkflowRecipeError("Package workflow entrypoint is missing or ambiguous")
            _, declared = _decorator(tree, nodes[0])
            bounds: dict[str, Any] = {}
            for name in ("enforced_bounds", "requested_bounds"):
                for key, value in (declared.get(name) or {}).items():
                    if value is not None:
                        bounds[key] = min(bounds.get(key, value), value)
            if not bounds:
                raise WorkflowRecipeError("Package workflows require explicit complete runtime bounds")
            snapshot = _workflow_snapshot(row)
            snapshots[row.id] = snapshot
            controls = {key: snapshot[key] for key in (
                "display_name", "execution_mode", "timeout_seconds", "cache_ttl_seconds",
                "time_saved", "value", "retry_policy", "access_level", "role_ids",
                "endpoint_enabled", "public_endpoint", "allowed_methods", "disable_global_key",
            )}
            declarations.append({"id": row.id, "path": item["path"], "function_name": item["function_name"],
                                 "organization_id": row.organization_id, "runtime_bounds": bounds, "controls": controls})
        recipe = ReviewedWorkflowRecipe.model_validate({
            "schema_version": "bifrost.solution-workflow-delivery/v1", "solution_id": sid,
            "files": {path: source.authored.repo_subpath + "/" + path for path in python}, "workflows": declarations,
        })
        from bifrost.workflow_parameters import WorkflowParameterCompiler
        workflows = compile_workflow_registrations(recipe, python, WorkflowParameterCompiler())
        for ref, entity in list(workflows.items()):
            payload = entity.model_dump(mode="json")
            snapshot = snapshots[entity.resolved_id]
            # A complete package reviews its manifest descriptors too. Keep
            # the installed projection of those descriptors, including NULL,
            # rather than overriding it with workflow-only docstring defaults.
            for key in ("name", "description", "category", "tags", "tool_description"):
                payload["definition"][key] = snapshot[key]
            workflows[ref] = RuntimeEntityDefinition.model_validate(payload)
            _require_registration(by_id[entity.resolved_id], workflows[ref], allow_inactive=True)

    def entities(items: list[dict[str, Any]], manifest_path: str) -> dict[str, RuntimeEntityDefinition]:
        return {item["id"]: RuntimeEntityDefinition.model_validate({
            "portable_ref": item["id"], "resolved_id": item["id"],
            "definition": _json_value({key: value for key, value in item.items()
                                       if key not in {"src_files", "bin_files", "dist_files", "bin_dist_files"}}),
            "source_ref": manifest_path, "source_hash": sources[manifest_path].content_hash,
        }) for item in items}

    applications = entities(bundle.apps, ".bifrost/apps.yaml")
    if {item.app_id for item in prepared.compiled_apps} != {entity.resolved_id for entity in applications.values()}:
        raise ValueError("Package compiled App identities are incomplete")
    for item in prepared.compiled_apps:
        payload = applications[str(item.app_id)].model_dump(mode="json")
        payload["definition"]["runtime_pin"] = item.runtime_pin
        applications[str(item.app_id)] = RuntimeEntityDefinition.model_validate(payload)
    resolution = DeploymentResolutionMap(
        workflows=workflows, applications=applications,
        agents=entities(bundle.agents, ".bifrost/agents.yaml"),
        forms=entities(bundle.forms, ".bifrost/forms.yaml"),
        events=entities(bundle.events, ".bifrost/events.yaml"), sources=sources,
    )
    controls = await capture_package_controls(db, sid)
    projection = await capture_package_projection(db, sid)
    package = {"schema_version": PACKAGE_RUNTIME_SCHEMA, "source": evidence,
               "publication_job_id": str(publication_job_id) if publication_job_id else None,
               "controls": controls, "controls_digest": canonical_digest(controls),
               "projection": projection, "projection_digest": canonical_digest(projection),
               "claims": _json_value(bundle.claims), "file_policies": _json_value(bundle.file_policies)}
    manifest = CompiledDeploymentManifest(
        solution_id=sid, deployment_id=deployment_id, bundle_hash=source.artifact_digest,
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(artifact_key=storage.source_artifact_key, runtime_prefix=storage.runtime_prefix),
        workflows=workflows, applications=applications, agents=resolution.agents,
        forms=resolution.forms, events=resolution.events,
        tables=entities(bundle.tables, ".bifrost/tables.yaml"),
        file_locations={name: {"location": name} for name in bundle.file_locations},
        connections={item["integration_name"]: _json_value(item) for item in prepared.source_bundle.connection_schemas},
        config_requirements={item["key"]: _json_value(item) for item in bundle.config_schemas},
        git=DeploymentGitProvenance(repository=evidence.get("repository"), resolved_ref="main", commit_sha=source.authored.commit_sha),
        package_evidence=package,
    )
    validate_runtime_closure(manifest, resolution, [], expected_manifest_hash=manifest.content_hash(),
                             expected_resolution_hash=manifest.resolution_map_hash)
    return manifest, resolution


async def stage_package_runtime(
    source: VerifiedSolutionPackageSource, prepared: PreparedSolutionDeployment,
    manifest: CompiledDeploymentManifest,
) -> None:
    """Unreferenced create-only source and App outputs; never move pointers."""
    storage = SolutionDeploymentStorage(manifest.solution_id, manifest.deployment_id)
    await storage.write_source_artifact(source.source_archive, idempotent=True)
    slots = asyncio.Semaphore(16)

    async def write(path: str, raw: bytes) -> None:
        async with slots:
            await storage.write_runtime_file(path, raw, idempotent=True)

    await asyncio.gather(*(write(path, raw) for path, raw in source.authored.files.items()))
    builder = SolutionAppBuilder()
    for item in prepared.compiled_apps:
        verify_compiled_app_runtime_pin(item.runtime_pin, solution_id=item.solution_id,
            application_id=item.app_id, deployment_id=item.deployment_id,
            source_sha256=source.evidence()["package"]["source_archive_sha256"], outputs=item.dist, source_built=True)
        await builder.upload_deployment(item.app_id, item.deployment_id, item.dist)


class PackageActivationHooks:
    def __init__(self, db: AsyncSession, source: VerifiedSolutionPackageSource,
                 prepared: PreparedSolutionDeployment, manifest: CompiledDeploymentManifest):
        self.db, self.source, self.prepared, self.manifest = db, source, prepared, manifest

    async def verify_finalized(self, deployment: SolutionDeployment) -> None:
        if deployment.compiled_manifest_hash != self.manifest.content_hash():
            raise ValueError("Package candidate manifest differs")
        storage = SolutionDeploymentStorage(deployment.solution_id, deployment.id)
        if await storage.read_source_artifact() != self.source.source_archive:
            raise ValueError("Package source archive differs")
        for path, raw in self.source.authored.files.items():
            if await storage.read_runtime_file(path, max_bytes=len(raw)) != raw:
                raise ValueError(f"Package runtime source differs: {path}")
        if canonical_digest(await capture_package_controls(self.db, deployment.solution_id)) != (
            self.manifest.package_evidence or {}
        ).get("controls_digest"):
            raise ValueError("Prepared package controls differ")
        builder = SolutionAppBuilder()
        for item in self.prepared.compiled_apps:
            paths = await builder.list_dist(item.app_id, deployment_id=item.deployment_id)
            if sorted(paths) != sorted(item.dist):
                raise ValueError("Package App output inventory differs")
            outputs = {path: await builder.read_dist(item.app_id, path, deployment_id=item.deployment_id) for path in paths}
            if outputs != item.dist:
                raise ValueError("Package App outputs differ")

    async def rebuild_projections(self, deployment: SolutionDeployment) -> None:
        await SolutionDeployer(self.db)._activate_compiled_dists_in_transaction(self.db, list(self.prepared.compiled_apps))


async def activate_package_runtime(db: AsyncSession, source: VerifiedSolutionPackageSource,
                                   prepared: PreparedSolutionDeployment, manifest: CompiledDeploymentManifest,
                                   expected_active_deployment_id: UUID | None) -> None:
    """Join existing Solution and all-App CAS; a failed result rolls back both."""
    async with db.begin_nested():
        result = await SolutionDeploymentActivationService(
            SolutionDeploymentRepository(db), PackageActivationHooks(db, source, prepared, manifest),
        ).activate(manifest.deployment_id, prepared.bundle.solution.organization_id, manifest.solution_id,
                   expected_active_deployment_id=expected_active_deployment_id)
        if result.state != "active" or result.active_deployment_id != manifest.deployment_id:
            raise ValueError("Complete package activation was not established")


async def readback_package_runtime(
    db: AsyncSession, solution_id: UUID, deployment_id: UUID, *,
    expected_source_sha256: str, expected_organization_id: UUID | None,
) -> dict[str, Any]:
    """Original-attempt proof from storage and DB; no prepare/upload/activation."""
    from src.services.solutions.deployment_runtime import pin_workflow_runtime

    solution = await db.scalar(select(Solution).where(Solution.id == solution_id)
        .with_for_update().execution_options(populate_existing=True))
    if (solution is None or solution.organization_id != expected_organization_id
            or solution.active_deployment_id != deployment_id or solution.execution_runtime_mode != "deployment-v1"):
        raise ValueError("Original package pointer/scope is not active")
    deployment = await SolutionDeploymentRepository(db).get_runtime_closure(
        deployment_id, expected_organization_id, solution_id)
    if deployment is None or deployment.state != "active":
        raise ValueError("Original package deployment is not active")
    manifest, resolution = validate_runtime_closure(deployment.compiled_manifest, deployment.resolution_map,
        deployment.dependencies, expected_manifest_hash=deployment.compiled_manifest_hash,
        expected_resolution_hash=deployment.resolution_map_hash)
    package = json.loads(canonical_json(manifest.package_evidence or {}))
    if package.get("schema_version") != PACKAGE_RUNTIME_SCHEMA:
        raise ValueError("Original complete package evidence is missing")
    source_proof = package["source"]["package"]
    if (source_proof["solution_id"] != str(solution_id)
            or source_proof["organization_id"] != (str(expected_organization_id) if expected_organization_id else None)
            or source_proof["source_archive_sha256"] != expected_source_sha256):
        raise ValueError("Original package source identity differs")
    controls = await capture_package_controls(db, solution_id)
    if canonical_digest(controls) != package["controls_digest"] or controls != package["controls"]:
        raise ValueError("Original package controls/resources differ")
    projection = await capture_package_projection(db, solution_id)
    if canonical_digest(projection) != package["projection_digest"] or projection != package["projection"]:
        raise ValueError("Original complete package metadata differs")
    storage = SolutionDeploymentStorage(solution_id, deployment_id)
    if await storage.read_compiled_manifest() != manifest.canonical_bytes():
        raise ValueError("Original package stored manifest differs")
    archive = await storage.read_source_artifact()
    if hashlib.sha256(archive).hexdigest() != expected_source_sha256:
        raise ValueError("Original package stored archive differs")
    expected = {item["path"]: item for item in source_proof["source_files"]}
    with zipfile.ZipFile(io.BytesIO(archive)) as source_zip:
        members = source_zip.infolist()
        if len(members) != len(expected) or {item.filename for item in members} != set(expected):
            raise ValueError("Original package stored inventory differs")
        if sum(item.file_size for item in members) > 10 * 1024 * 1024:
            raise ValueError("Original package byte limit differs")
        if set(resolution.sources) != set(expected):
            raise ValueError("Original package runtime inventory differs")
        for item in members:
            raw = source_zip.read(item)
            record = expected[item.filename]
            if len(raw) != record["size"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("Original package authored bytes differ")
            reference = resolution.sources[item.filename]
            if (reference.object_key != storage.runtime_prefix + item.filename
                    or reference.content_hash != sha256_digest(raw)
                    or await storage.read_runtime_file(item.filename, max_bytes=len(raw)) != raw):
                raise ValueError("Original package runtime bytes differ")
    rows = (await db.scalars(select(Workflow).where(Workflow.solution_id == solution_id)
        .options(selectinload(Workflow.roles)).execution_options(populate_existing=True))).all()
    if {row.id for row in rows} != {item.resolved_id for item in resolution.workflows.values()}:
        raise ValueError("Original package workflow inventory differs")
    by_id = {item.resolved_id: item for item in resolution.workflows.values()}
    pins = {}
    for row in rows:
        _require_registration(row, by_id[row.id], allow_inactive=True)
        if not row.is_active:
            continue
        pin = await pin_workflow_runtime(db, row.id)
        if pin is None or pin.deployment_id != deployment_id:
            raise ValueError("Original package workflow pin differs")
        pins[str(row.id)] = pin.queue_evidence()
    applications = (await db.scalars(select(Application).where(Application.solution_id == solution_id)
        .execution_options(populate_existing=True))).all()
    app_by_id = {item.resolved_id: item for item in resolution.applications.values()}
    if {row.id for row in applications} != set(app_by_id):
        raise ValueError("Original package App inventory differs")
    app_pins = {}
    builder = SolutionAppBuilder()
    for row in applications:
        expected_pin = json.loads(canonical_json(app_by_id[row.id].definition))["runtime_pin"]
        if row.active_deployment_id != UUID(expected_pin["deployment_id"]):
            raise ValueError("Original package App pointer differs")
        paths = await builder.list_dist(row.id, deployment_id=row.active_deployment_id)
        if len(paths) != len(set(paths)) or sorted(paths) != sorted(expected_pin["output_hashes"]):
            raise ValueError("Original package App outputs differ")
        outputs = {path: await builder.read_dist(row.id, path, deployment_id=row.active_deployment_id) for path in paths}
        app_pins[str(row.id)] = verify_compiled_app_runtime_pin(
            (row.published_snapshot or {}).get("runtime_pin"), solution_id=solution_id,
            application_id=row.id, deployment_id=row.active_deployment_id,
            source_sha256=expected_source_sha256, outputs=outputs, source_built=True)
        if app_pins[str(row.id)] != expected_pin:
            raise ValueError("Original package App runtime pin differs")
    return {"state": "active", "solution_id": str(solution_id), "deployment_id": str(deployment_id),
        "source_commit_sha": source_proof["source_commit_sha"], "source_tree_sha": source_proof["source_tree_sha"],
        "source_artifact_sha256": expected_source_sha256, "compiled_manifest_hash": manifest.content_hash(),
        "source_verified": True, "registrations_verified": True, "runtime_verified": True,
        "workflow_runtime_pins": pins, "app_runtime_pins": app_pins}
