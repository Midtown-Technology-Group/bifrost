"""Complete reviewed resources, static SDK references and immutable byte readback."""

from __future__ import annotations

from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZIP_STORED, ZipFile

from src.services.solutions.deployment_manifest import (
    MAX_DEPLOYMENT_RESOURCES_BYTES, DeploymentResolutionMap, sha256_digest,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.source_revision import SolutionSourceRevisionError
from bifrost.solution_delivery_review import (
    ReviewedWorkflowRecipe, WorkflowRecipeError, validate_resource_files as _validate_resource_files,
)


def validate_resource_files(recipe: ReviewedWorkflowRecipe, resources: dict[str, bytes],
                            files: dict[str, bytes]) -> None:
    try:
        _validate_resource_files(recipe, resources, files)
    except WorkflowRecipeError as exc:
        raise SolutionSourceRevisionError(str(exc)) from exc


async def read_deployment_resources(solution_id: UUID, deployment_id: UUID,
                                    resolution: DeploymentResolutionMap) -> dict[str, bytes]:
    if not resolution.resources:
        return {}
    storage = SolutionDeploymentStorage(solution_id, deployment_id)
    archive = await storage.read_resources_artifact()
    if len(archive) > MAX_DEPLOYMENT_RESOURCES_BYTES + 2 * 1024 * 1024:
        raise SolutionSourceRevisionError("Resource archive exceeds its byte bound")
    try:
        with ZipFile(BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if (len(members) != len(resolution.resources)
                    or {item.filename for item in members} != set(resolution.resources)
                    or any(item.compress_type != ZIP_STORED or item.is_dir()
                        or item.file_size != resolution.resources[item.filename].size_bytes for item in members)):
                raise SolutionSourceRevisionError("Resource archive members differ from immutable contracts")
            files = {item.filename: bundle.read(item) for item in members}
    except (BadZipFile, RuntimeError) as exc:
        raise SolutionSourceRevisionError("Resource archive is invalid") from exc
    for path, content in files.items():
        contract = resolution.resources[path]
        if (len(content) != contract.size_bytes or sha256_digest(content) != contract.content_hash
                or await storage.read_resource(path, contract.size_bytes) != content):
            raise SolutionSourceRevisionError("Resource archive or runtime bytes differ from immutable evidence")
    return files
