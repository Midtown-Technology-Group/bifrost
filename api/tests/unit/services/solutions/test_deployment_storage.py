from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.services.solutions.deployment_storage import (
    DeploymentArtifactIntegrityError,
    SolutionDeploymentStorage,
)


class ResourceExistsError(Exception):
    pass


class S3PreconditionFailed(Exception):
    def __init__(self):
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": 412},
            "Error": {"Code": "PreconditionFailed"},
        }


class FakeClient:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_object(self, *, Key, Body, IfNoneMatch, **kwargs):
        assert IfNoneMatch == "*"
        if Key in self.objects:
            raise ResourceExistsError(Key)
        self.objects[Key] = Body

    async def get_object(self, *, Key, **kwargs):
        return {"Body": SimpleNamespace(read=AsyncMock(return_value=self.objects[Key]))}

    async def head_object(self, *, Key, **kwargs):
        return {"ContentLength": len(self.objects[Key])}


def make_storage(client: FakeClient):
    @asynccontextmanager
    async def client_factory():
        yield client

    settings = SimpleNamespace(
        object_storage_provider="s3",
        s3_bucket="test",
        azure_blob_container=None,
    )
    return SolutionDeploymentStorage(
        uuid4(), uuid4(), settings=cast(Any, settings), client_factory=client_factory
    )


@pytest.mark.asyncio
async def test_finalized_source_manifest_and_runtime_keys_are_revision_addressed():
    client = FakeClient()
    storage = make_storage(client)

    source_key = await storage.write_source_artifact(b"zip")
    manifest_key = await storage.write_compiled_manifest(b"{}")
    runtime_key = await storage.write_runtime_file("workflows/run.py", b"code")

    assert f"/{storage.deployment_id}/" in source_key
    assert f"/{storage.deployment_id}/" in manifest_key
    assert runtime_key == f"{storage.runtime_prefix}workflows/run.py"
    assert client.objects[runtime_key] == b"code"
    assert await storage.read_source_artifact() == b"zip"
    assert await storage.read_compiled_manifest() == b"{}"
    assert await storage.read_runtime_file("workflows/run.py") == b"code"


@pytest.mark.asyncio
async def test_finalized_objects_are_create_only():
    client = FakeClient()
    storage = make_storage(client)
    await storage.write_compiled_manifest(b"first")

    with pytest.raises(DeploymentArtifactIntegrityError):
        await storage.write_compiled_manifest(b"replacement")

    assert client.objects[storage.manifest_key] == b"first"


@pytest.mark.asyncio
async def test_idempotent_candidate_retry_accepts_only_identical_bytes():
    storage = make_storage(FakeClient())
    await storage.write_source_artifact(b"same", idempotent=True)
    await storage.write_source_artifact(b"same", idempotent=True)
    with pytest.raises(DeploymentArtifactIntegrityError):
        await storage.write_source_artifact(b"different", idempotent=True)


@pytest.mark.asyncio
async def test_runtime_path_rejects_traversal():
    storage = make_storage(FakeClient())
    with pytest.raises(ValueError):
        await storage.write_runtime_file("../mutable.py", b"code")
    with pytest.raises(ValueError):
        await storage.read_runtime_file("../mutable.py")


@pytest.mark.parametrize(
    "error",
    [S3PreconditionFailed(), ResourceExistsError("azure duplicate")],
)
def test_provider_duplicate_write_exceptions_are_classified(error):
    assert SolutionDeploymentStorage._is_already_exists(error)


class S3InvalidRange(Exception):
    def __init__(self):
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": 416},
            "Error": {"Code": "InvalidRange"},
        }


class AzureInvalidRange(Exception):
    status_code = 416


@pytest.mark.asyncio
@pytest.mark.parametrize("max_bytes", [0, 64])
@pytest.mark.parametrize(
    "error,metadata",
    [(S3InvalidRange(), {"ContentLength": 0}),
     (AzureInvalidRange(), SimpleNamespace(content_length=0))],
)
async def test_bounded_empty_source_requires_exact_provider_metadata(error, metadata, max_bytes):
    client = FakeClient()
    client.get_object = AsyncMock(side_effect=error)
    client.head_object = AsyncMock(return_value=metadata)
    storage = make_storage(client)
    path = "modules/__init__.py"
    assert await storage.read_runtime_file(path, max_bytes=max_bytes) == b""
    client.get_object.assert_awaited_once_with(
        Bucket="test", Key=storage.runtime_prefix + path, Range=f"bytes=0-{max_bytes}",
    )
    client.head_object.assert_awaited_once_with(Bucket="test", Key=storage.runtime_prefix + path)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [
    {"ContentLength": 1}, {}, {"ContentLength": False},
    SimpleNamespace(content_length=1), SimpleNamespace(),
])
async def test_unsatisfiable_range_cannot_hide_nonempty_or_unknown_source(metadata):
    client = FakeClient()
    client.get_object = AsyncMock(side_effect=S3InvalidRange())
    client.head_object = AsyncMock(return_value=metadata)
    storage = make_storage(client)
    with pytest.raises(DeploymentArtifactIntegrityError, match="verified empty object"):
        await storage.read_runtime_file("modules/__init__.py", max_bytes=64)
    assert client.get_object.await_count == 1


@pytest.mark.asyncio
async def test_empty_source_metadata_failure_propagates_without_download_retry():
    client = FakeClient()
    client.get_object = AsyncMock(side_effect=S3InvalidRange())
    client.head_object = AsyncMock(side_effect=PermissionError("metadata unavailable"))
    storage = make_storage(client)
    with pytest.raises(PermissionError, match="metadata unavailable"):
        await storage.read_runtime_file("modules/__init__.py", max_bytes=64)
    assert client.get_object.await_count == 1


@pytest.mark.asyncio
async def test_other_download_errors_do_not_use_empty_source_handling():
    client = FakeClient()
    client.get_object = AsyncMock(side_effect=PermissionError("download unavailable"))
    client.head_object = AsyncMock()
    storage = make_storage(client)
    with pytest.raises(PermissionError, match="download unavailable"):
        await storage.read_runtime_file("modules/__init__.py", max_bytes=64)
    client.head_object.assert_not_awaited()
    assert client.get_object.await_count == 1


@pytest.mark.asyncio
async def test_negative_source_bound_fails_before_any_storage_read():
    client = FakeClient()
    client.get_object = AsyncMock()
    client.head_object = AsyncMock()
    storage = make_storage(client)
    with pytest.raises(DeploymentArtifactIntegrityError, match="total byte bound"):
        await storage.read_runtime_file("modules/__init__.py", max_bytes=-1)
    client.get_object.assert_not_awaited()
    client.head_object.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"correct", b"short", b"oversized-object"])
async def test_resource_read_requests_bounded_range_and_closes_body(content):
    class Body:
        closed = False
        offset = 0

        def __init__(self):
            self.read_sizes = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

        async def read(self, size):
            self.read_sizes.append(size)
            chunk = content[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

    body = Body()
    client = FakeClient()
    client.get_object = AsyncMock(return_value={"Body": body})
    storage = make_storage(client)
    if len(content) != 7:
        with pytest.raises(DeploymentArtifactIntegrityError, match="size differs"):
            await storage.read_resource("scripts/audit.ps1", 7)
    else:
        assert await storage.read_resource("scripts/audit.ps1", 7) == content
    client.get_object.assert_awaited_once_with(Bucket="test",
        Key=f"{storage.runtime_prefix}_resources/scripts/audit.ps1", Range="bytes=0-7")
    assert body.read_sizes[0] == 8 and body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("archive", [False, True])
async def test_resource_read_accumulates_short_transport_chunks_until_eof(archive):
    content = b"complete resource bytes"

    class Body:
        offset = 0
        closed = False
        reads = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

        async def read(self, size):
            self.reads += 1
            chunk = content[self.offset:self.offset + min(size, 2)]
            self.offset += len(chunk)
            return chunk

    body = Body()
    client = FakeClient()
    client.get_object = AsyncMock(return_value={"Body": body})
    storage = make_storage(client)
    result = await storage.read_resources_artifact() if archive else await storage.read_resource("rates.json", len(content))
    assert result == content and body.closed
    assert body.reads > 1 and body.offset == len(content)


@pytest.mark.asyncio
async def test_archive_reads_streaming_body_when_context_enters_underlying_response():
    content = b"archive bytes"
    entered = SimpleNamespace(read=AsyncMock(side_effect=AssertionError("Unbounded response read")))

    class Body:
        closed = False
        offset = 0

        async def __aenter__(self):
            return entered

        async def __aexit__(self, *_args):
            self.closed = True

        async def read(self, size):
            chunk = content[self.offset:self.offset + min(size, 3)]
            self.offset += len(chunk)
            return chunk

    body = Body()
    client = FakeClient()
    client.get_object = AsyncMock(return_value={"Body": body})
    storage = make_storage(client)
    assert await storage.read_resources_artifact() == content
    assert body.closed and body.offset == len(content)
    entered.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_short_chunk_archive_stops_at_extra_byte_bound_and_closes(monkeypatch):
    from src.services.solutions import deployment_manifest

    monkeypatch.setattr(deployment_manifest, "MAX_DEPLOYMENT_RESOURCES_BYTES", 0)
    limit = 2 * 1024 * 1024
    content = b"x" * (limit + 256)

    class Body:
        offset = 0
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

        async def read(self, size):
            chunk = content[self.offset:self.offset + min(size, 4096)]
            self.offset += len(chunk)
            return chunk

    body = Body()
    client = FakeClient()
    client.get_object = AsyncMock(return_value={"Body": body})
    storage = make_storage(client)
    with pytest.raises(DeploymentArtifactIntegrityError, match="archive exceeds"):
        await storage.read_resources_artifact()
    assert body.offset == limit + 1 and body.closed
    client.get_object.assert_awaited_once_with(Bucket="test", Key=storage.resources_artifact_key,
        Range=f"bytes=0-{limit}")


@pytest.mark.asyncio
@pytest.mark.parametrize("path,size", [("../secret", 1), ("/root/file", 1), ("rates.json", True),
    ("rates.json", 0), ("rates.json", 2 * 1024 * 1024 + 1)])
async def test_invalid_resource_contract_never_opens_storage(path, size):
    storage = make_storage(FakeClient())
    storage._client_factory = AsyncMock(side_effect=AssertionError("Storage must not be opened"))
    with pytest.raises(ValueError):
        await storage.read_resource(path, size)
    storage._client_factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("content,limit", [(b"", 0), (b"complete", 8), (b"oversized", 4)])
async def test_source_export_transport_is_bounded_and_accumulates_short_chunks(content, limit):
    class Body:
        offset = 0
        closed = False
        async def __aenter__(self): return self
        async def __aexit__(self, *_): self.closed = True
        async def read(self, size):
            chunk = content[self.offset:self.offset+min(size, 2)]
            self.offset += len(chunk)
            return chunk
    body = Body()
    client = FakeClient()
    client.get_object = AsyncMock(return_value={"Body": body})
    storage = make_storage(client)
    if len(content) > limit:
        with pytest.raises(DeploymentArtifactIntegrityError, match="byte bound"):
            await storage.read_runtime_file("helper.py", max_bytes=limit)
    else:
        assert await storage.read_runtime_file("helper.py", max_bytes=limit) == content
    assert body.closed and body.offset <= limit+1
    client.get_object.assert_awaited_once_with(Bucket="test",
        Key=storage.runtime_prefix+"helper.py", Range=f"bytes=0-{limit}")
