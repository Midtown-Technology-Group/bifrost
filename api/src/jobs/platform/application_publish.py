"""Application publishing as a standardized platform-job handler."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx
from pydantic import BaseModel

from src.config import get_settings
from src.core.database import get_db_context
from src.core.pubsub import publish_app_published
from src.jobs.execution_policy import (
    WorkloadClass,
    platform_job_operations_policy,
)
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
    PlatformJobRequiresAction,
)
from src.models.orm.applications import Application
from src.models.contracts.applications import ApplicationGitPublicationInput
from src.repositories.applications import ApplicationRepository
from src.services.application_publication import publication_controls_hash
from src.services.application_git_source import app_source_evidence, read_app_git_source
from src.services.github_config import get_github_config
from src.services.solutions.github_delivery_source import ProtectedGitReader
from src.services.app_storage import AppStorageService, PUBLICATION_INTENT_SCHEMA

APPLICATION_PUBLISH_JOB_TYPE = "application.publish"
logger = logging.getLogger(__name__)


class ApplicationPublishPayload(BaseModel):
    application_id: UUID
    message: str | None = None
    protected_git: ApplicationGitPublicationInput | None = None


def _publish_percent(
    phase: str,
    current: int,
    total: int | None,
) -> float:
    if phase == "building current source":
        return 5
    if phase == "promoting current bundle":
        if not total:
            return 10
        return min(95, 10 + round(85 * current / total))
    if phase == "recording published version":
        return 98
    return 0


async def run_application_publish(
    context: PlatformJobContext,
    raw_payload: BaseModel,
) -> dict[str, object]:
    payload = ApplicationPublishPayload.model_validate(raw_payload)
    intent = context.checkpoint
    try:
        async with get_db_context() as db:
            application = await db.get(Application, payload.application_id)
            if application is None:
                raise PlatformJobFailure(
                    "application_not_found",
                    "Application no longer exists.",
                )
            if (application.organization_id != context.organization_id
                    or application.app_model != "inline_v1" or application.solution_id is not None):
                raise ValueError("Application ownership/model changed after publication was queued")
            controls = await publication_controls_hash(db, application.id)
            protected_git = payload.protected_git
            policy = None
            if protected_git is not None:
                policy = get_settings().inline_app_git_delivery_policy
                if policy is None:
                    raise ValueError("Protected Git App publication is not configured")
                enrollment = policy.enrollment_for(application.id)
                if (enrollment.organization_id != application.organization_id
                        or enrollment.repo_subpath != application.repo_path
                        or not application.published_snapshot or application.published_at is None):
                    raise ValueError("App is outside its enrolled published source/scope")
                if controls != protected_git.expected_controls_hash:
                    raise ValueError("Application controls changed after protected Git admission")

            repo = ApplicationRepository(
                db,
                application.organization_id,
                user_id=context.requested_by_user_id,
                is_superuser=True,
            )
            last_phase: str | None = None
            last_reported = -1

            async def report(
                phase: str,
                current: int,
                total: int | None,
            ) -> None:
                nonlocal last_phase, last_reported
                report_step = max(1, (total or 1) // 100)
                should_report = (
                    phase != last_phase
                    or current == 0
                    or (total is not None and current == total)
                    or current - last_reported >= report_step
                )
                if not should_report:
                    return
                await context.report(
                    phase,
                    current,
                    total,
                    _publish_percent(phase, current, total),
                )
                last_phase = phase
                last_reported = current

            storage = AppStorageService()
            if intent is not None:
                if intent.get("controls_hash") != controls:
                    raise ValueError("Application controls differ from publication intent")
                files_published = await storage.verify_publication(str(application.id), intent)
                if await publication_controls_hash(db, application.id, lock=True) != controls:
                    raise ValueError("Application controls changed during publication readback")
                # Reconcile only publication bookkeeping, after exact artifact
                # readback. No build, output write or manifest switch is replayed.
                application.published_snapshot = {path: "" for path in intent["artifact_hashes"]}
                application.published_at = datetime.now(timezone.utc)
                await db.flush()
                published = application
            else:
                async def checkpoint(proof: dict) -> None:
                    nonlocal intent
                    if await publication_controls_hash(db, application.id, lock=True) != controls:
                        raise ValueError("Application controls changed during its build")
                    intent = {**proof, "controls_hash": controls}
                    await context.save_checkpoint(intent, phase="Publication intent recorded")

                if protected_git is None:
                    published = await repo.publish(
                        application.id, context.requested_by_email, payload.message,
                        progress_callback=report, checkpoint_callback=checkpoint,
                    )
                else:
                    # Reuse the existing encrypted repository integration only
                    # in memory. Its sync branch is untouched. Never persist the
                    # producer's ephemeral token or copy credentials into a job.
                    assert policy is not None
                    config = await get_github_config(db, policy.organization_id)
                    if (config is None or not config.token or config.repo_url not in {
                            f"https://github.com/{policy.repository}",
                            f"https://github.com/{policy.repository}.git"}):
                        raise ValueError("Configured Git read credential/repository is unavailable")
                    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                        reader = ProtectedGitReader(policy, config.token, client)
                        source = await read_app_git_source(reader, policy=policy,
                            application_id=application.id, commit_sha=protected_git.source_commit_sha,
                            ci_run_id=protected_git.ci_run_id, ci_run_attempt=protected_git.ci_run_attempt,
                            artifact_digest=protected_git.artifact_digest)
                        proof = {**app_source_evidence(application.id, policy, source),
                            "artifact_digest": protected_git.artifact_digest,
                            "ci_run_id": protected_git.ci_run_id,
                            "ci_run_attempt": protected_git.ci_run_attempt,
                            "producer_run_id": protected_git.producer_run_id,
                            "producer_run_attempt": protected_git.producer_run_attempt}

                        async def guard_source() -> None:
                            await reader.verify_ci(protected_git.source_commit_sha,
                                protected_git.ci_run_id, protected_git.ci_run_attempt)
                            if await publication_controls_hash(db, application.id, lock=True) != controls:
                                raise ValueError("Application controls changed before Git publication")
                            # Recheck the original attempt's lease before any
                            # manifest switch, including after output writes.
                            await context.report("Protected source reverified", percent=95)

                        published = await repo.publish(
                            application.id, context.requested_by_email, payload.message,
                            progress_callback=report, checkpoint_callback=checkpoint,
                            source_snapshot=source.snapshot, source_provenance=proof,
                            before_publication=guard_source,
                        )
                if intent is None:
                    raise ValueError("Publication did not record its intent")
                files_published = await storage.verify_publication(str(application.id), intent)
            if published is None:
                raise PlatformJobFailure(
                    "application_not_found",
                    "Application no longer exists.",
                )

            published_at = (
                published.published_at.isoformat()
                if published.published_at is not None
                else None
            )
            await db.commit()

        result: dict[str, object] = {
            "application_id": str(payload.application_id),
            "published_at": published_at,
            "files_published": files_published,
            "publication_verified": True,
            "publication_intent": intent,
            "recovered_from_intent": context.checkpoint is not None,
        }
        if payload.protected_git is not None:
            # This identifies the original admission, including readback of an
            # older commit reused by a newer producer. Never label that job as
            # delivery of the newer request merely because it succeeded.
            result["git_source"] = payload.protected_git.model_dump(mode="json")
        try:
            await publish_app_published(
                app_id=str(payload.application_id),
                user_id=context.requested_by_user_id,
                user_name=context.requested_by_name,
                new_version_id=published_at or "",
            )
        except Exception:
            logger.warning(
                "Published app but failed to broadcast its live update",
                extra={"application_id": str(payload.application_id)},
                exc_info=True,
            )
        return result
    except PlatformJobFailure as exc:
        if intent is not None:
            raise PlatformJobRequiresAction("Publication requires exact readback", intent) from exc
        raise
    except Exception as exc:
        if intent is not None:
            # Retain durable evidence even for an unobserved storage/SQL outcome.
            # A resumed lost attempt can only read back this intent.
            raise PlatformJobRequiresAction("Publication requires exact readback", intent) from exc
        if not isinstance(exc, ValueError):
            raise
        raise PlatformJobFailure(
            "application_publish_failed",
            str(exc),
            retryable=False,
        ) from exc


APPLICATION_PUBLISH_DEFINITION = PlatformJobDefinition(
    job_type=APPLICATION_PUBLISH_JOB_TYPE,
    payload_version=1,
    payload_model=ApplicationPublishPayload,
    handler=run_application_publish,
    readback_checkpoint_schema=PUBLICATION_INTENT_SCHEMA,
    policy=PlatformJobPolicy(
        timeout_seconds=20 * 60,
        max_attempts=2,
        max_concurrency=1,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=256,
    ),
    operations_policy=platform_job_operations_policy(
        APPLICATION_PUBLISH_JOB_TYPE,
        workload_class=WorkloadClass.PLATFORM_INTERACTIVE,
    ),
)
