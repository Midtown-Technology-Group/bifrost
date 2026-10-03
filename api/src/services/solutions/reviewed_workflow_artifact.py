"""Compile and verify reviewed workflow bytes without changing installed entities."""

from __future__ import annotations

import asyncio
from uuid import UUID

from bifrost.workflow_parameters import WorkflowParameterCompiler

from src.models.orm.solution_deployments import SolutionDeployment
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
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.live_handoff_source import (
    LiveHandoffSourceError,
    source_archive,
    source_closure,
)
from src.services.solutions.resource_delivery import (
    read_deployment_resources,
    validate_resource_files,
)
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict,
    SolutionSourceRevisionError,
    _archive_files,
    legacy_descriptor_evidence,
)
from src.services.solutions.workflow_revision_recipe import (
    ReviewedWorkflowRecipe,
    WorkflowRecipeError,
    compile_workflow_registrations,
)


def compile_reviewed_workflows(
    recipe: ReviewedWorkflowRecipe, files: dict[str, bytes],
    resources: dict[str, bytes], indexer: WorkflowParameterCompiler, *,
    has_owned_tables: bool = False,
    legacy_parameter_hashes: dict[UUID, str] | None = None,
    legacy_descriptor_snapshots: dict[UUID, dict] | None = None,
) -> dict[str, RuntimeEntityDefinition]:
    """Require the exact executable closure and declared resource bytes."""
    validate_resource_files(recipe, resources, files)
    try:
        closure = source_closure(
            files, {item.path for item in recipe.workflows},
            has_table_bindings=bool(recipe.shared_tables) or has_owned_tables,
            has_root_file_bindings=bool(recipe.root_file_bindings),
            has_resource_bindings=bool(recipe.resources),
        )
        entities = compile_workflow_registrations(recipe, files, indexer)
        if legacy_parameter_hashes:
            entities = {ref: item.model_copy(update={
                "legacy_parameters_schema_hash": legacy_parameter_hashes.get(item.resolved_id),
            }) for ref, item in entities.items()}
        if legacy_descriptor_snapshots is not None:
            if set(legacy_descriptor_snapshots) != {item.resolved_id for item in entities.values()}:
                raise SolutionSourceRevisionError("legacy descriptor identities differ from reviewed workflows")
            marked = {}
            for ref, item in entities.items():
                # Frozen runtime containers use tuples internally. Revalidate
                # JSON values, then freeze the augmented definition once.
                payload = item.model_dump(mode="json")
                payload["definition"]["legacy_descriptor_evidence"] = legacy_descriptor_evidence(
                    legacy_descriptor_snapshots[item.resolved_id])
                marked[ref] = RuntimeEntityDefinition.model_validate(payload)
            entities = marked
    except (WorkflowRecipeError, LiveHandoffSourceError) as exc:
        raise SolutionSourceRevisionError(str(exc)) from exc
    if set(closure) != set(files):
        raise SolutionSourceRevisionError("recipe differs from complete source dependency closure")
    return entities


def build_reviewed_artifact(
    solution_id: UUID, deployment_id: UUID, recipe: ReviewedWorkflowRecipe,
    files: dict[str, bytes], resources: dict[str, bytes],
    entities: dict[str, RuntimeEntityDefinition], source_commit_sha: str,
    marker_schema: str,
) -> tuple[CompiledDeploymentManifest, DeploymentResolutionMap]:
    """Build canonical storage references; install eligibility belongs to callers."""
    if recipe.solution_id != solution_id:
        raise SolutionSourceRevisionError("workflow recipe belongs to another install")
    storage = SolutionDeploymentStorage(solution_id, deployment_id)
    sources = {
        path: RuntimeSourceResolution(
            object_key=f"{storage.runtime_prefix}{path}", content_hash=sha256_digest(content),
        ) for path, content in files.items()
    }
    resource_map = {
        path: RuntimeResourceResolution(
            object_key=f"{storage.runtime_prefix}_resources/{path}",
            content_hash=sha256_digest(content), size_bytes=len(content),
        ) for path, content in resources.items()
    }
    resolution = DeploymentResolutionMap(
        workflows=entities, sources=sources, shared_tables=recipe.shared_tables,
        root_file_bindings=recipe.root_file_bindings, resources=resource_map,
    )
    hashes = {path: item.content_hash for path, item in {**sources, **resource_map}.items()}
    manifest = CompiledDeploymentManifest(
        solution_id=solution_id, deployment_id=deployment_id,
        bundle_hash=sha256_digest(canonical_json({
            "schema_version": marker_schema,
            "reviewed_recipe": recipe.model_dump(mode="json"),
            "source_commit_sha": source_commit_sha, "source_hashes": hashes,
        })),
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(
            artifact_key=storage.source_artifact_key, runtime_prefix=storage.runtime_prefix,
        ),
        workflows=entities, shared_tables=resolution.shared_tables,
        root_file_bindings=resolution.root_file_bindings, resources=resource_map,
        git=DeploymentGitProvenance(commit_sha=source_commit_sha),
    )
    return manifest, resolution


async def write_reviewed_artifact(
    storage: SolutionDeploymentStorage, files: dict[str, bytes], resources: dict[str, bytes],
) -> None:
    """Write create-only bytes; the deployment API finalizes the manifest separately."""
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


async def inspect_reviewed_artifact(
    solution_id: UUID, deployment_id: UUID, deployment: SolutionDeployment,
    recipe: ReviewedWorkflowRecipe, indexer: WorkflowParameterCompiler, marker_schema: str, *,
    has_owned_tables: bool = False,
    legacy_parameter_hashes: dict[UUID, str] | None = None,
    legacy_descriptor_snapshots: dict[UUID, dict] | None = None,
) -> tuple[CompiledDeploymentManifest, DeploymentResolutionMap]:
    """Recompile fresh archive bytes and compare the complete immutable document."""
    try:
        manifest, resolution = validate_runtime_closure(
            deployment.compiled_manifest, deployment.resolution_map, deployment.dependencies,
            expected_manifest_hash=deployment.compiled_manifest_hash,
            expected_resolution_hash=deployment.resolution_map_hash,
        )
    except ValueError as exc:
        raise SolutionSourceRevisionError("reviewed workflow candidate closure is invalid") from exc
    if manifest.git.commit_sha is None or deployment.git_commit_sha != manifest.git.commit_sha:
        raise SolutionSourceRevisionError("candidate has no reviewed source commit")
    storage = SolutionDeploymentStorage(solution_id, deployment_id)
    if (deployment.source_artifact_key != storage.source_artifact_key
            or deployment.runtime_storage_prefix != storage.runtime_prefix):
        raise SolutionSourceRevisionError("candidate storage reference is not canonical")
    files = _archive_files(await storage.read_source_artifact(), set(resolution.sources))
    resources = await read_deployment_resources(solution_id, deployment_id, resolution)
    entities = compile_reviewed_workflows(
        recipe, files, resources, indexer, has_owned_tables=has_owned_tables,
        legacy_parameter_hashes=legacy_parameter_hashes,
        legacy_descriptor_snapshots=legacy_descriptor_snapshots,
    )
    expected_manifest, expected_resolution = build_reviewed_artifact(
        solution_id, deployment_id, recipe, files, resources, entities,
        manifest.git.commit_sha, marker_schema,
    )
    if manifest != expected_manifest or resolution != expected_resolution:
        raise SolutionSourceRevisionConflict("candidate manifest or bundle differs from reviewed recipe")
    if await storage.read_compiled_manifest() != expected_manifest.canonical_bytes():
        raise SolutionSourceRevisionError("stored manifest differs from candidate")
    for path, content in files.items():
        if await storage.read_runtime_file(path) != content:
            raise SolutionSourceRevisionError("source archive or runtime bytes differ from reviewed evidence")
    return manifest, resolution
