"""Root data access from the immutable grant of an active engine attempt."""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import aclosing
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import UUID

from bifrost.root_file_bindings import RootFileBinding, RootFileOperation, root_data_path

if TYPE_CHECKING:
    from src.core.auth import ExecutionContext

logger = logging.getLogger(__name__)


class RootFileBindingError(ValueError):
    """A reviewed Root data request failed its runtime grant."""


@dataclass(frozen=True)
class ResolvedRootFileAccess:
    """Private server authority. Never accepted from a request or serialized."""

    context: ExecutionContext
    binding: RootFileBinding
    binding_name: str
    execution_id: UUID
    deployment_id: UUID

    def validate_bytes(self, content: bytes, *, create: bool = False) -> None:
        if len(content) > self.binding.max_bytes:
            raise RootFileBindingError("Root file exceeds the reviewed byte bound")
        if create:
            try:
                value = json.loads(content)
            except (ValueError, UnicodeDecodeError) as exc:
                raise RootFileBindingError("Root backup must be a JSON object") from exc
            if not isinstance(value, dict):
                raise RootFileBindingError("Root backup must be a JSON object")
        elif self.binding.expected_read_sha256 is not None:
            if "sha256:" + hashlib.sha256(content).hexdigest() != self.binding.expected_read_sha256:
                raise RootFileBindingError("Root file bytes differ from the reviewed hash")

    def audit(self, path: str, operation: RootFileOperation) -> None:
        # Repair backups and presigned URLs can contain credentials. Neither
        # their contents nor their URL or filename is an audit field.
        logger.info(
            "Reviewed Root file access",
            extra={
                "execution_id": str(self.execution_id),
                "deployment_id": str(self.deployment_id),
                "binding_name": self.binding_name,
                "operation": operation,
                "path_sha256": hashlib.sha256(path.encode()).hexdigest(),
                "organization_id": str(self.context.org_id) if self.context.org_id else None,
            },
        )


async def resolve_execution_root_file(
    ctx: ExecutionContext, *, location: str, path: str,
    operation: RootFileOperation, scope: str | None, mode: str = "cloud",
) -> ResolvedRootFileAccess | None:
    """Verify the signed attempt and its durable pin before selecting Root."""
    from src.core.auth import _active_engine_attempt
    from src.models.orm.executions import Execution
    from src.models.orm.solutions import Solution
    from src.repositories.solution_deployments import SolutionDeploymentRepository
    from src.services.solution_scope import parse_ctx_solution_id
    from src.services.solutions.deployment_manifest import validate_runtime_closure
    from src.services.solutions.deployment_runtime import (
        DeploymentRuntimeError, _pin_from_deployment, verify_runtime_evidence,
    )

    user = ctx.user
    if user is None:
        return None
    target = parse_ctx_solution_id(ctx)
    if user.is_engine_token and user.engine_solution_id is not None and target is None:
        raise RootFileBindingError("Solution engines require their signed file context")
    if (
        not user.is_engine_token or user.engine_execution_id is None
        or target is None or str(target) != user.engine_solution_id
    ):
        return None
    execution = await ctx.db.get(Execution, user.engine_execution_id, populate_existing=True)
    if execution is None or execution.runtime_mode != "deployment-v1":
        return None
    if (
        user.engine_attempt_token is None
        or not await _active_engine_attempt(ctx.db, execution.id, user.engine_attempt_token)
        or execution.solution_deployment_id is None
        or execution.organization_id != ctx.org_id
    ):
        raise RootFileBindingError("Root file execution attempt is no longer active")
    deployment = await SolutionDeploymentRepository(ctx.db).get_by_id_for_runtime(
        execution.solution_deployment_id
    )
    solution = await ctx.db.get(Solution, target, populate_existing=True)
    if (
        deployment is None or deployment.solution_id != target
        or solution is None or solution.status != "active"
        or deployment.organization_id != solution.organization_id
        or deployment.organization_id not in {None, ctx.org_id}
        or execution.workflow_id is None
    ):
        raise RootFileBindingError("Root file deployment binding is invalid")
    try:
        manifest, _ = validate_runtime_closure(
            deployment.compiled_manifest, deployment.resolution_map, deployment.dependencies,
            expected_manifest_hash=deployment.compiled_manifest_hash,
            expected_resolution_hash=deployment.resolution_map_hash,
        )
        pin = _pin_from_deployment(execution.workflow_id, solution, deployment, allow_superseded=True)
        verify_runtime_evidence(
            str(deployment.id), execution.runtime_evidence, execution.runtime_evidence,
            execution.runtime_evidence_hash, pin.queue_evidence(),
        )
    except (ValueError, KeyError, DeploymentRuntimeError) as exc:
        raise RootFileBindingError("Root file deployment evidence changed") from exc
    if manifest.solution_id != target or manifest.deployment_id != deployment.id:
        raise RootFileBindingError("Root file manifest identity changed")
    for name, binding in manifest.root_file_bindings.items():
        if binding.location != location:
            continue
        try:
            permitted = binding.permits(path, operation)
        except ValueError:
            # An unsafe Root selector receives no grant. Existing Solution
            # tiers still own validation of their ordinary logical paths.
            continue
        if not permitted:
            continue
        allowed_scope = str(execution.organization_id) if execution.organization_id else "global"
        if mode != "cloud" or scope not in {None, allowed_scope}:
            raise RootFileBindingError("Root files require the durable execution scope and cloud backend")
        # Keep the signed identity and exact execution organization. Existing
        # Root policy checks run on this private context, with no fallback.
        root_context = replace(ctx, app_id=None, solution_id=None, caller_solution_id=None)
        return ResolvedRootFileAccess(root_context, binding, name, execution.id, deployment.id)
    return None


async def read_reviewed_root_bytes(
    access: ResolvedRootFileAccess, path: str, *, verify_hash: bool = True,
    retain_bytes: bool = True,
) -> bytes:
    """Stream a current Root object with a bound, after the caller checks policy."""
    scope = str(access.context.org_id) if access.context.org_id else "global"
    return await _read_root_bound_bytes(
        access.context.db, access.binding, path, scope=scope,
        verify_hash=verify_hash, retain_bytes=retain_bytes,
    )


async def require_root_workspace_files(db, bindings: dict[str, RootFileBinding]) -> None:
    """Validate required assets inside the existing administrator delivery path.

    This returns no data and grants no runtime authority. Workspace file policy
    already requires the administrator protecting these delivery endpoints.
    Dynamic upload objects and create-only backup prefixes are not enumerated.
    """
    for binding in bindings.values():
        if binding.location != "workspace" or binding.directory_prefix:
            continue
        try:
            await _read_root_bound_bytes(db, binding, binding.path, scope="global", retain_bytes=False)
        except FileNotFoundError as exc:
            raise RootFileBindingError("Required reviewed Root workspace asset is missing") from exc


async def _read_root_bound_bytes(
    db, binding: RootFileBinding, path: str, *, scope: str,
    verify_hash: bool = True, retain_bytes: bool = True,
) -> bytes:
    from shared.file_paths import resolve_s3_key
    from src.services.file_storage import FileStorageService

    root_data_path(path)
    key = resolve_s3_key(binding.location, scope, path)
    size = 0
    digest = hashlib.sha256()
    parts = []
    async with aclosing(FileStorageService(db).iter_raw_s3_chunks(
        key, chunk_size=min(binding.max_bytes + 1, 64 * 1024)
    )) as chunks:
        async for chunk in chunks:
            size += len(chunk)
            if size > binding.max_bytes:
                raise RootFileBindingError("Root file exceeds the reviewed byte bound")
            digest.update(chunk)
            if retain_bytes:
                parts.append(chunk)
    if verify_hash and binding.expected_read_sha256 is not None:
        if "sha256:" + digest.hexdigest() != binding.expected_read_sha256:
            raise RootFileBindingError("Root file bytes differ from the reviewed hash")
    return b"".join(parts)
