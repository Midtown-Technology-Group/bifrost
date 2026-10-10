"""Protected source admission into the existing durable Solution deploy job."""

from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SYSTEM_USER_EMAIL, SYSTEM_USER_UUID
from src.core.security import decrypt_secret
from src.core.solution_package_delivery_policy import SolutionPackageGitDeliveryPolicy
from src.jobs.platform.solution_deploy import SolutionDeployPayload
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solutions import Solution
from src.services.solutions.package_controls import capture_package_controls
from src.services.solutions.package_git_source import VerifiedSolutionPackageSource
from bifrost.workspace_release import canonical_digest


def package_publication_id(solution_id: UUID, artifact_digest: str) -> UUID:
    return uuid5(solution_id, "bifrost.solution-package-publication/v1:" + artifact_digest)


async def admit_package(db: AsyncSession, policy: SolutionPackageGitDeliveryPolicy,
                        solution_id: UUID, source: VerifiedSolutionPackageSource, github_token: str) -> PlatformJob:
    from src.routers.solutions import _enqueue_solution_deploy_job

    enrollment = policy.enrollment_for(solution_id)
    solution = await db.get(Solution, solution_id, populate_existing=True)
    if (solution is None or solution.organization_id != enrollment.organization_id
            or solution.slug != source.authored.solution_slug):
        raise ValueError("Complete package target/scope differs from enrollment")
    controls = await capture_package_controls(db, solution_id)
    publication_id = package_publication_id(solution_id, source.artifact_digest)
    await _enqueue_solution_deploy_job(db, kind="deliver_package", install_id=solution_id,
        organization_id=enrollment.organization_id, input_bytes=source.source_archive,
        options={"package_source": source.evidence(), "artifact_digest": source.artifact_digest,
            "delivery_git_token": github_token,
            "expected_active_deployment_id": str(solution.active_deployment_id) if solution.active_deployment_id else None,
            "expected_controls_digest": canonical_digest(controls)},
        requested_by_user_id=SYSTEM_USER_UUID, requested_by_email=SYSTEM_USER_EMAIL,
        requested_by_name="Protected Workspace package delivery", publication_id=publication_id)
    job = await db.get(PlatformJob, publication_id, populate_existing=True)
    if job is None:
        raise ValueError("Original complete package job is unavailable; inspect before retry")
    return job


async def inspect_package_job(db: AsyncSession, solution_id: UUID, artifact_digest: str) -> PlatformJob:
    """Inspect one source-scoped job; never enqueue, resume or activate."""
    job = await db.get(PlatformJob, package_publication_id(solution_id, artifact_digest), populate_existing=True)
    if (job is None or job.job_type != "solution.deploy"
            or job.requested_by_user_id != str(SYSTEM_USER_UUID) or job.encrypted_payload is None):
        raise LookupError("Complete package publication not found")
    payload = SolutionDeployPayload.model_validate_json(decrypt_secret(job.encrypted_payload))
    if (payload.kind != "deliver_package" or payload.install_id != solution_id
            or payload.options["artifact_digest"] != artifact_digest):
        raise ValueError("Original complete package publication differs")
    if job.status == "succeeded":
        from src.services.solutions.package_runtime import readback_package_runtime
        result = job.result or {}
        proof = await readback_package_runtime(db, solution_id, UUID(result["deployment_id"]),
            expected_source_sha256=payload.input_sha256, expected_organization_id=job.organization_id)
        if result["compiled_manifest_hash"] != proof["compiled_manifest_hash"]:
            raise ValueError("Original complete package completion differs")
    return job


async def read_package_rollback(db: AsyncSession, job: PlatformJob) -> dict | None:
    """Certify no publication, never convert that into a delivery completion."""
    from src.jobs.platform.solution_deploy import SOLUTION_DEPLOY_INTENT_SCHEMA
    from src.jobs.platform.solution_package_delivery import PACKAGE_ROLLBACK_SCHEMA
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.models.orm.solution_deploy_jobs import SolutionDeployJob

    result = job.result or {}
    if result.get("schema_version") != PACKAGE_ROLLBACK_SCHEMA:
        return None
    if job.encrypted_payload is None:
        raise ValueError("Original rollback input is missing")
    payload = SolutionDeployPayload.model_validate_json(decrypt_secret(job.encrypted_payload))
    intent = result.get("original_intent") or {}
    projection = await db.get(SolutionDeployJob, job.id)
    if (result.get("publication_not_committed") is not True
            or result.get("original_job_id") != str(job.id)
            or result.get("solution_id") != str(payload.install_id)
            or intent.get("schema_version") != SOLUTION_DEPLOY_INTENT_SCHEMA
            or intent.get("delivery_kind") != "package"
            or intent.get("original_job_id") != str(job.id)
            or intent.get("solution_id") != str(payload.install_id)
            or intent.get("organization_id") != (str(job.organization_id) if job.organization_id else None)
            or intent.get("payload_digest") != canonical_digest(payload.model_dump(mode="json"))
            or projection is None or projection.install_id != payload.install_id
            or projection.status == "succeeded"
            or (projection.result or {}).get("deployment_id") == intent.get("deployment_id")
            or await db.get(SolutionDeployment, UUID(intent["deployment_id"])) is not None):
        raise ValueError("Original package rollback readback differs")
    return {"verified": True, "original_job_id": str(job.id),
        "solution_id": str(payload.install_id), "deployment_id": intent["deployment_id"]}


async def recover_pending_package(db: AsyncSession, policy: SolutionPackageGitDeliveryPolicy,
                                  solution_id: UUID) -> PlatformJob | None:
    """Resume only the original retained intent, even after Main advances.

    This does not capture source, renew its token, stage bytes or create a job.
    Its caller authenticates the current protected producer and CI first.
    """
    from src.jobs.platform.solution_deploy import (
        SOLUTION_DEPLOY_DEFINITION, SOLUTION_DEPLOY_INTENT_SCHEMA, unresolved_solution_deploy,
    )
    from src.routers.solutions import _lock_solution_operation
    from src.services.platform_jobs import enqueue_platform_job
    from src.jobs.platform.solution_package_delivery import PACKAGE_ROLLBACK_SCHEMA

    enrollment = policy.enrollment_for(solution_id)
    await _lock_solution_operation(db, solution_id)
    job = await unresolved_solution_deploy(db, solution_id)
    if job is None:
        # A failed fresh attempt may have durably certified the older
        # transaction absent. Return that receipt for independent status
        # readback, rather than hiding it as "no original publication".
        job = await db.scalar(select(PlatformJob).where(
            PlatformJob.job_type == "solution.deploy", PlatformJob.status == "failed",
            PlatformJob.result["schema_version"].as_string() == PACKAGE_ROLLBACK_SCHEMA,
            PlatformJob.result["solution_id"].as_string() == str(solution_id),
        ).order_by(PlatformJob.created_at.desc()).limit(1))
        if job is None:
            return None
    if (job.job_type != "solution.deploy" or job.requested_by_user_id != str(SYSTEM_USER_UUID)
            or job.organization_id != enrollment.organization_id or job.encrypted_payload is None):
        raise ValueError("Original publication is outside complete package recovery")
    payload = SolutionDeployPayload.model_validate_json(decrypt_secret(job.encrypted_payload))
    result = job.result or {}
    rolled_back = result.get("schema_version") == PACKAGE_ROLLBACK_SCHEMA
    intent = result.get("original_intent", {}) if rolled_back else result
    source = payload.options.get("package_source", {})
    package = source.get("package", {})
    artifact_digest = payload.options.get("artifact_digest")
    if (payload.kind != "deliver_package" or payload.install_id != solution_id
            or payload.deploy_job_id != job.id
            or artifact_digest != canonical_digest(source)
            or job.id != package_publication_id(solution_id, artifact_digest)
            or job.dedupe_key != str(job.id)
            or job.resource_type != "solution_deploy" or job.resource_id != str(job.id)
            or job.resource_lock_key != f"solution:{solution_id}"
            or source.get("repository") != policy.repository
            or source.get("repository_id") != policy.repository_id
            or source.get("repository_owner_id") != policy.repository_owner_id
            or source.get("recipe_path") != enrollment.recipe_path
            or package.get("repo_subpath") != enrollment.repo_subpath
            or package.get("solution_id") != str(solution_id)
            or package.get("organization_id") != (str(job.organization_id) if job.organization_id else None)
            or package.get("source_archive_sha256") != payload.input_sha256
            or intent.get("schema_version") != SOLUTION_DEPLOY_INTENT_SCHEMA
            or intent.get("delivery_kind") != "package"
            or intent.get("original_job_id") != str(job.id)
            or intent.get("solution_id") != str(solution_id)
            or intent.get("payload_digest") != canonical_digest(payload.model_dump(mode="json"))):
        raise ValueError("Original complete package intent/enrollment differs")
    if rolled_back:
        await read_package_rollback(db, job)
    elif job.status in {"requires_action", "failed", "cancelled"}:
        resumed, reused = await enqueue_platform_job(db, SOLUTION_DEPLOY_DEFINITION, payload,
            dedupe_key=job.dedupe_key, resource_lock_key=job.resource_lock_key,
            priority=job.priority, organization_id=job.organization_id,
            requested_by_user_id=SYSTEM_USER_UUID, requested_by_email=SYSTEM_USER_EMAIL,
            requested_by_name="Protected Workspace package recovery", resource_type=job.resource_type,
            resource_id=job.resource_id, title=job.title, action_url=job.action_url)
        if not reused or resumed.id != job.id:
            raise ValueError("Recovery cannot create a replacement publication")
        await db.commit()
    return job


async def read_package_accounting(db: AsyncSession, job: PlatformJob) -> dict:
    """Fresh ledger readback, including declarations after the job completed.

    The status endpoint cannot settle records or reuse a stale job-result flag.
    Runtime readback must already have verified this original publication.
    """
    from src.config import get_settings
    from src.models.orm.workspace_promotions import SolutionDeployObligation
    from src.services.solution_deploy_obligations import solution_source_content_id

    policy = get_settings().solution_package_git_delivery_policy
    if policy is None or job.encrypted_payload is None or job.status != "succeeded":
        return {"state": "not_tracked", "verified": False}
    payload = SolutionDeployPayload.model_validate_json(decrypt_secret(job.encrypted_payload))
    source = payload.options["package_source"]["package"]
    prefix = source["repo_subpath"] + "/"
    content_id = solution_source_content_id(solution_slug=source["repo_subpath"].split("/")[-1],
        repo_subpath=source["repo_subpath"], source_files=[{**row, "path": prefix + row["path"]}
            for row in source["source_files"]])
    records = (await db.scalars(select(SolutionDeployObligation).where(
        SolutionDeployObligation.organization_id == policy.organization_id,
        SolutionDeployObligation.repo_subpath == source["repo_subpath"],
        SolutionDeployObligation.source_content_id == content_id,
    ).execution_options(populate_existing=True))).all()
    pending = [record for record in records if record.disposition in {"pending", "attention_required"}]
    if pending:
        return {"state": "attention_required", "verified": False,
            "obligation_ids": sorted(str(record.id) for record in pending)}
    released = [record for record in records if record.disposition == "released"]
    if not released:
        return {"state": "not_tracked", "verified": False}
    for record in released:
        evidence = record.completion_evidence or {}
        if (record.solution_id != payload.install_id or record.source_artifact_sha256 != payload.input_sha256
                or evidence.get("evidence_id") != canonical_digest({key: value for key, value in evidence.items()
                    if key != "evidence_id"})):
            return {"state": "accounting_evidence_differs", "verified": False}
    return {"state": "released", "verified": True,
        "source_content_id": content_id, "obligation_ids": sorted(str(record.id) for record in released)}
