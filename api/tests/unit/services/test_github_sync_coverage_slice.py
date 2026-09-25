"""Focused branch coverage for GitHub sync helpers without real remotes."""

from pathlib import Path

import pytest
from src.services.github_sync import (
    GitHubSyncService,
    SyncError,
    _deleted_paths_in_head,
)


class _Head:
    def __init__(self, valid: bool = True) -> None:
        self._valid = valid

    def is_valid(self) -> bool:
        return self._valid


def _service() -> GitHubSyncService:
    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    return service


def test_deleted_paths_in_head_returns_deleted_paths_and_handles_git_errors() -> None:
    class Git:
        def diff_tree(self, *args):
            assert args == ("--no-commit-id", "--name-status", "-r", "HEAD")
            return "\n".join(
                [
                    "D\tworkflows/old.py",
                    "M\tworkflows/kept.py",
                    "D\tapps/old/page.tsx",
                ]
            )

    class Repo:
        git = Git()

    assert _deleted_paths_in_head(Repo()) == {
        "workflows/old.py",
        "apps/old/page.tsx",
    }

    class BrokenGit:
        def diff_tree(self, *args):
            raise RuntimeError("not a git repo")

    class BrokenRepo:
        git = BrokenGit()

    assert _deleted_paths_in_head(BrokenRepo()) == set()








def test_clone_or_init_initializes_origin_when_remote_branch_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import github_sync

    created_remotes = []

    class FakeGitRepo:
        @staticmethod
        def clone_from(*args, **kwargs):
            raise RuntimeError("remote repository empty")

        @staticmethod
        def init(path):
            assert path == str(tmp_path)
            return FakeRepo()

    class FakeRepo:
        def create_remote(self, name, url):
            created_remotes.append((name, url))

    monkeypatch.setattr(github_sync, "GitRepo", FakeGitRepo)
    service = _service()
    service.repo_url = "https://example.invalid/org/repo.git"

    repo = service._clone_or_init(tmp_path)

    assert isinstance(repo, FakeRepo)
    assert created_remotes == [("origin", "https://example.invalid/org/repo.git")]


def test_clone_or_init_wraps_unexpected_clone_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import github_sync

    class FakeGitRepo:
        @staticmethod
        def clone_from(*args, **kwargs):
            raise RuntimeError("permission denied")

    monkeypatch.setattr(github_sync, "GitRepo", FakeGitRepo)
    service = _service()
    service.repo_url = "https://example.invalid/org/repo.git"

    with pytest.raises(SyncError, match="Failed to clone"):
        service._clone_or_init(tmp_path)
