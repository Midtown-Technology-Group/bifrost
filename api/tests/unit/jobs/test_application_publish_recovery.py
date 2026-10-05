"""An uncertain App publication resumes by readback, never effect replay."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform import application_publish as jobs
from src.jobs.platform.base import PlatformJobContext, PlatformJobRequiresAction
from src.models.contracts.applications import ApplicationGitPublicationInput
from src.core.application_delivery_policy import InlineAppGitDeliveryPolicy
from src.services.inline_app_source import InlineAppSourceSnapshot
from src.services.solutions.github_delivery_source import VerifiedInlineAppSource
from tests.unit.services.test_application_git_source import app_policy


@pytest.fixture
def setup(monkeypatch):
    app = SimpleNamespace(id=uuid4(), organization_id=None, app_model="inline_v1", solution_id=None,
                          published_snapshot=None, published_at=None)
    db = SimpleNamespace(get=AsyncMock(return_value=app), flush=AsyncMock(), commit=AsyncMock())
    @asynccontextmanager
    async def context():
        yield db
    storage = SimpleNamespace(verify_publication=AsyncMock(return_value=2))
    monkeypatch.setattr(jobs, "read_app_publication_runtime_pin", AsyncMock(return_value={
        "runtime_pin_hash": "sha256:" + "f" * 64}))
    publisher = AsyncMock()
    monkeypatch.setattr(jobs, "get_db_context", context)
    monkeypatch.setattr(jobs, "publication_controls_hash", AsyncMock(return_value="sha256:" + "a" * 64))
    monkeypatch.setattr(jobs, "AppStorageService", lambda: storage)
    monkeypatch.setattr(jobs, "ApplicationRepository", lambda *_args, **_kwargs: SimpleNamespace(publish=publisher))
    monkeypatch.setattr(jobs, "publish_app_published", AsyncMock())
    intent = {"schema_version": "bifrost.application-publication-intent/v1", "application_id": str(app.id),
              "expected_live_etag": None, "controls_hash": "sha256:" + "a" * 64,
              "artifact_hashes": {"entry.js": "sha256:" + "b" * 64, "manifest.json": "sha256:" + "c" * 64}}
    return app, db, storage, publisher, intent


def _context(intent=None):
    return PlatformJobContext(job_id=uuid4(), lease_token=uuid4(), organization_id=None,
                              requested_by_user_id=str(uuid4()), requested_by_email="operator@example.com",
                              requested_by_name="Operator", checkpoint=intent)


@pytest.mark.asyncio
async def test_worker_loss_uses_saved_intent_without_build_or_publish(setup):
    app, db, storage, publisher, intent = setup
    result = await jobs.run_application_publish(_context(intent), jobs.ApplicationPublishPayload(application_id=app.id))
    assert result["publication_verified"] is True
    assert result["recovered_from_intent"] is True
    assert app.published_snapshot == {"entry.js": "", "manifest.json": ""}
    assert app.published_at is not None
    publisher.assert_not_awaited()
    storage.verify_publication.assert_awaited_once_with(str(app.id), intent)
    db.commit.assert_awaited_once()
    jobs.read_app_publication_runtime_pin.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["readback", "controls"])
async def test_uncertain_or_changed_readback_preserves_intent_without_writes(setup, failure):
    app, db, storage, publisher, intent = setup
    if failure == "readback":
        storage.verify_publication.side_effect = TimeoutError("unavailable")
    else:
        intent["controls_hash"] = "sha256:" + "d" * 64
    with pytest.raises(PlatformJobRequiresAction) as error:
        await jobs.run_application_publish(_context(intent), jobs.ApplicationPublishPayload(application_id=app.id))
    assert error.value.result == intent
    publisher.assert_not_awaited()
    db.commit.assert_not_awaited()
    assert app.published_at is None


@pytest.mark.asyncio
async def test_lost_response_after_intent_retains_evidence(monkeypatch, setup):
    app, db, storage, publisher, intent = setup
    save = AsyncMock()
    monkeypatch.setattr(PlatformJobContext, "save_checkpoint", save)
    async def lost(*_args, **kwargs):
        await kwargs["checkpoint_callback"]({key: value for key, value in intent.items() if key != "controls_hash"})
        raise TimeoutError("lost response")
    publisher.side_effect = lost
    with pytest.raises(PlatformJobRequiresAction) as error:
        await jobs.run_application_publish(_context(), jobs.ApplicationPublishPayload(application_id=app.id))
    assert error.value.result == intent
    save.assert_awaited_once_with(intent, phase="Publication intent recorded")
    db.commit.assert_not_awaited()
    storage.verify_publication.assert_not_awaited()


def protected_setup(monkeypatch, app):
    from datetime import datetime, timezone
    app.repo_path = "apps/fixture"
    app.published_snapshot = {"old.js": ""}
    app.published_at = datetime.now(timezone.utc)
    policy = app_policy().model_dump()
    policy["applications"] = {app.id: {"organization_id": None, "repo_subpath": app.repo_path}}
    policy = InlineAppGitDeliveryPolicy.model_validate(policy)
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(inline_app_git_delivery_policy=policy))
    protected = ApplicationGitPublicationInput(source_commit_sha="a" * 40, ci_run_id=123, ci_run_attempt=2,
        artifact_digest="sha256:" + "c" * 64, expected_controls_hash="sha256:" + "a" * 64,
        producer_run_id="456", producer_run_attempt=1)
    return jobs.ApplicationPublishPayload(application_id=app.id, protected_git=protected)


@pytest.mark.asyncio
async def test_protected_git_recovery_reads_original_intent_without_git_read_build_or_effect_replay(monkeypatch, setup):
    app, db, storage, publisher, intent = setup
    payload = protected_setup(monkeypatch, app)
    config = AsyncMock(side_effect=AssertionError("Original intent needs no new Git credential"))
    source = AsyncMock(side_effect=AssertionError("Original source must not be rebuilt"))
    monkeypatch.setattr(jobs, "get_github_config", config)
    monkeypatch.setattr(jobs, "read_app_git_source", source)
    context = _context(intent)
    result = await jobs.run_application_publish(context, payload)
    assert result["recovered_from_intent"] and result["publication_intent"] == intent
    config.assert_not_awaited()
    source.assert_not_awaited()
    publisher.assert_not_awaited()
    storage.verify_publication.assert_awaited_once_with(str(app.id), intent)
    jobs.read_app_publication_runtime_pin.assert_awaited_once_with(storage,
        application_id=app.id, publication_job_id=context.job_id, organization_id=None,
        intent=intent, protected_git=payload.protected_git.model_dump(mode="json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["manifest", "controls"])
async def test_recovery_pin_failure_preserves_intent_without_publication_or_commit(monkeypatch, setup, failure):
    app, db, storage, publisher, intent = setup
    payload = protected_setup(monkeypatch, app)
    if failure == "manifest":
        jobs.read_app_publication_runtime_pin.side_effect = ValueError("Manifest source differs")
    else:
        # Initial, readback and final checks. Drift at the last check must not
        # commit publication bookkeeping even though the output bytes match.
        jobs.publication_controls_hash.side_effect = [intent["controls_hash"], intent["controls_hash"], "changed"]
    with pytest.raises(PlatformJobRequiresAction) as error:
        await jobs.run_application_publish(_context(intent), payload)
    assert error.value.result == intent
    publisher.assert_not_awaited()
    db.commit.assert_not_awaited()
    storage.verify_publication.assert_awaited_once_with(str(app.id), intent)
    jobs.read_app_publication_runtime_pin.assert_awaited_once()


@pytest.mark.asyncio
async def test_protected_git_job_builds_captured_bytes_and_rechecks_source_controls_and_lease(monkeypatch, setup):
    app, db, storage, publisher, intent = setup
    payload = protected_setup(monkeypatch, app)
    config = SimpleNamespace(token="stored-credential-in-memory", repo_url="https://github.com/MTG-Thomas/bifrost-workspace",
        branch="production-live")
    monkeypatch.setattr(jobs, "get_github_config", AsyncMock(return_value=config))
    reader = SimpleNamespace(verify_ci=AsyncMock())
    monkeypatch.setattr(jobs, "ProtectedGitReader", lambda *_args: reader)
    source = VerifiedInlineAppSource("a" * 40, "b" * 40, "c" * 40, "apps/fixture",
        InlineAppSourceSnapshot({"app.yaml": b"scope: global", "pages/index.tsx": b"captured"}),
        {"app.yaml": "100644", "pages/index.tsx": "100644"})
    source_read = AsyncMock(return_value=source)
    monkeypatch.setattr(jobs, "read_app_git_source", source_read)
    save, report = AsyncMock(), AsyncMock()
    monkeypatch.setattr(PlatformJobContext, "save_checkpoint", save)
    monkeypatch.setattr(PlatformJobContext, "report", report)

    async def captured_publish(*_args, **kwargs):
        assert kwargs["source_snapshot"] is source.snapshot
        assert kwargs["source_provenance"]["source_commit_sha"] == "a" * 40
        await kwargs["before_publication"]()
        await kwargs["checkpoint_callback"]({key: value for key, value in intent.items() if key != "controls_hash"})
        await kwargs["before_publication"]()
        return app

    publisher.side_effect = captured_publish
    result = await jobs.run_application_publish(_context(), payload)
    assert result["publication_verified"] and not result["recovered_from_intent"]
    runtime_pin = result["runtime_pin"]
    assert isinstance(runtime_pin, dict)
    assert runtime_pin["runtime_pin_hash"] == "sha256:" + "f" * 64
    jobs.read_app_publication_runtime_pin.assert_awaited_once()
    assert jobs.read_app_publication_runtime_pin.await_args.kwargs["intent"] == intent
    assert reader.verify_ci.await_count == report.await_count == 2
    save.assert_awaited_once()
    assert config.branch == "production-live"
    source_read.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["absent", "token", "repository"])
async def test_protected_git_job_fails_before_build_when_existing_credential_route_is_invalid(monkeypatch, setup, fault):
    app, db, storage, publisher, intent = setup
    payload = protected_setup(monkeypatch, app)
    config = SimpleNamespace(token="stored-in-memory", repo_url="https://github.com/MTG-Thomas/bifrost-workspace")
    if fault == "token":
        config.token = None
    elif fault == "repository":
        config.repo_url = "https://attacker.invalid/repo"
    monkeypatch.setattr(jobs, "get_github_config", AsyncMock(return_value=None if fault == "absent" else config))
    from src.jobs.platform.base import PlatformJobFailure
    with pytest.raises(PlatformJobFailure, match="credential/repository is unavailable"):
        await jobs.run_application_publish(_context(), payload)
    publisher.assert_not_awaited()
    storage.verify_publication.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["Main moved during build", "output storage unavailable"])
async def test_stopped_before_manifest_write_releases_original_intent_without_publication_replay(
    monkeypatch, setup, reason,
):
    from src.jobs.platform.base import PlatformJobFailure

    app, db, storage, publisher, original = setup
    intent = {**original, "manifest_write_started": False}
    monkeypatch.setattr(PlatformJobContext, "save_checkpoint", AsyncMock())

    async def stopped(*_args, **kwargs):
        await kwargs["checkpoint_callback"]({k: v for k, v in intent.items() if k != "controls_hash"})
        raise ValueError(reason)

    publisher.side_effect = stopped
    with pytest.raises(PlatformJobFailure) as error:
        await jobs.run_application_publish(_context(), jobs.ApplicationPublishPayload(application_id=app.id))
    assert error.value.code == "application_publication_not_applied"
    assert error.value.result["publication_verified"] is False
    assert error.value.result["original_intent"] == intent
    assert error.value.result["schema_version"] != jobs.PUBLICATION_INTENT_SCHEMA
    publisher.assert_awaited_once()
    storage.verify_publication.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaimed_pre_manifest_intent_stops_even_after_control_drift(monkeypatch, setup):
    from src.jobs.platform.base import PlatformJobFailure

    app, db, storage, publisher, original = setup
    intent = {**original, "manifest_write_started": False}
    monkeypatch.setattr(jobs, "publication_controls_hash", AsyncMock(return_value="changed controls"))
    with pytest.raises(PlatformJobFailure) as error:
        await jobs.run_application_publish(_context(intent), jobs.ApplicationPublishPayload(application_id=app.id))
    assert error.value.code == "application_publication_not_applied"
    assert error.value.result["original_intent"] == intent
    publisher.assert_not_awaited()
    storage.verify_publication.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [None, True, 0, "false"])
async def test_unknown_write_boundary_never_releases_intent(setup, marker):
    app, db, storage, publisher, original = setup
    intent = dict(original)
    if marker is not None:
        intent["manifest_write_started"] = marker
    storage.verify_publication.side_effect = ValueError("Old manifest still observed")
    with pytest.raises(PlatformJobRequiresAction) as error:
        await jobs.run_application_publish(_context(intent), jobs.ApplicationPublishPayload(application_id=app.id))
    assert error.value.result == intent
    publisher.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["scope", "model", "solution", "intent_identity"])
async def test_pre_write_disposition_cannot_release_changed_ownership(setup, fault):
    app, db, storage, publisher, original = setup
    intent = {**original, "manifest_write_started": False}
    if fault == "scope":
        app.organization_id = uuid4()
    elif fault == "model":
        app.app_model = "other"
    elif fault == "solution":
        app.solution_id = uuid4()
    else:
        intent["application_id"] = str(uuid4())
        storage.verify_publication.side_effect = ValueError("Wrong intent identity")
    with pytest.raises(PlatformJobRequiresAction) as error:
        await jobs.run_application_publish(_context(intent), jobs.ApplicationPublishPayload(application_id=app.id))
    assert error.value.result == intent
    publisher.assert_not_awaited()
    db.commit.assert_not_awaited()
