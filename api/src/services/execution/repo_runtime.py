"""Durable source evidence for mutable ``repo-v1`` workflow executions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.module_cache import wait_for_workspace_generation
from src.models.orm.file_index import FileIndex
from src.models.orm.workflows import Workflow
from src.services.solutions.deployment_manifest import canonical_json, sha256_digest

REPO_RUNTIME_SCHEMA = "bifrost.repo-workflow-runtime/v1"


class RepoRuntimeError(RuntimeError):
    """The mutable repository source cannot be pinned or verified safely."""


@dataclass(frozen=True)
class PinnedRepoWorkflowRuntime:
    """Stable source identity captured before a repo workflow is queued."""

    workflow_id: UUID
    name: str
    function_name: str
    path: str
    source_hash: str
    workspace_generation: str
    solution_id: str | None = None
    parameters_schema: None = None

    @property
    def runtime_mode(self) -> str:
        return "repo-v1"

    def queue_evidence(self) -> dict[str, str]:
        evidence = {
            "schema_version": REPO_RUNTIME_SCHEMA,
            "workflow_id": str(self.workflow_id),
            "workflow_name": self.name,
            "workflow_function_name": self.function_name,
            "workflow_path": self.path,
            "workflow_source_hash": self.source_hash,
            "workspace_generation": self.workspace_generation,
        }
        if self.solution_id is not None:
            evidence["solution_id"] = self.solution_id
        return evidence


async def pin_repo_workflow_runtime(
    session: AsyncSession,
    workflow_id: UUID,
) -> PinnedRepoWorkflowRuntime:
    """Capture source bytes and generation without crossing a workspace update."""
    for _attempt in range(3):
        generation = await wait_for_workspace_generation()
        row = (
            await session.execute(
                select(Workflow, FileIndex)
                .outerjoin(FileIndex, FileIndex.path == Workflow.path)
                .where(
                    Workflow.id == workflow_id,
                    Workflow.is_active.is_(True),
                )
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if await wait_for_workspace_generation() != generation:
            continue
        if row is None:
            raise RepoRuntimeError(
                "repo workflow has no active registration with indexed source"
            )

        workflow, source = row
        solution_id = workflow.solution_id
        if solution_id is not None:
            from src.services.solutions.storage import SolutionStorage

            content = await SolutionStorage(solution_id).read(workflow.path)
            actual_hash = hashlib.sha256(content).hexdigest()
            if await wait_for_workspace_generation() != generation:
                continue
        else:
            if source is None or source.content is None or not source.content_hash:
                raise RepoRuntimeError("repo workflow source has no integrity hash")
            actual_hash = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
            if actual_hash != source.content_hash:
                raise RepoRuntimeError("repo workflow source integrity mismatch")
        return PinnedRepoWorkflowRuntime(
            workflow_id=workflow.id,
            name=workflow.name,
            function_name=workflow.function_name,
            path=workflow.path,
            source_hash=actual_hash,
            workspace_generation=generation,
            solution_id=str(solution_id) if solution_id is not None else None,
        )

    raise RepoRuntimeError("workspace source changed while execution was admitted")


def verify_repo_runtime_evidence(
    queued: object,
    durable: object,
    durable_hash: str | None,
) -> dict[str, str]:
    """Verify queued repo evidence against its durable execution pin."""
    if not isinstance(queued, dict) or queued != durable:
        raise RepoRuntimeError("queued repo runtime evidence differs from durable pin")
    if durable_hash != sha256_digest(canonical_json(queued)):
        raise RepoRuntimeError("repo runtime evidence hash mismatch")
    required = {
        "workflow_id",
        "workflow_name",
        "workflow_function_name",
        "workflow_path",
        "workflow_source_hash",
        "workspace_generation",
    }
    if queued.get("schema_version") != REPO_RUNTIME_SCHEMA or any(
        not isinstance(queued.get(key), str) or not queued[key]
        for key in required
    ):
        raise RepoRuntimeError("repo runtime evidence is incomplete")
    source_hash = str(queued["workflow_source_hash"])
    if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
        raise RepoRuntimeError("repo workflow source hash is invalid")
    return queued


def workflow_data_from_repo_evidence(
    workflow_data: dict[str, Any],
    evidence: dict[str, str],
    workflow_id: str,
) -> dict[str, Any]:
    """Bind current metadata to the source identity accepted at enqueue time."""
    if evidence["workflow_id"] != workflow_id:
        raise RepoRuntimeError("repo runtime workflow id mismatch")
    if workflow_data.get("solution_id") != evidence.get("solution_id"):
        raise RepoRuntimeError("repo runtime Solution scope changed after admission")
    comparisons = {
        "name": "workflow_name",
        "function_name": "workflow_function_name",
        "path": "workflow_path",
        "content_hash": "workflow_source_hash",
    }
    if any(
        str(workflow_data.get(current) or "") != evidence[pinned]
        for current, pinned in comparisons.items()
    ):
        raise RepoRuntimeError(
            "repo workflow registration or source changed after execution admission"
        )
    return {
        **workflow_data,
        "content_hash": evidence["workflow_source_hash"],
        "workspace_generation": evidence["workspace_generation"],
    }
