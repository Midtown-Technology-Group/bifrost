"""Admit protected Git App publication through the existing PlatformJob registry."""

from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.core.application_delivery_policy import InlineAppGitDeliveryPolicy
from src.core.constants import SYSTEM_USER_UUID
from src.jobs.platform.application_publish import APPLICATION_PUBLISH_DEFINITION, ApplicationPublishPayload
from src.models.contracts.applications import ApplicationGitPublicationInput, ApplicationGitSourcePublicationRequest
from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.services.application_git_source import read_app_git_source
from src.services.application_publication import publication_controls_hash
from src.services.application_publication_evidence import read_app_publication_runtime_pin
from src.services.app_storage import AppStorageService
from src.services.platform_jobs import enqueue_platform_job
from src.services.operation_receipts import canonical_request_fingerprint
from src.services.solutions.github_delivery_source import GitDeliveryIdentity, GitDeliverySourceError, ProtectedGitReader

APP_GIT_JOB_NAMESPACE = UUID("9c73b74d-3398-45a9-ad1c-1a8ea5e2ca23")


class AppGitPublicationNotFound(GitDeliverySourceError):
    pass


async def inspect_app_git_publication(db: AsyncSession, *, policy: InlineAppGitDeliveryPolicy,
                                      application_id: UUID, job_id: UUID) -> PlatformJob:
    """Inspect original scoped work and reverify successful live publication, with zero effects."""
    enrollment = policy.enrollment_for(application_id)
    job = await db.get(PlatformJob, job_id, populate_existing=True)
    if job is None:
        job = (await db.execute(select(PlatformJob).where(
            PlatformJob.job_type == APPLICATION_PUBLISH_DEFINITION.job_type,
            PlatformJob.requested_by_user_id == str(SYSTEM_USER_UUID),
            PlatformJob.organization_id == enrollment.organization_id,
            PlatformJob.resource_type == "application", PlatformJob.resource_id == str(application_id),
            PlatformJob.payload["protected_git"].as_string().is_not(None),
        ).order_by(PlatformJob.created_at.desc()).limit(1))).scalar_one_or_none()
    if (job is None or job.job_type != APPLICATION_PUBLISH_DEFINITION.job_type
            or job.requested_by_user_id != str(SYSTEM_USER_UUID)
            or job.organization_id != enrollment.organization_id
            or job.resource_type != "application" or job.resource_id != str(application_id)
            or not isinstance(job.payload, dict) or not job.payload.get("protected_git")):
        raise AppGitPublicationNotFound("Scoped protected App publication not found")
    if job.status == "succeeded":
        result = job.result or {}
        intent = result.get("publication_intent")
        if not result.get("publication_verified") or not isinstance(intent, dict):
            raise GitDeliverySourceError("Original publication evidence is unavailable")
        try:
            storage = AppStorageService()
            await storage.verify_publication(str(application_id), intent)
            pin = await read_app_publication_runtime_pin(storage,
                application_id=application_id, publication_job_id=job.id,
                organization_id=enrollment.organization_id, intent=intent,
                protected_git=job.payload["protected_git"])
            if result.get("runtime_pin") != pin:
                raise ValueError("App runtime pin differs from the original publication")
            if await publication_controls_hash(db, application_id) != intent.get("controls_hash"):
                raise ValueError("App controls differ from the original publication")
        except Exception as exc:
            raise GitDeliverySourceError("Original publication no longer matches live artifacts or controls") from exc
    return job


def app_git_job_id(application_id: UUID, request: ApplicationGitSourcePublicationRequest,
                   producer: GitDeliveryIdentity) -> UUID:
    """The producer can retain this identity before its single enqueue POST."""
    identity = {"application_id": str(application_id), **request.model_dump(mode="json"),
        "producer_run_id": producer.run_id, "producer_run_attempt": producer.run_attempt}
    return uuid5(APP_GIT_JOB_NAMESPACE, canonical_request_fingerprint(identity))


async def enqueue_app_git_publication(
    db: AsyncSession, *, policy: InlineAppGitDeliveryPolicy, reader: ProtectedGitReader,
    application_id: UUID, request: ApplicationGitSourcePublicationRequest, producer: GitDeliveryIdentity,
) -> tuple[PlatformJob, bool]:
    """Source-verify first; enqueue no uploaded source, token or separate job type."""
    await read_app_git_source(reader, policy=policy, application_id=application_id,
        commit_sha=request.source_commit_sha, ci_run_id=request.ci_run_id,
        ci_run_attempt=request.ci_run_attempt, artifact_digest=request.artifact_digest)
    enrollment = policy.enrollment_for(application_id)
    application = await db.get(Application, application_id, populate_existing=True)
    if (application is None or application.organization_id != enrollment.organization_id
            or application.repo_path != enrollment.repo_subpath or application.app_model != "inline_v1"
            or application.solution_id is not None or not application.published_snapshot
            or application.published_at is None):
        raise GitDeliverySourceError("Only the exact enrolled, already published inline App can be delivered")
    controls = await publication_controls_hash(db, application_id, lock=True)
    payload = ApplicationPublishPayload(application_id=application_id,
        protected_git=ApplicationGitPublicationInput(**request.model_dump(),
            expected_controls_hash=controls, producer_run_id=producer.run_id,
            producer_run_attempt=producer.run_attempt))
    job_id = app_git_job_id(application_id, request, producer)
    existing = await db.get(PlatformJob, job_id, populate_existing=True)
    if existing is not None:
        if (existing.job_type != APPLICATION_PUBLISH_DEFINITION.job_type
                or existing.organization_id != application.organization_id
                or existing.requested_by_user_id != str(SYSTEM_USER_UUID)
                or existing.resource_type != "application" or existing.resource_id != str(application_id)
                or existing.payload != payload.model_dump(mode="json")):
            raise GitDeliverySourceError("Original App admission identity or controls differ")
        # In particular, a completed original job must not be freshly enqueued
        # when its accepted/terminal HTTP response was lost.
        return existing, True
    job, reused = await enqueue_platform_job(db, APPLICATION_PUBLISH_DEFINITION, payload,
        job_id=job_id,
        dedupe_key=str(application_id), organization_id=application.organization_id,
        requested_by_user_id=SYSTEM_USER_UUID, requested_by_email="github-actions@bifrost.internal",
        requested_by_name="Protected Workspace Main delivery", resource_type="application",
        resource_id=str(application_id), title=f"Publishing {application.name} from Workspace Main",
        action_url=f"/apps/{application.slug}/edit")
    if reused and job.requested_by_user_id != str(SYSTEM_USER_UUID):
        # Roll back the enclosing request. No original requester or saved intent
        # may be taken over by the producer's scoped identity.
        raise GitDeliverySourceError("App has an unresolved publication owned by another requester")
    # A reused original intent is readback of its original commit, not delivery
    # of this request. The producer must compare the job's manifest/source result
    # to its exact requested commit before claiming delivery or accounting.
    return job, reused
