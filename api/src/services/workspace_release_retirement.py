"""Retirement of the immutable global Workspace Live release."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from bifrost.workspace_release import canonical_digest
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.models.contracts.workspace_promotions import (
    WorkspaceLiveRetirementInventory,
    WorkspaceLiveRetirementRegistration,
    WorkspaceLiveRetireRequest,
    WorkspaceLiveRetireResponse,
)
from src.models.orm.workflows import Workflow
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
    WorkspaceSourceRelease,
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

    @staticmethod
    def _loose_registration_predicate(
        descriptor: WorkspaceReleaseDescriptor,
    ) -> ColumnElement[bool]:
        registration_ids = [UUID(item["workflow_id"])
                            for item in descriptor.effective_registrations.values()]
        runtime_path = func.ltrim(func.replace(Workflow.path, "\\", "/"), "/")
        predicates = [runtime_path.in_(descriptor.governed_paths)]
        if registration_ids:
            predicates.append(Workflow.id.in_(registration_ids))
        # This is shared with mutation-time validation. Inactive rows and
        # uncaptured, normalized paths cannot disappear from the census.
        return Workflow.solution_id.is_(None) & or_(*predicates)

    async def inspect(self) -> WorkspaceLiveRetirementInventory:
        """Read the exact guard cohort without acquiring an activation lock or writing."""
        target = await self._live_release()
        if target is None:
            raise WorkspaceReleaseRetirementError("no Live Workspace release to inspect")
        release, artifact = target
        self._require_owner_context(release)
        try:
            descriptor = WorkspaceReleaseDescriptor.from_rows(release, artifact)
        except WorkspaceReleaseRuntimeError as exc:
            raise WorkspaceReleaseRetirementError(str(exc)) from exc
        rows = (await self.db.execute(select(
            Workflow.id, Workflow.organization_id, Workflow.path,
            Workflow.function_name, Workflow.is_active,
        ).where(self._loose_registration_predicate(descriptor))
            .order_by(Workflow.id).limit(1001))).all()
        if len(rows) > 1000:
            raise WorkspaceReleaseRetirementError(
                "retirement registration inventory exceeds its readback bound"
            )
        ids = {UUID(item["workflow_id"])
               for item in descriptor.effective_registrations.values()}
        registrations = []
        for row in rows:
            path = row.path.replace("\\", "/").lstrip("/")
            registrations.append(WorkspaceLiveRetirementRegistration(
                workflow_id=row.id, organization_id=row.organization_id,
                path=path, function_name=row.function_name, is_active=row.is_active,
                matched_by=(["governed_path"] if path in descriptor.governed_paths else [])
                + (["effective_registration"] if row.id in ids else []),
            ))
        obligation_rows = (await self.db.execute(
            select(WorkspaceSourceRelease.disposition, func.count(WorkspaceSourceRelease.id))
            .where(WorkspaceSourceRelease.organization_id == release.organization_id,
                   WorkspaceSourceRelease.disposition.in_(
                       ("pending", "attention_required", "deferred")))
            .group_by(WorkspaceSourceRelease.disposition)
        )).all()
        obligations = {disposition: count for disposition, count in obligation_rows}
        return WorkspaceLiveRetirementInventory(
            observed_at=datetime.now(UTC), release_row_id=release.id,
            release_id=descriptor.release_id, artifact_id=artifact.id,
            organization_id=release.organization_id,
            governed_manifest_id=descriptor.governed_manifest_id,
            history_locked=release.lock_state == "locked",
            loose_registrations=registrations,
            unresolved_source_obligations=obligations,
        )

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
                self._require_owner_context(release)
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
        self._require_owner_context(release)
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
        if release.lock_state != "locked":
            raise WorkspaceReleaseRetirementError(
                "Live release history is not locked; repair signed history before retirement"
            )
        await self._require_no_loose_consumers(descriptor)
        await self._require_resolved_source_obligations()
        now = datetime.now(UTC)
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

    def _require_owner_context(self, release: WorkspacePromotionRelease) -> None:
        # Live is global, but its source journal belongs to the release owner.
        # An unrelated context must not turn that owner's unresolved debt into
        # an empty query. Apply the same identity rule to idempotent readback.
        if release.organization_id != self.organization_id:
            raise WorkspaceReleaseRetirementError(
                "retirement requires the Live release owner context"
            )

    async def _require_no_loose_consumers(
        self, descriptor: WorkspaceReleaseDescriptor
    ) -> None:
        rows = (await self.db.execute(
            select(Workflow.id).where(self._loose_registration_predicate(descriptor)).limit(1)
        )).first()
        if rows is not None:
            raise WorkspaceReleaseRetirementError(
                "Live release still has loose workflow registrations; "
                "complete and verify their guarded handoff before retirement"
            )

    async def _require_resolved_source_obligations(self) -> None:
        row = (
            await self.db.execute(
                select(WorkspaceSourceRelease.id)
                .where(
                    WorkspaceSourceRelease.organization_id == self.organization_id,
                    WorkspaceSourceRelease.disposition.in_(
                        ("pending", "attention_required", "deferred")
                    ),
                )
                .limit(1)
            )
        ).first()
        if row is not None:
            raise WorkspaceReleaseRetirementError(
                "unresolved Workspace source-release obligations remain; "
                "read back and resolve each disposition before retirement"
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
