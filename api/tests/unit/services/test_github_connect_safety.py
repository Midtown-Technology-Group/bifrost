"""Requester, stale-preview and explicit-reconciliation ownership tests."""
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from src.models.contracts.github import GitConnectRequest
from src.services import github_sync as sync


def record(local, remote, *, head=None, expired=False):
    return sync._GitConnectPreviewRecord(
        token="reviewed-preview", repository_url="https://github.com/fixture/repository", branch="main",
        requested_by_user_id="actor", organization_id="org",
        expires_at=datetime.now(UTC) + timedelta(minutes=-1 if expired else 30),
        local_fingerprint=sync._connect_tree_fingerprint(local),
        remote_fingerprint=sync._connect_tree_fingerprint(remote), remote_head_sha=head,
        items=sync.classify_connect_trees(local, remote),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["valid", "missing", "invalid", "expired", "actor", "scope"])
async def test_cached_preview_never_crosses_actor_or_organization_and_expiry_is_not_reused(case):
    value = record({}, {}, expired=case == "expired")
    redis = SimpleNamespace(get=AsyncMock(return_value=value.model_dump_json()), delete=AsyncMock())
    if case == "missing":
        redis.get.return_value = None
    elif case == "invalid":
        redis.get.return_value = "not-json"
    actor = "other" if case == "actor" else "actor"
    organization = None if case == "scope" else "org"
    with patch("src.core.cache.redis_client.get_shared_redis", new=AsyncMock(return_value=redis)):
        if case == "valid":
            loaded = await sync.GitHubSyncService.load_connect_preview("reviewed-preview", requested_by_user_id=actor, organization_id=organization)
            assert loaded == value
        else:
            with pytest.raises(sync.GitConnectPreviewError):
                await sync.GitHubSyncService.load_connect_preview("reviewed-preview", requested_by_user_id=actor, organization_id=organization)
    if case == "expired":
        redis.delete.assert_awaited_once_with(sync.GitHubSyncService._connect_preview_key("reviewed-preview"))
    else:
        redis.delete.assert_not_awaited()


@pytest.mark.parametrize("branchless", ["empty", "invalid-head", "valid-other-branch", "refused"])
def test_remote_branch_error_cannot_silently_select_another_nonempty_branch(tmp_path, branchless):
    destination = tmp_path / "clone"
    destination.mkdir()
    (destination / "partial").write_text("failed clone")
    other = MagicMock()
    other.head.is_valid.return_value = branchless == "valid-other-branch"
    second = RuntimeError("empty repository") if branchless == "empty" else RuntimeError("authorization refused") if branchless == "refused" else other
    with patch.object(sync.GitRepo, "clone_from", side_effect=[RuntimeError("remote branch main not found"), second]) as clone:
        if branchless in {"empty", "invalid-head"}:
            assert sync.GitHubSyncService._clone_connect_remote(destination, "https://example.invalid/repo", "main") is None
        else:
            with pytest.raises(sync.GitConnectPreviewError):
                sync.GitHubSyncService._clone_connect_remote(destination, "https://example.invalid/repo", "main")
    assert clone.call_args_list == [
        call("https://example.invalid/repo", str(destination), branch="main"),
        call("https://example.invalid/repo", str(destination)),
    ]
    assert not (destination / "partial").exists()


def write_tree(root, files):
    root.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def hash_tree(files):
    return {name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()}


def service(root):
    @asynccontextmanager
    async def checkout():
        yield root
    value = object.__new__(sync.GitHubSyncService)
    value.repo_url = "https://example.invalid/repository"
    value.repo_manager = SimpleNamespace(checkout=checkout, checkout_readonly=checkout)
    value._run_preflight = AsyncMock(return_value=SimpleNamespace(valid=True))
    value.prepare_desktop_sync = AsyncMock(return_value="validated-plan")
    value.apply_desktop_sync = AsyncMock(return_value="verified-result")
    return value


def fake_repo():
    repo = MagicMock()
    repo.head.is_valid.return_value = True
    repo.head.commit.hexsha = "a" * 40
    repo.index.diff.return_value = [object()]
    repo.remotes = []
    return repo


@pytest.mark.asyncio
@pytest.mark.parametrize("same", [False, True])
async def test_preview_is_readonly_and_binds_both_source_snapshots_and_requester(tmp_path, same):
    local_files = {"entry.py": "local"}
    remote_files = local_files if same else {"entry.py": "remote", "remote.py": "remote-only"}
    root = tmp_path / "workspace"
    write_tree(root, local_files)
    value = service(root)
    redis = SimpleNamespace(setex=AsyncMock())
    def clone(destination, _url, _branch):
        write_tree(destination, remote_files)
        return fake_repo()
    with patch.object(value, "_clone_connect_remote", side_effect=clone), patch("src.core.cache.redis_client.get_shared_redis", new=AsyncMock(return_value=redis)):
        preview = await value.preview_connect("https://github.com/fixture/repository", "main", requested_by_user_id="actor", organization_id="org")
    assert preview.state == ("ready" if same else "requires_reconciliation")
    assert (root / "entry.py").read_text() == "local"
    assert not (root / "remote.py").exists()
    persisted = sync._GitConnectPreviewRecord.model_validate_json(redis.setex.await_args.args[2])
    assert persisted.requested_by_user_id == "actor"
    assert persisted.organization_id == "org"
    assert persisted.local_fingerprint == sync._connect_tree_fingerprint(hash_tree(local_files))
    assert persisted.remote_fingerprint == sync._connect_tree_fingerprint(hash_tree(remote_files))
    assert persisted.remote_head_sha == "a" * 40
    value.prepare_desktop_sync.assert_not_awaited()
    value.apply_desktop_sync.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["local-drift", "remote-drift", "head-drift", "publish-nonempty", "discard-unapproved", "remote-empty"])
async def test_stale_or_unapproved_connection_stops_before_replacement_and_apply(tmp_path, case):
    local_files = {"local.py": "retain"}
    remote_files = {} if case == "remote-empty" else {"remote.py": "reviewed"}
    root = tmp_path / "workspace"
    write_tree(root, local_files)
    value = service(root)
    snapshot = record(hash_tree(local_files), hash_tree(remote_files), head="a" * 40 if remote_files else None)
    if case == "local-drift":
        (root / "local.py").write_text("newer local edit")
    def clone(destination, _url, _branch):
        if not remote_files:
            return None
        files = {"remote.py": "changed"} if case == "remote-drift" else remote_files
        write_tree(destination, files)
        repo = fake_repo()
        if case == "head-drift":
            repo.head.commit.hexsha = "b" * 40
        return repo
    strategy = "publish_local" if case == "publish-nonempty" else "start_from_remote"
    request = GitConnectRequest(preview_token="reviewed-preview", strategy=strategy,
                                confirm_destructive=case == "remote-empty")
    before = sync._connect_tree_hashes(root)
    with (
        patch.object(value, "load_connect_preview", new=AsyncMock(return_value=snapshot)),
        patch.object(value, "_clone_connect_remote", side_effect=clone),
        pytest.raises((sync.GitConnectPreviewStale, sync.GitConnectDecisionError),
                      match="nonempty remote branch" if case == "remote-empty" else None),
    ):
        await value.desktop_connect(request, requested_by_user_id="actor", organization_id="org")
    assert sync._connect_tree_hashes(root) == before
    value.prepare_desktop_sync.assert_not_awaited()
    value.apply_desktop_sync.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy,remote_files,decisions,expected", [
    ("publish_local", {}, {}, {"shared.py": "local", "local.py": "local-only"}),
    ("reconcile", {}, {}, {"shared.py": "local", "local.py": "local-only"}),
    ("start_from_remote", {"shared.py": "remote", "remote.py": "remote-only"}, {}, {"shared.py": "remote", "remote.py": "remote-only"}),
    ("reconcile", {"shared.py": "remote", "remote.py": "remote-only"}, {"shared.py": "local"}, {"shared.py": "local", "local.py": "local-only", "remote.py": "remote-only"}),
    ("reconcile", {"shared.py": "remote", "remote.py": "remote-only"}, {"shared.py": "remote"}, {"shared.py": "remote", "local.py": "local-only", "remote.py": "remote-only"}),
])
async def test_only_explicit_reviewed_sources_materialize_before_normal_guarded_apply(tmp_path, strategy, remote_files, decisions, expected):
    local_files = {"shared.py": "local", "local.py": "local-only"}
    root = tmp_path / "workspace"
    write_tree(root, local_files)
    (root / ".git").mkdir()
    value = service(root)
    snapshot = record(hash_tree(local_files), hash_tree(remote_files), head="a" * 40 if remote_files else None)
    repo = fake_repo()
    def clone(destination, _url, _branch):
        if not remote_files:
            return None
        write_tree(destination, remote_files)
        return repo
    progress = AsyncMock()
    request = GitConnectRequest(preview_token="reviewed-preview", strategy=strategy, decisions=decisions, confirm_destructive=strategy == "start_from_remote")
    with patch.object(value, "load_connect_preview", new=AsyncMock(return_value=snapshot)), patch.object(value, "_clone_connect_remote", side_effect=clone), patch.object(sync, "GitRepo") as factory:
        factory.return_value = repo
        factory.init.return_value = repo
        assert await value.desktop_connect(request, requested_by_user_id="actor", organization_id="org", progress_fn=progress) == "verified-result"
    assert sync._connect_tree_hashes(root) == hash_tree(expected)
    value.prepare_desktop_sync.assert_awaited_once_with(root, repo, progress_fn=progress)
    value.apply_desktop_sync.assert_awaited_once_with(root, repo, "validated-plan", confirm_deletes=False, progress_fn=progress)
    progress.assert_awaited_once_with("Validating reconciled workspace")


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [False, True])
async def test_reviewed_commit_requires_preflight_and_clean_tree_does_not_create_new_commit(tmp_path, valid):
    value = service(tmp_path)
    repo = fake_repo()
    value._run_preflight.return_value = SimpleNamespace(valid=valid)
    if valid:
        await value._commit_connect_tree(tmp_path, repo)
        repo.index.commit.assert_called_once_with("Connect Bifrost workspace")
    else:
        with pytest.raises(sync.GitConnectDecisionError):
            await value._commit_connect_tree(tmp_path, repo)
        repo.index.commit.assert_not_called()
    repo.index.reset_mock()
    value._run_preflight.reset_mock()
    repo.index.diff.return_value = []
    repo.untracked_files = []
    await value._commit_connect_tree(tmp_path, repo)
    repo.index.commit.assert_not_called()
    value._run_preflight.assert_not_awaited()


def test_disappeared_source_and_symlink_input_cannot_be_substituted(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (destination / "entry.py").write_text("retain")
    with pytest.raises(sync.GitConnectPreviewStale):
        sync.GitHubSyncService._copy_connect_file(source, destination, "entry.py")
    assert (destination / "entry.py").read_text() == "retain"
    (source / "linked.py").symlink_to(destination / "entry.py")
    with pytest.raises(sync.GitConnectPreviewError, match="symlinks"):
        sync._connect_tree_hashes(source)
