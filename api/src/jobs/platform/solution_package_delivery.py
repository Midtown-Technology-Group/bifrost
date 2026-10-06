"""Complete package variant of solution.deploy; uncertain effects are not replayed."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
import httpx
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from bifrost.workspace_release import canonical_digest
from sqlalchemy import func, select

from src.core.database import get_db_context
from src.config import get_settings
from src.jobs.platform.base import PlatformJobContext, PlatformJobFailure, PlatformJobRequiresAction
from src.models.contracts.solution_deployments import SolutionDeploymentCreate
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.platform_jobs import PlatformJob
from src.services.solution_deploy_obligations import solution_source_content_id
from src.core.constants import SYSTEM_USER_UUID
from src.services.solutions.deploy import SolutionDeployer
from src.services.solutions.deploy_job_storage import SolutionDeployJobStorage
from src.services.solutions.deployment_api import SolutionDeploymentAPIService
from src.services.solutions.github_delivery_source import VerifiedAuthoredSolution, VerifiedAuthoredSolutionFile
from src.services.solutions.package_git_source import PACKAGE_GIT_SOURCE_SCHEMA, VerifiedSolutionPackageSource
from src.services.solutions.package_runtime import (
    activate_package_runtime, compile_package_runtime, stage_package_runtime,
)
from src.services.solutions.write_lock import SolutionWriteLockHeld, solution_write_lock

PACKAGE_ROLLBACK_SCHEMA = "bifrost.solution-package-rollback/v1"


if TYPE_CHECKING:
    from src.jobs.platform.solution_deploy import SolutionDeployPayload


def staged_package_source(archive: bytes, evidence: dict, artifact_digest: str) -> VerifiedSolutionPackageSource:
    """Restore only the server-captured envelope from the encrypted job payload."""
    if evidence.get("schema_version") != PACKAGE_GIT_SOURCE_SCHEMA or canonical_digest(evidence) != artifact_digest:
        raise ValueError("Original package source envelope differs")
    proof = evidence["package"]
    if hashlib.sha256(archive).hexdigest() != proof["source_archive_sha256"]:
        raise ValueError("Original package archive differs")
    with zipfile.ZipFile(io.BytesIO(archive)) as reader:
        members = reader.infolist()
        expected = {row["path"] for row in proof["source_files"]}
        if len(members) != len(expected) or {row.filename for row in members} != expected:
            raise ValueError("Original package inventory differs")
        if sum(row.file_size for row in members) > 10 * 1024 * 1024:
            raise ValueError("Original package exceeds its byte limit")
        files = {row.filename: reader.read(row) for row in members}
    manifest = tuple(VerifiedAuthoredSolutionFile(
        path=proof["repo_subpath"] + "/" + row["path"], mode=row["mode"], sha256=row["sha256"], size=row["size"],
    ) for row in proof["source_files"])
    authored = VerifiedAuthoredSolution(
        commit_sha=proof["source_commit_sha"], tree_sha=proof["source_tree_sha"],
        subtree_sha=evidence["source_subtree_sha"], solution_slug=proof["repo_subpath"].split("/")[-1],
        repo_subpath=proof["repo_subpath"], source_content_id=solution_source_content_id(
            solution_slug=proof["repo_subpath"].split("/")[-1], repo_subpath=proof["repo_subpath"],
            source_files=[{"path": row.path, "mode": row.mode, "sha256": row.sha256, "size": row.size} for row in manifest],
        ), source_files=manifest, files=MappingProxyType(files),
    )
    return VerifiedSolutionPackageSource(authored, archive, json.dumps(evidence, sort_keys=True).encode(), artifact_digest)


async def reconcile_reviewed_package_obligations(db, *, source_release_id=None, limit=100) -> list[UUID]:
    """The existing declaration/scheduler sweep also reads completed packages.

    A late declaration uses the original publication job and immutable source;
    this path never prepares, writes runtime bytes or activates pointers.
    """
    from src.core.security import decrypt_secret
    from src.models.orm.solutions import Solution
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.deployment_storage import SolutionDeploymentStorage
    from src.services.solutions.package_runtime import PACKAGE_RUNTIME_SCHEMA
    from src.services.solution_deploy_obligations import reconcile_solution_deploy_obligation
    from src.jobs.platform.solution_deploy import SolutionDeployPayload

    policy = getattr(get_settings(), "solution_package_git_delivery_policy", None)
    if policy is None:
        return []
    completed = []
    for sid, enrollment in list(policy.packages.items())[:min(max(limit, 1), 100)]:
        solution = await db.get(Solution, sid, populate_existing=True)
        if (solution is None or solution.status != "active" or solution.active_deployment_id is None
                or solution.organization_id != enrollment.organization_id):
            continue
        deployment = await db.get(SolutionDeployment, solution.active_deployment_id, populate_existing=True)
        package = (deployment.compiled_manifest or {}).get("package_evidence") if deployment else None
        if not isinstance(package, dict) or package.get("schema_version") != PACKAGE_RUNTIME_SCHEMA:
            continue
        job_id = package.get("publication_job_id")
        if not isinstance(job_id, str):
            continue
        job = await db.get(PlatformJob, UUID(job_id), populate_existing=True)
        if (job is None or job.status != "succeeded" or job.requested_by_user_id != str(SYSTEM_USER_UUID)
                or job.organization_id != enrollment.organization_id or not job.encrypted_payload):
            continue
        payload = SolutionDeployPayload.model_validate_json(decrypt_secret(job.encrypted_payload))
        if (payload.kind != "deliver_package" or payload.install_id != sid
                or payload.options["package_source"] != package["source"]
                or (job.result or {}).get("deployment_id") != str(deployment.id)):
            continue
        other = await db.scalar(select(Solution.id).where(
            Solution.slug == solution.slug, Solution.status == "active", Solution.id != sid,
        ).limit(1))
        if other is not None:
            continue
        try:
            async with solution_write_lock(sid):
                archive = await SolutionDeploymentStorage(sid, deployment.id).read_source_artifact()
                source = staged_package_source(archive, payload.options["package_source"], payload.options["artifact_digest"])
                result = await reconcile_solution_deploy_obligation(db, solution_id=sid,
                    solution_slug=source.authored.solution_slug, accountability_organization_id=policy.organization_id,
                    deploy_job_id=job.id, candidate_id=f"sha256:{payload.input_sha256}", artifact=archive,
                    repo_subpath=enrollment.repo_subpath, source_content_id=source.authored.source_content_id,
                    complete_all_matches=True, source_release_id=source_release_id)
                await db.commit()
                completed.extend(UUID(identity) for identity in result.get("obligation_ids", []))
        except SolutionWriteLockHeld:
            # A declaration may race a publication. Other packages can settle;
            # this one stays pending for the ordinary next sweep/replay.
            continue
    return completed


async def run_solution_package_delivery(context: PlatformJobContext, payload: SolutionDeployPayload) -> dict:
    from src.jobs.platform.solution_deploy import SOLUTION_DEPLOY_INTENT_SCHEMA, unresolved_solution_deploy
    from src.services.solutions.package_runtime import readback_package_runtime

    if (payload.install_id is None or payload.deploy_job_id != context.job_id
            or context.requested_by_user_id != str(SYSTEM_USER_UUID)):
        raise ValueError("Original package job/target identity differs")
    sid = payload.install_id
    policy = get_settings().solution_package_git_delivery_policy
    if policy is None:
        raise ValueError("Protected complete package delivery is not configured")
    enrollment = policy.enrollment_for(sid)
    envelope = payload.options["package_source"]
    package = envelope["package"]
    if (enrollment.organization_id != context.organization_id
            or package["solution_id"] != str(sid)
            or package["organization_id"] != (str(context.organization_id) if context.organization_id else None)
            or package["repo_subpath"] != enrollment.repo_subpath
            or envelope["recipe_path"] != enrollment.recipe_path
            or envelope["repository"] != policy.repository
            or envelope["repository_id"] != policy.repository_id
            or envelope["repository_owner_id"] != policy.repository_owner_id
            or canonical_digest(envelope) != payload.options["artifact_digest"]):
        raise ValueError("Original complete package enrollment differs")

    async def settle(db, archive: bytes) -> dict:
        from src.services.solution_deploy_obligations import reconcile_solution_deploy_obligation
        from src.models.orm.solutions import Solution
        other = await db.scalar(select(Solution.id).where(
            Solution.slug == enrollment.repo_subpath.split("/")[-1], Solution.status == "active", Solution.id != sid,
        ).limit(1))
        if other is not None:
            return {"state": "attention_required", "reason": "Complete package accounting requires every active scope installation"}
        authored = staged_package_source(archive, envelope, payload.options["artifact_digest"]).authored
        return await reconcile_solution_deploy_obligation(db, solution_id=sid,
            solution_slug=enrollment.repo_subpath.split("/")[-1],
            accountability_organization_id=policy.organization_id, deploy_job_id=context.job_id,
            candidate_id=f"sha256:{payload.input_sha256}", artifact=archive,
            repo_subpath=enrollment.repo_subpath, source_content_id=authored.source_content_id,
            complete_all_matches=True)
    rollback = None
    intent = context.checkpoint
    rolled_back_checkpoint = isinstance(intent, dict) and intent.get("schema_version") == PACKAGE_ROLLBACK_SCHEMA
    if rolled_back_checkpoint:
        intent = intent.get("original_intent")
        if not isinstance(intent, dict):
            raise ValueError("Original package rollback evidence is missing")
    if intent is not None:
        original_intent = intent
        try:
            if (intent.get("schema_version") != SOLUTION_DEPLOY_INTENT_SCHEMA
                    or intent.get("delivery_kind") != "package"
                    or intent.get("original_job_id") != str(context.job_id)
                    or intent.get("solution_id") != str(sid)
                    or intent.get("organization_id") != package["organization_id"]
                    or intent.get("payload_digest") != canonical_digest(payload.model_dump(mode="json"))):
                raise ValueError("Original package intent differs")
            async with solution_write_lock(sid):
                async with get_db_context() as db:
                    from src.models.orm.solution_deployments import SolutionDeployment
                    deployment = await db.get(SolutionDeployment, UUID(intent["deployment_id"]))
                    if deployment is not None:
                        if rolled_back_checkpoint:
                            raise ValueError("Original package rollback evidence differs")
                        proof = await readback_package_runtime(db, sid, UUID(intent["deployment_id"]),
                            expected_source_sha256=payload.input_sha256,
                            expected_organization_id=context.organization_id)
                        if proof["compiled_manifest_hash"] != intent["manifest_hash"]:
                            raise ValueError("Original complete package manifest differs from intent")
                        from src.services.solutions.deployment_storage import SolutionDeploymentStorage
                        archive = await SolutionDeploymentStorage(sid, UUID(intent["deployment_id"])).read_source_artifact()
                        proof["accounting"] = await settle(db, archive)
                        return {**proof, "original_job_id": str(context.job_id), "recovered_from_intent": True}
                    # The deployment row and all pointer/resource changes share
                    # one transaction, fenced by the shared job lease. A new
                    # lease can prove that transaction absent without replaying
                    # any committed publication. Retain that disposition before
                    # ordinary fresh Main/CI/controls/CAS checks run again.
                    rollback = {"schema_version": PACKAGE_ROLLBACK_SCHEMA,
                        "original_job_id": str(context.job_id), "solution_id": str(sid),
                        "original_intent": intent, "publication_not_committed": True}
                    await context.save_checkpoint(rollback, phase="Original package transaction rolled back")
        except Exception as exc:
            raise PlatformJobRequiresAction("Original package publication requires readback", original_intent) from exc
        if rollback is not None:
            try:
                # No publication occurred. Only now may the same shared job
                # perform an ordinary fresh attempt with a new deployment ID.
                # It revalidates Main/CI/controls and exact pointer CAS first.
                result = await run_solution_package_delivery(replace(context, checkpoint=None), payload)
                return {**result, "recovered_from_rollback": True}
            except PlatformJobRequiresAction:
                raise  # Keep any NEW uncertain publication intent intact.
            except Exception as exc:
                # A superseded Main, expired original token or changed control
                # may refuse that fresh attempt. Preserve the proven absence,
                # allowing a newer protected producer to publish separately.
                raise PlatformJobFailure("package_prepublication_refused",
                    "Original package transaction rolled back; fresh publication was refused.",
                    result=rollback) from exc

    storage = SolutionDeployJobStorage(context.job_id)
    await context.report("Loading reviewed package", percent=2)
    with tempfile.TemporaryDirectory(prefix="bifrost-package-job-") as directory:
        path = Path(directory) / "source.zip"
        await storage.copy_to_path(path, expected_sha256=payload.input_sha256)
        source = staged_package_source(path.read_bytes(), payload.options["package_source"], payload.options["artifact_digest"])
    proof = source.evidence()["package"]
    if proof["solution_id"] != str(sid) or proof["organization_id"] != (
        str(context.organization_id) if context.organization_id is not None else None
    ):
        raise ValueError("Original package target/scope differs")
    from src.services.solutions.github_delivery_source import ProtectedGitReader
    # This token exists only in the shared job's encrypted payload. Recheck
    # current Main/CI before any preparation or publication intent; a delayed
    # job cannot silently publish a superseded source. Recovery needs no token.
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        reader = ProtectedGitReader(policy, payload.options["delivery_git_token"], client)
        await reader.verify_ci(source.authored.commit_sha, envelope["ci_run_id"], envelope["ci_run_attempt"])
    expected = payload.options["expected_active_deployment_id"]
    expected_base = UUID(expected) if expected is not None else None
    did = uuid4()
    async with solution_write_lock(sid):
        try:
            async with get_db_context() as db:
                if await unresolved_solution_deploy(db, sid, exclude_job_id=context.job_id) is not None:
                    raise ValueError("An earlier Solution publication requires readback")
                prepared = await SolutionDeployer(db).prepare_reviewed_package(
                    source, expected_active_deployment_id=expected_base,
                    expected_controls_digest=payload.options["expected_controls_digest"],
                )
                manifest, resolution = await compile_package_runtime(db, source, prepared, did, publication_job_id=context.job_id)
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                    await ProtectedGitReader(policy, payload.options["delivery_git_token"], client).verify_ci(
                        source.authored.commit_sha, envelope["ci_run_id"], envelope["ci_run_attempt"])
                intent = {"schema_version": SOLUTION_DEPLOY_INTENT_SCHEMA, "delivery_kind": "package",
                    "original_job_id": str(context.job_id), "solution_id": str(sid), "deployment_id": str(did),
                    "payload_digest": canonical_digest(payload.model_dump(mode="json")),
                    "organization_id": proof["organization_id"], "manifest_hash": manifest.content_hash()}
                # Lease-fenced checkpoint precedes any durable artifact writes.
                await context.save_checkpoint(intent, phase="Complete package publication intent recorded")
                await stage_package_runtime(source, prepared, manifest)
                await SolutionDeploymentAPIService(db).create_ready_draft(sid, UUID(context.requested_by_user_id),
                    SolutionDeploymentCreate(compiled_manifest=manifest, resolution_map=resolution,
                        base_deployment_id=expected_base, parent_deployment_id=expected_base,
                        declared_version=prepared.source_bundle.version,
                        git_repository=source.evidence()["repository"], git_ref="main", git_commit_sha=source.authored.commit_sha))
                await activate_package_runtime(db, source, prepared, manifest, expected_base)
                projection = await db.get(SolutionDeployJob, context.job_id)
                if projection is None:
                    raise ValueError("Original package status is missing")
                projection.status = "succeeded"
                projection.result = {"solution_id": str(sid), "deployment_id": str(did),
                    "candidate_id": f"sha256:{payload.input_sha256}", "source_commit_sha": source.authored.commit_sha}
                # Fence the metadata/pointer commit in the SAME transaction.
                # A stale runner cannot publish after another lease reclaimed
                # its checkpoint while artifacts were being written.
                owner = await db.scalar(select(PlatformJob.id).where(
                    PlatformJob.id == context.job_id, PlatformJob.status == "running",
                    PlatformJob.lease_token == context.lease_token,
                    PlatformJob.lease_expires_at > func.now(),
                ).with_for_update())
                if owner is None:
                    raise ValueError("Original package publication lease is no longer owned")
            # Commit acknowledgment may be lost. No cleanup/activation replay is
            # permitted after the recorded intent; the next lease reads it.
            async with get_db_context() as db:
                result = await readback_package_runtime(db, sid, did, expected_source_sha256=payload.input_sha256,
                    expected_organization_id=context.organization_id)
                result["accounting"] = await settle(db, source.source_archive)
            await context.report("Complete package source, registrations and runtime verified", percent=100)
            return {**result, "original_job_id": str(context.job_id)}
        except BaseException as exc:
            if intent is not None:
                raise PlatformJobRequiresAction("Original package publication requires readback", intent) from exc
            raise
