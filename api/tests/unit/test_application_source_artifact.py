import builtins
import io
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


class MemoryBody:
    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class MemoryS3:
    exceptions = SimpleNamespace(NoSuchKey=KeyError)

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.page_size: int | None = None
        self.seen_tokens: list[str | None] = []

    async def put_object(self, *, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    async def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": MemoryBody(self.objects[Key])}

    async def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"ContentLength": len(self.objects[Key])}

    async def delete_object(self, *, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)

    async def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        token = kwargs.get("ContinuationToken")
        self.seen_tokens.append(token)
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        offset = int(token) if token else 0
        if self.page_size is None:
            page = keys[offset:]
            next_offset = len(keys)
        else:
            page = keys[offset : offset + self.page_size]
            next_offset = offset + self.page_size
        truncated = next_offset < len(keys)
        response = {
            "Contents": [{"Key": key} for key in page],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = str(next_offset)
        return response


@pytest.fixture
def source_storage(monkeypatch: pytest.MonkeyPatch):
    from src.services import application_source_artifact

    memory = MemoryS3()

    class StorageClient:
        def __init__(self, _settings):
            pass

        @asynccontextmanager
        async def get_client(self):
            yield memory

        async def put_object_from_chunks(
            self,
            path: str,
            chunks: AsyncIterator[bytes],
            *,
            content_type: str | None = None,
            part_size: int = 8 * 1024 * 1024,
        ) -> tuple[str, int]:
            data = b""
            async for chunk in chunks:
                data += chunk
            memory.objects[path] = data
            return ("hash", len(data))

        async def iter_object_chunks(self, path: str, *, chunk_size: int):
            data = memory.objects[path]
            for offset in range(0, len(data), chunk_size):
                yield data[offset : offset + chunk_size]

    monkeypatch.setattr(application_source_artifact, "S3StorageClient", StorageClient)
    return application_source_artifact.ApplicationSourceArtifactStorage(), memory


@pytest.mark.asyncio
async def test_source_artifact_writes_reads_and_deletes_exact_deployment_key(
    tmp_path: Path, source_storage
) -> None:
    storage, memory = source_storage
    app_id = uuid4()
    deployment_id = uuid4()
    source = tmp_path / "source.zip"
    source.write_bytes(b"zip-bytes")

    digest, size = await storage.write_deployment_source(app_id, deployment_id, source)

    key = f"_application_artifacts/{app_id}/deployments/{deployment_id}/source.zip"
    assert memory.objects == {key: b"zip-bytes"}
    assert digest == "hash"
    assert size == len(b"zip-bytes")

    copied = tmp_path / "copied.zip"
    assert await storage.copy_deployment_source_to_path(app_id, deployment_id, copied) == len(
        b"zip-bytes"
    )
    assert copied.read_bytes() == b"zip-bytes"

    assert await storage.read_deployment_source(app_id, deployment_id) == b"zip-bytes"
    assert await storage.deployment_source_exists(app_id, deployment_id) is True
    assert b"".join(
        [chunk async for chunk in storage.iter_deployment_source(app_id, deployment_id)]
    ) == b"zip-bytes"

    await storage.delete_deployment_source(app_id, deployment_id)
    assert memory.deleted == [key]
    assert memory.objects == {}


@pytest.mark.asyncio
async def test_source_artifact_disk_io_runs_off_event_loop(
    tmp_path: Path, source_storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, _memory = source_storage
    source = tmp_path / "source.zip"
    copied = tmp_path / "copied.zip"
    source.write_bytes(b"retained-source")
    event_loop_thread = threading.get_ident()
    original_open = builtins.open
    operations: set[str] = set()

    class CheckedFile:
        def __init__(self, file):
            self.file = file

        def __getattr__(self, name):
            return getattr(self.file, name)

        def read(self, size):
            assert threading.get_ident() != event_loop_thread
            operations.add("read")
            return self.file.read(size)

        def write(self, data):
            assert threading.get_ident() != event_loop_thread
            operations.add("write")
            return self.file.write(data)

        def close(self):
            assert threading.get_ident() != event_loop_thread
            operations.add("close")
            return self.file.close()

    def checked_open(file, *args, **kwargs):
        if str(file) not in {str(source), str(copied)}:
            return original_open(file, *args, **kwargs)
        assert threading.get_ident() != event_loop_thread
        operations.add("open")
        return CheckedFile(original_open(file, *args, **kwargs))

    app_id, deployment_id = uuid4(), uuid4()
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", checked_open)
        patch.setattr(io, "open", checked_open)
        await storage.write_deployment_source(app_id, deployment_id, source)
        await storage.copy_deployment_source_to_path(app_id, deployment_id, copied)

    assert copied.read_bytes() == b"retained-source"
    assert operations == {"open", "read", "write", "close"}


@pytest.mark.asyncio
async def test_source_artifact_deletes_all_artifacts_for_app_prefix(source_storage) -> None:
    storage, memory = source_storage
    app_id = uuid4()
    other_app_id = uuid4()
    first = uuid4()
    second = uuid4()
    memory.objects = {
        f"_application_artifacts/{app_id}/deployments/{first}/source.zip": b"1",
        f"_application_artifacts/{app_id}/deployments/{second}/source.zip": b"2",
        f"_application_artifacts/{other_app_id}/deployments/{uuid4()}/source.zip": b"3",
    }

    await storage.delete_application_artifacts(app_id)

    assert sorted(memory.objects) == [
        next(key for key in memory.objects if key.startswith(f"_application_artifacts/{other_app_id}/"))
    ]
    assert sorted(memory.deleted) == sorted(
        [
        f"_application_artifacts/{app_id}/deployments/{first}/source.zip",
        f"_application_artifacts/{app_id}/deployments/{second}/source.zip",
        ]
    )


@pytest.mark.asyncio
async def test_source_artifact_delete_application_artifacts_paginates(source_storage) -> None:
    storage, memory = source_storage
    app_id = uuid4()
    retained_keys = [
        f"_application_artifacts/{app_id}/deployments/{uuid4()}/source.zip"
        for _ in range(3)
    ]
    memory.objects = {key: str(index).encode() for index, key in enumerate(retained_keys)}
    memory.page_size = 1

    await storage.delete_application_artifacts(app_id)

    assert memory.objects == {}
    assert sorted(memory.deleted) == sorted(retained_keys)
    assert memory.seen_tokens == [None, "1", "2"]
