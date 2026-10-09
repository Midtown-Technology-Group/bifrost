"""Write-scope enforcement for the apps MCP tools (update_app, push_files).

Mirrors the REST rule enforced by ``get_application_for_write_or_404``:
writing to ANY app — own-org included — requires scope bypass (platform
admin or provider-org member). A global app (``organization_id is None``)
is likewise bypass-only for writes, even though it is still readable by
any org member.
"""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import apps as apps_tool

_NOT_FOUND = object()


def _is_error(result) -> bool:
    """Identify a structured MCP error without treating a successful tool response as a denial."""
    return bool(result.structured_content) and "error" in result.structured_content


def _table_name(stmt):
    """Extract a statement's first table name, returning None for unsupported statement shapes."""
    try:
        return stmt.get_final_froms()[0].name
    except Exception:
        return None


def _literal_sql(stmt) -> str:
    """Render bound SQL for the fake query dispatcher, falling back to the statement text."""
    try:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))
    except Exception:
        return str(stmt)


class FakeResult:
    def __init__(self, rows=None):
        """Keep seeded rows for the fake SQL result interfaces."""
        self._rows = rows or []

    def scalars(self):
        """Expose seeded result rows through the scalar-result interface used by repositories."""
        return SimpleNamespace(all=lambda: self._rows)

    def scalar_one_or_none(self):
        """Return the first seeded scalar row, or None when the fake result is empty."""
        return self._rows[0] if self._rows else None

    def first(self):
        """Return the first seeded result row, or None for an empty query result."""
        return self._rows[0] if self._rows else None

    def all(self):
        """Return all seeded rows through the query-result interface."""
        return self._rows


class FakeSession:
    """Routes db.execute() by target table for apps write-scope tests."""

    def __init__(self, apps=None, workflow_org_by_path=None):
        """Seed application and workflow scope lookups with mocked commit and query methods."""
        self.apps = apps or []
        self.workflow_org_by_path = workflow_org_by_path or {}
        self.commit = AsyncMock()
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, stmt):
        """Dispatch application and workflow scope queries, treating file-index lookups as new content."""
        table = _table_name(stmt)
        if table == "applications":
            return FakeResult(list(self.apps))
        if table == "workflows":
            sql = _literal_sql(stmt)
            m = re.search(r"path = '([^']*)'", sql)
            path = m.group(1) if m else None
            org = self.workflow_org_by_path.get(path, _NOT_FOUND)
            return FakeResult([] if org is _NOT_FOUND else [(org,)])
        if table == "file_index":
            # content_hash-per-file check: treat every file as new.
            return FakeResult([])
        return FakeResult([])


def _ctx(*, org_id, is_platform_admin=False, is_provider_org=False, session=None):
    """Build a caller context with explicit organization scope and privilege flags."""
    return MCPContext(
        user_id=uuid4(),
        org_id=org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        session=session,
    )


def _app(*, organization_id, repo_path=None):
    """Build unmanaged application metadata with an explicit organization and optional repository path."""
    return SimpleNamespace(
        id=uuid4(),
        organization_id=organization_id,
        repo_path=repo_path,
        solution_id=None,
        deployed_at=None,
        name="App",
        slug="app",
    )


# =============================================================================
# update_app
# =============================================================================


@pytest.mark.asyncio
class TestUpdateAppWriteScope:
    async def test_non_bypass_caller_denied_for_global_app(self):
        """Deny global application access to a caller without the required privileged scope."""
        app = _app(organization_id=None)
        session = FakeSession()
        session.execute = AsyncMock(return_value=FakeResult([app]))
        ctx = _ctx(org_id=uuid4(), session=session)

        result = await apps_tool.update_app(ctx, str(app.id), name="New Name")

        assert _is_error(result)
        assert "not found" in result.structured_content["error"].lower()
        assert app.name == "App"

    async def test_non_bypass_caller_denied_for_own_org_app(self):
        """Own-org membership alone no longer grants write access."""
        org_id = uuid4()
        app = _app(organization_id=org_id)
        session = FakeSession()
        session.execute = AsyncMock(return_value=FakeResult([app]))
        ctx = _ctx(org_id=org_id, session=session)

        with patch("src.services.mcp_server.tools.apps.publish_app_draft_update", new=AsyncMock()):
            result = await apps_tool.update_app(ctx, str(app.id), name="New Name")

        assert _is_error(result)
        assert app.name == "App"

    async def test_platform_admin_allowed_for_global_app(self):
        """Allow a platform admin to mutate a globally scoped application."""
        app = _app(organization_id=None)
        session = FakeSession()
        session.execute = AsyncMock(return_value=FakeResult([app]))
        ctx = _ctx(org_id=uuid4(), is_platform_admin=True, session=session)

        with patch("src.services.mcp_server.tools.apps.publish_app_draft_update", new=AsyncMock()):
            result = await apps_tool.update_app(ctx, str(app.id), name="Admin Renamed")

        assert not _is_error(result)
        assert app.name == "Admin Renamed"

    async def test_platform_admin_allowed_for_own_org_app(self):
        """Allow a platform admin to mutate an application within their organization."""
        org_id = uuid4()
        app = _app(organization_id=org_id)
        session = FakeSession()
        session.execute = AsyncMock(return_value=FakeResult([app]))
        ctx = _ctx(org_id=org_id, is_platform_admin=True, session=session)

        with patch("src.services.mcp_server.tools.apps.publish_app_draft_update", new=AsyncMock()):
            result = await apps_tool.update_app(ctx, str(app.id), name="Admin Renamed")

        assert not _is_error(result)
        assert app.name == "Admin Renamed"


# =============================================================================
# push_files
# =============================================================================


@pytest.mark.asyncio
class TestPushFilesWriteScope:
    async def test_global_app_path_denied_for_non_bypass(self):
        """Deny source-path writes to a global application for a regular caller."""
        app = _app(organization_id=None, repo_path="apps/global-app")
        session = FakeSession(apps=[app])
        ctx = _ctx(org_id=uuid4(), session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"apps/global-app/pages/index.tsx": "content"}
            )

        assert _is_error(result)
        assert "permission" in result.structured_content["error"].lower()
        write_file.assert_not_awaited()

    async def test_own_org_app_path_denied_for_non_bypass(self):
        """Own-org membership alone no longer grants `_repo/` write access
        — writing any path is bypass-only, matching REST's admin-only
        `_repo/` editor routes."""
        org_id = uuid4()
        app = _app(organization_id=org_id, repo_path="apps/org-app")
        session = FakeSession(apps=[app])
        ctx = _ctx(org_id=org_id, session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"apps/org-app/pages/index.tsx": "content"}
            )

        assert _is_error(result)
        write_file.assert_not_awaited()

    async def test_own_org_app_path_allowed_for_bypass(self):
        """Allow an authorized source-path write within the caller's organization."""
        org_id = uuid4()
        app = _app(organization_id=org_id, repo_path="apps/org-app")
        session = FakeSession(apps=[app])
        ctx = _ctx(org_id=org_id, is_platform_admin=True, session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"apps/org-app/pages/index.tsx": "content"}
            )

        assert not _is_error(result)
        write_file.assert_awaited()

    async def test_unresolvable_path_denied_for_non_bypass(self):
        """Deny regular callers source paths whose organization cannot be resolved."""
        session = FakeSession(apps=[])
        ctx = _ctx(org_id=uuid4(), session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"some/unowned/path.txt": "content"}
            )

        assert _is_error(result)
        assert "permission" in result.structured_content["error"].lower()
        write_file.assert_not_awaited()

    async def test_mixed_batch_with_one_denied_path_rejects_whole_batch(self):
        """Reject the entire source batch when any path is unauthorized."""
        org_id = uuid4()
        app = _app(organization_id=org_id, repo_path="apps/org-app")
        session = FakeSession(apps=[app])
        ctx = _ctx(org_id=org_id, session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx,
                {
                    "apps/org-app/pages/index.tsx": "ok content",
                    "some/unowned/path.txt": "denied content",
                },
            )

        assert _is_error(result)
        assert "permission" in result.structured_content["error"].lower()
        write_file.assert_not_awaited()

    async def test_bypass_caller_allowed_for_unresolvable_path(self):
        """Allow an authorized privileged caller to write a path without a resolved organization."""
        session = FakeSession(apps=[])
        ctx = _ctx(org_id=uuid4(), is_platform_admin=True, session=session)

        write_file = AsyncMock()
        with patch(
            "src.services.file_storage.FileStorageService.write_file",
            new=write_file,
        ):
            result = await apps_tool.push_files(
                ctx, {"some/unowned/path.txt": "content"}
            )

        assert not _is_error(result)
        write_file.assert_awaited()
