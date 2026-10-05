"""Existing Solution obligations completed from native per-install readback.

Runtime closure, authored Source and installed metadata are distinct evidence.
This adapter writes only the existing accounting row. It never deploys source,
changes controls, invents a generic deploy job or settles another package.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from bifrost.workspace_release import canonical_digest
from sqlalchemy import select, text, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.models.orm.solutions import Solution
from src.models.orm.workspace_promotions import SolutionDeployObligation, WorkspaceSourceRelease
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solution_source_accountability import (
    UnprovenSourceConsumers,
    _verified_delivery_proof,
)
from src.services.solutions.authored_archive import read_authored_archive
from src.services.solutions.deployment_manifest import canonical_json, validate_runtime_closure
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.github_delivery_source import MAX_HISTORICAL_AUTHORED_SOURCES, VerifiedAuthoredSolution
from src.services.solutions.native_authored_source import (
    NativeAuthoredSourceMismatch,
    _read_native_runtime,
    _VerifiedNativeRuntime,
    native_authored_install_readback,
)

COMPLETION_SCHEMA = "bifrost.native-solution-deploy-completion/v1"
SUPERSESSION_SCHEMA = "bifrost.native-solution-deploy-supersession/v1"
UNRESOLVED = ("pending", "attention_required")
MAX_CACHED_NATIVE_BYTES = 64 * 1024 * 1024


def _cached_native_evidence_size(
    source: VerifiedAuthoredSolution, proof: dict[str, Any], runtime: _VerifiedNativeRuntime
) -> int:
    return (sum(len(path.encode()) + len(raw) for path, raw in source.files.items())
        + sum(len(path.encode()) + len(raw) for path, raw in runtime.files.items())
        + sum(len(path.encode()) + len(raw) for path, raw in runtime.resources.items())
        + len(canonical_json(proof)))


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
    require_declared_authored_source(record, authored)
    return _native_installation_evidence(authored, target_installs=target_installs,
        installations=installations, verified_at=verified_at)


def require_declared_authored_source(record, authored: VerifiedAuthoredSolution) -> None:
    if (record.source_commit_sha != authored.commit_sha or record.source_tree_sha != authored.tree_sha
            or record.source_subtree_sha != authored.subtree_sha
            or record.source_content_id != authored.source_content_id
            or record.solution_slug != authored.solution_slug or record.repo_subpath != authored.repo_subpath
            or record.source_files != authored.file_manifest()):
        raise NativeAuthoredSourceMismatch("Protected authored inventory differs from the exact declaration")


def _require_historical_inventory(record, history: dict[str, Any]) -> None:
    expected = {"commit_sha": record.source_commit_sha, "tree_sha": record.source_tree_sha,
        "subtree_sha": record.source_subtree_sha, "source_content_id": record.source_content_id,
        "solution_slug": record.solution_slug, "repo_subpath": record.repo_subpath,
        "source_files": record.source_files}
    if not isinstance(history, dict) or any(history.get(key) != value for key, value in expected.items()):
        raise NativeAuthoredSourceMismatch("Protected historical inventory differs from the original declaration")


def _require_history_origin(record, origin) -> None:
    if (origin is None or origin.id != record.source_release_id
            or origin.declaration_actor != "github_actions_oidc"
            or origin.organization_id != record.organization_id
            or origin.source_commit_sha != record.source_commit_sha
            or origin.source_tree_sha != record.source_tree_sha):
        raise NativeAuthoredSourceMismatch("Historical obligation has no matching original producer declaration")


def native_supersession_evidence(
    record: SolutionDeployObligation, authored: VerifiedAuthoredSolution,
    *, target_installs: dict[str, str | None], installations: dict[str, dict[str, Any]],
    verified_at: datetime,
) -> dict[str, Any]:
    """A complete descendant replaces old authored input; it did not deploy it."""
    if record.solution_slug != authored.solution_slug or record.repo_subpath != authored.repo_subpath:
        raise NativeAuthoredSourceMismatch("Native descendant changes the authored package identity")
    for proof in installations.values():
        if record.source_commit_sha == authored.commit_sha or record.source_commit_sha not in proof.get("ancestor_commit_shas", []):
            raise NativeAuthoredSourceMismatch("Native supersession lacks verified first-parent ancestry")
        _require_historical_inventory(record, proof.get("historical_authored_source"))
    result = _native_installation_evidence(authored, target_installs=target_installs,
        installations=installations, verified_at=verified_at)
    result.pop("evidence_id")
    result.update(schema_version=SUPERSESSION_SCHEMA,
        superseded_source_commit_sha=record.source_commit_sha,
        superseded_source_tree_sha=record.source_tree_sha,
        superseded_source_subtree_sha=record.source_subtree_sha,
        superseded_source_content_id=record.source_content_id,
        superseded_source_files=record.source_files)
    result["evidence_id"] = canonical_digest(result)
    return result


def _native_installation_evidence(
    authored: VerifiedAuthoredSolution, *, target_installs: dict[str, str | None],
    installations: dict[str, dict[str, Any]], verified_at: datetime,
) -> dict[str, Any]:
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
    *, collected: tuple[VerifiedAuthoredSolution, dict[str, Any], _VerifiedNativeRuntime] | None = None,
) -> tuple[VerifiedAuthoredSolution, dict[str, Any], _VerifiedNativeRuntime]:
    if (solution.id not in policy.solutions
            or solution.organization_id != policy.organization_id_for(solution.id)
            or solution.status != "active"
            or solution.execution_runtime_mode != "deployment-v1" or solution.active_deployment_id is None):
        raise NativeAuthoredSourceMismatch("Intended installation lacks a configured immutable runtime")
    deployment = await SolutionDeploymentRepository(db).get_runtime_closure(
        solution.active_deployment_id, solution.organization_id, solution.id)
    if deployment is None or deployment.state != "active":
        raise NativeAuthoredSourceMismatch("Intended installation has no active deployment")
    # A prior delivery can retain this ORM instance across its SQL checkpoint.
    # Completion must read durable receipt provenance, never a cached proof.
    await db.refresh(deployment, attribute_names=["state", "compiled_manifest", "resolution_map",
        "compiled_manifest_hash", "resolution_map_hash", "dependencies", "validation_result"])
    manifest, resolution = validate_runtime_closure(deployment.compiled_manifest,
        deployment.resolution_map, deployment.dependencies,
        expected_manifest_hash=deployment.compiled_manifest_hash,
        expected_resolution_hash=deployment.resolution_map_hash)
    proof = await _verified_delivery_proof(db, deployment, manifest, resolution, policy)
    if not proof or not proof.get("authored_source"):
        raise NativeAuthoredSourceMismatch("No successful native authored delivery is retained")
    historical = None
    if proof["commit_sha"] != record.source_commit_sha:
        # Fetch only the original producer anchors. Loading the ORM parent
        # would also eagerly materialize every unrelated child file manifest.
        origin = (await db.execute(select(WorkspaceSourceRelease.id,
            WorkspaceSourceRelease.organization_id, WorkspaceSourceRelease.source_commit_sha,
            WorkspaceSourceRelease.source_tree_sha, WorkspaceSourceRelease.declaration_actor)
            .where(WorkspaceSourceRelease.id == record.source_release_id))).first()
        _require_history_origin(record, origin)
        if record.source_commit_sha not in proof.get("ancestor_commit_shas", []):
            raise NativeAuthoredSourceMismatch("No verified native descendant is retained")
        history = proof.get("authored_source_history", [])
        if not isinstance(history, list) or len(history) > MAX_HISTORICAL_AUTHORED_SOURCES:
            raise NativeAuthoredSourceMismatch("Retained historical authored inventory exceeds its bound")
        matches = [item for item in history
            if isinstance(item, dict) and item.get("commit_sha") == record.source_commit_sha]
        if len(matches) != 1:
            raise NativeAuthoredSourceMismatch("No unique historical authored archive is retained")
        historical = matches[0]
        _require_historical_inventory(record, historical)
    elif proof["tree_sha"] != record.source_tree_sha:
        raise NativeAuthoredSourceMismatch("Exact native declaration tree differs")
    if collected is None:
        storage = SolutionDeploymentStorage(solution.id, deployment.id)
        if historical is not None:
            prior = await read_authored_archive(storage, historical,
                expected_commit_sha=record.source_commit_sha, expected_tree_sha=record.source_tree_sha)
            require_declared_authored_source(record, prior)
        authored = await read_authored_archive(storage, proof["authored_source"],
            expected_commit_sha=proof["commit_sha"], expected_tree_sha=proof["tree_sha"])
        runtime = await _read_native_runtime(solution.id, deployment, resolution)
    else:
        authored, prior, runtime = collected
        if canonical_digest(proof) != prior["delivery_proof_hash"]:
            raise NativeAuthoredSourceMismatch("Native delivery receipt changed after byte collection")
    readback = await native_authored_install_readback(db, solution, authored,
        expected_active_deployment_id=deployment.id,
        expected_active_manifest_hash=deployment.compiled_manifest_hash, _verified_runtime=runtime)
    registry = proof.get("installation_registry")
    return authored, {"readback": readback, "receipt_id": proof["receipt_id"],
        "source_commit_sha": proof["commit_sha"], "source_tree_sha": proof["tree_sha"],
        "authored_source": proof["authored_source"], "installation_registry": registry,
        "delivery_proof_hash": canonical_digest(proof),
        "ancestor_commit_shas": proof.get("ancestor_commit_shas", []),
        "historical_authored_source": historical}, runtime


async def reconcile_native_solution_deploy_obligations(
    db: AsyncSession, *, policy: SolutionGitDeliveryPolicy | None = None,
    source_release_id: UUID | None = None, limit: int = 100,
) -> list[UUID]:
    """Success/replay, late declaration and scheduler share the same verifier.

Remote byte proof precedes a five-second membership/metadata fence.
Solution membership and pointer writes wait at most one second for its SHARE lock.
Install reads stay plain: waiting on a writer's row lock here would deadlock
with its pending table-write lock. Managed component writers also update their
Solution pointer atomically. Reviewed shared Root tables retain their existing
FOR SHARE verification locks. Historical execution pins need no Live fence.
This entry point commits its accounting checkpoint before the caller's separate
Root reconciliation; all delivery/declaration hooks enter after durable commit.
No new background job, endpoint or manual cleanup is needed.
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
    installs = list((await db.scalars(select(Solution).where(Solution.status == "active",
        tuple_(Solution.slug, Solution.repo_subpath).in_(packages)).order_by(Solution.id)
        .execution_options(populate_existing=True))).all())
    # Remote proof never holds the Solution-write fence. Reuse one exact
    # install/commit proof across declarations; each final DB read stays fresh.
    cache = {}
    cached_bytes = 0
    for record in selected:
        for solution in installs:
            if (solution.slug, solution.repo_subpath) != (record.solution_slug, record.repo_subpath):
                continue
            key = (solution.id, record.source_commit_sha, record.source_tree_sha)
            if key in cache:
                continue
            if cached_bytes >= MAX_CACHED_NATIVE_BYTES:
                cache[key] = None
                continue
            try:
                collected = await _installed_evidence(db, solution, record, policy)
                source, proof, runtime = collected
                size = _cached_native_evidence_size(source, proof, runtime)
                if cached_bytes + size > MAX_CACHED_NATIVE_BYTES:
                    # Omitted proof is never omitted membership. The complete
                    # target set below still prevents this family completing.
                    cache[key] = None
                else:
                    cache[key] = collected
                    cached_bytes += size
            except (NativeAuthoredSourceMismatch, UnprovenSourceConsumers, ValueError, KeyError, TypeError):
                cache[key] = None
            finally:
                # In particular release shared Root-table read locks before
                # another family performs remote storage I/O.
                await db.commit()
    selected_ids = [row.id for row in selected]
    try:
        async with asyncio.timeout(5):
            async with db.begin_nested():
                old_timeout = await db.scalar(text("SELECT current_setting('lock_timeout')"))
                await db.execute(text("SET LOCAL lock_timeout = '1s'"))
                await db.execute(text("LOCK TABLE solutions IN SHARE MODE"))
                completed = await _complete_native_obligations(db, query, selected_ids, packages, cache, policy)
                await db.execute(text("SELECT set_config('lock_timeout', :value, true)"), {"value": old_timeout})
            # Release membership immediately, before separate Root/Live proof.
            await db.commit()
            return completed
    except TimeoutError:
        await db.rollback()
        return []
    except DBAPIError as exc:
        await db.rollback()
        if getattr(exc.orig, "sqlstate", None) in {"55P03", "57014"}:
            return []
        raise


async def _complete_native_obligations(db, query, selected_ids, packages, cache, policy):
    installs = list((await db.scalars(select(Solution).where(Solution.status == "active",
        tuple_(Solution.slug, Solution.repo_subpath).in_(packages)).order_by(Solution.id)
        .execution_options(populate_existing=True))).all())
    records = list((await db.scalars(query.where(SolutionDeployObligation.id.in_(selected_ids))
        .order_by(SolutionDeployObligation.id).with_for_update()
        .execution_options(populate_existing=True))).all())
    origins = {}
    release_ids = {row.source_release_id for row in records}
    if release_ids:
        origin_rows = (await db.execute(select(WorkspaceSourceRelease.id,
            WorkspaceSourceRelease.organization_id, WorkspaceSourceRelease.source_commit_sha,
            WorkspaceSourceRelease.source_tree_sha, WorkspaceSourceRelease.declaration_actor)
            .where(WorkspaceSourceRelease.id.in_(release_ids)))).all()
        origins = {row.id: row for row in origin_rows}

    # Under the membership fence, perform one fresh authoritative deployment,
    # receipt, closure and runtime readback per install. The pre-fence cache
    # holds the complete bytes; the fresh closure/readback binds those bytes to
    # the still-active deployment. Per-row anchors are checked below against
    # this fresh receipt digest and the locked original declaration.
    fence_cache = {}
    for solution in installs:
        family_records = [row for row in records
            if (row.solution_slug, row.repo_subpath) == (solution.slug, solution.repo_subpath)]
        representative = next((row for row in family_records
            if cache.get((solution.id, row.source_commit_sha, row.source_tree_sha)) is not None), None)
        if representative is None:
            continue
        key = (solution.id, representative.source_commit_sha, representative.source_tree_sha)
        try:
            fence_cache[solution.id] = await _installed_evidence(
                db, solution, representative, policy, collected=cache[key])
        except (NativeAuthoredSourceMismatch, UnprovenSourceConsumers, ValueError, KeyError, TypeError):
            fence_cache[solution.id] = None

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
                key = (solution.id, record.source_commit_sha, record.source_tree_sha)
                collected = cache.get(key)
                if collected is None:
                    raise NativeAuthoredSourceMismatch("No collected immutable bytes for this exact installation")
                current = fence_cache.get(solution.id)
                if current is None:
                    raise NativeAuthoredSourceMismatch("No fresh fenced readback for this installation")
                source, proof, _runtime = current
                cached_source, cached_proof, _cached_runtime = collected
                if cached_proof["delivery_proof_hash"] != proof["delivery_proof_hash"]:
                    raise NativeAuthoredSourceMismatch("Collected native delivery proof changed before completion")
                if (cached_source.source_content_id != source.source_content_id
                        or cached_source.file_manifest() != source.file_manifest()
                        or dict(cached_source.files) != dict(source.files)):
                    raise NativeAuthoredSourceMismatch("Collected authored bytes differ from the fenced readback")
                _require_history_origin(record, origins.get(record.source_release_id))
                historical = None
                if proof["source_commit_sha"] == record.source_commit_sha:
                    if proof["source_tree_sha"] != record.source_tree_sha:
                        raise NativeAuthoredSourceMismatch("Exact native declaration tree differs")
                    require_declared_authored_source(record, source)
                else:
                    if record.source_commit_sha not in proof["ancestor_commit_shas"]:
                        raise NativeAuthoredSourceMismatch("No verified native descendant is retained")
                    historical = cached_proof["historical_authored_source"]
                    _require_historical_inventory(record, historical)
                proof = {**proof, "historical_authored_source": historical}
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
            exact = record.source_commit_sha == authored.commit_sha
            verifier = native_completion_evidence if exact else native_supersession_evidence
            evidence = verifier(record, authored, target_installs=target_installs,
                installations=installations, verified_at=now)
        except (NativeAuthoredSourceMismatch, UnprovenSourceConsumers, ValueError, KeyError, TypeError):
            # Leave debt open, preserving any prior mismatch/provenance receipt.
            # Infrastructure/storage failures are not swallowed as proof.
            continue
        record.disposition = "released" if exact else "superseded"
        record.reason = None if exact else f"Superseded by verified native production source {authored.commit_sha}"
        record.solution_id = UUID(evidence["solution_id"])
        record.deploy_job_id = None
        record.candidate_id = evidence["candidate_id"]
        record.source_artifact_sha256 = evidence["source_artifact_sha256"]
        record.completion_evidence, record.resolved_at = evidence, now
        completed.append(record.id)
    await db.flush()
    return completed
