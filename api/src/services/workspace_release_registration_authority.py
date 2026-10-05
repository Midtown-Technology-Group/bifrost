"""Authority guard for mutable Workspace workflow registrations."""

from __future__ import annotations

from enum import StrEnum
from typing import Iterable

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.models.orm.workflows import Workflow
from src.services.workspace_release_files import (
    global_active_workspace_release_descriptor,
    normalize_release_path,
)
from src.services.workspace_release_projection import acquire_workspace_release_lock


class WorkspaceRegistrationMutationAuthority(StrEnum):
    """Explicit authorities that may mutate Workspace registration rows."""

    EXTERNAL = "external"
    RELEASE_ACTIVATION = "release_activation"


class WorkspaceReleaseRegistrationGoverned(RuntimeError):
    """An external mutation targeted registration state owned by Live."""

    def __init__(self, reference: str, release_id: str, operation: str):
        self.reference = reference
        self.release_id = release_id
        self.operation = operation
        super().__init__(
            f"workflow registration {reference!r} is governed by active "
            f"workspace-release-v1 {release_id}; {operation} must use a reviewed "
            "Workspace release"
        )


class WorkflowRegistrationRetired(WorkspaceReleaseRegistrationGoverned):
    """A retained terminal registration cannot acquire another lifecycle."""

    def __init__(self, workflow: Workflow, operation: str):
        self.reference = f"{workflow.path}::{workflow.function_name}"
        evidence = workflow.retirement_evidence
        self.release_id = evidence.get("release_id", "retired") if isinstance(evidence, dict) else "retired"
        self.operation = operation
        RuntimeError.__init__(self,
            f"workflow registration {self.reference!r} ({workflow.id}) was permanently "
            f"retired; {operation} cannot mutate or recreate it")


async def _guard_retired_registrations(
    db: AsyncSession, workflows: tuple[object, ...], operation: str,
) -> None:
    """Reject terminal UUIDs, exact identities and reminted aliases of them."""
    identities = []
    workflow_ids = []
    for workflow in workflows:
        workflow_id = getattr(workflow, "id", None)
        if workflow_id is not None:
            workflow_ids.append(workflow_id)
        path = getattr(workflow, "path", None)
        function_name = getattr(workflow, "function_name", None)
        if (path is not None and function_name is not None
                and getattr(workflow, "solution_id", None) is None):
            organization_id = getattr(workflow, "organization_id", None)
            identities.append(and_(
                Workflow.solution_id.is_(None), Workflow.path == path,
                Workflow.function_name == function_name,
                Workflow.organization_id == organization_id,
            ))
            normalized = path.replace("\\", "/").lstrip("/")
            persisted = aliased(Workflow)
            # A distinct stored UUID at the same native scope may survive an
            # obsolete legacy alias. A caller-supplied fresh UUID cannot.
            survivor = exists(select(persisted.id).where(
                persisted.id == workflow_id, persisted.solution_id.is_(None),
                persisted.organization_id == organization_id,
                persisted.function_name == function_name,
                func.ltrim(func.replace(persisted.path, "\\", "/"), "/") == normalized,
            ))
            identities.append(and_(
                Workflow.solution_id.is_(None), Workflow.organization_id == organization_id,
                Workflow.function_name == function_name,
                func.ltrim(func.replace(Workflow.path, "\\", "/"), "/") == normalized,
                ~survivor,
            ))
    if workflow_ids:
        identities.append(Workflow.id.in_(workflow_ids))
    if not identities:
        return
    result = await db.execute(select(Workflow).where(
        Workflow.retirement_evidence.is_not(None), or_(*identities),
    ).order_by(Workflow.id).limit(1).execution_options(
        autoflush=False, populate_existing=True,
    ))
    retired = result.scalar_one_or_none()
    if retired is not None:
        raise WorkflowRegistrationRetired(retired, operation)


async def guard_workspace_registration_mutation(
    db: AsyncSession,
    *,
    operation: str,
    paths: Iterable[str] = (),
    workflows: Iterable[object] = (),
    authority: WorkspaceRegistrationMutationAuthority = (
        WorkspaceRegistrationMutationAuthority.EXTERNAL
    ),
) -> None:
    """Serialize with Live changes and reject external governed mutations.

    Release activation already owns the same global transaction lock and is the
    sole internal writer allowed to apply the immutable registration manifest.
    Every other caller is checked against both governed paths and effective
    registration identities so renaming/repointing cannot escape authority.
    """
    if (authority is not WorkspaceRegistrationMutationAuthority.EXTERNAL
            and authority is not WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION):
        raise ValueError(f"unsupported Workspace registration authority: {authority}")
    workflows = tuple(workflows)
    if authority is WorkspaceRegistrationMutationAuthority.EXTERNAL:
        await acquire_workspace_release_lock(db, None)
    await _guard_retired_registrations(db, workflows, operation)
    if authority is WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION:
        return
    release = await global_active_workspace_release_descriptor(db)
    if release is None:
        return

    governed_paths = set(release.governed_paths)
    effective_ids = {
        str(registration["workflow_id"])
        for registration in release.effective_registrations.values()
    }
    effective_keys = set(release.effective_registrations)

    for path in paths:
        normalized = normalize_release_path(path)
        if normalized in governed_paths:
            raise WorkspaceReleaseRegistrationGoverned(
                normalized, release.release_id, operation
            )

    for workflow in workflows:
        raw_path = getattr(workflow, "path", None)
        path = normalize_release_path(raw_path) if raw_path else None
        function_name = getattr(workflow, "function_name", None)
        workflow_id = getattr(workflow, "id", None)
        reference = (
            f"{path}::{function_name}"
            if path and function_name
            else str(workflow_id or path or "unknown")
        )
        if (
            path in governed_paths
            or str(workflow_id) in effective_ids
            or reference in effective_keys
        ):
            raise WorkspaceReleaseRegistrationGoverned(
                reference, release.release_id, operation
            )


__all__ = [
    "WorkspaceRegistrationMutationAuthority",
    "WorkspaceReleaseRegistrationGoverned",
    "WorkflowRegistrationRetired",
    "guard_workspace_registration_mutation",
]
