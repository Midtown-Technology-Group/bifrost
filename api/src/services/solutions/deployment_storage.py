"""Revision-addressed, create-only storage for immutable deployment content."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from src.config import Settings, get_settings

SOURCE_ARTIFACTS_ROOT = "_solution_artifacts"
SOLUTION_MANIFESTS_ROOT = "_solution_manifests"
SOLUTIONS_ROOT = "_solutions"
MAX_AUTHORED_ARCHIVE_BYTES = 12 * 1024 * 1024


class DeploymentArtifactIntegrityError(RuntimeError):
    """A finalized deployment object already exists at the requested key."""


class CreateOnlyArtifactStorage:
    """Shared create-only object writer for immutable platform artifacts."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ):
        self.settings = settings or get_settings()
        if client_factory is None:
            if self.settings.object_storage_provider == "azure_blob":
                from src.services.file_storage.azure_blob_client import (
                    AzureBlobStorageClient,
                )

                storage = AzureBlobStorageClient(self.settings)
            else:
                from src.services.file_storage.s3_client import S3StorageClient

                storage = S3StorageClient(self.settings)
            client_factory = storage.get_client
        self._client_factory = client_factory
        self._bucket = (
            self.settings.azure_blob_container
            if self.settings.object_storage_provider == "azure_blob"
            else self.settings.s3_bucket
        ) or ""

    async def _create(
        self,
        key: str,
        content: bytes,
        content_type: str,
        *,
        idempotent: bool = False,
    ) -> None:
        async with self._client_factory() as client:
            try:
                await client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=content,
                    ContentType=content_type,
                    IfNoneMatch="*",
                )
            except Exception as exc:
                if not self._is_already_exists(exc):
                    raise
                if idempotent:
                    response = await client.get_object(Bucket=self._bucket, Key=key)
                    if await response["Body"].read() == content:
                        return
                raise DeploymentArtifactIntegrityError(
                    f"Immutable artifact object already exists with different bytes: {key}"
                    if idempotent
                    else f"Finalized deployment object already exists: {key}"
                ) from exc

    async def _read(self, key: str) -> bytes:
        async with self._client_factory() as client:
            response = await client.get_object(Bucket=self._bucket, Key=key)
            return await response["Body"].read()

    @staticmethod
    def _is_already_exists(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        status = (
            response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if isinstance(response, dict)
            else None
        )
        code = (
            response.get("Error", {}).get("Code")
            if isinstance(response, dict)
            else None
        )
        return (
            status in {409, 412}
            or code in {"PreconditionFailed", "BlobAlreadyExists"}
            or type(exc).__name__ in {"PreconditionFailed", "ResourceExistsError"}
        )


def deployment_source_artifact_key(
    solution_id: UUID | str, deployment_id: UUID | str
) -> str:
    return f"{SOURCE_ARTIFACTS_ROOT}/{solution_id}/{deployment_id}/source.zip"


def deployment_manifest_key(solution_id: UUID | str, deployment_id: UUID | str) -> str:
    return f"{SOLUTION_MANIFESTS_ROOT}/{solution_id}/{deployment_id}/manifest.json"


def deployment_runtime_prefix(
    solution_id: UUID | str, deployment_id: UUID | str
) -> str:
    return f"{SOLUTIONS_ROOT}/{solution_id}/{deployment_id}/"


class SolutionDeploymentStorage(CreateOnlyArtifactStorage):
    """Writes finalized deployment objects exactly once."""

    def __init__(
        self,
        solution_id: UUID | str,
        deployment_id: UUID | str,
        settings: Settings | None = None,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ):
        self.solution_id = str(solution_id)
        self.deployment_id = str(deployment_id)
        super().__init__(settings=settings, client_factory=client_factory)

    @property
    def source_artifact_key(self) -> str:
        return deployment_source_artifact_key(self.solution_id, self.deployment_id)

    @property
    def manifest_key(self) -> str:
        return deployment_manifest_key(self.solution_id, self.deployment_id)

    @property
    def runtime_prefix(self) -> str:
        return deployment_runtime_prefix(self.solution_id, self.deployment_id)

    async def write_source_artifact(
        self, content: bytes, *, idempotent: bool = False
    ) -> str:
        await self._create(
            self.source_artifact_key, content, "application/zip", idempotent=idempotent
        )
        return self.source_artifact_key

    async def read_source_artifact(self) -> bytes:
        return await self._read(self.source_artifact_key)

    def authored_artifact_key(self, source_content_id: str) -> str:
        """Authored metadata successors may share one immutable runtime."""
        if re.fullmatch(r"sha256:[0-9a-f]{64}", source_content_id) is None:
            raise ValueError("Invalid authored source content identity")
        digest = source_content_id.removeprefix("sha256:")
        return f"{SOURCE_ARTIFACTS_ROOT}/{self.solution_id}/{self.deployment_id}/authored/{digest}.zip"

    async def write_authored_artifact(
        self, source_content_id: str, content: bytes, *, idempotent: bool = False,
    ) -> str:
        key = self.authored_artifact_key(source_content_id)
        if len(content) > MAX_AUTHORED_ARCHIVE_BYTES:
            raise DeploymentArtifactIntegrityError("Authored archive exceeds its byte bound")
        try:
            await self._create(key, content, "application/zip")
        except DeploymentArtifactIntegrityError:
            # The generic idempotent writer reads without a byte bound. Keep
            # authored retries on this bounded archive read as well.
            if not idempotent or await self.read_authored_artifact(source_content_id) != content:
                raise
        return key

    async def read_authored_artifact(self, source_content_id: str) -> bytes:
        key = self.authored_artifact_key(source_content_id)
        limit = MAX_AUTHORED_ARCHIVE_BYTES
        async with self._client_factory() as client:
            response = await client.get_object(Bucket=self._bucket, Key=key, Range=f"bytes=0-{limit}")
            body = response["Body"]
            async with body:
                content = await self._read_bounded(body, limit + 1)
            if len(content) > limit:
                raise DeploymentArtifactIntegrityError("Authored archive exceeds its byte bound")
            return content

    async def write_compiled_manifest(
        self, content: bytes, *, idempotent: bool = False
    ) -> str:
        await self._create(
            self.manifest_key, content, "application/json", idempotent=idempotent
        )
        return self.manifest_key

    async def read_compiled_manifest(self) -> bytes:
        return await self._read(self.manifest_key)

    async def write_runtime_file(
        self, path: str, content: bytes, *, idempotent: bool = False
    ) -> str:
        normalized = path.replace("\\", "/").lstrip("/")
        if not normalized or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        ):
            raise ValueError(f"Invalid deployment runtime path: {path!r}")
        key = f"{self.runtime_prefix}{normalized}"
        await self._create(
            key, content, "application/octet-stream", idempotent=idempotent
        )
        return key

    async def read_runtime_file(self, path: str, *, max_bytes: int | None = None) -> bytes:
        normalized = path.replace("\\", "/").lstrip("/")
        if not normalized or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        ):
            raise ValueError(f"Invalid deployment runtime path: {path!r}")
        key = f"{self.runtime_prefix}{normalized}"
        if max_bytes is None:
            return await self._read(key)
        if max_bytes < 0:
            raise DeploymentArtifactIntegrityError("Immutable source exceeds its total byte bound")
        async with self._client_factory() as client:
            response = await client.get_object(Bucket=self._bucket, Key=key, Range=f"bytes=0-{max_bytes}")
            body = response["Body"]
            async with body:
                content = await self._read_bounded(body, max_bytes + 1)
            if len(content) > max_bytes:
                raise DeploymentArtifactIntegrityError("Immutable source exceeds its total byte bound")
            return content

    async def read_resource(self, path: str, size_bytes: int) -> bytes:
        """Read one pinned resource with a transport bound before buffering bytes."""
        from src.core.solution_delivery_policy import delivery_path
        from src.services.solutions.deployment_manifest import (
            MAX_DEPLOYMENT_RESOURCE_BYTES,
        )

        delivery_path(path)
        if type(size_bytes) is not int or not 1 <= size_bytes <= MAX_DEPLOYMENT_RESOURCE_BYTES:
            raise ValueError("Resource size exceeds its immutable contract")
        key = f"{self.runtime_prefix}_resources/{path}"
        async with self._client_factory() as client:
            # One extra byte detects oversized objects. Azure applies the range
            # before readall; S3 streams only this range. Never fetch Root bytes.
            response = await client.get_object(Bucket=self._bucket, Key=key, Range=f"bytes=0-{size_bytes}")
            body = response["Body"]
            async with body:
                content = await self._read_bounded(body, size_bytes + 1)
                if len(content) != size_bytes:
                    raise DeploymentArtifactIntegrityError("Immutable resource size differs from its contract")
                return content

    @property
    def resources_artifact_key(self) -> str:
        return f"{SOURCE_ARTIFACTS_ROOT}/{self.solution_id}/{self.deployment_id}/resources.zip"

    async def write_resources_artifact(self, content: bytes, *, idempotent: bool = False) -> str:
        await self._create(self.resources_artifact_key, content, "application/zip", idempotent=idempotent)
        return self.resources_artifact_key

    async def read_resources_artifact(self) -> bytes:
        from src.services.solutions.deployment_manifest import (
            MAX_DEPLOYMENT_RESOURCES_BYTES,
        )

        limit = MAX_DEPLOYMENT_RESOURCES_BYTES + 2 * 1024 * 1024
        async with self._client_factory() as client:
            response = await client.get_object(Bucket=self._bucket, Key=self.resources_artifact_key,
                Range=f"bytes=0-{limit}")
            # aiobotocore enters the underlying aiohttp response, whose read()
            # is unbounded. Keep the size-aware StreamingBody for our reads.
            body = response["Body"]
            async with body:
                content = await self._read_bounded(body, limit + 1)
                if len(content) > limit:
                    raise DeploymentArtifactIntegrityError("Resource archive exceeds its byte bound")
                return content

    @staticmethod
    async def _read_bounded(body: Any, limit: int) -> bytes:
        """A transport read can return a short chunk before reaching EOF."""
        content = bytearray()
        while len(content) < limit:
            chunk = await body.read(limit - len(content))
            if not chunk:
                break
            content.extend(chunk)
        return bytes(content)
