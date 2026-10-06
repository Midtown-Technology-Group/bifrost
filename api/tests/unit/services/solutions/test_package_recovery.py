"""A current producer can resume only the older original readback job."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost.workspace_release import canonical_digest
from src.core.constants import SYSTEM_USER_UUID
from src.core.security import encrypt_secret
from src.jobs.platform.solution_deploy import SolutionDeployPayload, SOLUTION_DEPLOY_INTENT_SCHEMA
from src.models.orm.platform_jobs import PlatformJob
from src.services.solutions.package_admission import package_publication_id, recover_pending_package
from tests.unit.services.solutions.test_package_git_source import package_policy, SID


def retained_job(status="requires_action"):
    policy = package_policy()
    enrollment = policy.enrollment_for(SID)
    source = {"repository": policy.repository, "repository_id": policy.repository_id,
        "repository_owner_id": policy.repository_owner_id, "recipe_path": enrollment.recipe_path,
        "package": {"solution_id": str(SID), "organization_id": None,
            "repo_subpath": enrollment.repo_subpath, "source_archive_sha256": "c" * 64,
            "source_commit_sha": "a" * 40}}
    artifact = canonical_digest(source)
    job_id = package_publication_id(SID, artifact)
    payload = SolutionDeployPayload(deploy_job_id=job_id, kind="deliver_package", install_id=SID,
        input_sha256="c" * 64, options={"package_source": source, "artifact_digest": artifact})
    intent = {"schema_version": SOLUTION_DEPLOY_INTENT_SCHEMA, "delivery_kind": "package",
        "original_job_id": str(job_id), "solution_id": str(SID), "deployment_id": str(uuid4()),
        "payload_digest": canonical_digest(payload.model_dump(mode="json"))}
    job = PlatformJob(id=job_id, job_type="solution.deploy", payload_version=1,
        encrypted_payload=encrypt_secret(payload.model_dump_json()), status=status, result=intent,
        requested_by_user_id=str(SYSTEM_USER_UUID), organization_id=None, dedupe_key=str(job_id),
        resource_type="solution_deploy", resource_id=str(job_id), resource_lock_key=f"solution:{SID}",
        priority=500, title="Original package", action_url=f"/solutions/{SID}")
    return policy, job, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["requires_action", "failed", "cancelled", "queued", "running"])
async def test_recovery_keeps_original_input_and_identity_without_source_or_stage(monkeypatch, status):
    policy, job, payload = retained_job(status)
    db = SimpleNamespace(commit=AsyncMock())
    enqueue = AsyncMock(return_value=(job, True))
    monkeypatch.setattr("src.routers.solutions._lock_solution_operation", AsyncMock())
    monkeypatch.setattr("src.jobs.platform.solution_deploy.unresolved_solution_deploy", AsyncMock(return_value=job))
    monkeypatch.setattr("src.services.platform_jobs.enqueue_platform_job", enqueue)
    stage = AsyncMock(side_effect=AssertionError("Recovery must not restage source"))
    monkeypatch.setattr("src.services.solutions.deploy_job_storage.SolutionDeployJobStorage.write_bytes", stage)
    result = await recover_pending_package(db, policy, SID)
    assert result is job
    if status in {"requires_action", "failed", "cancelled"}:
        assert enqueue.await_args.args[2] == payload
        assert enqueue.await_args.kwargs["dedupe_key"] == str(job.id)
        assert enqueue.await_args.kwargs["resource_id"] == str(job.id)
        db.commit.assert_awaited_once()
    else:
        enqueue.assert_not_awaited()
        db.commit.assert_not_awaited()
    stage.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["actor", "scope", "kind", "identity", "resource", "intent", "source", "enrollment"])
async def test_changed_original_intent_cannot_resume(monkeypatch, drift):
    policy, job, payload = retained_job()
    if drift == "actor":
        job.requested_by_user_id = str(uuid4())
    elif drift == "scope":
        job.organization_id = uuid4()
    elif drift == "kind":
        payload.kind = "deploy"
    elif drift == "identity":
        payload.deploy_job_id = uuid4()
    elif drift == "resource":
        job.resource_id = str(uuid4())
    elif drift == "intent":
        job.result = {**(job.result or {}), "payload_digest": "sha256:" + "0" * 64}
    elif drift == "source":
        payload.options["package_source"]["package"]["source_archive_sha256"] = "0" * 64
    else:
        values = policy.model_dump()
        values["packages"][SID]["repo_subpath"] = "solutions/other"
        policy = type(policy).model_validate(values)
    job.encrypted_payload = encrypt_secret(payload.model_dump_json())
    enqueue = AsyncMock()
    db = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr("src.routers.solutions._lock_solution_operation", AsyncMock())
    monkeypatch.setattr("src.jobs.platform.solution_deploy.unresolved_solution_deploy", AsyncMock(return_value=job))
    monkeypatch.setattr("src.services.platform_jobs.enqueue_platform_job", enqueue)
    with pytest.raises(ValueError, match="Original"):
        await recover_pending_package(db, policy, SID)
    enqueue.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_dedupe_must_reuse_original_job(monkeypatch):
    policy, job, _payload = retained_job()
    db = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr("src.routers.solutions._lock_solution_operation", AsyncMock())
    monkeypatch.setattr("src.jobs.platform.solution_deploy.unresolved_solution_deploy", AsyncMock(return_value=job))
    monkeypatch.setattr("src.services.platform_jobs.enqueue_platform_job", AsyncMock(return_value=(
        PlatformJob(id=uuid4()), False)))
    with pytest.raises(ValueError, match="replacement"):
        await recover_pending_package(db, policy, SID)
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_without_original_intent_has_no_effects(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr("src.routers.solutions._lock_solution_operation", AsyncMock())
    monkeypatch.setattr("src.jobs.platform.solution_deploy.unresolved_solution_deploy", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.platform_jobs.enqueue_platform_job", enqueue)
    assert await recover_pending_package(db, package_policy(), SID) is None
    enqueue.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.e2e
async def test_shared_scheduler_requeues_the_original_row_without_replacing_payload(db_session, monkeypatch):
    from sqlalchemy import select
    from src.models.orm.solutions import Solution

    policy, job, payload = retained_job()
    db_session.add(Solution(id=SID, slug="fixture", name="Fixture", organization_id=None))
    job.payload = {"protected": True}
    job.requested_by_email = "system@bifrost.local"
    job.requested_by_name = "Protected package test"
    job.attempt, job.max_attempts, job.revision = 1, 2, 1
    db_session.add(job)
    await db_session.commit()
    original_payload, original_intent = job.encrypted_payload, dict(job.result)
    stage = AsyncMock(side_effect=AssertionError("No source staging during readback"))
    monkeypatch.setattr("src.services.solutions.deploy_job_storage.SolutionDeployJobStorage.write_bytes", stage)
    recovered = await recover_pending_package(db_session, policy, SID)
    assert recovered.id == payload.deploy_job_id and recovered.status == "queued"
    assert recovered.encrypted_payload == original_payload and recovered.result == original_intent
    assert recovered.max_attempts == 2 and recovered.attempt == 1
    assert (await db_session.scalars(select(PlatformJob).where(
        PlatformJob.dedupe_key == str(job.id)))).all() == [recovered]
    again = await recover_pending_package(db_session, policy, SID)
    assert again.id == recovered.id and again.revision == recovered.revision
    stage.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_rejects_stale_current_main_before_any_readback_enqueue(monkeypatch):
    from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
    from src.routers import solution_deployments as routes
    from src.services.solutions.github_delivery_source import GitDeliverySourceError

    policy = package_policy()
    body = SolutionGitSourceDeliveryRequest(source_commit_sha="e" * 40, ci_run_id=2,
        ci_run_attempt=1, artifact_digest="sha256:" + "f" * 64)
    monkeypatch.setattr(routes, "_authenticate_package", AsyncMock(return_value=policy))
    ci = AsyncMock(side_effect=GitDeliverySourceError("Current Main changed"))
    monkeypatch.setattr("src.services.solutions.github_delivery_source.ProtectedGitReader.verify_ci", ci)
    recovery = AsyncMock()
    monkeypatch.setattr("src.services.solutions.package_admission.recover_pending_package", recovery)
    db = SimpleNamespace(rollback=AsyncMock())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as stopped:
        await routes.recover_github_package(SID, body, db, None, "test-only-token")
    assert stopped.value.status_code == 409
    ci.assert_awaited_once_with(body.source_commit_sha, body.ci_run_id, body.ci_run_attempt)
    recovery.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_recovery_without_an_original_returns_a_versioned_object(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from src.core.database import get_db
    from src.routers import solution_deployments as routes

    policy = package_policy()
    monkeypatch.setattr(routes, "_authenticate_package", AsyncMock(return_value=policy))
    monkeypatch.setattr("src.services.solutions.github_delivery_source.ProtectedGitReader.verify_ci", AsyncMock())
    monkeypatch.setattr("src.services.solutions.package_admission.recover_pending_package", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(rollback=AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/solutions/{SID}/deployments/github-package/recover",
            headers={"Authorization": "Bearer test-identity", "X-GitHub-Job-Token": "test-only-token"},
            json={"source_commit_sha": "e" * 40, "ci_run_id": 2, "ci_run_attempt": 1,
                "artifact_digest": "sha256:" + "f" * 64})
    assert response.status_code == 200
    assert response.json() == {"schema_version": "bifrost.solution-package-recovery/v1", "job": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [LookupError("missing job"), ValueError("runtime differs")])
async def test_failed_status_inspection_releases_its_database_lock(monkeypatch, failure):
    from fastapi import HTTPException
    from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
    from src.routers import solution_deployments as routes

    monkeypatch.setattr(routes, "_authenticate_package", AsyncMock())
    monkeypatch.setattr("src.services.solutions.package_admission.inspect_package_job", AsyncMock(side_effect=failure))
    db = SimpleNamespace(rollback=AsyncMock())
    body = SolutionGitSourceDeliveryRequest(source_commit_sha="e" * 40, ci_run_id=2,
        ci_run_attempt=1, artifact_digest="sha256:" + "f" * 64)
    with pytest.raises(HTTPException) as stopped:
        await routes.inspect_github_package(SID, body, db, None)
    assert stopped.value.status_code == (404 if isinstance(failure, LookupError) else 409)
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [None, "committed", "payload", "target", "unverified"])
async def test_rollback_readback_proves_absence_not_runtime_delivery(monkeypatch, drift):
    from src.jobs.platform.solution_package_delivery import PACKAGE_ROLLBACK_SCHEMA
    from src.services.solutions.package_admission import read_package_rollback

    _, job, _payload = retained_job(status="failed")
    intent = job.result
    job.result = {"schema_version": PACKAGE_ROLLBACK_SCHEMA, "publication_not_committed": True,
        "original_job_id": str(job.id), "solution_id": str(SID), "original_intent": intent}
    if drift == "payload":
        intent["payload_digest"] = "sha256:" + "0" * 64
    elif drift == "target":
        job.result["solution_id"] = str(uuid4())
    elif drift == "unverified":
        job.result["publication_not_committed"] = False
    db = SimpleNamespace(get=AsyncMock(return_value=object() if drift == "committed" else None))
    if drift is None:
        proof = await read_package_rollback(db, job)
        assert proof == {"verified": True, "original_job_id": str(job.id),
            "solution_id": str(SID), "deployment_id": intent["deployment_id"]}
        assert "source_verified" not in proof and "runtime_verified" not in proof
    else:
        with pytest.raises(ValueError, match="rollback readback differs"):
            await read_package_rollback(db, job)
