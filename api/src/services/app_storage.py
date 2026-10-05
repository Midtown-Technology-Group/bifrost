"""
App Storage Service — S3 operations scoped to _apps/ prefix.

Manages the app serving store:
  _apps/{app_id}/preview/   ← draft/editor files
  _apps/{app_id}/live/      ← published files for end users

Data flow:
1. Git sync/import: copy from _repo/{app_path}/ to _apps/{app_id}/preview/
2. Editor write: write to _apps/{app_id}/preview/
3. Publish: write captured build outputs → live, then its manifest
4. Serve draft: read from preview (Redis cache → S3 fallback)
5. Serve live: read from live (Redis cache → S3 fallback)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from src.config import Settings, get_settings
from src.core.log_safety import log_safe

logger = logging.getLogger(__name__)

APPS_PREFIX = "_apps/"
PUBLISH_COPY_CONCURRENCY = 16
PUBLICATION_INTENT_SCHEMA = "bifrost.application-publication-intent/v1"

AppMode = Literal["preview", "live"]


@dataclass(frozen=True)
class LiveManifestRevision:
    """Storage revision observed before a captured publication starts building."""

    etag: str | None


class AppStorageService:
    """S3 storage scoped to _apps/ prefix for app serving."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        provider = self._settings.object_storage_provider
        if provider == "azure_blob":
            from src.services.file_storage.azure_blob_client import (
                AzureBlobStorageClient,
            )

            self._storage = AzureBlobStorageClient(self._settings)
            self._bucket = self._settings.azure_blob_container or ""
        elif provider == "s3":
            from src.services.file_storage.s3_client import S3StorageClient

            self._storage = S3StorageClient(self._settings)
            self._bucket = self._settings.s3_bucket or ""
        else:
            raise ValueError(f"Unsupported object_storage_provider: {provider}")

    @asynccontextmanager
    async def _get_client(self):
        async with self._storage.get_client() as client:
            yield client

    def _key(self, app_id: str, mode: AppMode, relative_path: str = "") -> str:
        """Build S3 key: _apps/{app_id}/{mode}/{relative_path}"""
        base = f"{APPS_PREFIX}{app_id}/{mode}/"
        if relative_path:
            return f"{base}{relative_path.lstrip('/')}"
        return base

    # -----------------------------------------------------------------
    # Sync preview from repo
    # -----------------------------------------------------------------

    async def sync_preview(self, app_id: str, source_dir_in_repo: str) -> int:
        """Copy all files from _repo/{source_dir}/ to _apps/{app_id}/preview/.

        Removes stale preview files that no longer exist in the source.

        Args:
            app_id: Application UUID as string.
            source_dir_in_repo: Directory path within _repo/ (e.g. "apps/tickbox-grc/").

        Returns:
            Number of files synced.
        """
        from src.services.repo_storage import REPO_PREFIX

        source_prefix = f"{REPO_PREFIX}{source_dir_in_repo.rstrip('/')}/"
        preview_prefix = self._key(app_id, "preview")

        async with self._get_client() as client:
            # List source files in _repo/
            source_keys = await self._list_keys(client, source_prefix)

            # List existing preview files
            existing_preview_keys = await self._list_keys(client, preview_prefix)
            existing_relative = {
                k[len(preview_prefix):] for k in existing_preview_keys
            }

            synced = 0
            new_relative: set[str] = set()

            for source_key in source_keys:
                # Derive relative path within the app dir
                rel_path = source_key[len(source_prefix):]
                if not rel_path:
                    continue

                new_relative.add(rel_path)
                dest_key = f"{preview_prefix}{rel_path}"

                # Copy from source to preview
                await client.copy_object(
                    Bucket=self._bucket,
                    CopySource={"Bucket": self._bucket, "Key": source_key},
                    Key=dest_key,
                )
                synced += 1

            # Remove stale preview files
            stale = existing_relative - new_relative
            for rel_path in stale:
                await client.delete_object(
                    Bucket=self._bucket,
                    Key=f"{preview_prefix}{rel_path}",
                )

            if stale:
                logger.info(f"Removed {len(stale)} stale preview files for app {app_id}")

            logger.info(f"Synced {synced} files to preview for app {app_id}")

        await self.invalidate_render_cache(app_id)
        return synced

    async def sync_preview_compiled(
        self, app_id: str, source_dir_in_repo: str
    ) -> tuple[int, list[str]]:
        """Compile source files and write compiled JS to preview.

        Like sync_preview, but compiles .tsx/.ts files via AppCompilerService
        before writing to _apps/{app_id}/preview/.

        Args:
            app_id: Application UUID as string.
            source_dir_in_repo: Directory path within _repo/ (e.g. "apps/tickbox-grc/").

        Returns:
            Tuple of (files_synced, compile_errors).
        """
        from src.services.app_compiler import AppCompilerService
        from src.services.repo_storage import REPO_PREFIX

        source_prefix = f"{REPO_PREFIX}{source_dir_in_repo.rstrip('/')}/"
        preview_prefix = self._key(app_id, "preview")

        async with self._get_client() as client:
            # List source files in _repo/
            source_keys = await self._list_keys(client, source_prefix)

            # List existing preview files
            existing_preview_keys = await self._list_keys(client, preview_prefix)
            existing_relative = {
                k[len(preview_prefix):] for k in existing_preview_keys
            }

            # Read all source files and collect TS/TSX for compilation
            source_files: list[tuple[str, bytes]] = []  # (rel_path, content)
            for source_key in source_keys:
                rel_path = source_key[len(source_prefix):]
                if not rel_path:
                    continue
                response = await client.get_object(Bucket=self._bucket, Key=source_key)
                content = await response["Body"].read()
                source_files.append((rel_path, content))

            # Batch-compile TS/TSX files
            ts_files = [
                (rel, content)
                for rel, content in source_files
                if rel.endswith((".tsx", ".ts"))
            ]
            compiled_map: dict[str, str] = {}
            compile_errors: list[str] = []

            if ts_files:
                compiler = AppCompilerService()
                batch_input = [
                    {"path": rel, "source": content.decode("utf-8")}
                    for rel, content in ts_files
                ]
                results = await compiler.compile_batch(batch_input)

                for result in results:
                    if result.success and result.compiled:
                        compiled_map[result.path] = result.compiled
                    elif result.error:
                        compile_errors.append(f"{result.path}: {result.error}")
                        logger.warning(
                            f"Compilation failed for {result.path}: {result.error}"
                        )

            # Write to preview (compiled JS for TS/TSX, raw for others)
            synced = 0
            new_relative: set[str] = set()

            for rel_path, content in source_files:
                new_relative.add(rel_path)
                dest_key = f"{preview_prefix}{rel_path}"

                if rel_path in compiled_map:
                    write_content = compiled_map[rel_path].encode("utf-8")
                else:
                    write_content = content

                await client.put_object(
                    Bucket=self._bucket,
                    Key=dest_key,
                    Body=write_content,
                )
                synced += 1

            # Remove stale preview files
            stale = existing_relative - new_relative
            for rel_path in stale:
                await client.delete_object(
                    Bucket=self._bucket,
                    Key=f"{preview_prefix}{rel_path}",
                )

            if stale:
                logger.info(f"Removed {len(stale)} stale preview files for app {app_id}")

            logger.info(
                f"Synced {synced} compiled files to preview for app {app_id}"
                f" ({len(compile_errors)} compile errors)"
            )

        await self.invalidate_render_cache(app_id)
        return synced, compile_errors

    # -----------------------------------------------------------------
    # Single file operations
    # -----------------------------------------------------------------

    async def write_preview_file(
        self, app_id: str, relative_path: str, content: bytes
    ) -> None:
        """Write a single file to _apps/{app_id}/preview/ and bust render cache."""
        key = self._key(app_id, "preview", relative_path)
        async with self._get_client() as client:
            await client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
            )
        await self.invalidate_render_cache(app_id)

    async def delete_preview_file(self, app_id: str, relative_path: str) -> None:
        """Delete a single file from _apps/{app_id}/preview/ and bust render cache."""
        key = self._key(app_id, "preview", relative_path)
        async with self._get_client() as client:
            try:
                await client.delete_object(Bucket=self._bucket, Key=key)
            except Exception:
                pass  # Idempotent
        await self.invalidate_render_cache(app_id)

    async def read_file(
        self, app_id: str, mode: AppMode, relative_path: str
    ) -> bytes:
        """Read a single file from _apps/{app_id}/{mode}/{path}.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        key = self._key(app_id, mode, relative_path)
        async with self._get_client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=key)
                return await response["Body"].read()
            except client.exceptions.NoSuchKey:
                raise FileNotFoundError(f"App file not found: {relative_path} (mode={mode})")
            except Exception as e:
                if "NoSuchKey" in str(type(e).__name__) or "404" in str(e):
                    raise FileNotFoundError(f"App file not found: {relative_path} (mode={mode})")
                raise

    async def list_files(self, app_id: str, mode: AppMode) -> list[str]:
        """List relative file paths in _apps/{app_id}/{mode}/.

        Returns:
            List of relative paths (e.g. ["pages/index.tsx", "components/Button.tsx"]).
        """
        prefix = self._key(app_id, mode)
        async with self._get_client() as client:
            keys = await self._list_keys(client, prefix)
            return [k[len(prefix):] for k in keys if k[len(prefix):]]

    # -----------------------------------------------------------------
    # Publish: captured outputs → live
    # -----------------------------------------------------------------

    async def live_manifest_revision(self, app_id: str) -> LiveManifestRevision:
        async with self._get_client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket,
                    Key=self._key(app_id, "live", "manifest.json"))
            except client.exceptions.NoSuchKey:
                return LiveManifestRevision(None)
            await response["Body"].read()
            etag = response.get("ETag")
            if not isinstance(etag, str) or not etag:
                raise ValueError("Live publication has no storage revision evidence")
            return LiveManifestRevision(etag)

    async def publish(
        self,
        app_id: str,
        *,
        bundle_files: Mapping[str, bytes],
        progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
        checkpoint_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        expected_revision: LiveManifestRevision | None = None,
        before_manifest_switch: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """Publish the exact captured build, with its manifest written last.

        Preview is editable during publication. It is neither the source of
        these buffers nor a cleanup target for this operation.
        """
        # Snapshot the mapping before any await. Values must be immutable bytes.
        captured = dict(bundle_files)
        if not captured or any(not isinstance(value, bytes) for value in captured.values()):
            raise ValueError("Publication artifact must contain immutable byte buffers")
        manifest_bytes = captured.get("manifest.json")
        if manifest_bytes is None:
            raise ValueError("Publication artifact is missing its manifest")
        artifacts = self._bundle_artifacts(manifest_bytes)
        if artifacts != set(captured):
            raise ValueError("Publication artifact does not exactly match its manifest outputs")
        if any(not path or path.startswith("/") or "\\" in path or "\0" in path
               or any(part in {"", ".", ".."} for part in path.split("/")) for path in artifacts):
            raise ValueError("Publication artifact contains an unsafe path")
        manifest = json.loads(manifest_bytes)
        evidence = manifest.get("build_evidence")
        outputs = artifacts - {"manifest.json"}
        css = manifest.get("css")
        if (len(manifest["outputs"]) != len(outputs)
                or css is not None and (not isinstance(css, str) or css not in outputs)):
            raise ValueError("Publication manifest has duplicate or missing runtime outputs")
        expected = {path: "sha256:" + hashlib.sha256(captured[path]).hexdigest() for path in outputs}
        if (not isinstance(evidence, dict)
                or evidence.get("schema_version") != "bifrost.inline-app-build/v1"
                or evidence.get("output_hashes") != expected):
            raise ValueError("Publication artifact output hashes do not match its build evidence")
        live_prefix = self._key(app_id, "live")

        async with self._get_client() as client:
            manifest_key = f"{live_prefix}manifest.json"
            try:
                prior = await client.get_object(Bucket=self._bucket, Key=manifest_key)
            except client.exceptions.NoSuchKey:
                etag = None
            else:
                await prior["Body"].read()
                etag = prior.get("ETag")
                if not isinstance(etag, str) or not etag:
                    raise ValueError("Live publication has no storage revision evidence")
            if expected_revision is not None and etag != expected_revision.etag:
                raise ValueError("Live publication changed during the captured source build")
            intent = {"schema_version": PUBLICATION_INTENT_SCHEMA, "application_id": app_id,
                      "expected_live_etag": etag, "manifest_write_started": False, "artifact_hashes": {
                          path: "sha256:" + hashlib.sha256(data).hexdigest() for path, data in captured.items()}}
            if checkpoint_callback:
                await checkpoint_callback(intent)
            output_artifacts = sorted(outputs)
            total = len(artifacts)
            completed = 0
            if progress_callback:
                await progress_callback(completed, total)

            semaphore = asyncio.Semaphore(PUBLISH_COPY_CONCURRENCY)

            async def _write_output(rel_path: str) -> None:
                async with semaphore:
                    key = f"{live_prefix}{rel_path}"
                    try:
                        await client.put_object(Bucket=self._bucket, Key=key,
                                                Body=captured[rel_path], IfNoneMatch="*")
                    except Exception as error:
                        # Resolve an existing or uncertain create by exact byte
                        # readback. Never overwrite or retry that output.
                        try:
                            existing = await client.get_object(Bucket=self._bucket, Key=key)
                        except Exception:
                            raise error from None
                        if await existing["Body"].read() != captured[rel_path]:
                            raise ValueError("Publication output conflicts with immutable storage") from None

            tasks = [
                asyncio.create_task(_write_output(rel_path))
                for rel_path in output_artifacts
            ]
            try:
                for task in asyncio.as_completed(tasks):
                    await task
                    completed += 1
                    if progress_callback:
                        await progress_callback(completed, total)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

            # The manifest is the live bundle pointer, so publish it only after
            # every referenced output is durable.
            if before_manifest_switch is not None:
                await before_manifest_switch()
            if checkpoint_callback:
                # The job's current lease must durably cross this boundary
                # before any pointer request. A reclaimed false checkpoint
                # therefore proves that its old runner cannot issue a late PUT.
                intent = {**intent, "manifest_write_started": True}
                await checkpoint_callback(intent)
            rel_path = "manifest.json"
            await client.put_object(
                Bucket=self._bucket,
                Key=f"{live_prefix}{rel_path}",
                Body=manifest_bytes,
                **({"IfMatch": etag} if etag is not None else {"IfNoneMatch": "*"}),
            )
            completed += 1
            if progress_callback:
                await progress_callback(completed, total)

            # Already-loaded browsers may still use older hashed chunks.
            # Publication has no garbage-collection authority over those bytes.
            published = len(artifacts)
            logger.info(
                f"Published {published} files for app {log_safe(app_id)}"
            )

        await self.invalidate_render_cache(app_id)
        return published

    @staticmethod
    def _bundle_artifacts(manifest_bytes: bytes) -> set[str]:
        """Return the exact preview objects referenced by a bundle manifest."""
        try:
            manifest: Any = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Preview bundle manifest is invalid JSON") from exc

        outputs = manifest.get("outputs") if isinstance(manifest, dict) else None
        entry = manifest.get("entry") if isinstance(manifest, dict) else None
        if not isinstance(outputs, list) or not all(
            isinstance(item, str) and item for item in outputs
        ):
            raise ValueError("Preview bundle manifest has invalid outputs")
        artifacts = set(outputs)
        if not isinstance(entry, str) or entry not in artifacts:
            raise ValueError("Preview bundle manifest entry is not in outputs")
        artifacts.add("manifest.json")
        return artifacts

    async def verify_publication(self, app_id: str, intent: dict[str, Any]) -> int:
        """Read back saved intent without rebuilding or writing any artifact."""
        hashes = intent.get("artifact_hashes")
        if (intent.get("schema_version") != PUBLICATION_INTENT_SCHEMA
                or intent.get("application_id") != app_id or not isinstance(hashes, dict)
                or "manifest.json" not in hashes
                or any(not isinstance(path, str) or not path or path.startswith("/")
                       or "\\" in path or "\0" in path
                       or any(part in {"", ".", ".."} for part in path.split("/"))
                       or not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                       for path, value in hashes.items())):
            raise ValueError("Publication checkpoint is invalid")
        async with self._get_client() as client:
            prefix = self._key(app_id, "live")
            async def read(path: str) -> bytes:
                result = await client.get_object(Bucket=self._bucket, Key=prefix + path)
                content = await result["Body"].read()
                if "sha256:" + hashlib.sha256(content).hexdigest() != hashes[path]:
                    raise ValueError("Live publication differs from the saved intent")
                return content
            manifest = await read("manifest.json")
            if self._bundle_artifacts(manifest) != set(hashes):
                raise ValueError("Publication checkpoint omits runtime outputs")
            for path in sorted(set(hashes) - {"manifest.json"}):
                await read(path)
            await read("manifest.json")
        return len(hashes)

    # -----------------------------------------------------------------
    # Render cache (Redis → S3 fallback)
    # -----------------------------------------------------------------

    @staticmethod
    def _render_cache_key(app_id: str, mode: AppMode) -> str:
        return f"bifrost:app_render:{app_id}:{mode}"

    async def get_render_cache(
        self, app_id: str, mode: AppMode
    ) -> dict[str, str] | None:
        """Try to read cached render bundle from Redis.

        Returns:
            dict of {rel_path: code} or None on cache miss.
        """
        try:
            from src.core.cache import get_shared_redis

            r = await get_shared_redis()
            data = await r.get(self._render_cache_key(app_id, mode))
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Render cache read failed for app {app_id} ({mode}): {e}")
        return None

    async def set_render_cache(
        self, app_id: str, mode: AppMode, files: dict[str, str]
    ) -> None:
        """Write render bundle to Redis cache.

        Args:
            files: dict of {rel_path: code}
        """
        try:
            from src.core.cache import get_shared_redis

            r = await get_shared_redis()
            await r.set(
                self._render_cache_key(app_id, mode),
                json.dumps(files),
            )
        except Exception as e:
            logger.warning(f"Failed to set render cache for app {app_id} ({mode}): {e}")

    async def invalidate_render_cache(self, app_id: str) -> None:
        """Invalidate render cache for both draft and live modes."""
        try:
            from src.core.cache import get_shared_redis

            r = await get_shared_redis()
            await r.delete(
                self._render_cache_key(app_id, "preview"),
                self._render_cache_key(app_id, "live"),
            )
        except Exception as e:
            logger.warning(f"Failed to invalidate render cache for app {log_safe(app_id)}: {log_safe(e)}")

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    async def _list_keys(self, client, prefix: str) -> list[str]:
        """List all S3 keys under a prefix."""
        keys: list[str] = []
        continuation_token = None

        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            response = await client.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
                key = obj["Key"]
                # Skip directory markers
                if not key.endswith("/"):
                    keys.append(key)

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return keys
