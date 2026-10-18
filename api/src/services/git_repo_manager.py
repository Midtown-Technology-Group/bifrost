"""
Git Repo Manager — object-storage-backed persistent git working tree.

Manages the lifecycle of a persistent local git working directory backed by
the configured object storage provider's _repo/ prefix. S3 uses `aws s3 sync`
for efficient incremental transfer; other providers use RepoStorage.

The working directory is persistent at PERSISTENT_WORK_DIR and is NOT deleted
between operations. This allows incremental syncs (only changed files are
transferred) and preserves the .git/ directory across operations.

Individual file writes (code editor, form/agent CRUD) continue using
the Python S3 client (RepoStorage/FileIndexService). This manager is
only used for bulk sync operations (git clone/fetch/merge/push).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Iterable, Iterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import redis.asyncio as redis
from anyio import open_file

from src.config import Settings, get_settings

if TYPE_CHECKING:
    from git import Repo as GitRepo

logger = logging.getLogger(__name__)


GIT_LOCK_KEY = "bifrost:git-lock"
GIT_LOCK_TIMEOUT = 300  # 5 minutes
WORKSPACE_CHECKPOINT_PREFIX = "_workspace_sync_checkpoints"

PERSISTENT_WORK_DIR = Path("/tmp/git")
OBJECT_STORAGE_CONCURRENCY = 16
TREE_HASH_CHUNK_SIZE = 8 * 1024 * 1024
# Fingerprint of S3 _repo/ taken when the working dir last matched it. Kept
# inside .git/ so it lives and dies with the working dir it describes.
STORAGE_BASELINE_FILE = "bifrost-storage-baseline"


# user:password@ in an http(s) URL, as earlier releases wrote into .git/config.
_URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+@")


class WorkspaceStorageChanged(RuntimeError):
    """S3 _repo/ changed since the persistent working dir last matched it."""


@dataclass(frozen=True)
class TreeEntryMetadata:
    """Content-independent metadata for a workspace file."""

    path: str
    size: int
    sha256: str


def iter_repo_files(root: Path) -> Iterator[Path]:
    """Yield workspace files without descending into Git internals."""
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        yield path


def hash_file(path: Path) -> tuple[int, str]:
    """Stream one file's hash without retaining its content."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(TREE_HASH_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def iter_tree_metadata(root: Path) -> Iterator[TreeEntryMetadata]:
    """Yield metadata for each workspace file while retaining bounded memory."""
    for path in iter_repo_files(root):
        size, sha256 = hash_file(path)
        yield TreeEntryMetadata(path.relative_to(root).as_posix(), size, sha256)


def merge_in_progress(work_dir: Path, repo: GitRepo) -> bool:
    """Whether a merge or stash pop left conflicts that are not yet resolved."""
    return (work_dir / ".git" / "MERGE_HEAD").exists() or bool(repo.index.unmerged_blobs())


def _unsynced_work_warning(work_dir: Path, branch: str) -> str:
    """Describe local-only git work that Fetch, a full mirror of S3, discards."""
    from git import GitCommandError, InvalidGitRepositoryError
    from git import Repo as GitRepo

    try:
        repo = GitRepo(str(work_dir))
    except InvalidGitRepositoryError:
        return ""
    if merge_in_progress(work_dir, repo):
        return (
            " You have an unfinished merge with conflict resolutions. Fetch "
            "discards it, so you'll need to sync and resolve the conflicts again."
        )
    if not repo.head.is_valid():
        return ""
    try:
        ahead = int(repo.git.rev_list("--count", f"origin/{branch}..HEAD"))
    except GitCommandError:
        # Never fetched: no commit has been published yet.
        ahead = int(repo.git.rev_list("--count", "HEAD"))
    if ahead == 0:
        return ""
    noun = "commit" if ahead == 1 else "commits"
    pronoun = "it" if ahead == 1 else "them"
    return (
        f" You have {ahead} local {noun} not yet synced. Fetch discards "
        f"{pronoun}, so you'll need to redo {pronoun} after fetching."
    )


class GitRepoManager:
    """Sync _repo/ between object storage and a persistent local working dir."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._downloaded_hashes: dict[str, str] = {}

    @property
    def work_dir(self) -> Path:
        """Return the persistent working directory, creating it if needed."""
        PERSISTENT_WORK_DIR.mkdir(parents=True, exist_ok=True)
        return PERSISTENT_WORK_DIR

    @property
    def is_initialized(self) -> bool:
        """Check if the persistent working directory has a .git/ directory."""
        return (PERSISTENT_WORK_DIR / ".git").is_dir()

    @asynccontextmanager
    async def checkout(self) -> AsyncIterator[Path]:
        """
        Acquire a deployment-scoped Redis lock, sync _repo/ from S3 to the
        persistent working dir, yield it, then sync back and release the lock.

        The lock prevents concurrent git operations from overwriting each
        other's changes in the shared S3 _repo/ prefix.

        The working directory is NOT deleted on exit — it persists for
        incremental syncs on subsequent operations.

        Usage:
            async with repo_manager.checkout() as work_dir:
                # work_dir contains the full _repo/ contents incl .git/
                repo = GitRepo(str(work_dir))
                ...
            # On exit: changes synced back to S3, lock released
        """
        async with self._acquire_lock():
            await self.sync_down(self.work_dir)
            yield self.work_dir
            await self.sync_up(self.work_dir)

    @asynccontextmanager
    async def checkout_readonly(self) -> AsyncIterator[Path]:
        """
        Like checkout() but skips sync_up. For read-only git operations
        (diff, status) that don't modify persistent state.

        The working directory is NOT deleted on exit.
        """
        async with self._acquire_lock():
            await self.sync_down(self.work_dir)
            yield self.work_dir
            # No sync_up — caller promises not to modify persistent state

    @asynccontextmanager
    async def lock(self) -> AsyncIterator[Path]:
        """
        Acquire the Redis lock and yield the persistent working dir WITHOUT
        any S3 sync. For operations that don't need an S3 round-trip (e.g.,
        local-only git commands on an already-synced working tree).
        """
        async with self._acquire_lock():
            yield self.work_dir

    @asynccontextmanager
    async def _acquire_lock(self) -> AsyncIterator[None]:
        """Acquire a Redis lock for the duration of a git operation."""
        redis_url = self._settings.redis_url
        if not redis_url:
            # No Redis — skip locking (e.g., in tests)
            logger.debug("No Redis URL configured, skipping git lock")
            yield
            return

        client = redis.from_url(redis_url)
        lock = client.lock(
            GIT_LOCK_KEY, timeout=GIT_LOCK_TIMEOUT, blocking_timeout=GIT_LOCK_TIMEOUT
        )
        try:
            acquired = await lock.acquire()
            if not acquired:
                raise RuntimeError(
                    "Failed to acquire git lock — another git operation is in progress"
                )
            logger.debug("Acquired git lock")
            yield
        finally:
            try:
                await lock.release()
                logger.debug("Released git lock")
            except Exception:
                pass  # Lock may have expired
            await client.aclose()

    async def sync_down(self, target: Path) -> None:
        """Sync _repo/ from the configured object store to a local directory."""
        target.mkdir(parents=True, exist_ok=True)
        if self._settings.object_storage_provider == "azure_blob":
            await self._sync_down_with_repo_storage(target)
            return

        s3_uri = self._s3_uri()
        # Without --exact-timestamps the CLI skips an S3 object that kept its
        # size when the local copy is older, so a same-size edit never arrives.
        cmd = self._build_sync_cmd(
            source=s3_uri, dest=str(target), delete=True, exact_timestamps=True,
        )
        logger.info(f"sync_down: {s3_uri} -> {target}")
        self._clear_storage_baseline(target)
        # Fingerprint before transferring: a write that lands mid-transfer then
        # shows up as a change rather than being silently absorbed.
        fingerprint = await self._storage_fingerprint()
        await self._run_aws_cli(cmd)
        self._write_storage_baseline(target, fingerprint)

    async def sync_up(self, source: Path) -> None:
        """Sync a local directory back to _repo/, deleting removed objects."""
        if self._settings.object_storage_provider == "azure_blob":
            await self._sync_up_with_repo_storage(source)
            return

        s3_uri = self._s3_uri()
        cmd = self._build_sync_cmd(source=str(source), dest=s3_uri, delete=True)
        logger.info(f"sync_up: {source} -> {s3_uri}")
        self._clear_storage_baseline(source)
        await self._run_aws_cli(cmd)
        self._write_storage_baseline(source, await self._storage_fingerprint())

    async def ensure_storage_unchanged(self, work_dir: Path, branch: str) -> None:
        """Refuse to act on a working dir that predates the current S3 _repo/.

        Editor, MCP and CLI writes reach S3 only. An operation that commits the
        working dir or syncs it back to S3 with --delete would otherwise revert
        or delete them.
        """
        baseline = work_dir / ".git" / STORAGE_BASELINE_FILE
        if not baseline.is_file() or baseline.read_text() != await self._storage_fingerprint():
            raise WorkspaceStorageChanged(
                "Workspace files changed since this working copy was last fetched, "
                "so continuing could overwrite them. Nothing was changed. "
                "Run Fetch to pick up the changes, then try again."
                + _unsynced_work_warning(work_dir, branch)
            )

    async def _storage_fingerprint(self) -> str:
        """Hash every S3 _repo/ key and ETag except .bifrost/, which commit regenerates."""
        from src.services.repo_storage import RepoStorage

        listing = await RepoStorage(self._settings).list_with_metadata("")
        digest = hashlib.sha256()
        for path in sorted(listing):
            if path.startswith(".bifrost/"):
                continue
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(listing[path].etag.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _clear_storage_baseline(work_dir: Path) -> None:
        (work_dir / ".git" / STORAGE_BASELINE_FILE).unlink(missing_ok=True)

    @staticmethod
    def _write_storage_baseline(work_dir: Path, fingerprint: str) -> None:
        git_dir = work_dir / ".git"
        if git_dir.is_dir():
            (git_dir / STORAGE_BASELINE_FILE).write_text(fingerprint)

    async def _sync_down_with_repo_storage(self, target: Path) -> None:
        """Materialize Azure-backed _repo/ through the shared storage adapter."""
        from src.services.repo_storage import RepoStorage

        storage = RepoStorage(self._settings)
        paths = await storage.list()
        remote_files = {path for path in paths if not path.endswith("/")}
        for path in remote_files:
            self._safe_local_path(target, path)
        semaphore = asyncio.Semaphore(OBJECT_STORAGE_CONCURRENCY)
        downloaded_hashes: dict[str, str] = {}

        local_files = {
            path.relative_to(target).as_posix(): path
            for path in target.rglob("*")
            if path.is_file() or path.is_symlink()
        }

        async def delete_stale_local(path: Path) -> None:
            await asyncio.to_thread(path.unlink)

        async def download(path: str) -> None:
            destination = self._safe_local_path(target, path)
            async with semaphore:
                content = await storage.read(path)
            content_hash = hashlib.sha256(content).hexdigest()
            if destination.is_file():
                current = await asyncio.to_thread(destination.read_bytes)
                if hashlib.sha256(current).hexdigest() == content_hash:
                    downloaded_hashes[path] = content_hash
                    return
            await asyncio.to_thread(self._replace_local_file, destination, content)
            downloaded_hashes[path] = content_hash

        await self._run_transfers(
            delete_stale_local(path)
            for relative_path, path in local_files.items()
            if relative_path not in remote_files
        )
        await self._run_transfers(download(path) for path in remote_files)
        self._downloaded_hashes = downloaded_hashes
        logger.info(
            "sync_down: object storage -> %s (%d files)", target, len(downloaded_hashes)
        )

    async def _sync_up_with_repo_storage(self, source: Path) -> None:
        """Persist local changes through RepoStorage with aws-sync semantics."""
        from src.services.repo_storage import RepoStorage

        storage = RepoStorage(self._settings)
        semaphore = asyncio.Semaphore(OBJECT_STORAGE_CONCURRENCY)
        local_files: dict[str, Path] = {}
        for path in source.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Symlinks are not supported: {path}")
            if path.is_file():
                local_files[path.relative_to(source).as_posix()] = path
        remote_paths = set(await storage.list())

        async def upload(relative_path: str, path: Path) -> None:
            content = await asyncio.to_thread(path.read_bytes)
            content_hash = hashlib.sha256(content).hexdigest()
            if self._downloaded_hashes.get(relative_path) == content_hash:
                return
            async with semaphore:
                await storage.write(relative_path, content)

        async def delete(relative_path: str) -> None:
            async with semaphore:
                await storage.delete(relative_path)

        await self._run_transfers(
            upload(relative_path, path) for relative_path, path in local_files.items()
        )
        removed_paths = remote_paths - local_files.keys()
        await self._run_transfers(delete(path) for path in removed_paths)
        logger.info(
            "sync_up: %s -> object storage (%d local files, %d deleted)",
            source,
            len(local_files),
            len(removed_paths),
        )

    @staticmethod
    async def _run_transfers(transfers: Iterable[Awaitable[None]]) -> None:
        """Run transfers and drain cancelled siblings before returning an error."""
        tasks = [asyncio.create_task(transfer) for transfer in transfers]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    @staticmethod
    def _safe_local_path(root: Path, relative_path: str) -> Path:
        """Resolve an object key below root and reject traversal or absolute keys."""
        posix_path = PurePosixPath(relative_path)
        if posix_path.is_absolute() or ".." in posix_path.parts:
            raise ValueError(f"Unsafe repository object path: {relative_path!r}")
        destination = root.joinpath(*posix_path.parts)
        if destination.resolve(strict=False) != root.resolve().joinpath(
            *posix_path.parts
        ):
            raise ValueError(f"Unsafe repository object path: {relative_path!r}")
        return destination

    @staticmethod
    def _replace_local_file(destination: Path, content: bytes) -> None:
        """Atomically replace a local object without writing through its mode bits."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    async def checkpoint_workspace(self, source: Path) -> str:
        """Persist an exact workspace snapshot for a post-DB publication retry."""
        checkpoint_id = str(uuid4())
        if self._settings.object_storage_provider == "azure_blob":
            from src.services.file_storage.azure_blob_client import AzureBlobStorageClient

            prefix = self._checkpoint_prefix(checkpoint_id)
            async with AzureBlobStorageClient(self._settings).get_client() as storage:
                for path in source.rglob("*"):
                    if path.is_symlink():
                        raise ValueError(f"Symlinks are not supported: {path}")
                    if not path.is_file():
                        continue

                    async def chunks(file_path: Path) -> AsyncIterator[bytes]:
                        async with await open_file(file_path, "rb") as file:
                            while chunk := await file.read(TREE_HASH_CHUNK_SIZE):
                                yield chunk

                    await storage.put_object_from_chunks(
                        prefix + path.relative_to(source).as_posix(),
                        chunks(path),
                    )
            return checkpoint_id
        uri = self._checkpoint_uri(checkpoint_id)
        await self._run_aws_cli(self._build_sync_cmd(str(source), uri, delete=True))
        return checkpoint_id

    async def restore_workspace_checkpoint(self, checkpoint_id: str, target: Path) -> None:
        """Restore a checkpoint exactly, including Git objects and uncommitted files."""
        target.mkdir(parents=True, exist_ok=True)
        if self._settings.object_storage_provider == "azure_blob":
            from src.services.file_storage.azure_blob_client import AzureBlobStorageClient

            prefix = self._checkpoint_prefix(checkpoint_id)
            bucket = self._settings.azure_blob_container or ""
            async with AzureBlobStorageClient(self._settings).get_client() as storage:
                keys: list[str] = []
                token: str | None = None
                while True:
                    page = await storage.list_objects_v2(
                        Bucket=bucket, Prefix=prefix, ContinuationToken=token
                    )
                    keys.extend(entry["Key"] for entry in page.get("Contents", []))
                    token = page.get("NextContinuationToken")
                    if not token:
                        break
                paths = {
                    key: self._safe_local_path(target, key.removeprefix(prefix))
                    for key in keys
                }
                local_files = {
                    path.relative_to(target).as_posix(): path
                    for path in target.rglob("*")
                    if path.is_file() or path.is_symlink()
                }
                for relative, path in local_files.items():
                    if prefix + relative not in paths:
                        path.unlink()
                for key, destination in paths.items():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
                    try:
                        async with await open_file(temporary, "wb") as file:
                            async with aclosing(storage.iter_object_chunks(key)) as chunks:
                                async for chunk in chunks:
                                    await file.write(chunk)
                        temporary.replace(destination)
                    finally:
                        temporary.unlink(missing_ok=True)
            return
        await self._run_aws_cli(
            self._build_sync_cmd(self._checkpoint_uri(checkpoint_id), str(target), delete=True)
        )

    async def delete_workspace_checkpoint(self, checkpoint_id: str) -> None:
        """Remove a checkpoint only after successful publication."""
        if self._settings.object_storage_provider == "azure_blob":
            from src.services.file_storage.azure_blob_client import AzureBlobStorageClient

            prefix = self._checkpoint_prefix(checkpoint_id)
            bucket = self._settings.azure_blob_container or ""
            async with AzureBlobStorageClient(self._settings).get_client() as storage:
                while True:
                    page = await storage.list_objects_v2(Bucket=bucket, Prefix=prefix)
                    keys = [entry["Key"] for entry in page.get("Contents", [])]
                    if not keys:
                        break
                    for key in keys:
                        await storage.delete_object(Bucket=bucket, Key=key)
            return
        cmd = ["aws", "s3", "rm", self._checkpoint_uri(checkpoint_id), "--recursive"]
        endpoint_url = self._settings.s3_endpoint_url
        if endpoint_url:
            cmd.extend(["--endpoint-url", endpoint_url])
        cmd.append("--only-show-errors")
        await self._run_aws_cli(cmd)

    async def remove_stored_credentials(self) -> None:
        """Strip credentials from every stored copy of .git/config.

        Covers this process's working dir, S3 _repo/ and every retry
        checkpoint. Another process's working dir is rewritten the next time a
        git operation opens it there.
        """
        from aiobotocore.session import get_session

        local = PERSISTENT_WORK_DIR / ".git" / "config"
        if local.is_file():
            local.write_text(_URL_CREDENTIALS.sub(r"\1", local.read_text()))

        bucket = self._settings.s3_bucket
        async with get_session().create_client(
            "s3",
            endpoint_url=self._settings.s3_endpoint_url,
            aws_access_key_id=self._settings.s3_access_key,
            aws_secret_access_key=self._settings.s3_secret_key,
            region_name=self._settings.s3_region,
        ) as client:
            keys = ["_repo/.git/config"]
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=bucket, Prefix=f"{WORKSPACE_CHECKPOINT_PREFIX}/"
            ):
                keys.extend(
                    key for obj in page.get("Contents", [])
                    if (key := obj.get("Key", "")).endswith("/.git/config")
                )
            for key in keys:
                try:
                    response = await client.get_object(Bucket=bucket, Key=key)
                except client.exceptions.NoSuchKey:
                    continue
                config = (await response["Body"].read()).decode("utf-8")
                stripped = _URL_CREDENTIALS.sub(r"\1", config)
                if stripped != config:
                    await client.put_object(
                        Bucket=bucket, Key=key, Body=stripped.encode("utf-8")
                    )

    async def has_git_dir(self) -> bool:
        """Check if .git/HEAD exists in S3 _repo/ (quick existence check)."""
        from src.services.repo_storage import RepoStorage

        storage = RepoStorage(self._settings)
        return await storage.exists(".git/HEAD")

    def _s3_uri(self) -> str:
        """Build the S3 URI for _repo/."""
        bucket = self._settings.s3_bucket
        return f"s3://{bucket}/_repo/"

    def _checkpoint_uri(self, checkpoint_id: str) -> str:
        """Build a bounded, validated prefix for one workspace checkpoint."""
        return f"s3://{self._settings.s3_bucket}/{self._checkpoint_prefix(checkpoint_id)}"

    @staticmethod
    def _checkpoint_prefix(checkpoint_id: str) -> str:
        try:
            checkpoint = UUID(checkpoint_id)
        except ValueError as error:
            raise ValueError("Invalid workspace checkpoint ID") from error
        return f"{WORKSPACE_CHECKPOINT_PREFIX}/{checkpoint}/"

    def _build_sync_cmd(
        self,
        source: str,
        dest: str,
        delete: bool = False,
        exact_timestamps: bool = False,
    ) -> list[str]:
        """Build the aws s3 sync command with proper flags."""
        cmd = ["aws", "s3", "sync", source, dest]
        if delete:
            cmd.append("--delete")
        if exact_timestamps:
            cmd.append("--exact-timestamps")
        # For self-hosted or custom S3 endpoints
        endpoint_url = self._settings.s3_endpoint_url
        if endpoint_url:
            cmd.extend(["--endpoint-url", endpoint_url])
        # Quiet output to avoid noisy logs
        cmd.append("--only-show-errors")
        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the aws CLI process."""
        import os

        env = {**os.environ}
        if self._settings.s3_access_key:
            env["AWS_ACCESS_KEY_ID"] = self._settings.s3_access_key
        if self._settings.s3_secret_key:
            env["AWS_SECRET_ACCESS_KEY"] = self._settings.s3_secret_key
        if self._settings.s3_region:
            env["AWS_DEFAULT_REGION"] = self._settings.s3_region
        return env

    async def _run_aws_cli(self, cmd: list[str]) -> None:
        """Run an aws CLI command as a subprocess."""
        env = self._build_env()
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            cmd_str = " ".join(cmd)
            raise RuntimeError(
                f"aws s3 sync failed (exit {process.returncode}): {stderr_text}\n"
                f"Command: {cmd_str}"
            )
        if stderr:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            if stderr_text:
                logger.debug(f"aws s3 sync stderr: {stderr_text}")
