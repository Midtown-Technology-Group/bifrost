"""Object storage contract for the Blob-first managed-exit spike.

This is intentionally adapter-shaped rather than S3-shaped. It captures the
smallest operation set the platform currently relies on so Garage/S3 and a
future Azure Blob adapter can be compared with the same scenario matrix.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from typing import Protocol
from urllib.parse import ParseResult, urlparse
from uuid import uuid4

import aiobotocore.session
import httpx
import pytest
import pytest_asyncio
from botocore.exceptions import ClientError

from src.services.file_storage.azure_blob_client import canonical_etag, wire_etag


@dataclass(frozen=True)
class StoredObject:
    body: bytes
    content_type: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectStat:
    size: int
    content_type: str | None
    metadata: dict[str, str]
    etag: str


class _PreconditionFailed(Exception):
    """Reference-fake conditional write rejection. Production keeps provider errors."""


class ObjectStorageContract(Protocol):
    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        raise NotImplementedError

    def iter_chunks(self, key: str, *, chunk_size: int) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def get(
        self, key: str, *, byte_range: tuple[int, int] | None = None
    ) -> bytes:
        raise NotImplementedError

    async def stat(self, key: str) -> ObjectStat:
        raise NotImplementedError

    async def copy(self, source_key: str, dest_key: str) -> None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def list(
        self,
        prefix: str = "",
        *,
        delimiter: str | None = None,
        page_size: int | None = None,
    ) -> tuple[list[str], list[str]]:
        raise NotImplementedError

    async def signed_get_url(self, key: str, *, expires_in: int = 600) -> str:
        raise NotImplementedError

    async def signed_put_url(
        self,
        key: str,
        *,
        content_type: str,
        expires_in: int = 600,
    ) -> str:
        raise NotImplementedError

    def signed_put_headers(self, *, content_type: str) -> dict[str, str]:
        raise NotImplementedError


class InMemoryObjectStorage:
    """Reference fake for the contract; real adapters should match this behavior."""

    def __init__(self) -> None:
        self._objects: dict[str, StoredObject] = {}
        self._version = 0
        self._etags: dict[str, str] = {}

    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        if if_none_match not in (None, "*") or (
            if_match is not None and if_none_match is not None
        ):
            raise ValueError("Unsupported or conflicting object write preconditions")
        current = self._etags.get(key)
        if if_none_match == "*" and current is not None:
            raise _PreconditionFailed(key)
        if if_match is not None and current != canonical_etag(if_match):
            raise _PreconditionFailed(key)
        self._version += 1
        self._objects[key] = StoredObject(
            body=body,
            content_type=content_type,
            metadata=dict(metadata or {}),
        )
        self._etags[key] = f"fake-etag-{self._version}"

    async def iter_chunks(self, key: str, *, chunk_size: int) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        body = self._objects[key].body
        for start in range(0, len(body), chunk_size):
            yield body[start : start + chunk_size]

    async def get(
        self, key: str, *, byte_range: tuple[int, int] | None = None
    ) -> bytes:
        obj = self._objects[key]
        if byte_range is None:
            return obj.body
        start, end = byte_range
        return obj.body[start : end + 1]

    async def stat(self, key: str) -> ObjectStat:
        obj = self._objects[key]
        return ObjectStat(
            size=len(obj.body),
            content_type=obj.content_type,
            metadata=dict(obj.metadata),
            etag=self._etags[key],
        )

    async def copy(self, source_key: str, dest_key: str) -> None:
        source = self._objects[source_key]
        await self.put(
            dest_key,
            source.body,
            content_type=source.content_type,
            metadata=source.metadata,
        )

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)
        self._etags.pop(key, None)

    async def list(
        self,
        prefix: str = "",
        *,
        delimiter: str | None = None,
        page_size: int | None = None,
    ) -> tuple[list[str], list[str]]:
        keys = sorted(k for k in self._objects if k.startswith(prefix))
        if page_size is not None and page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        # page_size is the service page size. The contract result includes every page.
        if delimiter is None:
            return keys, []

        files: list[str] = []
        folders: set[str] = set()
        for key in keys:
            suffix = key[len(prefix) :]
            if delimiter in suffix:
                folders.add(f"{prefix}{suffix.split(delimiter, 1)[0]}{delimiter}")
            else:
                files.append(key)
        return files, sorted(folders)

    async def signed_get_url(self, key: str, *, expires_in: int = 600) -> str:
        return f"https://storage-contract.local/get/{key}?expires={expires_in}"

    async def signed_put_url(
        self,
        key: str,
        *,
        content_type: str,
        expires_in: int = 600,
    ) -> str:
        return (
            f"https://storage-contract.local/put/{key}"
            f"?content_type={content_type}&expires={expires_in}"
        )

    def signed_put_headers(self, *, content_type: str) -> dict[str, str]:
        return {"Content-Type": content_type}


class S3ObjectStorage:
    """S3/Garage adapter for running the contract against a real bucket."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region_name: str = "auto",
        public_endpoint_url: str | None = None,
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._region_name = region_name
        self._public_endpoint_url = public_endpoint_url
        self._prefix = f"storage-contract/{uuid4().hex}/"
        self._session = aiobotocore.session.get_session()

    def _object_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def _logical_key(self, object_key: str) -> str:
        return object_key.removeprefix(self._prefix)

    def _client(self) -> Any:
        return self._session.create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            region_name=self._region_name,
        )

    def _rewrite_public_url(self, signed_url: str) -> str:
        if not self._public_endpoint_url:
            return signed_url
        parsed_signed = urlparse(signed_url)
        parsed_public = urlparse(self._public_endpoint_url)
        return parsed_signed._replace(
            scheme=parsed_public.scheme,
            netloc=parsed_public.netloc,
        ).geturl()

    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        if if_none_match not in (None, "*") or (
            if_match is not None and if_none_match is not None
        ):
            raise ValueError("Unsupported or conflicting object write preconditions")
        put_args: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": self._object_key(key),
            "Body": body,
            "Metadata": metadata or {},
        }
        if content_type:
            put_args["ContentType"] = content_type
        if if_match is not None:
            put_args["IfMatch"] = wire_etag(if_match)
        if if_none_match is not None:
            put_args["IfNoneMatch"] = "*"

        async with self._client() as client:
            await client.put_object(**put_args)

    async def iter_chunks(self, key: str, *, chunk_size: int) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        async with self._client() as client:
            response = await client.get_object(
                Bucket=self._bucket, Key=self._object_key(key)
            )
            body = response["Body"]
            async with body:
                while chunk := await body.read(chunk_size):
                    yield chunk

    async def get(
        self, key: str, *, byte_range: tuple[int, int] | None = None
    ) -> bytes:
        get_args: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": self._object_key(key),
        }
        if byte_range is not None:
            start, end = byte_range
            get_args["Range"] = f"bytes={start}-{end}"

        async with self._client() as client:
            response = await client.get_object(**get_args)
            async with response["Body"] as stream:
                return await stream.read()

    async def stat(self, key: str) -> ObjectStat:
        try:
            async with self._client() as client:
                response = await client.head_object(
                    Bucket=self._bucket,
                    Key=self._object_key(key),
                )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                raise KeyError(key) from exc
            raise

        return ObjectStat(
            size=response["ContentLength"],
            content_type=response.get("ContentType"),
            metadata=dict(response.get("Metadata") or {}),
            etag=canonical_etag(response.get("ETag")) or "",
        )

    async def copy(self, source_key: str, dest_key: str) -> None:
        async with self._client() as client:
            await client.copy_object(
                Bucket=self._bucket,
                Key=self._object_key(dest_key),
                CopySource={
                    "Bucket": self._bucket,
                    "Key": self._object_key(source_key),
                },
            )

    async def delete(self, key: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=self._bucket, Key=self._object_key(key))

    async def list(
        self,
        prefix: str = "",
        *,
        delimiter: str | None = None,
        page_size: int | None = None,
    ) -> tuple[list[str], list[str]]:
        files: list[str] = []
        folders: list[str] = []
        continuation_token = None
        async with self._client() as client:
            while True:
                list_args: dict[str, Any] = {
                    "Bucket": self._bucket,
                    "Prefix": self._object_key(prefix),
                }
                if delimiter:
                    list_args["Delimiter"] = delimiter
                if page_size:
                    list_args["MaxKeys"] = page_size
                if continuation_token:
                    list_args["ContinuationToken"] = continuation_token
                response = await client.list_objects_v2(**list_args)
                files.extend(
                    self._logical_key(item["Key"])
                    for item in response.get("Contents", [])
                    if item["Key"] != self._object_key(prefix)
                )
                folders.extend(
                    self._logical_key(item["Prefix"])
                    for item in response.get("CommonPrefixes", [])
                )
                if not response.get("IsTruncated"):
                    break
                next_token = response.get("NextContinuationToken")
                if not next_token or next_token == continuation_token:
                    break
                continuation_token = next_token
        return sorted(set(files)), sorted(set(folders))

    async def signed_get_url(self, key: str, *, expires_in: int = 600) -> str:
        async with self._client() as client:
            url = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": self._object_key(key)},
                ExpiresIn=expires_in,
            )
        return self._rewrite_public_url(url)

    async def signed_put_url(
        self,
        key: str,
        *,
        content_type: str,
        expires_in: int = 600,
    ) -> str:
        async with self._client() as client:
            url = await client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": self._object_key(key),
                    "ContentType": content_type,
                },
                ExpiresIn=expires_in,
            )
        return self._rewrite_public_url(url)

    def signed_put_headers(self, *, content_type: str) -> dict[str, str]:
        return {"Content-Type": content_type}

    async def cleanup(self) -> None:
        async with self._client() as client:
            response = await client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=self._prefix,
            )
            objects = [{"Key": item["Key"]} for item in response.get("Contents", [])]
            if objects:
                await client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )


class AzureBlobObjectStorage:
    """Azure Blob adapter candidate for the managed object-storage exit."""

    def __init__(
        self,
        *,
        account_url: str,
        container: str,
        account_name: str,
        credential: Any,
        account_key: str | None = None,
        use_user_delegation: bool = False,
    ) -> None:
        from azure.storage.blob.aio import BlobServiceClient

        self._container_name = container
        self._account_name = account_name
        self._account_key = account_key
        self._use_user_delegation = use_user_delegation
        self._credential = credential
        self._prefix = f"storage-contract/{uuid4().hex}/"
        self._service = BlobServiceClient(account_url, credential=credential)
        self._container = self._service.get_container_client(container)

    def _object_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def _logical_key(self, object_key: str) -> str:
        return object_key.removeprefix(self._prefix)

    def _blob(self, key: str) -> Any:
        return self._container.get_blob_client(self._object_key(key))

    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        from azure.core import MatchConditions
        from azure.storage.blob import ContentSettings

        if if_none_match not in (None, "*") or (
            if_match is not None and if_none_match is not None
        ):
            raise ValueError("Unsupported or conflicting object write preconditions")
        conditions: dict[str, Any] = {}
        if if_match is not None:
            conditions = {
                "etag": wire_etag(if_match),
                "match_condition": MatchConditions.IfNotModified,
            }
        await self._blob(key).upload_blob(
            body,
            overwrite=if_none_match != "*",
            content_settings=ContentSettings(content_type=content_type),
            metadata=metadata or {},
            **conditions,
        )

    async def iter_chunks(self, key: str, *, chunk_size: int) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        stream = await self._blob(key).download_blob()
        buffer = bytearray()
        async for piece in stream.chunks():
            if not piece:
                continue
            buffer.extend(piece)
            while len(buffer) >= chunk_size:
                yield bytes(buffer[:chunk_size])
                del buffer[:chunk_size]
        if buffer:
            yield bytes(buffer)

    async def get(
        self, key: str, *, byte_range: tuple[int, int] | None = None
    ) -> bytes:
        blob = self._blob(key)
        if byte_range is None:
            stream = await blob.download_blob()
        else:
            start, end = byte_range
            stream = await blob.download_blob(offset=start, length=end - start + 1)
        return await stream.readall()

    async def stat(self, key: str) -> ObjectStat:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            properties = await self._blob(key).get_blob_properties()
        except ResourceNotFoundError as exc:
            raise KeyError(key) from exc

        return ObjectStat(
            size=properties.size,
            content_type=properties.content_settings.content_type,
            metadata=dict(properties.metadata or {}),
            etag=canonical_etag(properties.etag) or "",
        )

    async def copy(self, source_key: str, dest_key: str) -> None:
        dest_blob = self._blob(dest_key)
        copy_result = await dest_blob.start_copy_from_url(
            await self.signed_get_url(source_key)
        )
        if copy_result.get("copy_status") == "success":
            return

        for _ in range(30):
            properties = await dest_blob.get_blob_properties()
            copy_props = properties.copy
            status = getattr(copy_props, "status", None)
            if status == "success":
                return
            if status in {"failed", "aborted"}:
                raise RuntimeError(f"Azure Blob copy {status}: {copy_props}")
            await asyncio.sleep(0.5)

        raise TimeoutError(f"Azure Blob copy did not complete for {dest_key!r}")

    async def delete(self, key: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            await self._blob(key).delete_blob()
        except ResourceNotFoundError:
            return

    async def list(
        self,
        prefix: str = "",
        *,
        delimiter: str | None = None,
        page_size: int | None = None,
    ) -> tuple[list[str], list[str]]:
        files: list[str] = []
        folders: list[str] = []
        continuation_token = None
        while True:
            if delimiter:
                pager = self._container.walk_blobs(
                    name_starts_with=self._object_key(prefix),
                    delimiter=delimiter,
                    results_per_page=page_size,
                ).by_page(continuation_token=continuation_token)
            else:
                pager = self._container.list_blobs(
                    name_starts_with=self._object_key(prefix),
                    results_per_page=page_size,
                ).by_page(continuation_token=continuation_token)
            try:
                page = await anext(pager)
            except StopAsyncIteration:
                break
            async for item in page:
                prefix_name = getattr(item, "prefix", None)
                if prefix_name:
                    folders.append(self._logical_key(prefix_name))
                else:
                    files.append(self._logical_key(item.name))
            next_token = getattr(pager, "continuation_token", None)
            if not next_token or next_token == continuation_token:
                break
            continuation_token = next_token

        return sorted(set(files)), sorted(set(folders))

    async def _generate_blob_sas(
        self,
        key: str,
        *,
        permissions: Any,
        expires_in: int,
        content_type: str | None = None,
    ) -> str:
        from azure.storage.blob import generate_blob_sas

        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        sas_args = {
            "account_name": self._account_name,
            "container_name": self._container_name,
            "blob_name": self._object_key(key),
            "permission": permissions,
            "expiry": expires_at,
        }
        if content_type is not None:
            sas_args["content_type"] = content_type

        if self._use_user_delegation:
            user_delegation_key = await self._service.get_user_delegation_key(
                key_start_time=datetime.now(UTC) - timedelta(minutes=5),
                key_expiry_time=expires_at,
            )
            sas_args["user_delegation_key"] = user_delegation_key
        elif self._account_key:
            sas_args["account_key"] = self._account_key
        else:
            raise RuntimeError(
                "Azure Blob SAS generation requires account_key or user delegation"
            )

        return generate_blob_sas(**sas_args)

    async def signed_get_url(self, key: str, *, expires_in: int = 600) -> str:
        from azure.storage.blob import BlobSasPermissions

        sas = await self._generate_blob_sas(
            key,
            permissions=BlobSasPermissions(read=True),
            expires_in=expires_in,
        )
        return f"{self._blob(key).url}?{sas}"

    async def signed_put_url(
        self,
        key: str,
        *,
        content_type: str,
        expires_in: int = 600,
    ) -> str:
        from azure.storage.blob import BlobSasPermissions

        sas = await self._generate_blob_sas(
            key,
            permissions=BlobSasPermissions(write=True, create=True),
            expires_in=expires_in,
            content_type=content_type,
        )
        return f"{self._blob(key).url}?{sas}"

    def signed_put_headers(self, *, content_type: str) -> dict[str, str]:
        return {
            "Content-Type": content_type,
            "x-ms-blob-type": "BlockBlob",
        }

    async def cleanup(self) -> None:
        blobs = []
        async for blob in self._container.list_blobs(name_starts_with=self._prefix):
            blobs.append(blob.name)
        for blob_name in blobs:
            await self._container.delete_blob(blob_name)
        await self._service.close()
        close_credential = getattr(self._credential, "close", None)
        if close_credential is not None:
            await close_credential()


@dataclass(frozen=True)
class StorageBackend:
    name: str
    create: Callable[[], ObjectStorageContract]


def _s3_backend_from_env() -> StorageBackend | None:
    bucket = os.getenv("BIFROST_STORAGE_CONTRACT_S3_BUCKET")
    endpoint_url = os.getenv("BIFROST_STORAGE_CONTRACT_S3_ENDPOINT_URL")
    access_key_id = os.getenv("BIFROST_STORAGE_CONTRACT_S3_ACCESS_KEY_ID")
    secret_access_key = os.getenv("BIFROST_STORAGE_CONTRACT_S3_SECRET_ACCESS_KEY")
    if not all((bucket, endpoint_url, access_key_id, secret_access_key)):
        return None

    return StorageBackend(
        name="s3",
        create=lambda: S3ObjectStorage(
            bucket=bucket or "",
            endpoint_url=endpoint_url or "",
            access_key_id=access_key_id or "",
            secret_access_key=secret_access_key or "",
            region_name=os.getenv("BIFROST_STORAGE_CONTRACT_S3_REGION", "auto"),
            public_endpoint_url=os.getenv(
                "BIFROST_STORAGE_CONTRACT_S3_PUBLIC_ENDPOINT_URL"
            ),
        ),
    )


def _azure_blob_backend_from_env() -> StorageBackend | None:
    account_url = os.getenv("BIFROST_STORAGE_CONTRACT_AZURE_BLOB_ACCOUNT_URL")
    container = os.getenv("BIFROST_STORAGE_CONTRACT_AZURE_BLOB_CONTAINER")
    credential = os.getenv("BIFROST_STORAGE_CONTRACT_AZURE_BLOB_CREDENTIAL")
    account_name = os.getenv("BIFROST_STORAGE_CONTRACT_AZURE_BLOB_ACCOUNT_NAME")
    account_key = os.getenv("BIFROST_STORAGE_CONTRACT_AZURE_BLOB_ACCOUNT_KEY")
    use_user_delegation = (
        os.getenv("BIFROST_STORAGE_CONTRACT_AZURE_BLOB_USER_DELEGATION") == "1"
    )
    if not all((account_url, container, account_name)):
        return None
    if not use_user_delegation and not any((credential, account_key)):
        return None

    if find_spec("azure.storage.blob") is None:
        return StorageBackend(
            name="azure-blob",
            create=lambda: pytest.skip(
                "azure-storage-blob is required for Azure Blob contract tests"
            ),
        )

    def create_azure_blob_storage() -> ObjectStorageContract:
        if use_user_delegation:
            if find_spec("azure.identity.aio") is None:
                pytest.skip(
                    "azure-identity is required for Azure Blob user delegation tests"
                )
            from azure.identity.aio import DefaultAzureCredential

            azure_credential: Any = DefaultAzureCredential()
        else:
            azure_credential = credential or account_key or ""

        return AzureBlobObjectStorage(
            account_url=account_url or "",
            container=container or "",
            credential=azure_credential,
            account_name=account_name or "",
            account_key=account_key,
            use_user_delegation=use_user_delegation,
        )

    return StorageBackend(
        name="azure-blob-user-delegation" if use_user_delegation else "azure-blob",
        create=create_azure_blob_storage,
    )


def _storage_backends() -> list[StorageBackend]:
    backends = [StorageBackend(name="memory", create=InMemoryObjectStorage)]
    s3_backend = _s3_backend_from_env()
    if s3_backend is not None:
        backends.append(s3_backend)
    azure_blob_backend = _azure_blob_backend_from_env()
    if azure_blob_backend is not None:
        backends.append(azure_blob_backend)
    return backends


@pytest_asyncio.fixture(params=_storage_backends(), ids=lambda backend: backend.name)
async def object_storage(
    request: pytest.FixtureRequest,
) -> AsyncIterator[ObjectStorageContract]:
    storage = request.param.create()
    try:
        yield storage
    finally:
        cleanup = getattr(storage, "cleanup", None)
        if cleanup is not None:
            await cleanup()


def _assert_signed_url(url: str, key: str) -> ParseResult:
    parsed = urlparse(url)
    assert parsed.scheme in {"http", "https"}
    assert parsed.netloc
    assert key in parsed.path
    return parsed


def _is_reference_fake(object_storage: ObjectStorageContract) -> bool:
    return isinstance(object_storage, InMemoryObjectStorage)


def _precondition_failed(exc: Exception) -> bool:
    if isinstance(exc, _PreconditionFailed):
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = response.get("Error", {}).get("Code")
    else:
        status = getattr(exc, "status_code", None)
        code = getattr(exc, "error_code", None)
    return status in {409, 412} and code in {
        "PreconditionFailed",
        "ConditionalRequestConflict",
        "ConditionNotMet",
        "BlobAlreadyExists",
    }


@pytest.mark.asyncio
async def test_round_trips_small_text_binary_prefixes_and_metadata(
    object_storage: ObjectStorageContract,
) -> None:
    await object_storage.put(
        "uploads/form-1/readme.txt",
        b"hello blob-first storage",
        content_type="text/plain",
        metadata={"source": "contract", "durable": "true"},
    )
    await object_storage.put("_repo/workflows/contract.py", b"def run(): return 1\n")
    await object_storage.put("uploads/form-1/blob.bin", bytes(range(256)))

    assert (
        await object_storage.get("uploads/form-1/readme.txt")
        == b"hello blob-first storage"
    )
    assert (
        await object_storage.get("_repo/workflows/contract.py")
        == b"def run(): return 1\n"
    )
    assert await object_storage.get("uploads/form-1/blob.bin") == bytes(range(256))

    stat = await object_storage.stat("uploads/form-1/readme.txt")
    assert stat.size == len(b"hello blob-first storage")
    assert stat.content_type == "text/plain"
    assert stat.metadata == {"source": "contract", "durable": "true"}
    assert stat.etag
    assert stat.etag == canonical_etag(stat.etag)


@pytest.mark.asyncio
async def test_iter_chunks_keeps_each_read_bounded(
    object_storage: ObjectStorageContract,
) -> None:
    body = b"abcdefghijklmnopqrstuvwxyz"
    await object_storage.put("uploads/form-1/chunks.bin", body)
    chunks: list[bytes] = []
    async for chunk in object_storage.iter_chunks(
        "uploads/form-1/chunks.bin", chunk_size=4
    ):
        assert 0 < len(chunk) <= 4
        chunks.append(chunk)
    assert b"".join(chunks) == body
    assert len(chunks) > 1


@pytest.mark.asyncio
async def test_overwrite_delete_head_and_range_read(
    object_storage: ObjectStorageContract,
) -> None:
    await object_storage.put("uploads/form-1/report.csv", b"first")
    first_stat = await object_storage.stat("uploads/form-1/report.csv")

    await object_storage.put(
        "uploads/form-1/report.csv", b"0123456789", content_type="text/csv"
    )
    second_stat = await object_storage.stat("uploads/form-1/report.csv")

    assert (
        await object_storage.get("uploads/form-1/report.csv", byte_range=(2, 5))
        == b"2345"
    )
    assert second_stat.size == 10
    assert second_stat.content_type == "text/csv"
    assert second_stat.etag != first_stat.etag
    assert second_stat.etag == canonical_etag(second_stat.etag)

    await object_storage.put(
        "uploads/form-1/report.csv",
        b"replaced",
        if_match=second_stat.etag,
    )
    with pytest.raises(Exception) as stale:
        await object_storage.put(
            "uploads/form-1/report.csv",
            b"rejected",
            if_match=first_stat.etag,
        )
    assert _precondition_failed(stale.value)
    assert await object_storage.get("uploads/form-1/report.csv") == b"replaced"

    await object_storage.delete("uploads/form-1/report.csv")
    with pytest.raises(KeyError):
        await object_storage.stat("uploads/form-1/report.csv")


@pytest.mark.asyncio
async def test_list_pagination_and_delimiter_semantics(
    object_storage: ObjectStorageContract,
) -> None:
    for key in (
        "_repo/apps/app-1/index.tsx",
        "_repo/apps/app-1/components/card.tsx",
        "_repo/apps/app-2/index.tsx",
        "uploads/form-1/a.txt",
        "uploads/form-1/nested/b.txt",
    ):
        await object_storage.put(key, b"fixture")

    files, _folders = await object_storage.list("_repo/apps/", page_size=2)
    assert files == [
        "_repo/apps/app-1/components/card.tsx",
        "_repo/apps/app-1/index.tsx",
        "_repo/apps/app-2/index.tsx",
    ]

    files, folders = await object_storage.list("uploads/form-1/", delimiter="/")
    assert files == ["uploads/form-1/a.txt"]
    assert folders == ["uploads/form-1/nested/"]


@pytest.mark.asyncio
async def test_copy_and_signed_url_capabilities(
    object_storage: ObjectStorageContract,
) -> None:
    await object_storage.put(
        "_apps/app-1/preview/index.js",
        b"export default 'preview';",
        content_type="application/javascript",
    )

    await object_storage.copy(
        "_apps/app-1/preview/index.js", "_apps/app-1/live/index.js"
    )

    assert (
        await object_storage.get("_apps/app-1/live/index.js")
        == b"export default 'preview';"
    )
    live_stat = await object_storage.stat("_apps/app-1/live/index.js")
    assert live_stat.content_type == "application/javascript"

    get_url = await object_storage.signed_get_url(
        "uploads/form-1/a.txt", expires_in=300
    )
    put_url = await object_storage.signed_put_url(
        "uploads/form-1/a.txt",
        content_type="text/plain",
        expires_in=300,
    )

    _assert_signed_url(get_url, "uploads/form-1/a.txt")
    parsed_put_url = _assert_signed_url(put_url, "uploads/form-1/a.txt")
    assert "text/plain" in parsed_put_url.query or "content-type" in (
        parsed_put_url.query.lower()
    )


@pytest.mark.asyncio
async def test_signed_url_http_round_trip(
    object_storage: ObjectStorageContract,
) -> None:
    if _is_reference_fake(object_storage):
        pytest.skip("reference fake does not expose an HTTP endpoint")

    key = "uploads/form-1/direct-client.txt"
    body = b"direct client upload via signed URL"
    content_type = "text/plain"

    put_url = await object_storage.signed_put_url(
        key,
        content_type=content_type,
        expires_in=300,
    )
    put_headers = object_storage.signed_put_headers(content_type=content_type)

    async with httpx.AsyncClient(timeout=30.0) as client:
        put_response = await client.put(put_url, content=body, headers=put_headers)
        assert put_response.status_code in {200, 201, 204}, put_response.text

        get_url = await object_storage.signed_get_url(key, expires_in=300)
        get_response = await client.get(get_url)
        assert get_response.status_code == 200, get_response.text
        assert get_response.content == body

    stat = await object_storage.stat(key)
    assert stat.content_type == content_type
