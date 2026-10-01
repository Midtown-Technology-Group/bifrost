"""Close Root-source debt from verified installed consumers, never from a run.

This adapter changes accounting only. It cannot activate, retire or write source.
Missing mappings, mutable installs and uncertain loose consumers fail closed.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bifrost.workspace_impact import analyze_workspace_impact, transitive_distances
from bifrost.workspace_release import canonical_digest
from src.config import get_settings
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.operation_receipts import OperationReceipt
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.models.orm.workspace_promotions import WorkspaceSourceRelease
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.operation_receipts import canonical_operation_scope_key, canonical_request_fingerprint
from src.services.solutions.deployment_manifest import validate_runtime_closure
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.source_revision import _require_registration
from src.services.workspace_release_files import global_active_workspace_release_descriptor
from src.services.workspace_release_projection import acquire_workspace_release_lock
from src.services.workspace_release_runtime import inspect_workspace_release_registration_bindings
from src.services.workspace_release_storage import WorkspaceReleaseStorage

MAPPING_SCHEMA = "bifrost.solution-git-source-mapping/v1"
COMPLETION_SCHEMA = "bifrost.solution-owned-source-completion/v1"
UNRESOLVED = ("pending", "attention_required", "deferred")
ACCEPTED = (ExecutionStatus.SCHEDULED, ExecutionStatus.PENDING, ExecutionStatus.RUNNING,
    ExecutionStatus.CANCELLING, ExecutionStatus.STUCK)


class UnprovenSourceConsumers(ValueError):
    """The observed platform cannot establish complete source ownership."""


@dataclass(frozen=True)
class SourceConsumer:
    deployment_id: str
    solution_id: str
    organization_id: str | None
    manifest_hash: str
    # One repository path may have several runtime aliases in one installation.
    sources: dict[str, dict[str, str]]
    proof: dict[str, Any] = field(default_factory=dict)
    admission: bool = True


def completion_for_source(record: Any, consumers: list[SourceConsumer], *,
                          loose_hashes: dict[str, str], uncertain_loose: bool,
                          verified_at: datetime) -> dict[str, Any] | None:
    """Pure per-path decision after pointer, registration and byte readback.

    A consumer with another commit may retain the same shared bytes, but at
    least one exact protected commit/tree must attest each declared path.
    Older declarations with different bytes remain unresolved; they require
    explicit supersession evidence rather than a time-order guess.
    """
    paths = dict(record.paths or {})
    if (record.disposition not in UNRESOLVED or not paths or uncertain_loose
            or any(digest is None for digest in paths.values())):
        return None
    per_path: dict[str, Any] = {}
    for path, expected in sorted(paths.items()):
        matches = [consumer for consumer in consumers if path in consumer.sources]
        controls = [consumer for consumer in consumers
            if path in consumer.proof.get("control_hashes", {})]
        if not matches and not controls:
            return None
        if path in loose_hashes and loose_hashes[path] != expected:
            return None
        anchors = []
        bindings = []
        for consumer in matches:
            aliases = consumer.sources[path]
            if not aliases or any(digest != expected for digest in aliases.values()):
                return None
            bindings.append({"deployment_id": consumer.deployment_id,
                "solution_id": consumer.solution_id, "organization_id": consumer.organization_id,
                "compiled_manifest_hash": consumer.manifest_hash, "runtime_sha256": aliases,
                "admission": consumer.admission})
            proof = consumer.proof
            if (consumer.admission and proof.get("commit_sha") == record.source_commit_sha
                    and proof.get("tree_sha") == record.source_tree_sha):
                anchors.append({"deployment_id": consumer.deployment_id,
                    "receipt_id": proof["receipt_id"], "artifact_digest": proof["artifact_digest"]})
        for consumer in controls:
            proof = consumer.proof
            if (not consumer.admission or proof["control_hashes"][path] != expected
                    or proof.get("commit_sha") != record.source_commit_sha
                    or proof.get("tree_sha") != record.source_tree_sha):
                return None
            registry = proof.get("installation_registry")
            if registry and registry.get("path") == path:
                installs = registry.get("installations", {})
                for identity, entry in installs.items():
                    if not any(other.admission and other.solution_id == identity
                            and other.organization_id == entry["organization_id"]
                            and other.proof.get("recipe_path") == entry["recipe_path"]
                            and other.proof.get("commit_sha") == record.source_commit_sha
                            and other.proof.get("tree_sha") == record.source_tree_sha
                            and other.proof.get("control_hashes", {}).get(path) == expected
                            and other.proof.get("installation_registry") == registry
                            for other in consumers):
                        return None
            anchors.append({"deployment_id": consumer.deployment_id,
                "receipt_id": proof["receipt_id"], "artifact_digest": proof["artifact_digest"]})
        if not anchors:
            return None
        per_path[path] = {"sha256": expected, "consumers": bindings,
            "protected_git_anchors": anchors,
            "loose_runtime_sha256": loose_hashes.get(path)}
    evidence: dict[str, Any] = {"schema_version": COMPLETION_SCHEMA,
        "source_release_id": str(record.id), "source_commit_sha": record.source_commit_sha,
        "source_tree_sha": record.source_tree_sha, "paths": per_path,
        "verified_at": verified_at.isoformat()}
    evidence["evidence_id"] = canonical_digest(evidence)
    return evidence


def supersession_for_source(record: Any, consumers: list[SourceConsumer], *,
                            loose_hashes: dict[str, str], uncertain_loose: bool,
                            verified_at: datetime) -> dict[str, Any] | None:
    """A later protected descendant must prove replacement of every old path."""
    if (record.disposition not in UNRESOLVED or record.declaration_actor != "github_actions_oidc"
            or not record.paths or any(value is None for value in record.paths.values())):
        return None
    for candidate in consumers:
        proof = candidate.proof
        if not candidate.admission or record.source_commit_sha not in proof.get("ancestor_commit_shas", []):
            continue
        commit, tree = proof.get("commit_sha"), proof.get("tree_sha")
        replacements: dict[str, str] = {}
        for path in record.paths:
            values = set()
            for consumer in consumers:
                if (consumer.admission and consumer.proof.get("commit_sha") == commit
                        and consumer.proof.get("tree_sha") == tree
                        and record.source_commit_sha in consumer.proof.get("ancestor_commit_shas", [])):
                    values.update(consumer.sources.get(path, {}).values())
                    digest = consumer.proof.get("control_hashes", {}).get(path)
                    if digest is not None:
                        values.add(digest)
            if len(values) != 1:
                break
            replacements[path] = values.pop()
        if set(replacements) != set(record.paths):
            continue
        readback = completion_for_source(SimpleNamespace(id=record.id, disposition=record.disposition,
            source_commit_sha=commit, source_tree_sha=tree, paths=replacements), consumers,
            loose_hashes=loose_hashes, uncertain_loose=uncertain_loose, verified_at=verified_at)
        if readback is None:
            continue
        evidence = {"schema_version": "bifrost.solution-owned-source-supersession/v1",
            "source_release_id": str(record.id), "source_commit_sha": record.source_commit_sha,
            "source_tree_sha": record.source_tree_sha, "original_sha256": dict(record.paths),
            "superseding_source_commit_sha": commit, "superseding_source_tree_sha": tree,
            "ancestor_attestation_deployment_id": candidate.deployment_id, "readback": readback}
        evidence["evidence_id"] = canonical_digest(evidence)
        return evidence
    return None


async def _verified_delivery_proof(db: AsyncSession, deployment: Any, manifest: Any,
                                   resolution: Any, policy: SolutionGitDeliveryPolicy) -> dict[str, Any]:
    proof = (deployment.validation_result or {}).get("github_delivery") or {}
    if (proof.get("source_mapping_schema") != MAPPING_SCHEMA
            or proof.get("solution_id") != str(deployment.solution_id)
            or proof.get("organization_id") != (str(deployment.organization_id)
                if deployment.organization_id is not None else None)
            or proof.get("repository") != policy.repository
            or proof.get("repository_id") != policy.repository_id
            or proof.get("repository_owner_id") != policy.repository_owner_id
            or proof.get("commit_sha") != manifest.git.commit_sha):
        if proof:
            raise UnprovenSourceConsumers("Protected delivery mapping is absent or outside repository policy")
        return {}
    hashes = {path: ref.content_hash for path, ref in {**resolution.sources, **resolution.resources}.items()}
    mapping = proof.get("repository_paths")
    if (proof.get("runtime_hashes") != hashes or not isinstance(mapping, dict)
            or set(mapping) != set(hashes)
            or any(not isinstance(value, str) for value in mapping.values())):
        raise UnprovenSourceConsumers("Protected repository mapping differs from deployment closure")
    try:
        receipt = await db.get(OperationReceipt, UUID(proof["receipt_id"]))
        identity = {"repository_id": policy.repository_id, "solution_id": str(deployment.solution_id),
            "source_commit_sha": proof["commit_sha"], "artifact_digest": proof["artifact_digest"],
            "ci_run_id": proof["ci_run_id"], "ci_run_attempt": proof["ci_run_attempt"],
            "producer_run_id": proof["producer_run_id"], "producer_run_attempt": proof["producer_run_attempt"]}
        if (receipt is None or receipt.namespace != "solution.git-source-delivery"
                or receipt.status != "succeeded"
                or receipt.scope_key != canonical_operation_scope_key(identity)
                or receipt.request_fingerprint != canonical_request_fingerprint(identity)):
            raise UnprovenSourceConsumers("Protected delivery has no matching successful receipt")
    except (ValueError, KeyError, TypeError) as exc:
        raise UnprovenSourceConsumers("Protected delivery identity is incomplete") from exc
    return proof


async def _collect_consumers(db: AsyncSession, policy: SolutionGitDeliveryPolicy) -> tuple[list[SourceConsumer], dict[str, str], bool]:
    # The delivery hook calls this AFTER releasing its install write lock. Take
    # the global Live fence, then all installs in stable UUID order, then source
    # rows. No one-install transaction may acquire this aggregate lock set.
    await acquire_workspace_release_lock(db, None)
    release = await global_active_workspace_release_descriptor(db)
    solutions = list((await db.scalars(select(Solution).where(Solution.status == "active")
        .order_by(Solution.id).with_for_update().execution_options(populate_existing=True))).all())
    repository = SolutionDeploymentRepository(db)
    consumers: dict[UUID, SourceConsumer] = {}
    visiting: set[UUID] = set()

    async def visit(deployment_id: UUID, *, admission: bool, expected_solution: UUID | None = None,
                    expected_scope: UUID | None = None, expected_bundle: str | None = None) -> None:
        if deployment_id in visiting:
            raise UnprovenSourceConsumers("Solution dependency cycle")
        deployment = await repository.get_by_id_for_runtime(deployment_id)
        if (deployment is None or deployment.state not in {"active", "superseded", "committed_unpushed"}
                or expected_solution is not None and deployment.solution_id != expected_solution
                or expected_solution is not None and deployment.organization_id != expected_scope
                or expected_bundle is not None and deployment.bundle_hash != expected_bundle):
            raise UnprovenSourceConsumers("Pinned Solution dependency or install differs")
        if deployment_id in consumers and (consumers[deployment_id].admission or not admission):
            return
        visiting.add(deployment_id)
        manifest, resolution = validate_runtime_closure(deployment.compiled_manifest, deployment.resolution_map,
            deployment.dependencies, expected_manifest_hash=deployment.compiled_manifest_hash,
            expected_resolution_hash=deployment.resolution_map_hash)
        if manifest.solution_id != deployment.solution_id or manifest.deployment_id != deployment.id:
            raise UnprovenSourceConsumers("Compiled runtime identity differs from its deployment")
        proof = await _verified_delivery_proof(db, deployment, manifest, resolution, policy)
        mapping = proof.get("repository_paths")
        if mapping is None:
            # The reviewed Live handoff preserves Root paths exactly. Follow
            # immutable revision parents to that origin rather than guessing
            # that an arbitrary Solution's runtime aliases are Git paths.
            origin = deployment
            seen = {origin.id}
            while (origin.validation_result or {}).get("schema_version") != "bifrost.workspace-live-handoff/v1":
                if origin.parent_deployment_id is None or origin.parent_deployment_id in seen:
                    raise UnprovenSourceConsumers("Solution has no proven repository path origin")
                seen.add(origin.parent_deployment_id)
                if len(seen) > 100:
                    raise UnprovenSourceConsumers("Solution origin exceeds its traversal bound")
                origin = await repository.get_by_id_for_runtime(origin.parent_deployment_id)
                if origin is None or origin.solution_id != deployment.solution_id:
                    raise UnprovenSourceConsumers("Solution origin chain differs")
            _, original = validate_runtime_closure(origin.compiled_manifest, origin.resolution_map,
                origin.dependencies, expected_manifest_hash=origin.compiled_manifest_hash,
                expected_resolution_hash=origin.resolution_map_hash)
            if not set(resolution.sources) <= set(original.sources) or resolution.resources:
                raise UnprovenSourceConsumers("Unmapped added source or resource")
            mapping = {path: path for path in resolution.sources}
        if admission:
            registrations = list((await db.scalars(select(Workflow).where(
                Workflow.solution_id == deployment.solution_id, Workflow.is_active.is_(True))
                .options(selectinload(Workflow.roles)).order_by(Workflow.id)
                .with_for_update(of=Workflow).execution_options(populate_existing=True))).all())
            if {row.id for row in registrations} != {item.resolved_id for item in resolution.workflows.values()}:
                raise UnprovenSourceConsumers("Installed workflow registrations differ")
            for row in registrations:
                _require_registration(row, resolution.resolve_workflow_id(row.id))
        storage = SolutionDeploymentStorage(deployment.solution_id, deployment.id)
        slots = asyncio.Semaphore(16)
        sources: dict[str, dict[str, str]] = {}

        async def read(path: str, ref: Any, resource: bool) -> None:
            async with slots:
                raw = await storage.read_resource(path, ref.size_bytes) if resource else await storage.read_runtime_file(path)
            digest = hashlib.sha256(raw).hexdigest()
            if "sha256:" + digest != ref.content_hash:
                raise UnprovenSourceConsumers("Installed immutable runtime bytes differ")
            sources.setdefault(mapping[path], {})[path] = digest

        await asyncio.gather(*(read(path, ref, False) for path, ref in resolution.sources.items()),
            *(read(path, ref, True) for path, ref in resolution.resources.items()))
        consumers[deployment_id] = SourceConsumer(str(deployment.id), str(deployment.solution_id),
            str(deployment.organization_id) if deployment.organization_id is not None else None,
            deployment.compiled_manifest_hash, sources, proof, admission)
        for edge in deployment.dependencies:
            child = await repository.get_by_id_for_runtime(edge.dependency_deployment_id)
            if child is None or child.organization_id not in {None, deployment.organization_id}:
                raise UnprovenSourceConsumers("Solution dependency scope differs")
            await visit(edge.dependency_deployment_id, admission=False,
                expected_solution=edge.dependency_solution_id, expected_scope=child.organization_id,
                expected_bundle=edge.resolved_bundle_hash)
        visiting.remove(deployment_id)

    for solution in solutions:
        if solution.execution_runtime_mode != "deployment-v1" or solution.active_deployment_id is None:
            raise UnprovenSourceConsumers("Mutable Solution consumer remains")
        await visit(solution.active_deployment_id, admission=True, expected_solution=solution.id,
            expected_scope=solution.organization_id)
    # Superseded dependency/accepted pins remain real consumers until drained.
    accepted_attempt = exists(select(ExecutionAttempt.id).where(
        ExecutionAttempt.logical_job_type == "workflow",
        ExecutionAttempt.logical_job_id == Execution.id,
        ExecutionAttempt.completed_at.is_(None)))
    accepted_execution = or_(Execution.status.in_(ACCEPTED), accepted_attempt)
    pins = (await db.scalars(select(Execution.solution_deployment_id).where(
        accepted_execution, Execution.solution_deployment_id.is_not(None)).distinct())).all()
    for identity in pins:
        if identity is not None:
            await visit(identity, admission=False)
    loose = list((await db.scalars(select(Workflow).where(Workflow.solution_id.is_(None),
        Workflow.is_active.is_(True)).order_by(Workflow.id).with_for_update())).all())
    loose_hashes: dict[str, str] = {}
    if loose:
        if release is None or any(row.path not in release.governed_paths for row in loose):
            raise UnprovenSourceConsumers("Unpinned loose source consumer remains")
        bindings = await inspect_workspace_release_registration_bindings(db, release, for_update=True)
        if {item.workflow_id for item in bindings} != {row.id for row in loose} or any(item.status != "bound" for item in bindings):
            raise UnprovenSourceConsumers("Loose registrations differ from immutable Live")
        python_paths = [path for path in release.source_hashes if path.endswith(".py")]
        contents = await WorkspaceReleaseStorage(release.runtime_storage_prefix).read_many(python_paths)
        if any(path not in contents or hashlib.sha256(contents[path]).hexdigest() != release.source_hashes[path]
                for path in python_paths):
            raise UnprovenSourceConsumers("Live source snapshot differs")
        analysis = analyze_workspace_impact(contents)
        reachable = set().union(*(set(transitive_distances(row.path, analysis.edges)) for row in loose))
        uncertain = bool(reachable & (set(analysis.unresolved_imports) | set(analysis.ambiguous_references)
            | set(analysis.dynamic_importers) | set(analysis.dynamic_reference_importers) | set(analysis.dynamic_exporters)))
        # The import graph cannot prove arbitrary operational file reads. Keep
        # these loose consumers unresolved until their reviewed handoff.
        uncertain |= any(b"files.read" in contents[path] or b"files.read_bytes" in contents[path]
            for path in reachable if path in contents)
        loose_hashes = {path: release.source_hashes[path] for path in reachable if path in release.source_hashes}
    else:
        uncertain = False
    # An accepted legacy execution may have an unpinned module resolver. It
    # cannot be inferred safe from an absence of active loose registrations.
    legacy = await db.scalar(select(Execution.id).where(accepted_execution,
        Execution.solution_deployment_id.is_(None)).limit(1))
    return list(consumers.values()), loose_hashes, uncertain or legacy is not None


async def reconcile_solution_owned_source(db: AsyncSession, *, limit: int = 100,
                                         policy: SolutionGitDeliveryPolicy | None = None,
                                         accountability_organization_id: UUID | None = None) -> list[UUID]:
    """Bounded recovery used after declaration/delivery and by the scheduler.

    Caller owns commit. No completed ledger entry is reopened. Infrastructure
    read failures propagate; incomplete consumer evidence leaves debt intact.
    """
    settings = get_settings()
    policy = policy or settings.solution_git_delivery_policy
    if policy is None:
        return []
    if accountability_organization_id is None:
        from src.services.github_actions_oidc import workspace_source_release_tracking_organization_id
        accountability_organization_id = workspace_source_release_tracking_organization_id(settings)
        if (accountability_organization_id is None
                or settings.workspace_source_release_oidc_repository != policy.repository
                or settings.workspace_source_release_oidc_repository_id != policy.repository_id
                or settings.workspace_source_release_oidc_repository_owner_id != policy.repository_owner_id):
            return []
    query = select(WorkspaceSourceRelease).where(
        WorkspaceSourceRelease.organization_id == accountability_organization_id,
        WorkspaceSourceRelease.disposition.in_(UNRESOLVED))
    if await db.scalar(query.limit(1)) is None:
        return []
    try:
        consumers, loose_hashes, uncertain = await _collect_consumers(db, policy)
    except (UnprovenSourceConsumers, ValueError):
        return []
    records = list((await db.scalars(query.order_by(WorkspaceSourceRelease.created_at.desc())
        .limit(min(max(limit, 1), 1000)).with_for_update().execution_options(populate_existing=True))).all())
    now = datetime.now(UTC)
    completed = []
    for record in records:
        evidence = completion_for_source(record, consumers, loose_hashes=loose_hashes,
            uncertain_loose=uncertain, verified_at=now)
        disposition = "released"
        if evidence is None:
            evidence = supersession_for_source(record, consumers, loose_hashes=loose_hashes,
                uncertain_loose=uncertain, verified_at=now)
            disposition = "superseded"
        if evidence is None:
            continue
        record.disposition, record.reason = disposition, None
        record.completion_evidence, record.resolved_at = evidence, now
        completed.append(record.id)
    await db.flush()
    return completed
