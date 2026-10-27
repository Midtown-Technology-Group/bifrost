"""An uncertain App publication resumes by readback, never effect replay."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from src.jobs.platform import application_publish as jobs
from src.jobs.platform.base import PlatformJobContext, PlatformJobRequiresAction
from src.models import User
from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.core.constants import SYSTEM_USER_UUID
from src.models.contracts.applications import ApplicationGitPublicationInput
from src.core.application_delivery_policy import InlineAppGitDeliveryPolicy
from src.services.inline_app_source import InlineAppSourceSnapshot
from src.services.solutions.github_delivery_source import VerifiedInlineAppSource
from tests.unit.services.test_application_git_source import app_policy


@pytest.fixture
def setup(monkeypatch):
    app = SimpleNamespace(id=uuid4(), organization_id=None, app_model="inline_v1", solution_id=None,
                          published_snapshot=None, published_at=None)
    actor = SimpleNamespace(id=uuid4(), organization_id=uuid4(), is_active=True,
                            is_superuser=False, is_external=False)
    db = SimpleNamespace(flush=AsyncMock(), commit=AsyncMock(), actor=actor)

    async def get(model, _entity_id, **kwargs):
        if model is User:
            assert kwargs == {"populate_existing": True}
            return db.actor
        if model is PlatformJob:
            return db.job
        assert model is Application
        return app

    db.get = AsyncMock(side_effect=get)
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
    application_repo = SimpleNamespace(get=AsyncMock(return_value=app), publish=publisher)
    db.application_repo = application_repo
    monkeypatch.setattr(jobs, "resolve_provider_org_claim", AsyncMock(return_value=True))
    monkeypatch.setattr(jobs, "ApplicationRepository", lambda *_args, **_kwargs: application_repo)
    monkeypatch.setattr(jobs, "publish_app_published", AsyncMock())
    intent = {"schema_version": "bifrost.application-publication-intent/v1", "application_id": str(app.id),
              "expected_live_etag": None, "controls_hash": "sha256:" + "a" * 64,
              "artifact_hashes": {"entry.js": "sha256:" + "b" * 64, "manifest.json": "sha256:" + "c" * 64}}
    return app, db, storage, publisher, intent


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["missing", "inactive", "bypass", "access"])
async def test_job_rechecks_current_publish_authority_before_any_effect(setup, revocation):
    from src.jobs.platform.base import PlatformJobFailure

    app, db, storage, publisher, _intent = setup
    if revocation == "missing":
        db.actor = None
    elif revocation == "inactive":
        db.actor.is_active = False
    elif revocation == "bypass":
        jobs.resolve_provider_org_claim.return_value = False
    else:
        db.application_repo.get.return_value = None

    with pytest.raises(PlatformJobFailure) as error:
        await jobs.run_application_publish(
            _context(), jobs.ApplicationPublishPayload(application_id=app.id)
        )
    assert error.value.code == "application_publish_permission_revoked"
    publisher.assert_not_awaited()
    storage.verify_publication.assert_not_awaited()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_rechecks_authority_again_at_publication_boundary(monkeypatch, setup):
    from src.jobs.platform.base import PlatformJobFailure

    app, db, storage, publisher, intent = setup
    save = AsyncMock()
    context = _context()
    monkeypatch.setattr(PlatformJobContext, "save_checkpoint", save)

    async def revoke_before_effect(*_args, **kwargs):
        await kwargs["checkpoint_callback"]({
            key: value for key, value in intent.items() if key != "controls_hash"
        })
        db.actor = SimpleNamespace(
            id=db.actor.id,
            organization_id=db.actor.organization_id,
            is_active=False,
            is_superuser=False,
            is_external=False,
        )
        await kwargs["before_publication"]()

    publisher.side_effect = revoke_before_effect
    with pytest.raises(PlatformJobFailure) as error:
        await jobs.run_application_publish(
            context, jobs.ApplicationPublishPayload(application_id=app.id)
        )
    assert error.value.code == "application_publish_permission_revoked"
    save.assert_awaited_once()
    user_gets = [call for call in db.get.await_args_list if call.args[0] is User]
    assert len(user_gets) == 2
    storage.verify_publication.assert_not_awaited()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()


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
@pytest.mark.parametrize("revocation", ["missing", "inactive", "bypass", "access"])
async def test_applied_intent_reconciles_without_current_requester_authority(setup, revocation):
    app, db, storage, publisher, intent = setup
    if revocation == "missing":
        db.actor = None
    elif revocation == "inactive":
        db.actor.is_active = False
    elif revocation == "bypass":
        jobs.resolve_provider_org_claim.return_value = False
    else:
        db.application_repo.get.return_value = None

    result = await jobs.run_application_publish(
        _context(intent), jobs.ApplicationPublishPayload(application_id=app.id)
    )

    assert result["publication_verified"] is True
    assert result["recovered_from_intent"] is True
    publisher.assert_not_awaited()
    storage.verify_publication.assert_awaited_once_with(str(app.id), intent)
    db.commit.assert_awaited_once()
    user_gets = [call for call in db.get.await_args_list if call.args[0] is User]
    assert user_gets == []
    jobs.resolve_provider_org_claim.assert_not_awaited()
    db.application_repo.get.assert_not_awaited()


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


def protected_admission(db, payload):
    from dataclasses import replace

    context = replace(_context(), requested_by_user_id=str(SYSTEM_USER_UUID))
    db.actor.id = SYSTEM_USER_UUID
    db.actor.organization_id = None
    jobs.resolve_provider_org_claim.return_value = False
    db.job = SimpleNamespace(
        job_type="application.publish", requested_by_user_id=str(SYSTEM_USER_UUID),
        organization_id=context.organization_id, resource_type="application",
        resource_id=str(payload.application_id), payload=payload.model_dump(mode="json"),
        status="running", lease_token=context.lease_token,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "manual", "requester", "missing_job", "job_type",
    "job_requester", "organization", "resource_type", "resource_id", "payload", "lease", "status",
    "missing_actor", "inactive_actor", "policy", "scope", "subpath", "unpublished", "controls",
    "missing_expiry", "expired"])
async def test_protected_authority_requires_current_scoped_admission_without_human_admin_role(monkeypatch, setup, fault):
    from dataclasses import replace
    from src.jobs.platform.base import PlatformJobFailure

    app, db, storage, publisher, _intent = setup
    payload = protected_setup(monkeypatch, app)
    context = protected_admission(db, payload)
    if fault == "manual":
        payload = jobs.ApplicationPublishPayload(application_id=app.id)
    elif fault == "requester":
        context = replace(context, requested_by_user_id=str(uuid4()))
    elif fault == "missing_job":
        db.job = None
    elif fault in {"missing_expiry", "expired"}:
        db.job.lease_expires_at = None if fault == "missing_expiry" else datetime(2000, 1, 1, tzinfo=timezone.utc)
    elif fault in {"job_type", "job_requester", "organization", "resource_type", "resource_id", "payload", "lease", "status"}:
        field = {"job_requester": "requested_by_user_id", "organization": "organization_id", "lease": "lease_token"}.get(fault, fault)
        setattr(db.job, field, {} if fault == "payload" else "changed")
    elif fault == "missing_actor":
        db.actor = None
    elif fault == "inactive_actor":
        db.actor.is_active = False
    elif fault == "policy":
        monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(inline_app_git_delivery_policy=None))
    elif fault == "scope":
        app.organization_id = uuid4()
    elif fault == "subpath":
        app.repo_path = "apps/other"
    elif fault == "unpublished":
        app.published_at = None
    elif fault == "controls":
        jobs.publication_controls_hash.return_value = "changed"

    if fault is None:
        assert await jobs._authorize_application_publish(db, context, app, payload) is db.application_repo
        jobs.resolve_provider_org_claim.assert_not_awaited()
    else:
        with pytest.raises(PlatformJobFailure, match="permission was revoked"):
            await jobs._authorize_application_publish(db, context, app, payload)
    publisher.assert_not_awaited()
    storage.verify_publication.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["actor", "lease", "scope", "controls", "policy", "missing_expiry", "expired"])
async def test_protected_publication_rechecks_admission_at_effect_boundary(monkeypatch, setup, revocation):
    from src.jobs.platform.base import PlatformJobFailure

    app, db, storage, publisher, _intent = setup
    payload = protected_setup(monkeypatch, app)
    context = protected_admission(db, payload)
    await jobs._authorize_application_publish(db, context, app, payload)
    if revocation == "actor":
        db.actor.is_active = False
    elif revocation == "lease":
        db.job.lease_token = uuid4()
    elif revocation in {"missing_expiry", "expired"}:
        db.job.lease_expires_at = None if revocation == "missing_expiry" else datetime(2000, 1, 1, tzinfo=timezone.utc)
    elif revocation == "scope":
        app.organization_id = uuid4()
    elif revocation == "controls":
        jobs.publication_controls_hash.return_value = "changed"
    else:
        monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(inline_app_git_delivery_policy=None))
    with pytest.raises(PlatformJobFailure, match="permission was revoked"):
        await jobs._authorize_application_publish(db, context, app, payload)
    publisher.assert_not_awaited()
    storage.verify_publication.assert_not_awaited()


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
@pytest.mark.parametrize("provider_member", [False, True])
async def test_protected_git_job_builds_captured_bytes_and_rechecks_source_controls_and_lease(monkeypatch, setup, provider_member):
    app, db, storage, publisher, intent = setup
    payload = protected_setup(monkeypatch, app)
    context = protected_admission(db, payload)
    jobs.resolve_provider_org_claim.return_value = provider_member
    # Production's system actor belongs to the provider org but has no role on
    # this role-based App. Human access is not protected Git admission authority.
    db.application_repo.get.return_value = None
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
    result = await jobs.run_application_publish(context, payload)
    assert result["publication_verified"] and not result["recovered_from_intent"]
    runtime_pin = result["runtime_pin"]
    assert isinstance(runtime_pin, dict)
    assert runtime_pin["runtime_pin_hash"] == "sha256:" + "f" * 64
    jobs.read_app_publication_runtime_pin.assert_awaited_once()
    assert jobs.read_app_publication_runtime_pin.await_args.kwargs["intent"] == intent
    assert reader.verify_ci.await_count == report.await_count == 2
    db.application_repo.get.assert_not_awaited()
    save.assert_awaited_once_with(intent, phase="Publication intent recorded", require_unexpired_lease=True)
    assert config.branch == "production-live"
    source_read.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["absent", "token", "repository"])
async def test_protected_git_job_fails_before_build_when_existing_credential_route_is_invalid(monkeypatch, setup, fault):
    app, db, storage, publisher, intent = setup
    payload = protected_setup(monkeypatch, app)
    context = protected_admission(db, payload)
    config = SimpleNamespace(token="stored-in-memory", repo_url="https://github.com/MTG-Thomas/bifrost-workspace")
    if fault == "token":
        config.token = None
    elif fault == "repository":
        config.repo_url = "https://attacker.invalid/repo"
    monkeypatch.setattr(jobs, "get_github_config", AsyncMock(return_value=None if fault == "absent" else config))
    from src.jobs.platform.base import PlatformJobFailure
    with pytest.raises(PlatformJobFailure, match="credential/repository is unavailable"):
        await jobs.run_application_publish(context, payload)
    publisher.assert_not_awaited()
    storage.verify_publication.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry_during", ["source", "progress"])
async def test_protected_git_guard_rechecks_expiry_after_async_verification(monkeypatch, setup, expiry_during):
    from src.jobs.platform.base import PlatformJobFailure

    app, db, storage, publisher, _intent = setup
    payload = protected_setup(monkeypatch, app)
    context = protected_admission(db, payload)
    config = SimpleNamespace(token="stored-in-memory", repo_url="https://github.com/MTG-Thomas/bifrost-workspace")
    monkeypatch.setattr(jobs, "get_github_config", AsyncMock(return_value=config))
    source = VerifiedInlineAppSource("a" * 40, "b" * 40, "c" * 40, "apps/fixture",
        InlineAppSourceSnapshot({"app.yaml": b"scope: global", "pages/index.tsx": b"captured"}),
        {"app.yaml": "100644", "pages/index.tsx": "100644"})
    monkeypatch.setattr(jobs, "read_app_git_source", AsyncMock(return_value=source))

    async def expire(*_args, **_kwargs):
        db.job.lease_expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

    reader = SimpleNamespace(verify_ci=AsyncMock(side_effect=expire if expiry_during == "source" else None))
    report = AsyncMock(side_effect=expire if expiry_during == "progress" else None)
    save = AsyncMock()
    monkeypatch.setattr(jobs, "ProtectedGitReader", lambda *_args: reader)
    monkeypatch.setattr(PlatformJobContext, "report", report)
    monkeypatch.setattr(PlatformJobContext, "save_checkpoint", save)

    async def before_effect(*_args, **kwargs):
        await kwargs["before_publication"]()
        pytest.fail("Expired protected authority must stop publication")

    publisher.side_effect = before_effect
    with pytest.raises(PlatformJobFailure, match="permission was revoked"):
        await jobs.run_application_publish(context, payload)
    reader.verify_ci.assert_awaited_once()
    report.assert_awaited_once()
    save.assert_not_awaited()
    storage.verify_publication.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest_asyncio.fixture
async def committed_job_ids(db_session):
    """Remove committed runner fixtures even when a receipt assertion fails."""
    from sqlalchemy import delete

    job_ids = []
    try:
        yield job_ids
    finally:
        await db_session.rollback()
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id.in_(job_ids)))
        await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("save_outcome", ["rejected", "uncertain"])
async def test_runner_preserves_acknowledged_intent_after_final_checkpoint_failure(
    monkeypatch, setup, db_session, committed_job_ids, save_outcome,
):
    """Run handler, storage, checkpoint SQL and terminal finalization together."""
    from sqlalchemy import update
    from src.jobs.platform import runner
    from src.services import platform_jobs as service
    from tests.unit.test_app_storage_publish import _S3Client, _bundle, _storage

    app, db, _storage_stub, publisher, _intent = setup
    payload = protected_setup(monkeypatch, app)
    context = protected_admission(db, payload)
    row = PlatformJob(id=context.job_id, job_type="application.publish", status="running",
        lease_token=context.lease_token, lease_expires_at=db.job.lease_expires_at,
        requested_by_user_id=context.requested_by_user_id,
        requested_by_email=context.requested_by_email, requested_by_name=context.requested_by_name,
        resource_type="application", resource_id=str(app.id), organization_id=None,
        dedupe_key=str(app.id), title="Checkpoint receipt fixture", payload=payload.model_dump(mode="json"))
    db_session.add(row)
    committed_job_ids.append(row.id)
    await db_session.commit()
    db.job = row
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())
    monkeypatch.setattr(PlatformJobContext, "report", AsyncMock())
    config = SimpleNamespace(token="stored-in-memory", repo_url="https://github.com/MTG-Thomas/bifrost-workspace")
    monkeypatch.setattr(jobs, "get_github_config", AsyncMock(return_value=config))
    reader = SimpleNamespace(verify_ci=AsyncMock())
    monkeypatch.setattr(jobs, "ProtectedGitReader", lambda *_args: reader)
    source = VerifiedInlineAppSource("a" * 40, "b" * 40, "c" * 40, "apps/fixture",
        InlineAppSourceSnapshot({"app.yaml": b"scope: global", "pages/index.tsx": b"captured"}),
        {"app.yaml": "100644", "pages/index.tsx": "100644"})
    monkeypatch.setattr(jobs, "read_app_git_source", AsyncMock(return_value=source))
    client = _S3Client({})
    manifest_key = f"_apps/{app.id}/live/manifest.json"
    original_manifest = b'{"entry":"old.js","outputs":["old.js"]}'
    client.objects[manifest_key] = original_manifest
    storage = _storage(client)
    monkeypatch.setattr(jobs, "AppStorageService", lambda: storage)
    persist = service.checkpoint_platform_job
    acknowledged = []

    async def checkpoint(job_id, lease_token, *, result, phase, require_unexpired_lease=False):
        if result["manifest_write_started"]:
            # Both publication guards passed before the last checkpoint starts.
            assert reader.verify_ci.await_count == 2
            if save_outcome == "rejected":
                await db_session.execute(update(PlatformJob).where(PlatformJob.id == row.id).values(
                    lease_expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
                ))
                await db_session.commit()
        accepted = await persist(job_id, lease_token, result=result, phase=phase,
            require_unexpired_lease=require_unexpired_lease)
        if result["manifest_write_started"] and save_outcome == "uncertain":
            assert accepted
            raise TimeoutError("Checkpoint committed but acknowledgement was lost")
        if accepted:
            acknowledged.append(result)
        return accepted

    monkeypatch.setattr(service, "checkpoint_platform_job", checkpoint)

    async def publish(*_args, **kwargs):
        await kwargs["before_publication"]()
        await storage.publish(str(app.id), bundle_files=_bundle(),
            checkpoint_callback=kwargs["checkpoint_callback"],
            before_manifest_switch=kwargs["before_publication"])
        pytest.fail("Failed checkpoint must stop the manifest write")

    publisher.side_effect = publish
    assert await runner.run_claimed_platform_job(row.id, context.lease_token)
    await db_session.refresh(row)
    assert len(acknowledged) == 1 and acknowledged[0]["manifest_write_started"] is False
    assert client.objects[manifest_key] == original_manifest
    assert len(client.put_calls) == 2 and all(key != manifest_key for key, _body in client.put_calls)
    assert row.lease_token is None
    if save_outcome == "rejected":
        assert row.status == "failed"
        assert row.error_code == "application_publication_not_applied"
        assert row.result["original_intent"] == acknowledged[0]
        assert row.result["publication_verified"] is False
        replacement, reused = await service.enqueue_platform_job(db_session, jobs.APPLICATION_PUBLISH_DEFINITION,
            payload.model_copy(update={"message": "New reviewed request"}), dedupe_key=str(app.id),
            organization_id=None, requested_by_user_id=SYSTEM_USER_UUID,
            requested_by_email=context.requested_by_email, requested_by_name=context.requested_by_name,
            resource_type="application", resource_id=str(app.id), title="Reviewed successor", action_url=None)
        assert not reused and replacement.id != row.id
    else:
        assert row.status == "requires_action"
        assert row.result["manifest_write_started"] is True
        assert "publication_verified" not in row.result


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


@pytest.mark.asyncio
async def test_durable_conditional_rejection_releases_without_replaying_publication(setup):
    from src.jobs.platform.base import PlatformJobFailure

    app, db, storage, publisher, original = setup
    intent = {**original, "manifest_write_started": True, "manifest_write_rejected": True}
    with pytest.raises(PlatformJobFailure) as error:
        await jobs.run_application_publish(_context(intent), jobs.ApplicationPublishPayload(application_id=app.id))
    assert error.value.code == "application_publication_not_applied"
    assert error.value.result["disposition"] == "manifest_conditional_rejected"
    assert error.value.result["original_intent"] == intent
    assert error.value.result["publication_verified"] is False
    publisher.assert_not_awaited()
    storage.verify_publication.assert_not_awaited()
    db.commit.assert_not_awaited()
