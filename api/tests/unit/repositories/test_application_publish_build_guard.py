from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository
from src.services.app_bundler import BundleMessage, BundleResult


@pytest.mark.asyncio
@pytest.mark.parametrize(("success", "expected_error"), [
    (False, "Could not resolve import"),
    (True, "did not retain its publication artifact"),
])
async def test_failed_bundle_never_promotes_preview(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    success: bool,
    expected_error: str,
) -> None:
    app = Application(
        id=uuid4(),
        name="Broken",
        slug=f"broken-{uuid4().hex[:8]}",
        repo_path=f"apps/broken-{uuid4().hex[:8]}",
        app_model="inline_v1",
    )
    db_session.add(app)
    await db_session.commit()

    async def failed_build(*_args, **_kwargs):
        return (
            BundleResult(
                success=success,
                errors=[BundleMessage(text="Could not resolve import")],
            ),
            [],
        )

    from src.services import app_bundler
    from src.services import app_storage

    promote = AsyncMock()
    monkeypatch.setattr(app_bundler, "build_with_migrate", failed_build)
    monkeypatch.setattr(app_storage.AppStorageService, "publish", promote)
    repo = ApplicationRepository(
        db_session,
        None,
        is_superuser=True,
    )

    with pytest.raises(ValueError, match=expected_error):
        await repo.publish(app.id, "dev@example.com")

    promote.assert_not_awaited()
    await db_session.refresh(app)
    assert app.published_at is None


@pytest.mark.asyncio
async def test_published_snapshot_uses_captured_build_not_later_preview(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = Application(
        id=uuid4(),
        name="Captured",
        slug=f"captured-{uuid4().hex[:8]}",
        repo_path=f"apps/captured-{uuid4().hex[:8]}",
        app_model="inline_v1",
    )
    db_session.add(app)
    await db_session.commit()
    captured = MappingProxyType({"entry-captured.js": b"entry", "manifest.json": b"manifest"})

    async def build(*_args, **_kwargs):
        return BundleResult(success=True, publication_files=captured), []

    from src.services import app_bundler, app_storage

    promote = AsyncMock(return_value=len(captured))
    preview = AsyncMock(return_value=["entry-later.js", "manifest.json"])
    monkeypatch.setattr(app_bundler, "build_with_migrate", build)
    monkeypatch.setattr(app_storage.AppStorageService, "publish", promote)
    monkeypatch.setattr(app_storage.AppStorageService, "list_files", preview)
    repo = ApplicationRepository(db_session, None, is_superuser=True)

    published = await repo.publish(app.id, "dev@example.com")

    assert published is not None
    assert published.published_snapshot == {path: "" for path in captured}
    assert published.published_at is not None
    assert promote.await_args.kwargs["bundle_files"] is captured
    preview.assert_not_awaited()
