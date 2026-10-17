"""Run the production adapter against the installed, lockfile-pinned SDK shape.

No Azure credentials/network: only the transport responses are mocked. In
particular, adding a fictional downloader.close() must fail at the boundary.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import (
    BlobServiceClient,
    ContainerClient,
    StorageStreamDownloader,
)
from src.services.file_storage.azure_blob_client import AzureBlobStorageClient


@pytest.fixture
def boundary(monkeypatch):
    downloader = create_autospec(StorageStreamDownloader, instance=True, spec_set=True)
    service = create_autospec(BlobServiceClient, instance=True, spec_set=True)
    credential = create_autospec(DefaultAzureCredential, instance=True, spec_set=True)
    container = create_autospec(ContainerClient, instance=True, spec_set=True)
    container.download_blob.return_value = downloader
    downloader.readall.return_value = b"abcdef"

    async def chunks():
        yield b"abc"
        yield b"def"

    downloader.chunks.side_effect = chunks

    async def ensure(client):
        client._service_client = service
        client._credential = credential
        client._container_client = container

    monkeypatch.setattr(AzureBlobStorageClient, "_ensure_client", ensure)
    adapter = AzureBlobStorageClient(
        SimpleNamespace(azure_blob_account_url="https://test.blob.core.windows.net")
    )
    return adapter, downloader, container, service, credential


@pytest.mark.asyncio
async def test_read_all_and_bounded_reads_reach_eof(boundary):
    adapter, downloader, container, service, credential = boundary
    response = await adapter.get_object(Bucket="ignored", Key="fixture")
    body = response["Body"]
    assert await body.read(2) == b"ab"
    assert await body.read() == b"cdef"
    assert await body.read(1) == b""
    downloader.readall.assert_awaited_once_with()
    container.download_blob.assert_awaited_once_with("fixture")
    service.close.assert_awaited_once_with()
    credential.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_chunked_eof_closes_transport_owner(boundary):
    adapter, _, _, service, credential = boundary
    assert [
        chunk async for chunk in adapter.iter_object_chunks("fixture", chunk_size=2)
    ] == [b"ab", b"c", b"de", b"f"]
    service.close.assert_awaited_once_with()
    credential.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_partial_read_cleanup_closes_owner(boundary):
    adapter, _, _, service, credential = boundary
    stream = adapter.iter_object_chunks("fixture", chunk_size=2)
    try:
        assert await anext(stream) == b"ab"
        service.close.assert_not_awaited()
    finally:
        await stream.aclose()
    service.close.assert_awaited_once_with()
    credential.close.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("provider failed"), asyncio.CancelledError()]
)
async def test_read_failure_or_cancellation_propagates_and_closes_owner(
    boundary, failure
):
    adapter, downloader, _, service, credential = boundary

    async def chunks():
        yield b"first"
        raise failure

    downloader.chunks.side_effect = chunks
    stream = adapter.iter_object_chunks("fixture")
    assert await anext(stream) == b"first"
    with pytest.raises(type(failure)) as caught:
        await anext(stream)
    assert caught.value is failure
    service.close.assert_awaited_once_with()
    credential.close.assert_awaited_once_with()
