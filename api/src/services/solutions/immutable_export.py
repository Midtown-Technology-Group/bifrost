"""Export source from the active deployment, never from mutable Root storage."""

from __future__ import annotations

import asyncio
import io
import tempfile
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bifrost.solution_source_closure import MAX_ARCHIVE_BYTES
from src.core.solution_delivery_policy import delivery_path
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import (
    DeploymentResolutionMap,
    sha256_digest,
    validate_runtime_closure,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage


class ImmutableSolutionExportError(ValueError):
    """The selected immutable source cannot be exported faithfully."""


async def read_active_source(
    db: AsyncSession,
    solution: Solution,
) -> tuple[dict[str, str], dict[str, bytes]]:
    source, resources, _, _ = await _read_active_source(db, solution)
    return source, resources


async def _read_active_source(
    db: AsyncSession,
    solution: Solution,
) -> tuple[dict[str, str], dict[str, bytes], DeploymentResolutionMap, bytes | None]:
    deployment_id = solution.active_deployment_id
    if solution.execution_runtime_mode != "deployment-v1" or deployment_id is None:
        raise ImmutableSolutionExportError(
            "Solution runtime and active pointer disagree"
        )
    deployment = await SolutionDeploymentRepository(db).get_runtime_closure(
        deployment_id,
        solution.organization_id,
        solution.id,
    )
    if (
        deployment is None
        or deployment.state not in {"active", "committed_unpushed"}
        or deployment.solution_id != solution.id
        or deployment.organization_id != solution.organization_id
    ):
        raise ImmutableSolutionExportError(
            "Active deployment is missing or outside the Solution scope"
        )
    manifest, resolution = validate_runtime_closure(
        deployment.compiled_manifest,
        deployment.resolution_map,
        deployment.dependencies,
        expected_manifest_hash=deployment.compiled_manifest_hash,
        expected_resolution_hash=deployment.resolution_map_hash,
    )
    if manifest.dependencies:
        raise ImmutableSolutionExportError(
            "Solution dependencies require a complete portable dependency export"
        )
    storage = SolutionDeploymentStorage(solution.id, deployment_id)
    if (
        manifest.solution_id != solution.id
        or manifest.deployment_id != deployment_id
        or manifest.bundle_hash != deployment.bundle_hash
        or deployment.source_artifact_key != storage.source_artifact_key
        or deployment.runtime_storage_prefix != storage.runtime_prefix
        or manifest.source.artifact_key != storage.source_artifact_key
        or manifest.source.runtime_prefix != storage.runtime_prefix
        or await storage.read_compiled_manifest() != manifest.canonical_bytes()
    ):
        raise ImmutableSolutionExportError(
            "Active deployment manifest or storage identity changed"
        )
    if manifest.package_evidence is not None:
        from src.services.solutions.package_runtime import readback_package_runtime

        # Complete packages retain manifests, App source and binary assets as
        # authored input. Use the same full runtime/control/pin verifier as
        # publication recovery; do not reconstruct source from mutable captures.
        source = manifest.package_evidence.get("source")
        package = source.get("package") if isinstance(source, dict) else None
        source_digest = package.get("source_archive_sha256") if isinstance(package, dict) else None
        if not isinstance(source_digest, str):
            raise ImmutableSolutionExportError("Complete package source evidence is missing")
        await readback_package_runtime(db, solution.id, deployment_id,
            expected_source_sha256=source_digest, expected_organization_id=solution.organization_id)
        archive = await storage.read_source_artifact()
        if sha256_digest(archive) != "sha256:" + source_digest:
            raise ImmutableSolutionExportError("Complete package source archive changed during export")
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            source_files = {path: bundle.read(path).decode("utf-8")
                for path in bundle.namelist() if path.endswith(".py")}
            resource_files = {path: bundle.read(path) for path in resolution.resources}
        return source_files, resource_files, resolution, archive
    python_files: dict[str, str] = {}
    total = 0
    for path, source in sorted(resolution.sources.items()):
        delivery_path(path)
        if (
            not path.endswith(".py")
            or source.object_key != f"{storage.runtime_prefix}{path}"
        ):
            raise ImmutableSolutionExportError(
                "Source object is outside its immutable deployment"
            )
        content = await storage.read_runtime_file(
            path, max_bytes=MAX_ARCHIVE_BYTES - total
        )
        total += len(content)
        if sha256_digest(content) != source.content_hash:
            raise ImmutableSolutionExportError(f"Immutable source hash differs: {path}")
        python_files[path] = content.decode("utf-8")
    resources: dict[str, bytes] = {}
    for path, resource in sorted(resolution.resources.items()):
        if total + resource.size_bytes > MAX_ARCHIVE_BYTES:
            raise ImmutableSolutionExportError(
                "combined source and resource bytes exceed the archive limit"
            )
        content = await storage.read_resource(path, resource.size_bytes)
        total += len(content)
        if sha256_digest(content) != resource.content_hash:
            raise ImmutableSolutionExportError(
                f"Immutable resource hash differs: {path}"
            )
        resources[path] = content
    return python_files, resources, resolution, None


async def require_export_registration(
    db: AsyncSession,
    solution: Solution,
    resolution: DeploymentResolutionMap,
) -> None:
    """Mutable registrations cannot silently redefine an immutable export."""
    from src.services.solutions.source_revision import _require_registration

    rows = (
        await db.scalars(
            select(Workflow)
            .options(selectinload(Workflow.roles))
            .where(Workflow.solution_id == solution.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    entities = {entity.resolved_id: entity for entity in resolution.workflows.values()}
    if len(entities) != len(resolution.workflows) or {row.id for row in rows} != set(
        entities
    ):
        raise ImmutableSolutionExportError(
            "Owned registration set differs from the active runtime"
        )
    for row in rows:
        if (
            row.solution_id != solution.id
            or row.organization_id != solution.organization_id
        ):
            raise ImmutableSolutionExportError(
                "Owned registration is outside the active Solution scope"
            )
        _require_registration(row, entities[row.id])


async def write_active_export(
    db: AsyncSession,
    solution: Solution,
    destination: Path,
    *,
    include_values: bool,
    include_data: bool,
    include_files: bool,
    password: str | None,
) -> None:
    from src.services.solutions.capture import SolutionCaptureService
    from src.services.solutions.export import (
        add_live_content_to_workspace_zip_file,
        add_source_resources,
        build_workspace_zip_for_export,
    )

    selected_deployment = solution.active_deployment_id
    source_files, resources, resolution, authored_archive = await _read_active_source(db, solution)
    await require_export_registration(db, solution, resolution)
    if authored_archive is not None:
        if include_values or include_data or include_files:
            if not password:
                raise ValueError("backup export requires a password")
            from src.services.solutions.deploy import SolutionBundle

            capture = SolutionCaptureService(db)
            content = SolutionBundle(solution=solution,
                config_values=await capture._config_values(solution) if include_values else {},
                table_data=await capture._table_data(solution) if include_data else {},
                solution_files=await capture._solution_file_entries(solution) if include_files else [])
            with tempfile.TemporaryDirectory(prefix="bifrost-package-export-") as temporary:
                source_path = Path(temporary) / "source.zip"
                await asyncio.to_thread(source_path.write_bytes, authored_archive)
                await add_live_content_to_workspace_zip_file(source_path, content, db, destination,
                    password=password, preserve_source_readme=True)
        else:
            await asyncio.to_thread(destination.write_bytes, authored_archive)
    else:
        bundle = await SolutionCaptureService(db).bundle_for(
            solution,
            source_files=source_files,
            include_values=include_values,
            include_data=include_data,
            include_files=include_files,
        )
        if any(wf.get("path") not in source_files for wf in bundle.workflows):
            raise ImmutableSolutionExportError(
                "Owned workflow is absent from the active source closure"
            )
        await build_workspace_zip_for_export(bundle, db, destination, password=password)
        add_source_resources(destination, resources)
    await require_export_registration(db, solution, resolution)
    await db.refresh(solution)
    if (
        solution.execution_runtime_mode != "deployment-v1"
        or solution.active_deployment_id != selected_deployment
    ):
        raise ImmutableSolutionExportError(
            "Active Solution pointer changed during export"
        )
