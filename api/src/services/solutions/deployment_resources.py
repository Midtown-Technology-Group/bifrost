"""Read reviewed resource bytes using the requesting worker's durable runtime pin."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from src.core.solution_delivery_policy import delivery_path
from src.models.orm.executions import Execution
from src.models.orm.solutions import Solution
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import sha256_digest, validate_runtime_closure
from src.services.solutions.deployment_runtime import (
    DeploymentRuntimeError, _pin_from_deployment, verify_runtime_evidence,
)
from src.services.solutions.deployment_storage import (
    DeploymentArtifactIntegrityError, SolutionDeploymentStorage,
)

if TYPE_CHECKING:
    from src.core.auth import ExecutionContext


class DeploymentResourceDenied(ValueError):
    """No active, verified execution authorizes this immutable resource read."""


async def read_execution_resource(ctx: ExecutionContext, path: str) -> bytes:
    """The signed execution chooses the deployment; request selectors cannot."""
    from src.core.auth import _active_engine_attempt

    delivery_path(path)
    user = ctx.user
    if (not user.is_engine_token or user.engine_execution_id is None
            or user.engine_attempt_token is None or user.engine_solution_id is None):
        raise DeploymentResourceDenied("An active Solution workflow attempt is required")
    try:
        solution_id = UUID(user.engine_solution_id)
    except ValueError as exc:
        raise DeploymentResourceDenied("Signed Solution identity is invalid") from exc
    if ctx.solution_id is not None and ctx.solution_id != str(solution_id):
        raise DeploymentResourceDenied("Resource target differs from the signed Solution")
    execution = await ctx.db.get(Execution, user.engine_execution_id, populate_existing=True)
    if (execution is None or execution.runtime_mode != "deployment-v1"
            or execution.solution_deployment_id is None or execution.workflow_id is None
            or execution.organization_id != ctx.org_id
            or not await _active_engine_attempt(ctx.db, execution.id, user.engine_attempt_token)):
        raise DeploymentResourceDenied("Execution attempt or durable deployment is no longer valid")
    deployment = await SolutionDeploymentRepository(ctx.db).get_by_id_for_runtime(execution.solution_deployment_id)
    solution = await ctx.db.get(Solution, solution_id, populate_existing=True)
    if (deployment is None or deployment.solution_id != solution_id
            or solution is None or solution.status != "active"
            or deployment.organization_id != solution.organization_id
            or deployment.organization_id not in {None, ctx.org_id}):
        raise DeploymentResourceDenied("Resource deployment owner or organization is invalid")
    try:
        manifest, resolution = validate_runtime_closure(
            deployment.compiled_manifest, deployment.resolution_map, deployment.dependencies,
            expected_manifest_hash=deployment.compiled_manifest_hash,
            expected_resolution_hash=deployment.resolution_map_hash,
        )
        pin = _pin_from_deployment(execution.workflow_id, solution, deployment, allow_superseded=True)
        verify_runtime_evidence(str(deployment.id), execution.runtime_evidence,
            execution.runtime_evidence, execution.runtime_evidence_hash, pin.queue_evidence())
    except (ValueError, KeyError, DeploymentRuntimeError) as exc:
        raise DeploymentResourceDenied("Durable resource evidence changed") from exc
    if manifest.solution_id != solution_id or manifest.deployment_id != deployment.id:
        raise DeploymentResourceDenied("Resource manifest identity changed")
    resource = resolution.resources.get(path)
    if resource is None:
        raise FileNotFoundError("Resource is absent from this execution's deployment")
    try:
        content = await SolutionDeploymentStorage(solution_id, deployment.id).read_resource(path, resource.size_bytes)
    except DeploymentArtifactIntegrityError as exc:
        raise DeploymentResourceDenied("Resource object differs from its pinned size") from exc
    if sha256_digest(content) != resource.content_hash:
        raise DeploymentResourceDenied("Resource object differs from its pinned hash")
    return content
