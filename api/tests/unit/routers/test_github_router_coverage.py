from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status

from src.models import (
    CreateRepoRequest,
    DiffRequest,
    DiscardRequest,
    CommitRequest,
    GitOpRequest,
    ResolveRequest,
    SyncRequest,
    ValidateTokenRequest,
)
from src.routers import github
from src.services.github_api import GitHubAPIError


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(org_id=UUID("11111111-1111-1111-1111-111111111111"))


def _user() -> SimpleNamespace:
    return SimpleNamespace(email="admin@example.com", user_id="user-1")


def _config(
    *,
    token: str | None = "ghp_test",
    repo_url: str | None = "https://github.com/acme/repo.git",
    branch: str = "main",
) -> SimpleNamespace:
    return SimpleNamespace(
        token=token,
        repo_url=repo_url,
        branch=branch,
        last_synced_at="2026-07-05T00:00:00Z",
    )


def test_extract_repo_from_url_accepts_urls_and_owner_repo() -> None:
    assert github._extract_repo_from_url("https://github.com/acme/repo.git") == "acme/repo"
    assert github._extract_repo_from_url("acme/repo") == "acme/repo"


@pytest.mark.asyncio
async def test_get_config_endpoint_maps_missing_and_saved_config() -> None:
    with patch.object(github, "get_github_config", AsyncMock(return_value=None)):
        missing = await github.get_config_endpoint(_ctx(), _user(), AsyncMock())

    assert missing.configured is False
    assert missing.token_saved is False
    assert missing.repo_url is None

    with patch.object(github, "get_github_config", AsyncMock(return_value=_config())):
        configured = await github.get_config_endpoint(_ctx(), _user(), AsyncMock())

    assert configured.configured is True
    assert configured.token_saved is True
    assert configured.repo_url == "https://github.com/acme/repo.git"
    assert configured.branch == "main"


@pytest.mark.asyncio
async def test_get_config_endpoint_translates_unexpected_error() -> None:
    with patch.object(github, "get_github_config", AsyncMock(side_effect=RuntimeError("db down"))):
        with pytest.raises(HTTPException) as exc:
            await github.get_config_endpoint(_ctx(), _user(), AsyncMock())

    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc.value.detail == "Failed to get GitHub configuration"


@pytest.mark.asyncio
async def test_github_status_reports_unconfigured_token_only_configured_and_errors() -> None:
    with patch.object(github, "get_github_config", AsyncMock(return_value=None)):
        missing = await github.get_github_status(_ctx(), _user(), AsyncMock())

    assert missing.success is True
    assert missing.configured is False
    assert missing.initialized is False

    with patch.object(github, "get_github_config", AsyncMock(return_value=_config(repo_url=None))):
        token_only = await github.get_github_status(_ctx(), _user(), AsyncMock())

    assert token_only.success is True
    assert token_only.configured is False
    assert token_only.current_branch is None

    with patch.object(github, "get_github_config", AsyncMock(return_value=_config())):
        configured = await github.get_github_status(_ctx(), _user(), AsyncMock())

    assert configured.success is True
    assert configured.configured is True
    assert configured.initialized is True
    assert configured.current_branch == "main"
    assert configured.last_synced == "2026-07-05T00:00:00Z"

    with patch.object(github, "get_github_config", AsyncMock(side_effect=RuntimeError("boom"))):
        failed = await github.get_github_status(_ctx(), _user(), AsyncMock())

    assert failed.success is False
    assert failed.error == "boom"


@pytest.mark.asyncio
async def test_repo_status_combines_git_config_and_dirty_marker() -> None:
    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch("src.core.repo_dirty.get_repo_dirty_since", AsyncMock(return_value="2026-07-05T12:00:00Z")),
    ):
        result = await github.get_repo_status(_ctx(), _user(), AsyncMock())

    assert result.git_configured is True
    assert result.dirty is True
    assert result.dirty_since == "2026-07-05T12:00:00Z"


@pytest.mark.asyncio
async def test_validate_github_token_requires_token() -> None:
    with pytest.raises(HTTPException) as exc:
        await github.validate_github_token(
            ValidateTokenRequest(token=""),
            _ctx(),
            _user(),
            AsyncMock(),
        )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "GitHub token required"


@pytest.mark.asyncio
async def test_validate_github_token_saves_token_and_maps_repositories() -> None:
    client = SimpleNamespace(
        list_repositories=AsyncMock(
            return_value=[
                {
                    "name": "repo",
                    "full_name": "acme/repo",
                    "description": "Test repo",
                    "url": "https://github.com/acme/repo",
                    "private": True,
                }
            ]
        )
    )

    with (
        patch.object(github, "GitHubAPIClient", return_value=client),
        patch.object(github, "save_github_config", AsyncMock()) as save_config,
    ):
        result = await github.validate_github_token(
            ValidateTokenRequest(token="ghp_test"),
            _ctx(),
            _user(),
            AsyncMock(),
        )

    assert result.repositories[0].full_name == "acme/repo"
    save_config.assert_awaited_once()
    assert save_config.await_args.kwargs["repo_url"] is None
    assert save_config.await_args.kwargs["branch"] == "main"


@pytest.mark.asyncio
async def test_list_github_repos_requires_saved_token_and_maps_success() -> None:
    with patch.object(github, "get_github_config", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await github.list_github_repos(_ctx(), _user(), AsyncMock())

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    client = SimpleNamespace(
        list_repositories=AsyncMock(
            return_value=[
                {
                    "name": "repo",
                    "full_name": "acme/repo",
                    "description": None,
                    "url": "https://github.com/acme/repo",
                    "private": False,
                }
            ]
        )
    )

    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "GitHubAPIClient", return_value=client),
    ):
        result = await github.list_github_repos(_ctx(), _user(), AsyncMock())

    assert result.repositories[0].full_name == "acme/repo"
    assert result.repositories[0].private is False


@pytest.mark.asyncio
async def test_list_github_repos_translates_github_api_error() -> None:
    client = SimpleNamespace(
        list_repositories=AsyncMock(
            side_effect=GitHubAPIError("rate limited", status_code=403)
        )
    )

    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "GitHubAPIClient", return_value=client),
    ):
        with pytest.raises(HTTPException) as exc:
            await github.list_github_repos(_ctx(), _user(), AsyncMock())

    assert exc.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc.value.detail == "GitHub API error: rate limited"


@pytest.mark.asyncio
async def test_list_github_branches_validates_repo_token_and_maps_branches() -> None:
    with pytest.raises(HTTPException) as exc:
        await github.list_github_branches(_ctx(), _user(), AsyncMock(), repo="")

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "Repository name required"

    client = SimpleNamespace(
        list_branches=AsyncMock(
            return_value=[{"name": "main", "protected": True, "commit_sha": "abc123"}]
        )
    )
    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "GitHubAPIClient", return_value=client),
    ):
        result = await github.list_github_branches(
            _ctx(),
            _user(),
            AsyncMock(),
            repo="acme/repo",
        )

    client.list_branches.assert_awaited_once_with("acme/repo")
    assert result.branches[0].name == "main"
    assert result.branches[0].protected is True


@pytest.mark.asyncio
async def test_create_github_repository_maps_success_and_api_errors() -> None:
    client = SimpleNamespace(
        create_repository=AsyncMock(
            return_value={
                "full_name": "acme/new-repo",
                "url": "https://github.com/acme/new-repo",
                "clone_url": "https://github.com/acme/new-repo.git",
            }
        )
    )
    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "GitHubAPIClient", return_value=client),
    ):
        result = await github.create_github_repository(
            CreateRepoRequest(
                name="new-repo",
                description="New repo",
                private=False,
                organization="acme",
            ),
            _ctx(),
            _user(),
            AsyncMock(),
        )

    client.create_repository.assert_awaited_once_with(
        name="new-repo",
        description="New repo",
        private=False,
        organization="acme",
    )
    assert result.full_name == "acme/new-repo"

    failing_client = SimpleNamespace(
        create_repository=AsyncMock(
            side_effect=GitHubAPIError("already exists", status_code=422)
        )
    )
    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "GitHubAPIClient", return_value=failing_client),
    ):
        with pytest.raises(HTTPException) as exc:
            await github.create_github_repository(
                CreateRepoRequest(name="new-repo"),
                _ctx(),
                _user(),
                AsyncMock(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "Failed to create repository: already exists"


@pytest.mark.asyncio
async def test_disconnect_github_deletes_config() -> None:
    with patch.object(github, "delete_github_config", AsyncMock()) as delete_config:
        result = await github.disconnect_github(_ctx(), _user(), AsyncMock())

    delete_config.assert_awaited_once()
    assert result == {"success": True, "message": "GitHub integration disconnected"}


@pytest.mark.asyncio
async def test_get_commits_extracts_repo_paginates_and_truncates_message() -> None:
    commit = SimpleNamespace(
        sha="abc123",
        commit=SimpleNamespace(
            message="first line\nbody",
            author=SimpleNamespace(name="Ada", date="2026-07-05T01:02:03Z"),
        ),
    )
    client = SimpleNamespace(list_commits=AsyncMock(return_value=[commit]))

    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "GitHubAPIClient", return_value=client),
    ):
        result = await github.get_commits(
            _ctx(),
            _user(),
            AsyncMock(),
            limit=20,
            offset=40,
        )

    client.list_commits.assert_awaited_once_with(
        repo="acme/repo",
        sha="main",
        per_page=20,
        page=3,
    )
    assert result.commits[0].message == "first line"
    assert result.has_more is False


@pytest.mark.asyncio
async def test_get_commits_caps_limit_and_reports_has_more() -> None:
    commits = [
        SimpleNamespace(
            sha=f"abc{i}",
            commit=SimpleNamespace(
                message=f"message {i}",
                author=SimpleNamespace(name="Ada", date="2026-07-05T01:02:03Z"),
            ),
        )
        for i in range(100)
    ]
    client = SimpleNamespace(list_commits=AsyncMock(return_value=commits))

    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "GitHubAPIClient", return_value=client),
    ):
        result = await github.get_commits(
            _ctx(),
            _user(),
            AsyncMock(),
            limit=250,
            offset=0,
        )

    client.list_commits.assert_awaited_once_with(
        repo="acme/repo",
        sha="main",
        per_page=100,
        page=1,
    )
    assert len(result.commits) == 100
    assert result.has_more is True
    assert result.total_commits == 101


@pytest.mark.asyncio
async def test_git_fetch_requires_complete_configuration() -> None:
    with patch.object(github, "get_github_config", AsyncMock(return_value=_config(repo_url=None))):
        with pytest.raises(HTTPException) as exc:
            await github.git_fetch(_ctx(), _user(), AsyncMock(), GitOpRequest())

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "GitHub not configured"


@pytest.mark.asyncio
async def test_git_operation_endpoints_enqueue_expected_options() -> None:
    job_id = uuid4()
    accepted = object()
    cases = [
        (
            github.git_commit,
            CommitRequest(message="ship it", job_id=job_id),
            "commit",
            {"message": "ship it"},
        ),
        (
            github.git_sync,
            SyncRequest(job_id=job_id, confirm_deletes=True),
            "sync",
            {"confirm_deletes": True, "retry_plan": None},
        ),
        (github.git_abort_merge, GitOpRequest(job_id=job_id), "abort_merge", None),
        (github.git_changes, GitOpRequest(job_id=job_id), "status", None),
        (
            github.git_resolve,
            ResolveRequest(job_id=job_id, resolutions={"a.py": "ours"}),
            "resolve",
            {"resolutions": {"a.py": "ours"}},
        ),
        (github.git_diff, DiffRequest(job_id=job_id, path="a.py"), "diff", {"path": "a.py"}),
        (
            github.git_discard,
            DiscardRequest(job_id=job_id, paths=["a.py", "b.py"]),
            "discard",
            {"paths": ["a.py", "b.py"]},
        ),
    ]

    for endpoint, request, operation, options in cases:
        enqueue = AsyncMock(return_value=accepted)
        with (
            patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
            patch.object(github, "_enqueue_git_operation", enqueue),
        ):
            if endpoint in {github.git_sync, github.git_abort_merge, github.git_changes}:
                result = await endpoint(_ctx(), _user(), AsyncMock(), request)
            else:
                result = await endpoint(request, _ctx(), _user(), AsyncMock())

        assert result is accepted
        enqueue.assert_awaited_once()
        payload = enqueue.await_args.kwargs
        assert payload["operation"] == operation
        assert payload["organization_id"] == _ctx().org_id
        assert payload["job_id"] == job_id
        if options is None:
            assert "options" not in payload
        else:
            assert payload["options"] == options
