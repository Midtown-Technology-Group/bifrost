"""Retirement of the immutable global Workspace Live release."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import UUID

from bifrost.workspace_release import canonical_digest
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from src.models.contracts.workspace_promotions import (
    WorkflowRetirementConsumerInventory,
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
from src.services.workflow_registration_retirement import (
    WorkflowRetirementEvidenceError,
    build_workflow_retirement_evidence,
    validate_workflow_retirement_evidence,
    workflow_retirement_snapshot_hash,
)
from src.services.workflow_retirement_consumers import (
    WorkflowRetirementInventoryError,
    inspect_workflow_retirement_consumers,
)
from src.services.workspace_release_files import normalize_release_path
from src.services.workspace_release_projection import acquire_workspace_release_lock
from src.services.workspace_release_runtime import (
    WorkspaceReleaseDescriptor,
    WorkspaceReleaseRuntimeError,
)

RETIREMENT_EVIDENCE_SCHEMA = "bifrost.workspace-release-retirement/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_SOURCE_OBLIGATION_ROWS = 1000


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
            target = (await self.db.execute(select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
                .join(WorkspacePromotionArtifact, WorkspacePromotionRelease.artifact_id == WorkspacePromotionArtifact.id)
                .where(WorkspacePromotionRelease.activation_state == "retired")
                .order_by(WorkspacePromotionRelease.retired_at.desc(), WorkspacePromotionRelease.id.desc())
                .limit(1))).first()
        if target is None:
            raise WorkspaceReleaseRetirementError("no Live or retired Workspace release to inspect")
        release, artifact = target
        self._require_owner_context(release)
        try:
            descriptor = WorkspaceReleaseDescriptor.from_rows(release, artifact)
        except WorkspaceReleaseRuntimeError as exc:
            raise WorkspaceReleaseRetirementError(str(exc)) from exc
        rows = list((await self.db.scalars(select(Workflow)
            .where(self._loose_registration_predicate(descriptor))
            .options(selectinload(Workflow.roles))
            .order_by(Workflow.id).limit(1001))).all())
        if len(rows) > 1000:
            raise WorkspaceReleaseRetirementError(
                "retirement registration inventory exceeds its readback bound"
            )
        ids = {UUID(item["workflow_id"])
               for item in descriptor.effective_registrations.values()}
        registrations = []
        for row in rows:
            retirement_id = None
            if row.retirement_evidence is not None:
                try:
                    retirement_id = validate_workflow_retirement_evidence(row)["evidence_id"]
                except WorkflowRetirementEvidenceError as exc:
                    raise WorkspaceReleaseRetirementError(str(exc)) from exc
            path = row.path.replace("\\", "/").lstrip("/")
            registrations.append(WorkspaceLiveRetirementRegistration(
                workflow_id=row.id, organization_id=row.organization_id,
                path=path, function_name=row.function_name, is_active=row.is_active,
                matched_by=(["governed_path"] if path in descriptor.governed_paths else [])
                + (["effective_registration"] if row.id in ids else []),
                registration_hash=workflow_retirement_snapshot_hash(row),
                retirement_evidence_id=retirement_id,
                consumer_inventory=WorkflowRetirementConsumerInventory.model_validate(
                    await self._consumer_inventory(row)),
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
            state="retired" if release.activation_state == "retired" else "live",
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
                    or evidence.get("obsolete_registration_reviews_digest", canonical_digest([]))
                    != canonical_digest([item.model_dump(mode="json") for item in request.obsolete_registrations])
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
        retired_registrations = await self._retire_obsolete_registrations(
            descriptor, request, user_id=user_id,
        )
        await self._require_no_loose_consumers(descriptor)
        excluded_source_obligations = await self._require_resolved_source_obligations(
            descriptor
        )
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
            "retired_registration_evidence": retired_registrations,
            "excluded_source_obligations": excluded_source_obligations,
            "source_obligation_boundary_digest": canonical_digest({
                "release_row_id": str(descriptor.release_row_id),
                "artifact_id": str(descriptor.artifact_id),
                "release_id": descriptor.release_id,
                "effective_manifest_id": descriptor.effective_manifest_id,
                "source_hashes": descriptor.source_hashes,
            }),
            "obsolete_registration_reviews_digest": canonical_digest([
                item.model_dump(mode="json") for item in request.obsolete_registrations
            ]),
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
        rows = list((await self.db.scalars(select(Workflow)
            .where(self._loose_registration_predicate(descriptor))
            .options(selectinload(Workflow.roles)).order_by(Workflow.id)
            .limit(1001).with_for_update(of=Workflow))).all())
        if len(rows) > 1000:
            raise WorkspaceReleaseRetirementError("retirement registration inventory exceeds its bound")
        for row in rows:
            try:
                validate_workflow_retirement_evidence(row)
            except WorkflowRetirementEvidenceError as exc:
                raise WorkspaceReleaseRetirementError(
                    "Live release still has loose workflow registrations without terminal evidence; "
                    "complete its guarded handoff or exact reviewed retirement"
                ) from exc

    async def _retire_obsolete_registrations(
        self, descriptor: WorkspaceReleaseDescriptor, request: WorkspaceLiveRetireRequest,
        *, user_id: UUID,
    ) -> list[dict[str, str]]:
        if not request.obsolete_registrations:
            return []
        # The admission fence serializes dispatch. Table locks additionally stop
        # native callers and role/control writers changing after this census.
        # External/Source callers remain an explicit reviewed cutover obligation.
        await self.db.execute(text(
            "LOCK TABLE forms, form_fields, agent_tools, event_subscriptions, "
            "service_definitions, applications, workflow_roles, agent_action_approvals IN SHARE MODE"
        ))
        ids = [item.workflow_id for item in request.obsolete_registrations]
        rows = list((await self.db.scalars(select(Workflow)
            .where(self._loose_registration_predicate(descriptor), Workflow.id.in_(ids))
            .options(selectinload(Workflow.roles)).order_by(Workflow.id)
            .with_for_update(of=Workflow).execution_options(populate_existing=True))).all())
        indexed = {row.id: row for row in rows}
        if set(indexed) != set(ids):
            raise WorkspaceReleaseRetirementError("obsolete registration is outside the exact Live guard cohort")
        evidence = []
        for review in request.obsolete_registrations:
            row = indexed[review.workflow_id]
            if row.retirement_evidence is not None:
                raise WorkspaceReleaseRetirementError("registration is already retired; inspect its retained evidence")
            before = workflow_retirement_snapshot_hash(row)
            consumers = await self._consumer_inventory(row, lock=True)
            if (before != review.expected_registration_hash
                    or consumers["inventory_digest"] != review.expected_consumer_inventory_digest):
                raise WorkspaceReleaseRetirementError("obsolete registration or caller inventory CAS mismatch")
            if consumers["native_callers"] or consumers["accepted_work"]:
                raise WorkspaceReleaseRetirementError("obsolete registration still has native callers or accepted work")
            row.is_active = False
            row.endpoint_enabled = False
            row.public_endpoint = False
            row.api_key_enabled = False
            marker = build_workflow_retirement_evidence(
                row, release_id=descriptor.release_id, artifact_id=descriptor.artifact_id,
                before_registration_hash=before,
                caller_inventory_digest=consumers["inventory_digest"],
                review_digest=canonical_digest(review.model_dump(mode="json")),
                reason=review.reason, retired_by_user_id=user_id, retired_at=datetime.now(UTC),
            )
            row.retirement_evidence = marker
            await emit_audit(self.db, "workflow.registration_retired", resource_type="workflow",
                resource_id=row.id, details={"evidence": marker,
                    "review": review.model_dump(mode="json")}, strict=True)
            evidence.append({"workflow_id": str(row.id), "evidence_id": marker["evidence_id"]})
        await self.db.flush()
        return evidence

    async def _consumer_inventory(self, row: Workflow, *, lock: bool = False) -> dict:
        try:
            return await inspect_workflow_retirement_consumers(self.db, row, lock=lock)
        except WorkflowRetirementInventoryError as exc:
            raise WorkspaceReleaseRetirementError(str(exc)) from exc

    @staticmethod
    def _classify_source_obligation(
        record: WorkspaceSourceRelease,
        source_hashes: dict[str, str],
    ) -> dict[str, str] | None:
        """Return auditable exclusion evidence only for a complete disjoint map."""
        paths = record.paths
        if not isinstance(paths, dict) or not paths:
            return None
        normalized: dict[str, str] = {}
        try:
            for raw_path, digest in paths.items():
                if not isinstance(raw_path, str):
                    return None
                path = normalize_release_path(raw_path)
                if (
                    PurePosixPath(path).as_posix() != path
                    or path in normalized
                    or not isinstance(digest, str)
                    or not _SHA256_RE.fullmatch(digest)
                ):
                    return None
                normalized[path] = digest
        except (TypeError, ValueError):
            return None
        if set(normalized) & set(source_hashes):
            return None
        return {
            "source_release_id": str(record.id),
            "path_map_digest": canonical_digest(dict(sorted(normalized.items()))),
        }

    async def _require_resolved_source_obligations(
        self, descriptor: WorkspaceReleaseDescriptor
    ) -> list[dict[str, str]]:
        # Fence inserts and updates until retirement commits. Do not row-lock
        # these records: source writers may lock a row before needing this table.
        await self.db.execute(
            text("LOCK TABLE workspace_source_releases IN SHARE MODE")
        )
        result = await self.db.scalars(
            select(WorkspaceSourceRelease)
            .where(
                WorkspaceSourceRelease.organization_id == self.organization_id,
                WorkspaceSourceRelease.disposition.in_(
                    ("pending", "attention_required", "deferred")
                ),
            )
            .order_by(WorkspaceSourceRelease.id)
            .limit(_MAX_SOURCE_OBLIGATION_ROWS + 1)
            .execution_options(populate_existing=True)
        )
        records = list(result.all())
        if len(records) > _MAX_SOURCE_OBLIGATION_ROWS:
            raise WorkspaceReleaseRetirementError(
                "unresolved Workspace source-release inventory exceeds its bound"
            )
        excluded: list[dict[str, str]] = []
        for record in records:
            evidence = self._classify_source_obligation(record, descriptor.source_hashes)
            if evidence is None:
                raise WorkspaceReleaseRetirementError(
                    "unresolved Workspace source-release obligations remain; "
                    "read back and resolve each relevant or invalid disposition before retirement"
                )
            excluded.append(evidence)
        return excluded

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
