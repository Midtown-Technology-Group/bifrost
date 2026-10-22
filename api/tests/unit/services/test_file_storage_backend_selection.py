from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config import Settings
from src.services.file_storage.azure_blob_client import AzureBlobStorageClient
from src.services.file_storage.s3_client import S3StorageClient
from src.services.file_storage.service import FileStorageService


def _settings(**overrides):
    values = {
        "secret_key": "x" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def test_file_storage_uses_s3_backend_by_default():
    service = FileStorageService(db=MagicMock(), settings=_settings())

    assert isinstance(service._s3_storage, S3StorageClient)


def test_file_storage_uses_azure_blob_backend_when_configured():
    service = FileStorageService(
        db=MagicMock(),
        settings=_settings(
            object_storage_provider="azure_blob",
            azure_blob_account_url="https://example.blob.core.windows.net",
            azure_blob_container="bifrost-objects",
            azure_blob_auth="default_credential",
        ),
    )

    assert isinstance(service._s3_storage, AzureBlobStorageClient)
    assert service.presigned_upload_headers("text/plain") == {
        "Content-Type": "text/plain",
        "x-ms-blob-type": "BlockBlob",
    }


def _client_with_blob_names(names, *, next_token=None):
    client = AzureBlobStorageClient(
        _settings(
            object_storage_provider="azure_blob",
            azure_blob_account_url="https://example.blob.core.windows.net",
            azure_blob_container="bifrost-objects",
            azure_blob_auth="default_credential",
        )
    )

    class FakePager:
        continuation_token = next_token

        def __init__(self, blobs):
            self._blobs = blobs
            self._sent = False

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return self._blobs

        def __aiter__(self):
            return self

    class FakePaged:
        def __init__(self, blobs):
            self._blobs = blobs

        def by_page(self, **kwargs):
            assert set(kwargs) <= {"continuation_token"}
            return FakePager(self._blobs)

    class FakeContainer:
        def walk_blobs(self, name_starts_with="", delimiter="/", results_per_page=None):
            del results_per_page
            prefixes: list[str] = []
            seen: set[str] = set()
            direct = []
            for name in names:
                if not name.startswith(name_starts_with):
                    continue
                remainder = name[len(name_starts_with) :]
                if delimiter in remainder:
                    prefix = name_starts_with + remainder.split(delimiter, 1)[0] + delimiter
                    if prefix not in seen:
                        seen.add(prefix)
                        prefixes.append(prefix)
                else:
                    direct.append(
                        SimpleNamespace(
                            name=name,
                            size=10,
                            etag='"etag"',
                            last_modified=None,
                        )
                    )
            items = [SimpleNamespace(prefix=prefix) for prefix in prefixes] + direct
            return FakePaged(items)

        def list_blobs(self, name_starts_with="", results_per_page=None):
            blobs = [
                SimpleNamespace(
                    name=name,
                    size=10,
                    etag='"etag"',
                    last_modified=None,
                )
                for name in names
                if name.startswith(name_starts_with)
            ]
            if results_per_page:
                blobs = blobs[:results_per_page]
            return FakePaged(blobs)

    client._operation_owned = True
    client._container_client = FakeContainer()
    return client


@pytest.mark.asyncio
async def test_azure_blob_client_exposes_s3_shaped_list_objects_v2():
    client = _client_with_blob_names(
        [
            "_repo/workflows/a.py",
            "_repo/apps/app.tsx",
            "_repo/apps/components/button.tsx",
        ]
    )

    response = await client.list_objects_v2(
        Bucket="ignored",
        Prefix="_repo/",
        Delimiter="/",
    )

    assert response["IsTruncated"] is False
    assert response["Contents"] == []
    assert response["CommonPrefixes"] == [
        {"Prefix": "_repo/apps/"},
        {"Prefix": "_repo/workflows/"},
    ]

    response = await client.list_objects_v2(
        Bucket="ignored",
        Prefix="_repo/workflows/",
    )

    assert response["Contents"] == [
        {
            "Key": "_repo/workflows/a.py",
            "Size": 10,
            "ETag": "etag",
            "LastModified": None,
        }
    ]


@pytest.mark.asyncio
async def test_azure_blob_client_exposes_s3_shaped_list_objects_v2_pagination():
    client = _client_with_blob_names(
        ["_repo/workflows/a.py", "_repo/workflows/b.py"],
        next_token="next-page",
    )

    response = await client.list_objects_v2(
        Bucket="ignored",
        Prefix="_repo/workflows/",
        ContinuationToken="first-page",
        MaxKeys=1,
    )

    assert response["IsTruncated"] is True
    assert response["NextContinuationToken"] == "next-page"
    assert response["Contents"] == [
        {
            "Key": "_repo/workflows/a.py",
            "Size": 10,
            "ETag": "etag",
            "LastModified": None,
        }
    ]


@pytest.mark.parametrize(
    ("provider", "client_type", "bucket_attr"),
    [
        ("azure_blob", AzureBlobStorageClient, "azure-container"),
        ("s3", S3StorageClient, "s3-bucket"),
    ],
)
def test_remaining_storage_callers_follow_the_configured_provider(provider, client_type, bucket_attr):
    from uuid import uuid4

    from src.services.application_deploy_storage import ApplicationDeployStorage
    from src.services.application_source_artifact import ApplicationSourceArtifactStorage
    from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage

    settings = _settings(
        object_storage_provider=provider,
        azure_blob_account_url="https://example.blob.core.windows.net",
        azure_blob_container="azure-container",
        azure_blob_auth="default_credential",
        s3_bucket="s3-bucket",
    )
    callers = [
        ApplicationSourceArtifactStorage(settings),
        ApplicationDeployStorage(uuid4(), settings),
        WorkspaceBundleStorage(uuid4(), settings),
    ]
    assert all(isinstance(caller._storage, client_type) for caller in callers)
    assert {caller._bucket for caller in callers} == {bucket_attr}
