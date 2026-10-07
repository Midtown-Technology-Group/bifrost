"""Unit tests for SolutionStorage — S3 operations scoped to
``_solutions/{solution_id}/``.

Mirrors RepoStorage but every key is prefixed by the install's solution_id, so
two installs (and _repo/) never collide. This is the storage half of the
self-contained-world guarantee (success-criteria §3.5/§3.6).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.services.solutions.storage import SolutionStorage


def _settings(provider: str):
    return cast(
        Any,
        SimpleNamespace(
            object_storage_provider=provider,
            azure_blob_account_url="https://acct.blob.core.windows.net",
            azure_blob_container="azure-container",
            s3_bucket="s3-bucket",
        ),
    )


def test_key_prefix() -> None:
    sid = uuid.uuid4()
    s = SolutionStorage(sid)
    assert s._key("workflows/triage.py") == f"_solutions/{sid}/workflows/triage.py"


def test_key_strips_leading_slash() -> None:
    sid = uuid.uuid4()
    s = SolutionStorage(sid)
    assert s._key("/modules/x.py") == f"_solutions/{sid}/modules/x.py"


def test_key_accepts_str_or_uuid() -> None:
    sid = uuid.uuid4()
    by_uuid = SolutionStorage(sid)
    by_str = SolutionStorage(str(sid))
    assert by_uuid._key("a.py") == by_str._key("a.py")


def test_prefix_is_isolated_per_install() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    assert SolutionStorage(a)._key("modules/x.py") != SolutionStorage(b)._key(
        "modules/x.py"
    )


def test_solution_prefix_constant() -> None:
    sid = uuid.uuid4()
    s = SolutionStorage(sid)
    assert s.prefix == f"_solutions/{sid}/"


def test_storage_uses_configured_azure_blob_provider() -> None:
    storage = SolutionStorage(uuid.uuid4(), settings=_settings("azure_blob"))

    assert type(storage._storage).__module__ == (
        "src.services.file_storage.azure_blob_client"
    )
    assert type(storage._storage).__qualname__ == "AzureBlobStorageClient"
    assert storage._bucket == "azure-container"


def test_storage_uses_configured_s3_provider() -> None:
    storage = SolutionStorage(uuid.uuid4(), settings=_settings("s3"))

    assert type(storage._storage).__module__ == "src.services.file_storage.s3_client"
    assert type(storage._storage).__qualname__ == "S3StorageClient"
    assert storage._bucket == "s3-bucket"


def test_storage_rejects_unknown_provider() -> None:
    solution_id = uuid.uuid4()

    with pytest.raises(ValueError, match="Unsupported object_storage_provider"):
        SolutionStorage(solution_id, settings=_settings("filesystem"))


@pytest.mark.parametrize("provider", ["s3", "azure_blob"])
@pytest.mark.parametrize("content", [b"abcd", b"abcde"])
async def test_bounded_source_read_limits_transport_and_handles_short_chunks(monkeypatch, provider, content):
    storage = SolutionStorage(uuid.uuid4(), settings=_settings(provider))
    calls, sizes = [], []

    class Body:
        offset = 0
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def read(self, size):
            sizes.append(size)
            chunk = content[self.offset:self.offset + min(size, 1)]
            self.offset += len(chunk)
            return chunk

    body = Body()

    class Client:
        async def get_object(self, **kwargs):
            calls.append(kwargs)
            return {"Body": body}  # Deliberately ignore Range; local reads still enforce the bound.

    @asynccontextmanager
    async def client():
        yield Client()

    monkeypatch.setattr(storage, "_get_client", client)
    if len(content) > 4:
        with pytest.raises(ValueError, match="exceeds its byte bound"):
            await storage.read("functions/main.py", max_bytes=4)
    else:
        assert await storage.read("functions/main.py", max_bytes=4) == content
    assert calls == [{"Bucket": storage._bucket, "Key": storage.prefix + "functions/main.py", "Range": "bytes=0-4"}]
    assert sizes == [5, 4, 3, 2, 1]
    assert body.closed


@pytest.mark.parametrize("limit", [-1, True])
async def test_invalid_source_bound_stops_before_download(monkeypatch, limit):
    storage = SolutionStorage(uuid.uuid4())

    def rejected():
        raise AssertionError("Invalid bound must stop before opening storage")

    monkeypatch.setattr(storage, "_get_client", rejected)
    with pytest.raises(ValueError, match="byte bound is invalid"):
        await storage.read("functions/main.py", max_bytes=limit)
