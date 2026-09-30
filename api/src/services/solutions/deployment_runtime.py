"""Execution-time resolution against an immutable Solution deployment closure."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow
from src.models.orm.solutions import Solution
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import validate_runtime_closure


class DeploymentRuntimeError(RuntimeError):
    """A Solution execution could not prove its immutable runtime closure."""


def verify_runtime_evidence(
    deployment_id: str,
    queued: dict[str, Any] | None,
    durable: dict[str, Any] | None,
    durable_hash: str | None,
    authoritative: dict[str, Any],
) -> None:
    """Require queue, database, and immutable-manifest evidence to be identical."""
    from src.services.solutions.deployment_manifest import canonical_json, sha256_digest

    if not isinstance(queued, dict) or queued != durable:
        raise DeploymentRuntimeError("queue evidence differs from durable pin")
    if queued.get("solution_deployment_id") != deployment_id:
        raise DeploymentRuntimeError("pinned execution deployment evidence mismatch")
    if sha256_digest(canonical_json(queued)) != durable_hash:
        raise DeploymentRuntimeError("durable runtime evidence hash mismatch")
    if queued != authoritative:
        raise DeploymentRuntimeError(
            "runtime evidence differs from immutable deployment manifest"
        )


@dataclass(frozen=True)
class PinnedWorkflowRuntime:
    workflow_id: UUID
    solution_id: UUID
    deployment_id: UUID
    bundle_hash: str
    compiled_manifest_hash: str
    git_commit_sha: str | None
    runtime_storage_prefix: str
    portable_ref: str
    name: str
    function_name: str
    path: str
    source_hash: str
    timeout_seconds: int
    time_saved: int
    value: float
    execution_mode: str
    workflow_type: str
    cache_ttl_seconds: int
    organization_id: str | None
    can_access_global_repo: bool
    source_hashes: dict[str, str]
    runtime_bounds: dict[str, int] | None = None
    parameters_schema: dict[str, Any] | None = None

    def queue_evidence(self) -> dict[str, Any]:
        evidence = {
            "solution_id": str(self.solution_id),
            "solution_deployment_id": str(self.deployment_id),
            "bundle_hash": self.bundle_hash,
            "compiled_manifest_hash": self.compiled_manifest_hash,
            "git_commit_sha": self.git_commit_sha,
            "runtime_storage_prefix": self.runtime_storage_prefix,
            "workflow_portable_ref": self.portable_ref,
            "workflow_name": self.name,
            "workflow_function_name": self.function_name,
            "workflow_path": self.path,
            "workflow_source_hash": self.source_hash,
            "workflow_timeout_seconds": self.timeout_seconds,
            "workflow_time_saved": self.time_saved,
            "workflow_value": self.value,
            "workflow_execution_mode": self.execution_mode,
            "workflow_type": self.workflow_type,
            "workflow_cache_ttl_seconds": self.cache_ttl_seconds,
            "workflow_organization_id": self.organization_id,
            "solution_global_repo_access": self.can_access_global_repo,
            "deployment_source_hashes": self.source_hashes,
        }
        if self.runtime_bounds is not None:
            evidence["workflow_runtime_bounds"] = self.runtime_bounds
        if self.parameters_schema is not None:
            evidence["workflow_parameters_schema"] = self.parameters_schema
        return evidence


def _validated_runtime_bounds(value: Any) -> dict[str, int]:
    required = {
        "max_duration_seconds",
        "max_external_calls",
        "max_records_read",
        "max_output_bytes",
    }
    if not isinstance(value, dict) or not required.issubset(value):
        raise DeploymentRuntimeError("deployment runtime bounds are incomplete")
    if any(
        not isinstance(key, str)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit <= 0
        for key, limit in value.items()
    ):
        raise DeploymentRuntimeError("deployment runtime bounds are invalid")
    return dict(sorted(value.items()))


def workflow_data_from_evidence(
    deployment_id: str, evidence: dict[str, Any] | None
) -> dict[str, Any]:
    """Validate trusted queue evidence and expose the legacy consumer shape."""
    if not isinstance(evidence, dict):
        raise DeploymentRuntimeError("pinned execution is missing runtime evidence")
    if evidence.get("solution_deployment_id") != deployment_id:
        raise DeploymentRuntimeError("pinned execution deployment evidence mismatch")
    required = (
        "workflow_name", "workflow_function_name", "workflow_path",
        "workflow_source_hash", "solution_id", "runtime_storage_prefix",
    )
    if any(not evidence.get(key) for key in required):
        raise DeploymentRuntimeError("pinned execution runtime evidence is incomplete")
    data = {
        "name": evidence["workflow_name"],
        "function_name": evidence["workflow_function_name"],
        "path": evidence["workflow_path"],
        "content_hash": evidence["workflow_source_hash"],
        "type": evidence.get("workflow_type", "workflow"),
        "cache_ttl_seconds": evidence.get("workflow_cache_ttl_seconds", 0),
        "timeout_seconds": evidence.get("workflow_timeout_seconds", 1800),
        "time_saved": evidence.get("workflow_time_saved", 0),
        "value": evidence.get("workflow_value", 0),
        "solution_id": evidence["solution_id"],
        "organization_id": evidence.get("workflow_organization_id"),
        "can_access_global_repo": evidence.get("solution_global_repo_access", False),
        "runtime_storage_prefix": evidence["runtime_storage_prefix"],
    }
    if "workflow_runtime_bounds" in evidence:
        bounds = _validated_runtime_bounds(evidence["workflow_runtime_bounds"])
        timeout = data["timeout_seconds"]
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or timeout <= 0
            or timeout > bounds["max_duration_seconds"]
        ):
            raise DeploymentRuntimeError("deployment timeout exceeds immutable bound")
        data["workflow_runtime_bounds"] = bounds
    return data


def _required(definition: dict[str, Any], key: str) -> Any:
    value = definition.get(key)
    if value is None or value == "":
        raise DeploymentRuntimeError(f"compiled workflow definition missing {key}")
    return value


def _number(definition: dict[str, Any], key: str, default: int | float) -> int | float:
    value = definition.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DeploymentRuntimeError(f"compiled workflow definition has invalid {key}")
    return value


async def pin_workflow_runtime(
    session: AsyncSession,
    workflow_id: UUID,
    *,
    caller_deployment_id: UUID | None = None,
) -> PinnedWorkflowRuntime | None:
    """Return immutable evidence for a Solution workflow; None means `_repo`."""
    row = (
        await session.execute(
            select(Workflow, Solution)
            .outerjoin(Solution, Workflow.solution_id == Solution.id)
            .where(Workflow.id == workflow_id, Workflow.is_active.is_(True))
        )
    ).one_or_none()
    if row is None:
        raise DeploymentRuntimeError(f"workflow {workflow_id} is not executable")
    workflow, solution = row
    if workflow.solution_id is None:
        if caller_deployment_id is None:
            return None
        return await _pin_rolled_back_handoff_runtime(
            session, workflow_id, caller_deployment_id
        )
    if solution is None or solution.status != "active":
        raise DeploymentRuntimeError("Solution is not active")
    selected_deployment_id = await _select_deployment_id(
        session, workflow.solution_id, solution.active_deployment_id, caller_deployment_id
    )
    if selected_deployment_id is None:
        if solution.execution_runtime_mode == "repo-v1":
            return None
        raise DeploymentRuntimeError(
            "Solution has no active deployment; capture an initial deployment before execution"
        )

    deployment = await SolutionDeploymentRepository(session).get_runtime_closure(
        selected_deployment_id, solution.organization_id
    )
    if deployment is None or deployment.solution_id != solution.id:
        raise DeploymentRuntimeError("active deployment is missing or out of scope")
    return _pin_from_deployment(
        workflow_id, solution, deployment, allow_superseded=caller_deployment_id is not None
    )


def _pin_from_deployment(
    workflow_id: UUID,
    solution: Solution,
    deployment: Any,
    *,
    allow_superseded: bool,
) -> PinnedWorkflowRuntime:
    from src.services.solutions.deployment_manifest import canonical_json
    executable_states = {"active", "committed_unpushed"}
    if allow_superseded:
        executable_states.add("superseded")
    if deployment.state not in executable_states:
        raise DeploymentRuntimeError("active deployment is not executable")
    _, resolution = validate_runtime_closure(
        deployment.compiled_manifest,
        deployment.resolution_map,
        deployment.dependencies,
        expected_manifest_hash=deployment.compiled_manifest_hash,
        expected_resolution_hash=deployment.resolution_map_hash,
    )
    entity = _resolve_workflow_entity(resolution, workflow_id)
    definition = entity.definition
    runtime_bounds = (
        _validated_runtime_bounds(definition["runtime_bounds"])
        if "runtime_bounds" in definition
        else None
    )
    if (
        runtime_bounds is not None
        and int(_number(definition, "timeout_seconds", 1800))
        > runtime_bounds["max_duration_seconds"]
    ):
        raise DeploymentRuntimeError("deployment timeout exceeds immutable bound")
    source_ref = entity.source_ref or str(_required(definition, "path"))
    try:
        source = resolution.resolve_source(source_ref)
    except KeyError as exc:
        raise DeploymentRuntimeError(f"source {source_ref} is unresolved") from exc
    runtime_prefix = deployment.runtime_storage_prefix.rstrip("/") + "/"
    workflow_path = str(_required(definition, "path")).lstrip("/")
    if source.object_key != f"{runtime_prefix}{workflow_path}":
        raise DeploymentRuntimeError(
            "workflow source is outside deployment runtime prefix"
        )
    return PinnedWorkflowRuntime(
        workflow_id=workflow_id,
        solution_id=solution.id,
        deployment_id=deployment.id,
        bundle_hash=deployment.bundle_hash,
        compiled_manifest_hash=deployment.compiled_manifest_hash,
        git_commit_sha=deployment.git_commit_sha,
        runtime_storage_prefix=runtime_prefix,
        portable_ref=entity.portable_ref,
        name=str(_required(definition, "name")),
        function_name=str(_required(definition, "function_name")),
        path=workflow_path,
        source_hash=source.content_hash,
        timeout_seconds=int(_number(definition, "timeout_seconds", 1800)),
        time_saved=int(_number(definition, "time_saved", 0)),
        value=float(_number(definition, "value", 0)),
        execution_mode=str(definition.get("execution_mode") or "async"),
        workflow_type=str(definition.get("type") or "workflow"),
        cache_ttl_seconds=int(_number(definition, "cache_ttl_seconds", 0)),
        organization_id=(
            str(definition["organization_id"])
            if definition.get("organization_id")
            else None
        ),
        can_access_global_repo=bool(solution.allow_outbound_access),
        source_hashes={key: item.content_hash for key, item in resolution.sources.items()},
        runtime_bounds=runtime_bounds,
        parameters_schema=(json.loads(canonical_json(definition["parameters_schema"]))
            if isinstance(definition.get("parameters_schema"), dict) else None),
    )


async def _pin_rolled_back_handoff_runtime(
    session: AsyncSession, workflow_id: UUID, deployment_id: UUID
) -> PinnedWorkflowRuntime | None:
    deployment = await SolutionDeploymentRepository(session).get_by_id_for_runtime(
        deployment_id
    )
    if deployment is None:
        return None
    marker = getattr(deployment, "validation_result", None) or {}
    if (
        marker.get("schema_version") != "bifrost.workspace-live-handoff/v1"
        or str(workflow_id) not in marker.get("workflow_ids", [])
    ):
        return None
    solution = await session.get(Solution, deployment.solution_id)
    if solution is None or solution.status != "active":
        raise DeploymentRuntimeError("handoff Solution is not active")
    return _pin_from_deployment(
        workflow_id, solution, deployment, allow_superseded=True
    )


async def _select_deployment_id(
    session: AsyncSession,
    workflow_solution_id: UUID,
    active_deployment_id: UUID | None,
    caller_deployment_id: UUID | None,
) -> UUID | None:
    if caller_deployment_id is None:
        return active_deployment_id
    caller = await SolutionDeploymentRepository(session).get_by_id_for_runtime(
        caller_deployment_id
    )
    if caller is None:
        raise DeploymentRuntimeError("caller deployment is missing")
    if workflow_solution_id == caller.solution_id:
        return caller.id
    edge = next(
        (
            item
            for item in caller.dependencies
            if item.dependency_solution_id == workflow_solution_id
        ),
        None,
    )
    if edge is None:
        raise DeploymentRuntimeError(
            "cross-Solution workflow is not pinned by the caller deployment"
        )
    return edge.dependency_deployment_id


def _resolve_workflow_entity(resolution: Any, workflow_id: UUID) -> Any:
    try:
        return resolution.resolve_workflow_id(workflow_id)
    except KeyError as exc:
        raise DeploymentRuntimeError(
            f"workflow {workflow_id} is absent from active deployment"
        ) from exc


async def resolve_pinned_workflow_runtime(
    session: AsyncSession, deployment_id: UUID, workflow_id: UUID
) -> PinnedWorkflowRuntime:
    """Resolve accepted work from its deployment, including retired registrations.

    Current registration activity controls new admissions. An accepted execution
    instead retains the immutable definition it already pinned. Ownership and
    Solution status still gate that read; only a certified handoff rollback may
    resolve a registration that has returned to Root.
    """
    row = (
        await session.execute(
            select(Workflow, Solution)
            .outerjoin(Solution, Workflow.solution_id == Solution.id)
            .where(Workflow.id == workflow_id)
        )
    ).one_or_none()
    if row is None:
        raise DeploymentRuntimeError("workflow does not belong to the pinned deployment")
    workflow, solution = row
    if workflow.solution_id is None:
        pinned = await _pin_rolled_back_handoff_runtime(session, workflow_id, deployment_id)
        if pinned is None or pinned.deployment_id != deployment_id:
            raise DeploymentRuntimeError("workflow does not belong to the pinned deployment")
        return pinned
    if solution is None or solution.status != "active":
        raise DeploymentRuntimeError("Solution is not active")
    deployment = await SolutionDeploymentRepository(session).get_runtime_closure(
        deployment_id, solution.organization_id, solution.id
    )
    if deployment is None or deployment.solution_id != solution.id:
        raise DeploymentRuntimeError("workflow does not belong to the pinned deployment")
    return _pin_from_deployment(workflow_id, solution, deployment, allow_superseded=True)
