"""Source-scoped admission uses the canonical App job and preserves its requester."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from src.core.constants import SYSTEM_USER_UUID
from src.jobs.platform.application_publish import APPLICATION_PUBLISH_DEFINITION
from src.models.contracts.applications import ApplicationGitSourcePublicationRequest
from src.services.application_git_delivery import (
    AppGitPublicationNotFound,
    app_git_job_id,
    enqueue_app_git_publication,
    inspect_app_git_publication,
)
from src.services.solutions.github_delivery_source import GitDeliveryIdentity, GitDeliverySourceError
from tests.unit.services.test_application_git_source import SID, app_policy


def installed_app():
    return SimpleNamespace(id=SID, name="Installed App", organization_id=app_policy().organization_id,
        repo_path="apps/fixture", app_model="inline_v1", solution_id=None,
        published_snapshot={"entry.js": ""}, published_at=datetime.now(timezone.utc))


def request():
    return ApplicationGitSourcePublicationRequest(source_commit_sha="a" * 40,
        ci_run_id=123, ci_run_attempt=2, artifact_digest="sha256:" + "c" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing", "scope", "root", "v2", "solution", "unpublished",
    "unpublished_time", "source", "foreign_requester"])
async def test_admission_verifies_source_preserves_app_and_enqueues_no_credentials(fault):
    app = installed_app()
    if fault == "scope":
        app.organization_id = None
    elif fault == "root":
        app.repo_path = "apps/other"
    elif fault == "v2":
        app.app_model = "standalone_v2"
    elif fault == "solution":
        app.solution_id = UUID(int=20)
    elif fault == "unpublished":
        app.published_snapshot = None
    elif fault == "unpublished_time":
        app.published_at = None
    db = SimpleNamespace(get=AsyncMock(side_effect=lambda model, *_args, **_kwargs:
        None if model.__name__ != "Application" or fault == "missing" else app))
    source_read = AsyncMock(side_effect=GitDeliverySourceError("Source superseded") if fault == "source" else None)
    job = SimpleNamespace(requested_by_user_id=str(UUID(int=30) if fault == "foreign_requester" else SYSTEM_USER_UUID))
    enqueue = AsyncMock(return_value=(job, fault == "foreign_requester"))
    controls = "sha256:" + "d" * 64
    with patch("src.services.application_git_delivery.read_app_git_source", new=source_read), \
         patch("src.services.application_git_delivery.publication_controls_hash", new=AsyncMock(return_value=controls)), \
         patch("src.services.application_git_delivery.enqueue_platform_job", new=enqueue):
        arguments = dict(policy=app_policy(), reader=object(), application_id=SID,
            request=request(), producer=GitDeliveryIdentity("456", 1))
        if fault:
            with pytest.raises(GitDeliverySourceError):
                await enqueue_app_git_publication(db, **arguments)
            if fault != "foreign_requester":
                enqueue.assert_not_awaited()
        else:
            assert await enqueue_app_git_publication(db, **arguments) == (job, False)
            assert enqueue.await_args.args[1] is APPLICATION_PUBLISH_DEFINITION
            payload = enqueue.await_args.args[2].model_dump(mode="json")
            assert payload["protected_git"]["expected_controls_hash"] == controls
            assert payload["protected_git"]["source_commit_sha"] == "a" * 40
            assert enqueue.await_args.kwargs["requested_by_user_id"] == SYSTEM_USER_UUID
            assert enqueue.await_args.kwargs["dedupe_key"] == str(SID)
            assert enqueue.await_args.kwargs["job_id"] == app_git_job_id(SID, request(), GitDeliveryIdentity("456", 1))
            assert set(payload["protected_git"]) == {
                "expected_controls_hash", "source_commit_sha", "ci_run_id", "ci_run_attempt",
                "artifact_digest", "producer_run_id", "producer_run_attempt"}
    source_read.assert_awaited_once()


@pytest.mark.asyncio
async def test_lost_terminal_response_returns_same_completed_job_without_effect_replay():
    app = installed_app()
    from src.jobs.platform.application_publish import ApplicationPublishPayload
    from src.models.contracts.applications import ApplicationGitPublicationInput
    payload = ApplicationPublishPayload(application_id=SID, protected_git=ApplicationGitPublicationInput(
        **request().model_dump(), expected_controls_hash="sha256:" + "d" * 64,
        producer_run_id="456", producer_run_attempt=1))
    job = SimpleNamespace(job_type=APPLICATION_PUBLISH_DEFINITION.job_type, organization_id=app.organization_id,
        requested_by_user_id=str(SYSTEM_USER_UUID), resource_type="application", resource_id=str(SID),
        payload=payload.model_dump(mode="json"), status="succeeded")
    db = SimpleNamespace(get=AsyncMock(side_effect=[app, job]))
    enqueue = AsyncMock(side_effect=AssertionError("Completed admission must not execute again"))
    with patch("src.services.application_git_delivery.read_app_git_source", new=AsyncMock()), \
         patch("src.services.application_git_delivery.publication_controls_hash", new=AsyncMock(return_value="sha256:" + "d" * 64)), \
         patch("src.services.application_git_delivery.enqueue_platform_job", new=enqueue):
        result = await enqueue_app_git_publication(db, policy=app_policy(), reader=object(),
            application_id=SID, request=request(), producer=GitDeliveryIdentity("456", 1))
    assert result == (job, True)
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["requires_action", "failed", "cancelled"])
@pytest.mark.parametrize("checkpoint", [False, True])
async def test_original_admission_can_resume_only_sealed_readback_checkpoint(status, checkpoint):
    from src.jobs.platform.application_publish import ApplicationPublishPayload
    from src.models.contracts.applications import ApplicationGitPublicationInput
    app = installed_app()
    payload = ApplicationPublishPayload(application_id=SID, protected_git=ApplicationGitPublicationInput(
        **request().model_dump(), expected_controls_hash="sha256:" + "d" * 64,
        producer_run_id="456", producer_run_attempt=1))
    identity = app_git_job_id(SID, request(), GitDeliveryIdentity("456", 1))
    job = SimpleNamespace(id=identity, job_type=APPLICATION_PUBLISH_DEFINITION.job_type,
        organization_id=app.organization_id, requested_by_user_id=str(SYSTEM_USER_UUID),
        resource_type="application", resource_id=str(SID), payload=payload.model_dump(mode="json"),
        status=status, result={"schema_version": APPLICATION_PUBLISH_DEFINITION.readback_checkpoint_schema}
        if checkpoint else {})
    db = SimpleNamespace(get=AsyncMock(side_effect=[app, job]))
    enqueue = AsyncMock(return_value=(job, True))
    with patch("src.services.application_git_delivery.read_app_git_source", new=AsyncMock()), \
         patch("src.services.application_git_delivery.publication_controls_hash", new=AsyncMock(return_value="sha256:" + "d" * 64)), \
         patch("src.services.application_git_delivery.enqueue_platform_job", new=enqueue):
        result = await enqueue_app_git_publication(db, policy=app_policy(), reader=object(),
            application_id=SID, request=request(), producer=GitDeliveryIdentity("456", 1))
    assert result == (job, True)
    if checkpoint:
        enqueue.assert_awaited_once()
        assert enqueue.await_args.kwargs["job_id"] == identity
        assert enqueue.await_args.args[2].model_dump(mode="json") == job.payload
    else:
        enqueue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "other_app", "scope", "requester", "manual_payload",
    "job_type", "artifact_drift", "control_drift", "missing_intent", "runtime_pin_drift"])
async def test_scoped_inspection_rechecks_live_outputs_and_controls_without_effects(fault):
    controls = "sha256:" + "d" * 64
    intent = {"controls_hash": controls}
    job = SimpleNamespace(id=UUID(int=40), job_type=APPLICATION_PUBLISH_DEFINITION.job_type,
        organization_id=app_policy().organization_id, requested_by_user_id=str(SYSTEM_USER_UUID),
        resource_type="application", resource_id=str(SID), payload={"protected_git": {"source_commit_sha": "a" * 40}},
        status="succeeded", result={"publication_verified": True, "publication_intent": intent,
            "runtime_pin": {"runtime_pin_hash": "sha256:" + "e" * 64}})
    if fault == "other_app":
        job.resource_id = str(UUID(int=20))
    elif fault == "scope":
        job.organization_id = None
    elif fault == "requester":
        job.requested_by_user_id = str(UUID(int=30))
    elif fault == "manual_payload":
        job.payload["protected_git"] = None
    elif fault == "job_type":
        job.job_type = "application.deploy"
    elif fault == "missing_intent":
        job.result["publication_intent"] = None
    db = SimpleNamespace(get=AsyncMock(return_value=job))
    runtime_pin = {"runtime_pin_hash": "sha256:" + ("f" if fault == "runtime_pin_drift" else "e") * 64}
    verify = AsyncMock(side_effect=ValueError("changed") if fault == "artifact_drift" else None)
    with patch("src.services.application_git_delivery.read_app_publication_runtime_pin",
            new=AsyncMock(return_value=runtime_pin)), \
         patch("src.services.application_git_delivery.AppStorageService",
            return_value=SimpleNamespace(verify_publication=verify)), \
         patch("src.services.application_git_delivery.publication_controls_hash",
            new=AsyncMock(return_value="different" if fault == "control_drift" else controls)):
        arguments = dict(policy=app_policy(), application_id=SID, job_id=UUID(int=40))
        if fault:
            error = (AppGitPublicationNotFound if fault in {"other_app", "scope", "requester", "manual_payload", "job_type"}
                else GitDeliverySourceError)
            with pytest.raises(error):
                await inspect_app_git_publication(db, **arguments)
        else:
            assert await inspect_app_git_publication(db, **arguments) is job
            verify.assert_awaited_once_with(str(SID), intent)


@pytest.mark.asyncio
async def test_lost_enqueue_response_inspects_original_deduplicated_source_not_requested_commit():
    original = SimpleNamespace(job_type=APPLICATION_PUBLISH_DEFINITION.job_type,
        organization_id=app_policy().organization_id, requested_by_user_id=str(SYSTEM_USER_UUID),
        resource_type="application", resource_id=str(SID),
        payload={"protected_git": {"source_commit_sha": "older"}}, status="requires_action")
    query_result = SimpleNamespace(scalar_one_or_none=lambda: original)
    db = SimpleNamespace(get=AsyncMock(return_value=None), execute=AsyncMock(return_value=query_result))
    storage = AsyncMock(side_effect=AssertionError("Uncertain status is inspection, not recovery replay"))
    with patch("src.services.application_git_delivery.AppStorageService", new=storage):
        inspected = await inspect_app_git_publication(db, policy=app_policy(),
            application_id=SID, job_id=UUID(int=40))
    assert inspected is original
    assert inspected.payload["protected_git"]["source_commit_sha"] == "older"
    storage.assert_not_called()
