"""Durable scheduler adapter for all Solution deploy and install variants."""

from __future__ import annotations

import tempfile
import logging
import hashlib
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.workspace_release import canonical_digest

from src.core.database import get_db_context
from src.jobs.execution_policy import (
    WorkloadClass,
    platform_job_operations_policy,
)
from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
    PlatformJobRequiresAction,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solutions import Solution
from src.services.solutions.deploy_job_storage import SolutionDeployJobStorage

logger = logging.getLogger(__name__)
SOLUTION_DEPLOY_INTENT_SCHEMA = "bifrost.solution-deploy-intent/v1"


class SolutionDeployPayload(BaseModel):
    deploy_job_id: UUID
    kind: Literal["deploy", "install", "install_from_repo", "deliver_package"]
    install_id: UUID | None = None
    input_sha256: str
    options: dict[str, Any]


async def unresolved_solution_deploy(
    db: AsyncSession, solution_id: UUID, *, exclude_job_id: UUID | None = None,
) -> PlatformJob | None:
    """An original commit intent must be reconciled before another writer."""
    query = select(PlatformJob).where(
        PlatformJob.job_type == "solution.deploy",
        PlatformJob.status != "succeeded",
        PlatformJob.result["schema_version"].as_string() == SOLUTION_DEPLOY_INTENT_SCHEMA,
        PlatformJob.result["solution_id"].as_string() == str(solution_id),
    )
    if exclude_job_id is not None:
        query = query.where(PlatformJob.id != exclude_job_id)
    return await db.scalar(query.order_by(PlatformJob.created_at.asc()).limit(1))


async def _readback_solution_deploy(
    context: PlatformJobContext,
    payload: SolutionDeployPayload,
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Inspect the original completed operation; never repeat its effects."""
    from src.services.solution_deploy_obligations import _runtime_and_registration_readback
    from src.services.solutions.source_artifact import SolutionSourceArtifactStorage
    from src.services.solutions.write_lock import solution_write_lock

    if (
        intent.get("schema_version") != SOLUTION_DEPLOY_INTENT_SCHEMA
        or intent.get("original_job_id") != str(context.job_id)
        or payload.deploy_job_id != context.job_id
        or intent.get("payload_digest") != canonical_digest(payload.model_dump(mode="json"))
        or intent.get("organization_id") != (
            str(context.organization_id) if context.organization_id is not None else None
        )
    ):
        raise ValueError("Solution deployment intent differs from its original job")
    solution_id = UUID(intent["solution_id"])
    if payload.install_id is not None and payload.install_id != solution_id:
        raise ValueError("Solution deployment target differs from its original job")
    async with solution_write_lock(solution_id):
        async with get_db_context() as db:
            projection = await db.get(SolutionDeployJob, payload.deploy_job_id)
            solution = await db.get(Solution, solution_id)
            if (
                projection is None or projection.status != "succeeded"
                or solution is None or solution.organization_id != context.organization_id
                or (projection.result or {}).get("solution_id") != str(solution_id)
                or (projection.result or {}).get("candidate_id") != f"sha256:{payload.input_sha256}"
                or projection.install_id not in (None, solution_id)
            ):
                raise ValueError("Original Solution deployment completion is not established")
            newer = await db.scalar(select(SolutionDeployJob.id).where(
                SolutionDeployJob.created_at > projection.created_at,
                or_(SolutionDeployJob.install_id == solution_id,
                    SolutionDeployJob.result["solution_id"].as_string() == str(solution_id)),
            ).limit(1))
            if newer is not None:
                raise ValueError("A newer Solution deployment prevents original-job readback")
            artifact = await SolutionSourceArtifactStorage(solution_id).read()
            if artifact is None or hashlib.sha256(artifact).hexdigest() != payload.input_sha256:
                raise ValueError("Original Solution source artifact no longer matches")
            valid, reason, readback = await _runtime_and_registration_readback(
                db, solution_id=solution_id, artifact=artifact,
            )
            if not valid:
                raise ValueError(reason or "Original Solution runtime does not match")
            await context.report("Original Solution source and runtime verified", percent=100)
            return {**(projection.result or {}), "recovered_from_intent": True,
                    "original_job_id": str(context.job_id), "runtime_readback": readback}


async def run_solution_deploy(
    context: PlatformJobContext,
    payload: SolutionDeployPayload,
) -> dict:
    from src.routers.solutions import _run_deploy_job, _run_install_job

    if payload.kind == "deliver_package":
        from src.jobs.platform.solution_package_delivery import run_solution_package_delivery
        return await run_solution_package_delivery(context, payload)

    if payload.deploy_job_id != context.job_id:
        raise PlatformJobFailure("solution_deploy_identity_mismatch", "Solution job identity differs from its payload.")
    intent = context.checkpoint
    if intent is not None:
        try:
            return await _readback_solution_deploy(context, payload, intent)
        except Exception as exc:
            raise PlatformJobRequiresAction("Original Solution deployment requires readback", intent) from exc

    async def before_commit(solution_id: UUID) -> None:
        nonlocal intent
        if payload.install_id is not None and solution_id != payload.install_id:
            raise ValueError("Solution deployment target changed before commit")
        async with get_db_context() as db:
            if await unresolved_solution_deploy(db, solution_id, exclude_job_id=context.job_id) is not None:
                raise ValueError("An earlier Solution deployment requires original-job readback")
        proof = {
            "schema_version": SOLUTION_DEPLOY_INTENT_SCHEMA,
            "original_job_id": str(context.job_id),
            "solution_id": str(solution_id),
            "organization_id": str(context.organization_id) if context.organization_id is not None else None,
            "payload_digest": canonical_digest(payload.model_dump(mode="json")),
        }
        await context.save_checkpoint(proof, phase="Solution commit intent recorded")
        intent = proof

    storage = SolutionDeployJobStorage(payload.deploy_job_id)
    completed = False
    cleanup_safe = False
    await context.report("Loading staged Solution input", percent=2)
    try:
        with tempfile.TemporaryDirectory(prefix="bifrost-solution-job-") as tmp:
            zip_path = Path(tmp) / "input.zip"
            await storage.copy_to_path(
                zip_path,
                expected_sha256=payload.input_sha256,
            )
            raw_accountability_org_id = payload.options.get(
                "accountability_organization_id"
            )
            accountability_organization_id = (
                UUID(str(raw_accountability_org_id))
                if raw_accountability_org_id
                else None
            )
            candidate_id = str(payload.options.get("candidate_id") or "")
            if payload.kind in {"deploy", "install_from_repo"}:
                if payload.install_id is None:
                    raise PlatformJobFailure(
                        "solution_not_found", "Solution install is missing."
                    )
                await _run_deploy_job(
                    payload.deploy_job_id,
                    payload.install_id,
                    zip_path,
                    force=bool(payload.options.get("force", False)),
                    candidate_id=candidate_id,
                    accountability_organization_id=accountability_organization_id,
                    # A from-repo install is created git-connected by the same
                    # request that enqueues this first deploy; without the
                    # exemption the one-writer refusal would deadlock creation.
                    # Manual deploys (kind="deploy") stay refused.
                    allow_connected_install=payload.kind == "install_from_repo",
                    before_commit=before_commit,
                )
            else:
                raw_org_id = payload.options.get("organization_id")
                await _run_install_job(
                    payload.deploy_job_id,
                    zip_path,
                    organization_id=UUID(raw_org_id) if raw_org_id else None,
                    config_values=payload.options.get("config_values", {}),
                    deployer_email=str(payload.options["deployer_email"]),
                    force=bool(payload.options.get("force", False)),
                    password=payload.options.get("password"),
                    replace_secrets=bool(payload.options.get("replace_secrets", False)),
                    replace_data=bool(payload.options.get("replace_data", False)),
                    reactivate=bool(payload.options.get("reactivate", False)),
                    candidate_id=candidate_id,
                    accountability_organization_id=accountability_organization_id,
                    before_commit=before_commit,
                )
        failure: PlatformJobFailure | None = None
        result: dict[str, Any] = {}
        async with get_db_context() as db:
            projection = await db.get(SolutionDeployJob, payload.deploy_job_id)
            if projection is None:
                raise PlatformJobFailure("deploy_job_missing", "Deploy job is missing.")
            if projection.status != "succeeded":
                if intent is not None:
                    raise PlatformJobRequiresAction("Original Solution deployment requires readback", intent)
                if payload.kind == "install_from_repo" and payload.install_id:
                    projection.install_id = None
                    await db.flush()
                    orphan = await db.get(Solution, payload.install_id)
                    if orphan is not None:
                        await db.delete(orphan)
                failure = PlatformJobFailure(
                    "solution_deploy_failed",
                    projection.error or "Solution deploy failed.",
                )
            else:
                result = projection.result or {}
        # The failed install cleanup above must commit before the platform job
        # reports failure. Raising inside get_db_context would roll the delete
        # back and leave an orphan that blocks a retry with the same slug.
        if failure is not None:
            raise failure
        await context.report("Solution deploy complete", percent=100)
        await context.log(
            "info",
            "solution_deploy_completed",
            f"Solution {payload.kind} job {payload.deploy_job_id} completed",
        )
        completed = True
        return result
    except PlatformJobCancelled:
        if intent is not None:
            raise PlatformJobRequiresAction("Original Solution deployment requires readback", intent)
        raise
    except Exception as exc:
        if intent is not None:
            raise PlatformJobRequiresAction("Original Solution deployment requires readback", intent) from exc
        cleanup_safe = True
        raise
    finally:
        if cleanup_safe or completed:
            try:
                await storage.delete()
            except Exception:
                logger.warning(
                    "Failed to delete staged Solution input for job %s",
                    payload.deploy_job_id,
                    exc_info=True,
                )


SOLUTION_DEPLOY_DEFINITION = PlatformJobDefinition(
    job_type="solution.deploy",
    payload_version=1,
    payload_model=SolutionDeployPayload,
    handler=run_solution_deploy,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=512,
    ),
    operations_policy=platform_job_operations_policy(
        "solution.deploy",
        workload_class=WorkloadClass.PLATFORM_INTERACTIVE,
    ),
    encrypt_payload=True,
    readback_checkpoint_schema=SOLUTION_DEPLOY_INTENT_SCHEMA,
)
