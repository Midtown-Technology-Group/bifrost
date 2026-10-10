from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.models.contracts.applications import ApplicationUpdate
from src.routers import app_code_files, applications
from src.routers.app_code_files import FileMode
from src.services.app_bundler import BundleManifest, BundleResult, SCHEMA_VERSION


def _user(*, is_platform_admin: bool = False) -> SimpleNamespace:
    """Build a test principal with explicit identity and authorization flags."""
    return SimpleNamespace(
        is_platform_admin=is_platform_admin,
        is_provider_org=False,
        user_id=uuid4(),
        email="user@example.com",
        name="User",
    )


def _ctx(user):
    """Build a caller context with explicit organization scope and privilege flags."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None  # An unmanaged application.
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return SimpleNamespace(db=db, org_id=uuid4(), user=user)


@pytest.fixture(autouse=True)
def accessible_application(monkeypatch):
    """Stub an accessible application so mutation tests reach the separate write-authority gate."""
    monkeypatch.setattr(
        applications, "get_application_by_id_or_404",
        AsyncMock(return_value=SimpleNamespace()),
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_update_app_slug() -> None:
    """Require platform-admin authority to change an application slug."""
    with pytest.raises(HTTPException) as exc:
        await applications.update_application(
            uuid4(),
            ApplicationUpdate(slug="new-slug"),
            ctx=_ctx(_user()),
            user=_user(is_platform_admin=False),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        ApplicationUpdate(scope="global"),
        ApplicationUpdate(access_level="role_based"),
        ApplicationUpdate(role_ids=[uuid4()]),
    ],
)
async def test_non_admin_cannot_update_app_control_plane_fields(
    update: ApplicationUpdate,
) -> None:
    """Deny regular callers changes to application control-plane fields."""
    with pytest.raises(HTTPException) as exc:
        await applications.update_application(
            uuid4(),
            update,
            ctx=_ctx(_user()),
            user=_user(is_platform_admin=False),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_update_app_slug_proceeds_past_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow an admin slug update through authorization to the underlying lookup."""
    class FakeRepo:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def update_application(self, *args, **kwargs):
            return None

    monkeypatch.setattr(applications, "ApplicationRepository", FakeRepo)

    with pytest.raises(HTTPException) as exc:
        await applications.update_application(
            uuid4(),
            ApplicationUpdate(slug="new-slug"),
            ctx=_ctx(_user(is_platform_admin=True)),
            user=_user(is_platform_admin=True),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_update_non_sensitive_app_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deny regular callers metadata writes even when the fields are not sensitive."""
    class FakeRepo:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def update_application(self, *args, **kwargs):
            """Fail if a denied metadata write reaches the repository mutation method."""
            raise AssertionError("Non-admin metadata writes must be denied")

    monkeypatch.setattr(applications, "ApplicationRepository", FakeRepo)

    with pytest.raises(HTTPException) as exc:
        await applications.update_application(
            uuid4(),
            ApplicationUpdate(title="Renamed App"),
            ctx=_ctx(_user()),
            user=_user(is_platform_admin=False),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_update_browser_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require platform-admin authority to alter application browser dependencies."""
    async def fail_if_lookup_runs(*args, **kwargs):  # pragma: no cover - assertion helper
        raise AssertionError("dependency mutation should fail before app lookup")

    monkeypatch.setattr(app_code_files, "get_application_or_404", fail_if_lookup_runs)

    with pytest.raises(HTTPException) as exc:
        await app_code_files.put_dependencies(
            {"date-fns": "4.1.0"},
            uuid4(),
            ctx=_ctx(_user()),
            user=_user(is_platform_admin=False),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_cannot_write_app_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject regular-user app source writes before invoking storage."""
    async def fail_if_lookup_runs(*args, **kwargs):  # pragma: no cover - assertion helper
        raise AssertionError("code mutation should fail before app lookup")

    monkeypatch.setattr(app_code_files, "get_application_or_404", fail_if_lookup_runs)

    with pytest.raises(HTTPException) as exc:
        await app_code_files.write_app_file(
            app_code_files.AppFileUpdate(source="export default function Page() { return null }"),
            uuid4(),
            "pages/index.tsx",
            ctx=_ctx(_user()),
            user=_user(is_platform_admin=False),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_cannot_delete_app_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject regular-user app source deletion before invoking storage."""
    async def fail_if_lookup_runs(*args, **kwargs):  # pragma: no cover - assertion helper
        raise AssertionError("code deletion should fail before app lookup")

    monkeypatch.setattr(app_code_files, "get_application_or_404", fail_if_lookup_runs)

    with pytest.raises(HTTPException) as exc:
        await app_code_files.delete_app_file(
            uuid4(),
            "pages/index.tsx",
            ctx=_ctx(_user()),
            user=_user(is_platform_admin=False),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_live_stale_manifest_fails_closed_without_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = uuid4()
    app = SimpleNamespace(
        id=app_id,
        repo_prefix="apps/hardened/",
        dependencies={"date-fns": "4.1.0"},
        organization_id=None,
    )

    async def fake_get_app(*args, **kwargs):
        return app

    class FakeStorage:
        async def read_file(self, app_id_arg: str, mode: str, rel_path: str) -> bytes:
            assert app_id_arg == str(app_id)
            assert mode == "live"
            assert rel_path == "manifest.json"
            return b'{"schema_version": 0, "entry": "old.js", "css": null}'

    async def fail_if_builds(*args, **kwargs):  # pragma: no cover - assertion helper
        raise AssertionError("live viewer must not rebuild from draft source")

    monkeypatch.setattr(app_code_files, "get_application_or_404", fake_get_app)
    monkeypatch.setattr(app_code_files, "AppStorageService", FakeStorage)
    monkeypatch.setattr("src.services.app_bundler.build_with_migrate", fail_if_builds)

    with pytest.raises(HTTPException) as exc:
        await app_code_files.get_bundle_manifest(
            app_id,
            mode=FileMode.live,
            ctx=SimpleNamespace(),
            _user=_user(is_platform_admin=False),
        )

    assert exc.value.status_code == 409
    assert "Publish the application" in exc.value.detail


@pytest.mark.asyncio
async def test_preview_stale_manifest_can_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow the authorized preview path to rebuild an obsolete manifest."""
    app_id = uuid4()
    app = SimpleNamespace(
        id=app_id,
        repo_prefix="apps/hardened/",
        dependencies={"date-fns": "4.1.0"},
        organization_id=None,
    )

    async def fake_get_app(*args, **kwargs):
        return app

    class FakeStorage:
        async def read_file(self, app_id_arg: str, mode: str, rel_path: str) -> bytes:
            assert app_id_arg == str(app_id)
            assert mode == "preview"
            assert rel_path == "manifest.json"
            return b'{"schema_version": 0, "entry": "old.js", "css": null}'

    class FakeRedis:
        async def set(self, *args, **kwargs) -> bool:
            return True

        async def delete(self, *args, **kwargs) -> None:
            return None

    async def fake_get_redis() -> FakeRedis:
        return FakeRedis()

    async def fake_build_with_migrate(
        app_id_arg: str,
        repo_prefix: str,
        mode: str,
        *,
        dependencies: dict[str, str],
    ):
        assert app_id_arg == str(app_id)
        assert repo_prefix == "apps/hardened/"
        assert mode == "preview"
        assert dependencies == {"date-fns": "4.1.0"}
        return (
            BundleResult(
                success=True,
                manifest=BundleManifest(
                    entry="entry-new.js",
                    css=None,
                    outputs=["entry-new.js"],
                    duration_ms=1,
                    warnings=[],
                    dependencies=dependencies,
                ),
            ),
            False,
        )

    monkeypatch.setattr(app_code_files, "get_application_or_404", fake_get_app)
    monkeypatch.setattr(app_code_files, "AppStorageService", FakeStorage)
    monkeypatch.setattr("src.core.cache.get_shared_redis", fake_get_redis)
    monkeypatch.setattr("src.services.app_bundler.build_with_migrate", fake_build_with_migrate)

    manifest = await app_code_files.get_bundle_manifest(
        app_id,
        mode=FileMode.draft,
        ctx=_ctx(_user(is_platform_admin=True)),
        _user=_user(is_platform_admin=True),
    )

    assert manifest["entry"] == "entry-new.js"
    assert manifest["mode"] == "preview"
    assert manifest["dependencies"] == {"date-fns": "4.1.0"}
    assert SCHEMA_VERSION >= 1
