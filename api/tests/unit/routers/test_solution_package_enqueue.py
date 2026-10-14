"""Lost admission acknowledgments retain one package publisher and input."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.security import encrypt_secret
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.routers.solutions import _enqueue_solution_deploy_job
from tests.unit.services.solutions.test_package_recovery import retained_job


@pytest.fixture
def admission(monkeypatch):
    _, job, payload = retained_job("queued")
    archive = b"retained reviewed package archive"
    payload.input_sha256 = hashlib.sha256(archive).hexdigest()
    job.encrypted_payload = encrypt_secret(payload.model_dump_json())
    projection = SolutionDeployJob(
        id=job.id, install_id=payload.install_id, status="queued"
    )

    async def get(model, identity):
        assert identity == job.id
        return job if model is PlatformJob else projection

    db = SimpleNamespace(get=get, commit=AsyncMock())
    stage, enqueue, readback = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr("src.routers.solutions._lock_solution_operation", AsyncMock())
    monkeypatch.setattr(
        "src.routers.solutions.SolutionDeployJobStorage.write_bytes", stage
    )
    monkeypatch.setattr("src.routers.solutions.enqueue_platform_job", enqueue)
    monkeypatch.setattr(
        "src.services.solutions.package_runtime.readback_package_runtime", readback
    )
    request = {
        "kind": "deliver_package",
        "install_id": payload.install_id,
        "organization_id": job.organization_id,
        "options": dict(payload.options),
        "requested_by_user_id": job.requested_by_user_id,
        "requested_by_email": "system@bifrost.local",
        "requested_by_name": "Protected producer",
        "input_bytes": archive,
        "publication_id": job.id,
    }
    return SimpleNamespace(
        db=db,
        job=job,
        payload=payload,
        projection=projection,
        stage=stage,
        enqueue=enqueue,
        readback=readback,
        request=request,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running", "succeeded"])
async def test_duplicate_admission_reads_original_publication_without_restage(
    admission, status
):
    admission.job.status = status
    did = uuid4()
    admission.projection.result = {"deployment_id": str(did)}
    result = await _enqueue_solution_deploy_job(admission.db, **admission.request)
    assert result is admission.projection
    admission.stage.assert_not_awaited()
    admission.enqueue.assert_not_awaited()
    admission.db.commit.assert_not_awaited()
    if status == "succeeded":
        admission.readback.assert_awaited_once_with(
            admission.db,
            admission.payload.install_id,
            did,
            expected_source_sha256=admission.payload.input_sha256,
            expected_organization_id=None,
        )
    else:
        admission.readback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift", ["actor", "scope", "archive", "kind", "source", "artifact"]
)
async def test_reused_publication_identity_rejects_changed_admission(admission, drift):
    if drift == "actor":
        admission.request["requested_by_user_id"] = uuid4()
    elif drift == "scope":
        admission.request["organization_id"] = uuid4()
    elif drift == "archive":
        admission.request["input_bytes"] = b"replacement archive"
    elif drift == "kind":
        admission.request["kind"] = "deploy"
    else:
        key = "package_source" if drift == "source" else "artifact_digest"
        admission.request["options"][key] = "different protected source"
    with pytest.raises(HTTPException) as rejected:
        await _enqueue_solution_deploy_job(admission.db, **admission.request)
    assert rejected.value.status_code == 409
    assert rejected.value.detail == "Original package job identity differs."
    admission.stage.assert_not_awaited()
    admission.enqueue.assert_not_awaited()
    admission.readback.assert_not_awaited()
    admission.db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "requires_action"])
async def test_uncertain_admission_requeues_original_sealed_input(admission, status):
    admission.job.status = status
    result = await _enqueue_solution_deploy_job(admission.db, **admission.request)
    assert result is admission.projection
    args, kwargs = admission.enqueue.await_args
    assert args[2] == admission.payload
    assert kwargs["dedupe_key"] == admission.job.dedupe_key
    assert kwargs["resource_id"] == admission.job.resource_id
    assert kwargs["resource_lock_key"] == admission.job.resource_lock_key
    admission.enqueue.assert_awaited_once()
    admission.db.commit.assert_awaited_once()
    admission.stage.assert_not_awaited()
    admission.readback.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_admission_without_sealed_intent_cannot_publish_again(admission):
    admission.job.status = "failed"
    admission.job.result = {"error": "unverified failure"}
    with pytest.raises(HTTPException) as rejected:
        await _enqueue_solution_deploy_job(admission.db, **admission.request)
    assert rejected.value.status_code == 409
    assert "requires diagnosis" in rejected.value.detail
    admission.stage.assert_not_awaited()
    admission.enqueue.assert_not_awaited()
    admission.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_succeeded_admission_cannot_credit_unverified_runtime(admission):
    admission.job.status = "succeeded"
    admission.projection.result = {"deployment_id": str(uuid4())}
    admission.readback.side_effect = ValueError("Original package runtime bytes differ")
    with pytest.raises(ValueError, match="runtime bytes differ"):
        await _enqueue_solution_deploy_job(admission.db, **admission.request)
    admission.readback.assert_awaited_once()
    admission.stage.assert_not_awaited()
    admission.enqueue.assert_not_awaited()
    admission.db.commit.assert_not_awaited()
