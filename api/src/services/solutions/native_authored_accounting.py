"""Existing Solution obligations completed from native per-install readback.

Runtime closure, authored Source and installed metadata are distinct evidence.
This adapter writes only the existing accounting row. It never deploys source,
changes controls, invents a generic deploy job or settles another package.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from bifrost.workspace_release import canonical_digest
from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.models.orm.solutions import Solution
from src.models.orm.workspace_promotions import SolutionDeployObligation
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solution_source_accountability import (
    UnprovenSourceConsumers,
    _verified_delivery_proof,
)
from src.services.solutions.authored_archive import read_authored_archive
from src.services.solutions.deployment_manifest import validate_runtime_closure
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.github_delivery_source import VerifiedAuthoredSolution
from src.services.solutions.native_authored_source import (
    NativeAuthoredSourceMismatch,
    native_authored_install_readback,
)
from src.services.workspace_release_projection import acquire_workspace_release_lock

COMPLETION_SCHEMA = "bifrost.native-solution-deploy-completion/v1"
UNRESOLVED = ("pending", "attention_required")


def intended_native_targets(registry: dict[str, Any] | None, repo_subpath: str) -> dict[str, str | None]:
    """Protected recipe mappings retain even missing and mixed-source targets."""
    if not registry or registry.get("target") != "production":
        raise NativeAuthoredSourceMismatch("No complete production target registry is retained")
    intended = {}
    for identity, entry in registry["installations"].items():
        packages = entry.get("package_subpaths")
        if not isinstance(packages, list) or packages != sorted(set(packages)):
            raise NativeAuthoredSourceMismatch("Complete authored package associations are absent")
        if repo_subpath in packages:
            intended[identity] = entry["organization_id"]
    if not intended:
        raise NativeAuthoredSourceMismatch("No intended native target consumes this package")
    return intended


def native_completion_evidence(
    record: SolutionDeployObligation, authored: VerifiedAuthoredSolution,
    *, target_installs: dict[str, str | None], installations: dict[str, dict[str, Any]],
    verified_at: datetime,
) -> dict[str, Any]:
    """Bind the complete declaration to every intended actual installation."""
    if (record.source_commit_sha != authored.commit_sha or record.source_tree_sha != authored.tree_sha
            or record.source_subtree_sha != authored.subtree_sha
            or record.source_content_id != authored.source_content_id
            or record.solution_slug != authored.solution_slug or record.repo_subpath != authored.repo_subpath
            or record.source_files != authored.file_manifest()):
        raise NativeAuthoredSourceMismatch("Protected authored inventory differs from the exact declaration")
    if not target_installs or set(installations) != set(target_installs):
        raise NativeAuthoredSourceMismatch("Every intended installation requires its own readback")
    archives = set()
    for identity, proof in installations.items():
        readback, provenance = proof["readback"], proof["authored_source"]
        if (readback["schema_version"] != "bifrost.native-solution-authored-readback/v1"
                or readback["solution_id"] != identity
                or readback["organization_id"] != target_installs[identity]
                or readback["source_content_id"] != authored.source_content_id
                or provenance["source_content_id"] != authored.source_content_id
                or proof["source_commit_sha"] != authored.commit_sha
                or proof["source_tree_sha"] != authored.tree_sha
                or not proof["receipt_id"] or not readback["deployment_id"]
                or canonical_digest({k: v for k, v in readback.items() if k != "evidence_id"}) != readback["evidence_id"]):
            raise NativeAuthoredSourceMismatch("Native installation evidence differs from its protected source")
        UUID(identity)
        UUID(readback["deployment_id"])
        UUID(proof["receipt_id"])
        archives.add(provenance["archive_sha256"])
    if len(archives) != 1:
        raise NativeAuthoredSourceMismatch("Native installations retain different authored archives")
    archive_sha = archives.pop()
    if len(archive_sha) != 64 or any(c not in "0123456789abcdef" for c in archive_sha):
        raise NativeAuthoredSourceMismatch("Authored archive digest is invalid")
    result = {"schema_version": COMPLETION_SCHEMA,
        "source_commit_sha": authored.commit_sha, "source_tree_sha": authored.tree_sha,
        "source_subtree_sha": authored.subtree_sha, "source_content_id": authored.source_content_id,
        "solution_id": min(target_installs), "solution_slug": authored.solution_slug,
        "candidate_id": "sha256:" + archive_sha, "source_artifact_sha256": archive_sha,
        "target_installs": target_installs, "installations": installations,
        "verified_at": verified_at.isoformat()}
    result["evidence_id"] = canonical_digest(result)
    return result


async def _installed_evidence(
    db: AsyncSession, solution: Solution, record: SolutionDeployObligation,
    policy: SolutionGitDeliveryPolicy,
) -> tuple[VerifiedAuthoredSolution, dict[str, Any]]:
    if (solution.id not in policy.solutions
            or solution.organization_id != policy.organization_id_for(solution.id)
            or solution.status != "active"
            or solution.execution_runtime_mode != "deployment-v1" or solution.active_deployment_id is None):
        raise NativeAuthoredSourceMismatch("Intended installation lacks a configured immutable runtime")
    deployment = await SolutionDeploymentRepository(db).get_runtime_closure(
        solution.active_deployment_id, solution.organization_id, solution.id)
    if deployment is None or deployment.state != "active":
        raise NativeAuthoredSourceMismatch("Intended installation has no active deployment")
    manifest, resolution = validate_runtime_closure(deployment.compiled_manifest,
        deployment.resolution_map, deployment.dependencies,
        expected_manifest_hash=deployment.compiled_manifest_hash,
        expected_resolution_hash=deployment.resolution_map_hash)
    proof = await _verified_delivery_proof(db, deployment, manifest, resolution, policy)
    if (not proof or proof["commit_sha"] != record.source_commit_sha
            or proof["tree_sha"] != record.source_tree_sha or not proof.get("authored_source")):
        raise NativeAuthoredSourceMismatch("No exact successful native authored delivery is retained")
    storage = SolutionDeploymentStorage(solution.id, deployment.id)
    authored = await read_authored_archive(storage, proof["authored_source"],
        expected_commit_sha=record.source_commit_sha, expected_tree_sha=record.source_tree_sha)
    readback = await native_authored_install_readback(db, solution, authored,
        expected_active_deployment_id=deployment.id,
        expected_active_manifest_hash=deployment.compiled_manifest_hash)
    registry = proof.get("installation_registry")
    return authored, {"readback": readback, "receipt_id": proof["receipt_id"],
        "source_commit_sha": proof["commit_sha"], "source_tree_sha": proof["tree_sha"],
        "authored_source": proof["authored_source"], "installation_registry": registry}


async def reconcile_native_solution_deploy_obligations(
    db: AsyncSession, *, policy: SolutionGitDeliveryPolicy | None = None,
    source_release_id: UUID | None = None, limit: int = 100,
) -> list[UUID]:
    """Success/replay, late declaration and scheduler share the same verifier.

Lock order remains Live fence -> native installs -> accounting rows. Immutable
activation and generic deploy both lock each install row before writes; generic
deploy refuses an active immutable pointer. All ordinary managed-entity write
routes reject controls/metadata changes outside that deployment path.
Caller owns commit; no new background job, endpoint or manual cleanup is needed.
"""
    policy = policy or get_settings().solution_git_delivery_policy
    if policy is None:
        return []
    query = select(SolutionDeployObligation).where(
        SolutionDeployObligation.organization_id == policy.organization_id,
        SolutionDeployObligation.declared_disposition == "solution_deploy_required",
        SolutionDeployObligation.disposition.in_(UNRESOLVED))
    if source_release_id is not None:
        query = query.where(SolutionDeployObligation.source_release_id == source_release_id)
    # updated_at rotates failures rather than letting an unsupported package
    # monopolize the bounded recovery sweep. No prior completion is reopened.
    selected = list((await db.scalars(query.order_by(SolutionDeployObligation.updated_at,
        SolutionDeployObligation.id).limit(min(max(limit, 1), 100)))).all())
    if not selected:
        return []
    packages = {(row.solution_slug, row.repo_subpath) for row in selected}
    await acquire_workspace_release_lock(db, None)
    # Row locks cannot fence insertion or inactive->active membership changes.
    # Take this before any install row lock: EXCLUSIVE also waits for existing
    # SELECT FOR UPDATE holders, avoiding a table-upgrade/row-lock cycle. Plain
    # reads remain available. All native/generic writers take install row locks.
    await db.execute(text("LOCK TABLE solutions IN EXCLUSIVE MODE"))
    installs = list((await db.scalars(select(Solution).where(Solution.status == "active",
        tuple_(Solution.slug, Solution.repo_subpath).in_(packages)).order_by(Solution.id)
        .with_for_update().execution_options(populate_existing=True))).all())
    records = list((await db.scalars(query.where(SolutionDeployObligation.id.in_([r.id for r in selected]))
        .order_by(SolutionDeployObligation.id).with_for_update()
        .execution_options(populate_existing=True))).all())
    completed = []
    now = datetime.now(UTC)
    for record in records:
        record.updated_at = now
        family = [s for s in installs if (s.slug, s.repo_subpath) == (record.solution_slug, record.repo_subpath)]
        if not family:
            continue
        target_installs = {str(s.id): str(s.organization_id) if s.organization_id else None for s in family}
        try:
            installations = {}
            authored = None
            intended = None
            for solution in family:
                source, proof = await _installed_evidence(db, solution, record, policy)
                registry = proof["installation_registry"]
                # Target membership comes from every protected recipe, NOT the
                # surviving active rows. A missing/inactive intended install
                # must remain visible and prevent completion.
                declared_targets = intended_native_targets(registry, record.repo_subpath)
                if not declared_targets or declared_targets != target_installs:
                    raise NativeAuthoredSourceMismatch("Intended production target set differs from active family")
                if intended is not None and intended != registry:
                    raise NativeAuthoredSourceMismatch("Native installations retain different target registries")
                intended = registry
                installations[str(solution.id)] = proof
                authored = source
            assert authored is not None
            evidence = native_completion_evidence(record, authored, target_installs=target_installs,
                installations=installations, verified_at=now)
        except (NativeAuthoredSourceMismatch, UnprovenSourceConsumers, ValueError, KeyError, TypeError):
            # Leave debt open, preserving any prior mismatch/provenance receipt.
            # Infrastructure/storage failures are not swallowed as proof.
            continue
        record.disposition, record.reason = "released", None
        record.solution_id = UUID(evidence["solution_id"])
        record.deploy_job_id = None
        record.candidate_id = evidence["candidate_id"]
        record.source_artifact_sha256 = evidence["source_artifact_sha256"]
        record.completion_evidence, record.resolved_at = evidence, now
        completed.append(record.id)
    await db.flush()
    return completed
