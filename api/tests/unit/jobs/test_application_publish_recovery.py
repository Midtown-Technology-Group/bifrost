"""An uncertain App publication resumes by readback, never effect replay."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform import application_publish as jobs
from src.jobs.platform.base import PlatformJobContext, PlatformJobRequiresAction


@pytest.fixture
def setup(monkeypatch):
    app = SimpleNamespace(id=uuid4(), organization_id=None, app_model="inline_v1", solution_id=None,
                          published_snapshot=None, published_at=None)
    db = SimpleNamespace(get=AsyncMock(return_value=app), flush=AsyncMock(), commit=AsyncMock())
    @asynccontextmanager
    async def context():
        yield db
    storage = SimpleNamespace(verify_publication=AsyncMock(return_value=2))
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
