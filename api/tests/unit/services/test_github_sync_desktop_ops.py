"""Desktop git-sync operation coverage without network or real git remotes."""

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from src.services.github_sync import GitHubSyncService


class _AsyncPathContext:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def __aenter__(self) -> Path:
        return self.path

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _NestedTx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _RepoManager:
    def __init__(self, work_dir: Path, initialized: bool = True) -> None:
        self.work_dir = work_dir
        self.is_initialized = initialized
        self.synced_up = False

    def lock(self) -> _AsyncPathContext:
        return _AsyncPathContext(self.work_dir)

    def checkout(self) -> _AsyncPathContext:
        return _AsyncPathContext(self.work_dir)

    async def sync_up(self, work_dir: Path) -> None:
        assert work_dir == self.work_dir
        self.synced_up = True


class _Db:
    def __init__(self) -> None:
        self.commits = 0

    def begin_nested(self) -> _NestedTx:
        return _NestedTx()

    async def commit(self) -> None:
        self.commits += 1


class _Head:
    def __init__(self, valid: bool = True, hexsha: str = "abc123def456") -> None:
        self._valid = valid
        self.commit = type("Commit", (), {"hexsha": hexsha})()

    def is_valid(self) -> bool:
        return self._valid


def _service(tmp_path: Path, repo) -> GitHubSyncService:
    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    service.db = _Db()
    service.repo_manager = _RepoManager(tmp_path)
    service._resolver = type("Resolver", (), {})()
    service._open_or_init = lambda work_dir: repo
    return service




@pytest.mark.asyncio
async def test_desktop_status_returns_empty_status_when_repo_uninitialized(tmp_path):
    service = object.__new__(GitHubSyncService)
    service.repo_manager = _RepoManager(tmp_path, initialized=False)

    result = await service.desktop_status()

    assert result.changed_files == []
    assert result.total_changes == 0
    assert result.conflicts == []




@pytest.mark.asyncio
async def test_desktop_commit_returns_error_result_when_core_commit_raises(tmp_path):
    service = _service(tmp_path, object())
    service._do_commit = AsyncMock(side_effect=RuntimeError("preflight crashed"))

    result = await service.desktop_commit("sync")

    assert result.success is False
    assert result.error == "preflight crashed"




@pytest.mark.asyncio
async def test_do_pull_returns_success_when_remote_branch_is_absent(tmp_path):
    class Origin:
        def fetch(self, branch):
            raise RuntimeError("couldn't find remote ref main")

    class Git:
        def merge(self, ref):
            raise AssertionError("missing remote branch should not merge")

    class Repo:
        remotes = type("Remotes", (), {"origin": Origin()})()
        git = Git()

    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    service._sync_app_previews = AsyncMock()

    result = await service._do_pull(tmp_path, Repo())

    assert result.success is True
    assert result.pulled == 0
    assert result.commit_sha is None
    service._sync_app_previews.assert_not_called()












@pytest.mark.asyncio
async def test_desktop_resolve_commits_merge_and_reports_ahead_behind(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "MERGE_HEAD").write_text("merge")

    class Git:
        def __init__(self):
            self.calls = []

        def checkout(self, *args):
            self.calls.append(("checkout", args))

        def add(self, path):
            self.calls.append(("add", path))

        def commit(self, *args):
            self.calls.append(("commit", args))

        def rev_list(self, *args):
            if args[-1] == "origin/main..HEAD":
                return "3"
            if args[-1] == "HEAD..origin/main":
                return "1"
            raise AssertionError(args)

    class Index:
        def unmerged_blobs(self):
            return {"workflows/conflict.py": [(1, object()), (2, object())]}

    class Repo:
        git = Git()
        index = Index()

    service = _service(tmp_path, Repo())

    result = await service.desktop_resolve({"workflows/conflict.py": "ours"})

    assert result.success is True
    assert result.commits_ahead == 3
    assert result.commits_behind == 1
    assert Repo.git.calls == [
        ("checkout", ("--ours", "workflows/conflict.py")),
        ("add", "workflows/conflict.py"),
        ("commit", ("-m", "Merge with conflict resolution")),
    ]


@pytest.mark.asyncio
async def test_desktop_resolve_handles_stash_conflict_without_merge_head(tmp_path):
    class Git:
        def __init__(self):
            self.calls = []

        def checkout(self, *args):
            self.calls.append(("checkout", args))
            raise RuntimeError("deleted on one side")

        def rm(self, path):
            self.calls.append(("rm", path))
            raise RuntimeError("already gone")

        def add(self, path):
            self.calls.append(("add", path))

        def rev_list(self, *args):
            raise RuntimeError("no origin ref")

    class Index:
        def __init__(self):
            self.commits = []

        def unmerged_blobs(self):
            return {"workflows/conflict.py": [(2, object()), (3, object())]}

        def commit(self, message):
            self.commits.append(message)

    class Repo:
        git = Git()
        index = Index()

    service = _service(tmp_path, Repo())

    result = await service.desktop_resolve({"workflows/conflict.py": "theirs"})

    assert result.success is True
    assert result.commits_ahead == 0
    assert result.commits_behind == 0
    assert Repo.git.calls == [
        ("checkout", ("--theirs", "workflows/conflict.py")),
        ("rm", "workflows/conflict.py"),
        ("add", "workflows/conflict.py"),
    ]
    assert Repo.index.commits == ["Apply stashed changes with conflict resolution"]


@pytest.mark.asyncio
async def test_desktop_resolve_returns_error_result_when_checkout_open_fails(tmp_path):
    service = object.__new__(GitHubSyncService)
    service.repo_manager = type(
        "FailingManager",
        (),
        {"lock": lambda self: (_ for _ in ()).throw(RuntimeError("storage offline"))},
    )()

    result = await service.desktop_resolve({"workflows/conflict.py": "ours"})

    assert result.success is False
    assert result.error == "storage offline"


@pytest.mark.asyncio
async def test_desktop_abort_merge_reports_no_merge_in_progress(tmp_path):
    service = _service(tmp_path, object())

    result = await service.desktop_abort_merge()

    assert result.success is False
    assert result.error == "No merge in progress"


@pytest.mark.asyncio
async def test_desktop_abort_merge_runs_git_abort_when_merge_head_exists(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "MERGE_HEAD").write_text("merge")

    class Git:
        def __init__(self):
            self.calls = []

        def merge(self, *args):
            self.calls.append(args)

    class Repo:
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_abort_merge()

    assert result.success is True
    assert Repo.git.calls == [("--abort",)]


@pytest.mark.asyncio
async def test_desktop_diff_handles_new_file_and_missing_working_file(tmp_path):
    class Git:
        def show(self, ref):
            raise RuntimeError("missing in head")

    class Repo:
        head = _Head(valid=True)
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_diff("workflows/new.py")

    assert result.path == "workflows/new.py"
    assert result.head_content is None
    assert result.working_content is None


@pytest.mark.asyncio
async def test_desktop_diff_returns_head_and_replacement_decoded_content(tmp_path):
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "changed.py").write_bytes(b"working\xff\n")

    class Git:
        def show(self, ref):
            assert ref == "HEAD:workflows/changed.py"
            return "head\n"

    class Repo:
        head = _Head(valid=True)
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_diff("workflows/changed.py")

    assert result.head_content == "head\n"
    assert result.working_content in {"working\ufffd\n", "working\xff\n"}


@pytest.mark.asyncio
async def test_desktop_discard_restores_tracked_and_deletes_untracked_paths(
    tmp_path, monkeypatch
):
    refresh = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "src.core.module_cache",
        types.SimpleNamespace(refresh_modules_from_directory=refresh),
    )
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "new.py").write_text("new")

    class Git:
        def __init__(self):
            self.checkouts = []

        def checkout(self, *args):
            self.checkouts.append(args)
            if args[-1] in {"workflows/new.py", "workflows/missing.py"}:
                raise RuntimeError("not tracked")

    class Repo:
        head = _Head(valid=True)
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_discard(
        ["workflows/tracked.py", "workflows/new.py", "workflows/missing.py"]
    )

    assert result.success is True
    assert result.discarded == ["workflows/tracked.py", "workflows/new.py"]
    assert not (tmp_path / "workflows" / "new.py").exists()
    assert service.repo_manager.synced_up is True
    refresh.assert_awaited_once_with(tmp_path)
