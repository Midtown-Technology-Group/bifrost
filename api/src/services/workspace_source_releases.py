"""Durable accountability for reviewed Workspace source commits."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.promotion import normalize_workspace_path
from bifrost.workspace_release import canonical_digest
from src.models.contracts.workspace_promotions import (
    WorkspaceSourceReleaseDeclareRequest,
    WorkspaceSourceReleaseListResponse,
    WorkspaceSourceReleaseResponse,
    WorkspaceSourceSupersessionEvidence,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionRelease,
    WorkspaceSourceRelease,
)
from src.models.orm.solutions import Solution
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.file_index import FileIndex
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.github_actions_oidc import WorkspaceSourceReleaseProducer
from src.services.repo_storage import RepoStorage
from src.services.solutions.deployment_manifest import validate_runtime_closure
from src.services.solution_deploy_obligations import (
    declare_solution_deploy_obligations,
    solution_deploy_obligation_declaration,
)
from src.services.workspace_release_runtime import (
    WorkspaceReleaseRuntimeError,
    active_workspace_release,
)

DEFAULT_RELEASE_DUE_AFTER = timedelta(minutes=30)
COMPLETION_EVIDENCE_SCHEMA = "bifrost.workspace-source-release-completion/v1"


class WorkspaceSourceReleaseConflict(ValueError):
    """A source commit was redeclared with different immutable evidence."""


def _assert_compatible_replay(
    record: WorkspaceSourceRelease,
    request: WorkspaceSourceReleaseDeclareRequest,
    paths: dict[str, str | None],
) -> None:
    existing_solution_obligations = [
        solution_deploy_obligation_declaration(item).model_dump(
            mode="json", exclude_none=True
        )
        for item in record.solution_deploy_obligations
    ]
    requested_solution_obligations = [
        item.model_dump(mode="json", exclude_none=True)
        for item in (request.solution_deploy_obligations or [])
    ]
    if (
        record.source_tree_sha != request.source_tree_sha
        or dict(record.paths or {}) != paths
        or record.declared_disposition != request.disposition
        or record.reason != request.reason
        or existing_solution_obligations != requested_solution_obligations
    ):
        raise WorkspaceSourceReleaseConflict(
            "source commit already has different release accountability evidence"
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_paths(paths: dict[str, str | None]) -> dict[str, str | None]:
    normalized: dict[str, str | None] = {}
    for raw_path, digest in paths.items():
        path = normalize_workspace_path(raw_path)
        if path != raw_path:
            raise ValueError(f"Workspace source path is not normalized: {raw_path!r}")
        if path == "solutions" or path.startswith("solutions/"):
            raise ValueError(
                "Workspace source release paths must not include the 'solutions/' "
                "subtree; declare Solution source with solution_deploy_obligations"
            )
        normalized[path] = digest
    return dict(sorted(normalized.items()))


def source_release_declaration_digest(
    request: WorkspaceSourceReleaseDeclareRequest,
) -> str:
    """Hash the canonical declaration JSON shared with the Workspace producer.

    Canonical JSON omits null fields, sorts keys recursively, uses compact
    separators, and preserves non-ASCII text as raw UTF-8 before SHA-256.
    """
    payload = request.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def source_release_response(
    record: WorkspaceSourceRelease,
    *,
    now: datetime | None = None,
) -> WorkspaceSourceReleaseResponse:
    now = now or _utc_now()
    overdue = bool(
        record.disposition in {"pending", "attention_required"}
        and record.due_at is not None
        and record.due_at <= now
    )
    return WorkspaceSourceReleaseResponse(
        id=record.id,
        organization_id=record.organization_id,
        source_commit_sha=record.source_commit_sha,
        source_tree_sha=record.source_tree_sha,
        paths=dict(record.paths or {}),
        declaration_actor=record.declaration_actor,
        producer_oidc_commit_sha=record.producer_oidc_commit_sha,
        producer_event_name=record.producer_event_name,
        producer_run_id=record.producer_run_id,
        producer_triggering_workflow_run_id=(
            record.producer_triggering_workflow_run_id
        ),
        producer_triggering_workflow_run_attempt=(
            record.producer_triggering_workflow_run_attempt
        ),
        producer_declaration_digest=record.producer_declaration_digest,
        producer_actor=record.producer_actor,
        producer_actor_id=record.producer_actor_id,
        disposition=record.disposition,
        reason=record.reason,
        release_row_id=record.release_row_id,
        completion_evidence=record.completion_evidence,
        due_at=record.due_at,
        overdue=overdue,
        requires_attention=record.disposition == "attention_required" or overdue,
        resolved_at=record.resolved_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        solution_deploy_obligations=[
            solution_deploy_obligation_declaration(item)
            for item in record.solution_deploy_obligations
        ],
    )


class WorkspaceSourceReleaseService:
    def __init__(self, db: AsyncSession, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    async def declare(
        self,
        request: WorkspaceSourceReleaseDeclareRequest,
        *,
        created_by: UUID,
        producer: WorkspaceSourceReleaseProducer | None = None,
    ) -> WorkspaceSourceReleaseResponse:
        if producer is not None:
            if producer.organization_id != self.organization_id:
                raise ValueError(
                    "authenticated producer organization does not match the service"
                )
            if producer.source_commit_sha != request.source_commit_sha:
                raise ValueError(
                    "authenticated producer source commit does not match the declaration"
                )
            if (
                producer.declaration_digest is not None
                and producer.declaration_digest
                != source_release_declaration_digest(request)
            ):
                raise ValueError(
                    "authenticated producer declaration digest does not match the request"
                )
        paths = _normalize_paths(request.paths)
        existing = await self.db.scalar(
            select(WorkspaceSourceRelease).where(
                WorkspaceSourceRelease.organization_id == self.organization_id,
                WorkspaceSourceRelease.source_commit_sha == request.source_commit_sha,
            )
        )
        if existing is not None:
            _assert_compatible_replay(existing, request, paths)
            return source_release_response(existing)

        now = _utc_now()
        disposition = request.disposition
        record = WorkspaceSourceRelease(
            id=uuid4(),
            organization_id=self.organization_id,
            source_commit_sha=request.source_commit_sha,
            source_tree_sha=request.source_tree_sha,
            paths=paths,
            declaration_actor=(
                "github_actions_oidc" if producer is not None else "platform_admin"
            ),
            producer_oidc_commit_sha=(
                producer.oidc_commit_sha if producer is not None else None
            ),
            producer_event_name=(producer.event_name if producer is not None else None),
            producer_run_id=(producer.run_id if producer is not None else None),
            producer_triggering_workflow_run_id=(
                producer.triggering_workflow_run_id if producer is not None else None
            ),
            producer_triggering_workflow_run_attempt=(
                producer.triggering_workflow_run_attempt
                if producer is not None
                else None
            ),
            producer_declaration_digest=(
                producer.declaration_digest if producer is not None else None
            ),
            producer_actor=(producer.actor if producer is not None else None),
            producer_actor_id=(producer.actor_id if producer is not None else None),
            disposition=disposition,
            declared_disposition=disposition,
            reason=request.reason,
            due_at=(now + DEFAULT_RELEASE_DUE_AFTER)
            if disposition in {"pending", "attention_required"}
            else None,
            resolved_at=now if disposition == "non_production" else None,
            created_by=created_by,
        )
        self.db.add(record)
        try:
            await declare_solution_deploy_obligations(
                self.db,
                source_release_id=record.id,
                organization_id=self.organization_id,
                source_commit_sha=request.source_commit_sha,
                source_tree_sha=request.source_tree_sha,
                requests=list(request.solution_deploy_obligations or []),
            )
            await self.db.commit()
        except IntegrityError:
            # Two delivery attempts for one push can race. The unique key is the
            # serialization point; after rollback, accept only exact evidence.
            await self.db.rollback()
            existing = await self.db.scalar(
                select(WorkspaceSourceRelease).where(
                    WorkspaceSourceRelease.organization_id == self.organization_id,
                    WorkspaceSourceRelease.source_commit_sha
                    == request.source_commit_sha,
                )
            )
            if existing is None:
                raise
            _assert_compatible_replay(existing, request, paths)
            return source_release_response(existing)
        await self.db.refresh(record, attribute_names=["solution_deploy_obligations"])
        return source_release_response(record, now=now)

    async def set_manual_disposition(
        self,
        record_id: UUID,
        *,
        disposition: str,
        reason: str,
        supersession_evidence: WorkspaceSourceSupersessionEvidence | None = None,
    ) -> WorkspaceSourceReleaseResponse:
        record = await self._get(record_id, for_update=True)
        if record is None:
            raise KeyError(record_id)
        if disposition == "superseded":
            if supersession_evidence is None:
                raise WorkspaceSourceReleaseConflict(
                    "supersession requires per-path production readback"
                )
            if set(supersession_evidence.paths) != set(record.paths or {}):
                raise WorkspaceSourceReleaseConflict(
                    "supersession must review every declared source path"
                )
            now = _utc_now()
            verified_at = supersession_evidence.verified_at
            if (
                verified_at.tzinfo is None
                or verified_at > now + timedelta(minutes=5)
                or verified_at < now - timedelta(days=1)
            ):
                raise WorkspaceSourceReleaseConflict(
                    "supersession needs production readback from the last day"
                )
            later = None
            if supersession_evidence.superseding_source_release_id is not None:
                later = await self._get(
                    supersession_evidence.superseding_source_release_id
                )
                if (
                    later is None
                    or later.id == record.id
                    or later.disposition != "released"
                    or later.created_at <= record.created_at
                    or not later.completion_evidence
                    or later.completion_evidence.get("schema_version")
                    != COMPLETION_EVIDENCE_SCHEMA
                ):
                    raise WorkspaceSourceReleaseConflict(
                        "supersession requires a later verified source release"
                    )
            deployments = {}
            repository = SolutionDeploymentRepository(self.db)
            for (
                deployment_id
            ) in supersession_evidence.superseding_solution_deployment_ids:
                deployment = await repository.get_by_id_for_runtime(deployment_id)
                if (
                    deployment is None
                    or deployment.organization_id != self.organization_id
                    or deployment.state != "active"
                    or deployment.activated_at is None
                    or deployment.activated_at <= record.created_at
                    or (deployment.validation_result or {}).get("schema_version")
                    not in {
                        "bifrost.workspace-live-handoff/v1",
                        "bifrost.solution-source-revision/v1",
                    }
                ):
                    raise WorkspaceSourceReleaseConflict(
                        "supersession Solution deployment is not active and reviewed"
                    )
                solution = await self.db.scalar(
                    select(Solution)
                    .where(Solution.id == deployment.solution_id)
                    .with_for_update()
                )
                if (
                    solution is None
                    or solution.status != "active"
                    or solution.active_deployment_id != deployment_id
                ):
                    raise WorkspaceSourceReleaseConflict(
                        "supersession Solution pointer changed"
                    )
                try:
                    _, resolution = validate_runtime_closure(
                        deployment.compiled_manifest,
                        deployment.resolution_map,
                        deployment.dependencies,
                        expected_manifest_hash=deployment.compiled_manifest_hash,
                        expected_resolution_hash=deployment.resolution_map_hash,
                    )
                except ValueError as exc:
                    raise WorkspaceSourceReleaseConflict(
                        "supersession Solution closure is invalid"
                    ) from exc
                deployments[deployment_id] = resolution
            live = None
            removed_paths = {
                path for path, review in supersession_evidence.paths.items()
                if review.runtime_owner == "removed"
            }
            root_paths = set(await RepoStorage().list()) if removed_paths else set()
            if any(
                path.runtime_owner in {"workspace", "removed"}
                for path in supersession_evidence.paths.values()
            ):
                try:
                    live = await active_workspace_release(self.db, self.organization_id)
                except WorkspaceReleaseRuntimeError as exc:
                    raise WorkspaceSourceReleaseConflict(
                        "supersession Live release is invalid"
                    ) from exc
            for old_path, path_review in supersession_evidence.paths.items():
                if path_review.runtime_owner == "workspace":
                    completion = (later.completion_evidence or {}) if later else {}
                    runtime_path = path_review.runtime_path or old_path
                    if (
                        later is None
                        or path_review.runtime_ref
                        != completion.get("workspace_release_id")
                        or (completion.get("runtime_sha256") or {}).get(runtime_path)
                        != path_review.runtime_source_sha256
                        or live is None
                        or live.release_id != path_review.runtime_ref
                        or live.source_hashes.get(runtime_path)
                        != path_review.runtime_source_sha256
                    ):
                        raise WorkspaceSourceReleaseConflict(
                            f"Workspace runtime source is unverified: {old_path}"
                        )
                elif path_review.runtime_owner == "solution":
                    try:
                        deployment_id = UUID(path_review.runtime_ref or "")
                    except ValueError as exc:
                        raise WorkspaceSourceReleaseConflict(
                            f"Solution runtime reference is invalid: {old_path}"
                        ) from exc
                    resolution = deployments.get(deployment_id)
                    runtime_path = path_review.runtime_path or old_path
                    if (
                        resolution is None
                        or runtime_path not in resolution.sources
                        or resolution.sources[runtime_path].content_hash
                        != f"sha256:{path_review.runtime_source_sha256}"
                    ):
                        raise WorkspaceSourceReleaseConflict(
                            f"Solution runtime hash is unverified: {old_path}"
                        )
                elif path_review.runtime_owner == "removed":
                    if old_path in root_paths or await self.db.scalar(
                        select(FileIndex.path).where(FileIndex.path == old_path)
                    ) is not None:
                        raise WorkspaceSourceReleaseConflict(
                            f"removed source is still present in Root or its index: {old_path}"
                        )
                    if live is not None and old_path in live.source_hashes:
                        raise WorkspaceSourceReleaseConflict(
                            f"removed source is still present in Live: {old_path}"
                        )
                    installed = await self.db.scalar(
                        select(SolutionDeployment.id)
                        .join(Solution, Solution.active_deployment_id == SolutionDeployment.id)
                        .where(
                            Solution.status == "active",
                            SolutionDeployment.resolution_map["sources"].op("?")(old_path),
                        )
                        .limit(1)
                    )
                    if installed is not None:
                        raise WorkspaceSourceReleaseConflict(
                            f"removed source is still present in a Solution: {old_path}"
                        )
            evidence = {
                "schema_version": "bifrost.workspace-source-release-supersession/v1",
                "source_release_id": str(record.id),
                "source_commit_sha": record.source_commit_sha,
                "superseding_source_release_id": str(later.id) if later else None,
                "superseding_source_commit_sha": (
                    later.source_commit_sha if later else None
                ),
                "superseding_completion_evidence_id": (
                    later.completion_evidence.get("evidence_id") if later else None
                ),
                "review": supersession_evidence.model_dump(
                    mode="json", exclude_none=True
                ),
                "reason": reason,
            }
            evidence["evidence_id"] = canonical_digest(evidence)
        else:
            if supersession_evidence is not None:
                raise WorkspaceSourceReleaseConflict(
                    "review evidence is only valid for supersession"
                )
            evidence = None
        if (
            record.disposition == disposition
            and record.reason == reason
            and record.completion_evidence == evidence
        ):
            return source_release_response(record)
        if record.disposition in {"released", "superseded"}:
            raise WorkspaceSourceReleaseConflict(
                "completed source accountability evidence is immutable"
            )
        record.disposition = disposition
        record.reason = reason
        record.completion_evidence = evidence
        record.resolved_at = _utc_now()
        await self.db.commit()
        await self.db.refresh(record)
        return source_release_response(record)

    async def list(
        self,
        *,
        limit: int = 100,
        tracking_expected: bool = False,
    ) -> WorkspaceSourceReleaseListResponse:
        records = list(
            (
                await self.db.scalars(
                    select(WorkspaceSourceRelease)
                    .where(
                        WorkspaceSourceRelease.organization_id == self.organization_id
                    )
                    .order_by(WorkspaceSourceRelease.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )
        now = _utc_now()
        responses = [source_release_response(record, now=now) for record in records]
        counts = {
            disposition: int(count)
            for disposition, count in (
                await self.db.execute(
                    select(
                        WorkspaceSourceRelease.disposition,
                        func.count(WorkspaceSourceRelease.id),
                    )
                    .where(
                        WorkspaceSourceRelease.organization_id == self.organization_id
                    )
                    .group_by(WorkspaceSourceRelease.disposition)
                )
            ).all()
        }
        overdue_pending = int(
            await self.db.scalar(
                select(func.count(WorkspaceSourceRelease.id)).where(
                    WorkspaceSourceRelease.organization_id == self.organization_id,
                    WorkspaceSourceRelease.disposition == "pending",
                    WorkspaceSourceRelease.due_at.is_not(None),
                    WorkspaceSourceRelease.due_at <= now,
                )
            )
            or 0
        )
        overdue = int(
            await self.db.scalar(
                select(func.count(WorkspaceSourceRelease.id)).where(
                    WorkspaceSourceRelease.organization_id == self.organization_id,
                    WorkspaceSourceRelease.disposition.in_(
                        ("pending", "attention_required")
                    ),
                    WorkspaceSourceRelease.due_at.is_not(None),
                    WorkspaceSourceRelease.due_at <= now,
                )
            )
            or 0
        )
        total = sum(counts.values())
        return WorkspaceSourceReleaseListResponse(
            records=responses,
            total=total,
            pending=counts.get("pending", 0),
            attention_required=(counts.get("attention_required", 0) + overdue_pending),
            overdue=overdue,
            tracking_state=(
                "active" if total or tracking_expected else "not_configured"
            ),
            last_observed_source_commit_sha=(
                responses[0].source_commit_sha if responses else None
            ),
            last_observed_at=responses[0].created_at if responses else None,
            producer_contract=(
                "The trusted protected-main merge producer must declare every "
                "new source commit, including non-production commits."
            ),
        )

    async def get(self, record_id: UUID) -> WorkspaceSourceReleaseResponse:
        record = await self._get(record_id)
        if record is None:
            raise KeyError(record_id)
        return source_release_response(record)

    async def _get(
        self, record_id: UUID, *, for_update: bool = False
    ) -> WorkspaceSourceRelease | None:
        statement = select(WorkspaceSourceRelease).where(
            WorkspaceSourceRelease.id == record_id,
            WorkspaceSourceRelease.organization_id == self.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.db.scalar(statement)


async def reconcile_source_releases_after_lock(
    db: AsyncSession,
    *,
    organization_id: UUID,
    release_row_id: UUID,
    release_id: str,
    runtime_hashes: dict[str, str],
    history_commit_sha: str,
    history_hashes: dict[str, str | None],
    verified_at: datetime | None = None,
) -> list[UUID]:
    """Close records only when runtime and signed history prove exact bytes."""
    verified_at = verified_at or _utc_now()
    records = list(
        (
            await db.scalars(
                select(WorkspaceSourceRelease)
                .where(
                    WorkspaceSourceRelease.organization_id == organization_id,
                    WorkspaceSourceRelease.disposition.in_(
                        ("pending", "attention_required", "deferred")
                    ),
                )
                .with_for_update()
            )
        ).all()
    )
    completed: list[UUID] = []
    for record in records:
        paths = dict(record.paths or {})
        if any(digest is None for digest in paths.values()):
            continue
        if not paths or any(
            runtime_hashes.get(path) != digest for path, digest in paths.items()
        ):
            continue
        if any(history_hashes.get(path) != digest for path, digest in paths.items()):
            continue
        evidence: dict[str, Any] = {
            "schema_version": COMPLETION_EVIDENCE_SCHEMA,
            "source_commit_sha": record.source_commit_sha,
            "source_tree_sha": record.source_tree_sha,
            "workspace_release_row_id": str(release_row_id),
            "workspace_release_id": release_id,
            "runtime_sha256": paths,
            "history": {
                "commit_sha": history_commit_sha,
                "file_sha256": paths,
            },
            "verified_at": verified_at.isoformat(),
        }
        evidence["evidence_id"] = canonical_digest(evidence)
        record.disposition = "released"
        record.reason = None
        record.release_row_id = release_row_id
        record.completion_evidence = evidence
        record.resolved_at = verified_at
        completed.append(record.id)
    await db.flush()
    return completed


async def mark_source_release_attention(
    db: AsyncSession,
    *,
    organization_id: UUID,
    source_commit_sha: str,
    code: str,
    message: str,
) -> UUID | None:
    record = await db.scalar(
        select(WorkspaceSourceRelease)
        .where(
            WorkspaceSourceRelease.organization_id == organization_id,
            WorkspaceSourceRelease.source_commit_sha == source_commit_sha,
            WorkspaceSourceRelease.disposition == "pending",
        )
        .with_for_update()
    )
    if record is None:
        return None
    record.disposition = "attention_required"
    record.reason = f"{code}: {message}"[:2000]
    await db.flush()
    return record.id


async def sweep_overdue_workspace_releases(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """Turn missed source and history deadlines into durable attention state."""
    now = now or _utc_now()
    # Projection takes the Live release row before source-accountability rows.
    # Keep the scheduler in the same order so the two transactions cannot
    # deadlock while a history lock completes at the attention deadline.
    live_releases = list(
        (
            await db.scalars(
                select(WorkspacePromotionRelease)
                .where(
                    WorkspacePromotionRelease.activation_state == "live",
                    WorkspacePromotionRelease.lock_state.in_(("queued", "in_progress")),
                    WorkspacePromotionRelease.attention_deadline.is_not(None),
                    WorkspacePromotionRelease.attention_deadline <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for release in live_releases:
        release.lock_state = "attention_required"
        release.error_code = "workspace_release_history_overdue"
        release.error_message = (
            "Live runtime has not reached verified production-live history "
            "before its accountability deadline"
        )

    source_records = list(
        (
            await db.scalars(
                select(WorkspaceSourceRelease)
                .where(
                    WorkspaceSourceRelease.disposition == "pending",
                    WorkspaceSourceRelease.due_at.is_not(None),
                    WorkspaceSourceRelease.due_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for record in source_records:
        record.disposition = "attention_required"
        record.reason = "reviewed Workspace source has not reached verified production"
    await db.flush()
    return {
        "source_release_ids": [str(record.id) for record in source_records],
        "workspace_release_ids": [str(release.id) for release in live_releases],
    }


__all__ = [
    "WorkspaceSourceReleaseConflict",
    "WorkspaceSourceReleaseService",
    "mark_source_release_attention",
    "reconcile_source_releases_after_lock",
    "source_release_declaration_digest",
    "source_release_response",
    "sweep_overdue_workspace_releases",
]
