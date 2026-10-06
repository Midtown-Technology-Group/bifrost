"""Captured publication keeps Git inputs separate from the editable preview."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.repositories.applications import ApplicationRepository
from src.services.app_storage import LiveManifestRevision
from src.services.inline_app_source import InlineAppSourceSnapshot


@pytest.mark.asyncio
async def test_capture_uses_prebuild_revision_retains_provenance_and_rechecks_before_switch():
    events = []
    application = SimpleNamespace(id=uuid4(), repo_prefix="apps/fixture/", dependencies={})
    repository = object.__new__(ApplicationRepository)
    repository.session = AsyncMock()
    repository.get = AsyncMock(return_value=application)
    source = InlineAppSourceSnapshot({"app.yaml": b"scope: global", "pages/index.tsx": b"original"})
    provenance = {"application_id": str(application.id), "source_commit_sha": "a" * 40}
    original_bundle = {"entry.js": b"compiled", "manifest.json": b'{"entry":"entry.js"}'}
    storage = SimpleNamespace()

    async def revision(_app_id):
        events.append("read_before_build")
        return LiveManifestRevision('"original"')

    async def build(_bundler, _app_id, _prefix, mode, **kwargs):
        events.append("compile")
        assert mode == "capture" and kwargs["source_snapshot"] is source
        return SimpleNamespace(success=True, publication_files=original_bundle)

    async def guard():
        events.append("verify_git_and_controls")

    async def publish(_app_id, **kwargs):
        events.append("publish_outputs")
        assert kwargs["expected_revision"] == LiveManifestRevision('"original"')
        assert json.loads(kwargs["bundle_files"]["manifest.json"])["git_source_evidence"] == provenance
        await kwargs["before_manifest_switch"]()
        events.append("switch")
        return 2

    storage.live_manifest_revision = AsyncMock(side_effect=revision)
    storage.publish = AsyncMock(side_effect=publish)
    mutable_preview = AsyncMock(side_effect=AssertionError("Mutable preview build must not run"))
    with patch("src.services.app_storage.AppStorageService", return_value=storage), \
         patch("src.services.app_bundler.BundlerService.build", new=build), \
         patch("src.services.app_bundler.build_with_migrate", new=mutable_preview):
        result = await repository.publish(application.id, "reviewed producer", source_snapshot=source,
            source_provenance=provenance, before_publication=guard)
    assert result is application
    assert events == ["read_before_build", "compile", "verify_git_and_controls", "publish_outputs",
        "verify_git_and_controls", "switch"]
    assert "git_source_evidence" not in json.loads(original_bundle["manifest.json"])
    mutable_preview.assert_not_awaited()


@pytest.mark.asyncio
async def test_superseded_source_after_build_never_reaches_storage_publish():
    application = SimpleNamespace(id=uuid4(), repo_prefix="apps/fixture/", dependencies={})
    repository = object.__new__(ApplicationRepository)
    repository.session = AsyncMock()
    repository.get = AsyncMock(return_value=application)
    storage = SimpleNamespace(live_manifest_revision=AsyncMock(return_value=LiveManifestRevision(None)),
        publish=AsyncMock())
    built = SimpleNamespace(success=True, publication_files={"manifest.json": b"{}"})
    with patch("src.services.app_storage.AppStorageService", return_value=storage), \
         patch("src.services.app_bundler.BundlerService.build", new=AsyncMock(return_value=built)):
        with pytest.raises(ValueError, match="Main superseded"):
            await repository.publish(application.id, "reviewed producer",
                source_snapshot=InlineAppSourceSnapshot({"app.yaml": b"{}", "pages/index.tsx": b"source"}),
                source_provenance={"source_commit_sha": "a" * 40},
                before_publication=AsyncMock(side_effect=ValueError("Main superseded")))
    storage.publish.assert_not_awaited()
    repository.session.flush.assert_not_awaited()
