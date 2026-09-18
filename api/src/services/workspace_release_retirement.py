"""Retirement of the immutable global Workspace Live release."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from bifrost.workspace_release import canonical_digest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.workspace_promotions import (
    WorkspaceLiveRetireRequest,
    WorkspaceLiveRetireResponse,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.services.audit import emit_audit
from src.services.workspace_release_projection import acquire_workspace_release_lock
from src.services.workspace_release_runtime import (
    WorkspaceReleaseDescriptor,
    WorkspaceReleaseRuntimeError,
)

RETIREMENT_EVIDENCE_SCHEMA = "bifrost.workspace-release-retirement/v1"


class WorkspaceReleaseRetirementError(ValueError):
    """The global Live Workspace release failed a retirement invariant."""


class WorkspaceReleaseRetirementService:
    def __init__(self, db: AsyncSession, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    async def retire(
        self,
        request: WorkspaceLiveRetireRequest,
        *,
        user_id: UUID,
    ) -> WorkspaceLiveRetireResponse:
        try:
            return await self._retire_locked(request, user_id=user_id)
        except Exception:
            await self.db.rollback()
            raise

    async def _retire_locked(
        self,
        request: WorkspaceLiveRetireRequest,
        *,
        user_id: UUID,
    ) -> WorkspaceLiveRetireResponse:
        await acquire_workspace_release_lock(self.db, None)
        target = await self._live_release(for_update=True)
        if target is None:
            retired = await self._retired_release(
                request.expected_release_id, for_update=True
            )
            if retired is not None:
                release, artifact = retired
                evidence = getattr(release, "retirement_evidence", None) or {}
                if (
                    artifact.id != request.expected_artifact_id
                    or evidence.get("governed_manifest_id")
                    != request.governed_manifest_id
                ):
                    raise WorkspaceReleaseRetirementError(
                        "retirement identity CAS mismatch"
                    )
                return self._retired_response(release, artifact)
            raise WorkspaceReleaseRetirementError(
                "no Live Workspace release to retire"
            )
        release, artifact = target
        if (
            artifact.release_id != request.expected_release_id
            or artifact.id != request.expected_artifact_id
        ):
            raise WorkspaceReleaseRetirementError("retirement identity CAS mismatch")
        try:
            descriptor = WorkspaceReleaseDescriptor.from_rows(release, artifact)
        except WorkspaceReleaseRuntimeError as exc:
            raise WorkspaceReleaseRetirementError(str(exc)) from exc
        if descriptor.governed_manifest_id != request.governed_manifest_id:
            raise WorkspaceReleaseRetirementError("governed manifest CAS mismatch")
        now = datetime.now(timezone.utc)
        evidence = {
            "schema_version": RETIREMENT_EVIDENCE_SCHEMA,
            "release_row_id": str(release.id),
            "release_id": descriptor.release_id,
            "artifact_id": str(artifact.id),
            "reason": request.reason,
            "acknowledgement": request.acknowledgement,
            "retired_by_user_id": str(user_id),
            "governed_manifest_id": descriptor.governed_manifest_id,
            "governed_path_count": len(descriptor.governed_paths),
            "retired_at": now.isoformat(),
        }
        evidence["evidence_id"] = canonical_digest(evidence)
        release.activation_state = "retired"
        release.retired_at = now
        release.retirement_evidence = evidence
        release.attention_deadline = None
        await self.db.flush()
        await emit_audit(
            self.db,
            "workspace_release.retired",
            resource_type="workspace_promotion_release",
            resource_id=release.id,
            details={
                "release_id": descriptor.release_id,
                "artifact_id": str(artifact.id),
                "reason": request.reason,
                "governed_manifest_id": descriptor.governed_manifest_id,
                "governed_path_count": len(descriptor.governed_paths),
                "retirement_evidence_id": evidence["evidence_id"],
            },
            strict=True,
        )
        await self.db.commit()
        await self.db.refresh(release)
        return WorkspaceLiveRetireResponse(
            release_row_id=release.id,
            release_id=descriptor.release_id,
            retired_at=now,
            governed_path_count=len(descriptor.governed_paths),
            evidence_id=evidence["evidence_id"],
        )

    def _retired_response(
        self,
        release: WorkspacePromotionRelease,
        artifact: WorkspacePromotionArtifact,
    ) -> WorkspaceLiveRetireResponse:
        evidence = getattr(release, "retirement_evidence", None) or {}
        return WorkspaceLiveRetireResponse(
            release_row_id=release.id,
            release_id=str(artifact.release_id or evidence.get("release_id")),
            retired_at=release.retired_at,
            governed_path_count=int(evidence.get("governed_path_count") or 0),
            evidence_id=str(evidence.get("evidence_id")),
        )

    async def _live_release(
        self, *, for_update: bool = False
    ) -> tuple[WorkspacePromotionRelease, WorkspacePromotionArtifact] | None:
        statement = (
            select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
            .join(
                WorkspacePromotionArtifact,
                WorkspacePromotionArtifact.id == WorkspacePromotionRelease.artifact_id,
            )
            .where(WorkspacePromotionRelease.activation_state == "live")
            .limit(2)
        )
        if for_update:
            statement = statement.with_for_update()
        rows = (await self.db.execute(statement)).all()
        if len(rows) > 1:
            raise WorkspaceReleaseRetirementError(
                "platform has more than one global Live Workspace release"
            )
        if not rows:
            return None
        return rows[0][0], rows[0][1]

    async def _retired_release(
        self, release_id: str, *, for_update: bool = False
    ) -> tuple[WorkspacePromotionRelease, WorkspacePromotionArtifact] | None:
        statement = (
            select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
            .join(
                WorkspacePromotionArtifact,
                WorkspacePromotionArtifact.id == WorkspacePromotionRelease.artifact_id,
            )
            .where(
                WorkspacePromotionRelease.activation_state == "retired",
                WorkspacePromotionArtifact.release_id == release_id,
            )
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self.db.execute(statement)).one_or_none()
        if row is None:
            return None
        return row[0], row[1]


__all__ = [
    "RETIREMENT_EVIDENCE_SCHEMA",
    "WorkspaceReleaseRetirementError",
    "WorkspaceReleaseRetirementService",
]
