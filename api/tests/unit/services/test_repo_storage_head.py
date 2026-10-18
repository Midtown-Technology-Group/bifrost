"""Repo metadata remains usable across the fork's supported storage providers."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from botocore.exceptions import ClientError
import pytest

from src.services.repo_storage import RepoStorage


class MissingObject(Exception):
    pass


def _storage(provider: str, client: SimpleNamespace) -> RepoStorage:
    storage = RepoStorage.__new__(RepoStorage)
    storage._settings = SimpleNamespace(object_storage_provider=provider)
    storage._bucket = "repo-container"

    @asynccontextmanager
    async def get_client():
        yield client

    storage._get_client = get_client
    return storage


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["s3", "azure_blob"])
async def test_head_normalizes_metadata_without_reading_object_content(provider):
    modified = datetime(2026, 10, 17, tzinfo=timezone.utc)
    metadata = (
        SimpleNamespace(etag="azure-etag", last_modified=modified, content_length=37)
        if provider == "azure_blob"
        else {"ETag": '"s3-etag"', "LastModified": modified, "ContentLength": 37}
    )
    client = SimpleNamespace(
        exceptions=SimpleNamespace(NoSuchKey=MissingObject),
        head_object=AsyncMock(return_value=metadata),
        get_object=AsyncMock(),
    )

    result = await _storage(provider, client).head("/modules/example.py")

    assert result is not None
    assert result.etag == ("azure-etag" if provider == "azure_blob" else "s3-etag")
    assert result.last_modified == modified
    assert result.size == 37
    client.head_object.assert_awaited_once_with(
        Bucket="repo-container", Key="_repo/modules/example.py"
    )
    client.get_object.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["s3", "azure_blob"])
async def test_head_returns_none_for_provider_missing_object(provider):
    client = SimpleNamespace(
        exceptions=SimpleNamespace(NoSuchKey=MissingObject),
        head_object=AsyncMock(side_effect=MissingObject()),
    )

    assert await _storage(provider, client).head("missing.py") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["s3", "azure_blob"])
async def test_head_does_not_disguise_storage_access_failure_as_missing(provider):
    denied = ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")
    client = SimpleNamespace(
        exceptions=SimpleNamespace(NoSuchKey=MissingObject),
        head_object=AsyncMock(side_effect=denied),
    )

    with pytest.raises(ClientError) as failure:
        await _storage(provider, client).head("protected.py")

    assert failure.value is denied
