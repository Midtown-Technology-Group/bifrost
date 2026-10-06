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
