"""Owned Azure transports close on all exits without racing sibling operations."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.file_storage.azure_blob_client import AzureBlobStorageClient


@pytest.fixture
def clients(monkeypatch):
    opened = []

    async def ensure(client):
        if client._container_client is not None:
            return
        client._service_client = SimpleNamespace(close=AsyncMock())
        client._credential = SimpleNamespace(close=AsyncMock())

        async def chunks():
            hang = getattr(client, "_stream_hang", False)
            yield b"first" if hang else b"content"
            if hang:
                await asyncio.Event().wait()

        stream = SimpleNamespace(
            readall=AsyncMock(return_value=b"content"),
            chunks=chunks,
            close=AsyncMock(),
        )

        async def blobs(**_kwargs):
            for blob in ():
                yield blob

        client._container_client = SimpleNamespace(
            download_blob=AsyncMock(return_value=stream),
            get_container_properties=AsyncMock(return_value={}),
            upload_blob=AsyncMock(),
            list_blobs=lambda **kwargs: SimpleNamespace(by_page=lambda **page: blobs()),
        )
        opened.append((client, client._service_client, client._credential))

    monkeypatch.setattr(AzureBlobStorageClient, "_ensure_client", ensure)
    return opened


def adapter():
    return AzureBlobStorageClient(
        SimpleNamespace(azure_blob_account_url="https://example.blob.core.windows.net")
    )


def assert_closed(opened):
    assert opened
    for _, service, credential in opened:
        service.close.assert_awaited_once()
        credential.close.assert_awaited_once()


def solution_storage():
    from src.services.solutions.storage import SolutionStorage

    return SolutionStorage(
        "test",
        settings=SimpleNamespace(
            object_storage_provider="azure_blob",
            azure_blob_account_url="https://example.blob.core.windows.net",
            azure_blob_container="test",
        ),
    )


@pytest.mark.asyncio
async def test_solution_operation_reuses_transport_and_isolates_sibling(clients):
    storage = solution_storage()
    async with storage.operation() as scoped:
        await scoped.write("first.py", b"first")
        await scoped.write("second.py", b"second")
        assert len(clients) == 1
        # The original object does not borrow the publication's transport.
        await storage.write("sibling.py", b"sibling")
        assert len(clients) == 2
        assert clients[0][0]._container_client.upload_blob.await_count == 2
        clients[0][1].close.assert_not_awaited()
    assert_closed(clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_solution_operation_closes_on_failure_and_cancellation(clients, failure):
    with pytest.raises(failure):
        async with solution_storage().operation() as scoped:
            await scoped.write("first.py", b"first")
            clients[0][0]._container_client.upload_blob.side_effect = failure
            await scoped.write("second.py", b"second")
    assert len(clients) == 1
    assert_closed(clients)


@pytest.mark.asyncio
async def test_python_publication_shares_client_and_reports_cache_timing(
    clients, monkeypatch
):
    from src.services.solutions.deploy import (
        SolutionDeployer,
        SolutionDeployInstrumentation,
        solution_deploy_instrumentation,
    )

    cache_write, timing = AsyncMock(), AsyncMock()
    monkeypatch.setattr("src.core.module_cache.set_module", cache_write)
    monkeypatch.setattr(
        "src.services.solutions.deploy.SolutionStorage", lambda _sid: solution_storage()
    )
    with solution_deploy_instrumentation(
        SolutionDeployInstrumentation(timing_callback=timing)
    ):
        deployer = SolutionDeployer(AsyncMock())
    await deployer._write_python("test", {"a.py": "A = 1", "b.py": "B = 2"})
    assert cache_write.await_count == 2
    assert len(clients) == 1
    assert_closed(clients)
    assert {c.args[0] for c in timing.await_args_list} == {
        "runtime_source_storage",
        "redis_module_cache",
    }


@pytest.mark.asyncio
async def test_direct_read_and_nested_methods_close_one_owner(clients):
    assert await adapter().read_uploaded_file("test") == b"content"
    assert len(clients) == 1
    assert_closed(clients)


@pytest.mark.asyncio
async def test_context_error_closes_both_resources(clients):
    operation = AsyncMock(side_effect=RuntimeError("operation failed"))
    with pytest.raises(RuntimeError, match="operation failed"):
        async with adapter().get_client():
            await operation()
    assert_closed(clients)


@pytest.mark.asyncio
async def test_partial_initialization_closes_resources(monkeypatch):
    service, credential = (
        SimpleNamespace(close=AsyncMock()),
        SimpleNamespace(close=AsyncMock()),
    )

    async def fail(client):
        client._service_client, client._credential = service, credential
        raise RuntimeError("initialization failed")

    monkeypatch.setattr(AzureBlobStorageClient, "_ensure_client", fail)
    storage = adapter()
    with pytest.raises(RuntimeError):
        await storage.head_bucket(Bucket="ignored")
    service.close.assert_awaited_once()
    credential.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_operations_have_independent_clients(clients):
    root = adapter()
    async with root.get_client() as first:
        async with root.get_client() as second:
            assert first is not second
            assert len(clients) == 2
        clients[1][1].close.assert_awaited_once()
        clients[0][1].close.assert_not_awaited()
        assert await first.read_uploaded_file("test") == b"content"
    assert_closed(clients)


@pytest.mark.asyncio
async def test_stream_early_close_keeps_client_until_consumed(clients):
    stream = adapter().iter_object_chunks("test")
    assert await anext(stream) == b"content"
    clients[0][1].close.assert_not_awaited()
    await stream.aclose()
    assert_closed(clients)


@pytest.mark.asyncio
async def test_stream_cancellation_closes_client(clients, monkeypatch):
    monkeypatch.setattr(AzureBlobStorageClient, "_stream_hang", True, raising=False)
    received = asyncio.Event()

    async def consume():
        async for _ in adapter().iter_object_chunks("test"):
            received.set()

    task = asyncio.create_task(consume())
    await received.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert_closed(clients)


@pytest.mark.asyncio
async def test_service_close_failure_still_closes_credential(clients):
    with pytest.raises(RuntimeError):
        async with adapter().get_client():
            clients[0][1].close.side_effect = RuntimeError("close failed")
    assert_closed(clients)


@pytest.mark.asyncio
async def test_stream_completion_and_read_error_close_owner(clients, monkeypatch):
    assert [chunk async for chunk in adapter().iter_object_chunks("test")] == [
        b"content"
    ]
    assert_closed(clients)

    async def fail(self, **kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(AzureBlobStorageClient, "get_object", fail)
    storage = adapter()
    with pytest.raises(RuntimeError, match="download failed"):
        await storage.read_uploaded_file("test")
    assert len(clients) == 2
    assert_closed(clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["solution", "application"])
async def test_destination_write_failure_closes_download(clients, monkeypatch, kind):
    from src.services.application_deploy_storage import ApplicationDeployStorage
    from src.services.solutions import deploy_job_storage
    from unittest.mock import MagicMock

    storage_type = (
        deploy_job_storage.SolutionDeployJobStorage
        if kind == "solution"
        else ApplicationDeployStorage
    )
    storage = object.__new__(storage_type)
    storage._storage = adapter()
    storage.key = "test"
    storage.job_id = "test"
    path = MagicMock()
    if kind == "solution":
        destination = AsyncMock()
        destination.write.side_effect = OSError("disk full")
        context = AsyncMock()
        context.__aenter__.return_value = destination
        monkeypatch.setattr(
            deploy_job_storage, "open_file", AsyncMock(return_value=context)
        )
    else:
        destination = path.open.return_value.__enter__.return_value
        destination.write.side_effect = OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        await storage.copy_to_path(path, expected_sha256="unused")
    assert_closed(clients)
