from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from src.services.app_storage import AppStorageService


class _Body:
    def __init__(self, value: bytes):
        self.value = value

    async def read(self) -> bytes:
        return self.value


class _S3Client:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.copy_calls: list[tuple[str, str]] = []
        self.put_calls: list[tuple[str, bytes]] = []
        self.delete_calls: list[list[str]] = []

    async def list_objects_v2(self, *, Prefix: str, **_kwargs):  # noqa: N803
        if Prefix.endswith("/preview/"):
            names = [
                "manifest.json",
                "entry-new.js",
                "chunk-new.js",
                "entry-old.js",
                "chunk-old.js",
            ]
        else:
            names = ["manifest.json", "entry-old.js", "chunk-old.js"]
        return {
            "Contents": [{"Key": f"{Prefix}{name}"} for name in names],
            "IsTruncated": False,
        }

    async def get_object(self, *, Key: str, **_kwargs):  # noqa: N803
        assert Key.endswith("/preview/manifest.json")
        return {"Body": _Body(json.dumps(self.manifest).encode())}

    async def copy_object(
        self,
        *,
        CopySource: dict,  # noqa: N803
        Key: str,  # noqa: N803
        **_kwargs,
    ):
        self.copy_calls.append((CopySource["Key"], Key))

    async def delete_objects(self, *, Delete: dict, **_kwargs):  # noqa: N803
        self.delete_calls.append([item["Key"] for item in Delete["Objects"]])

    async def put_object(self, *, Key: str, Body: bytes, **_kwargs):  # noqa: N803
        self.put_calls.append((Key, Body))


def _bundle(outputs=None):
    outputs = outputs or {"entry-new.js": b"entry", "chunk-new.js": b"chunk"}
    manifest = {
        "entry": "entry-new.js", "outputs": list(outputs),
        "build_evidence": {
            "schema_version": "bifrost.inline-app-build/v1",
            "output_hashes": {path: "sha256:" + hashlib.sha256(data).hexdigest()
                              for path, data in outputs.items()},
        },
    }
    return {**outputs, "manifest.json": json.dumps(manifest).encode()}


def _storage(client: _S3Client) -> AppStorageService:
    storage = object.__new__(AppStorageService)
    storage._bucket = "test"
    storage._settings = None

    @asynccontextmanager
    async def _get_client():
        yield client

    storage._get_client = _get_client
    storage.invalidate_render_cache = AsyncMock()
    return storage


@pytest.mark.asyncio
async def test_publish_promotes_only_captured_outputs_and_writes_manifest_last():
    client = _S3Client(
        {
            "entry": "entry-new.js",
            "outputs": ["entry-new.js", "chunk-new.js"],
        }
    )
    storage = _storage(client)
    progress: list[tuple[int, int]] = []

    published = await storage.publish(
        "app-1",
        bundle_files=_bundle(),
        progress_callback=lambda current, total: _record(
            progress, current, total
        ),
    )

    assert published == 3
    published_paths = [path.rsplit("/", 1)[-1] for path, _ in client.put_calls]
    assert set(published_paths) == {
        "entry-new.js",
        "chunk-new.js",
        "manifest.json",
    }
    assert published_paths[-1] == "manifest.json"
    assert client.copy_calls == []
    deleted = {key.rsplit("/", 1)[-1] for call in client.delete_calls for key in call}
    assert deleted == {"entry-old.js", "chunk-old.js"}
    assert progress[0] == (0, 3)
    assert progress[-1] == (3, 3)
    storage.invalidate_render_cache.assert_awaited_once_with("app-1")


async def _record(
    target: list[tuple[int, int]],
    current: int,
    total: int,
) -> None:
    target.append((current, total))


@pytest.mark.asyncio
async def test_publish_rejects_snapshot_missing_declared_output_before_write():
    client = _S3Client(
        {
            "entry": "entry-new.js",
            "outputs": ["entry-new.js", "missing.js"],
        }
    )
    storage = _storage(client)

    files = _bundle()
    del files["chunk-new.js"]
    with pytest.raises(ValueError, match="exactly match"):
        await storage.publish("app-1", bundle_files=files)

    assert client.copy_calls == []
    assert client.put_calls == []
    assert client.delete_calls == []


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_turn_promoted_manifest_into_failed_publish():
    client = _S3Client(
        {
            "entry": "entry-new.js",
            "outputs": ["entry-new.js", "chunk-new.js"],
        }
    )
    client.delete_objects = AsyncMock(side_effect=RuntimeError("cleanup unavailable"))
    storage = _storage(client)

    published = await storage.publish("app-1", bundle_files=_bundle())

    assert published == 3
    assert client.put_calls[-1][0].endswith("/manifest.json")
    storage.invalidate_render_cache.assert_awaited_once_with("app-1")


@pytest.mark.asyncio
async def test_preview_and_caller_mapping_changes_cannot_change_captured_publication():
    client = _S3Client({})
    storage = _storage(client)
    files = _bundle()
    retained = dict(files)

    async def concurrent_preview(_current, _total):
        # Change both mutable inputs while the publisher awaits progress.
        client.manifest = {"entry": "other.js", "outputs": ["other.js"]}
        files["manifest.json"] = json.dumps(client.manifest).encode()
        files["entry-new.js"] = b"a later build"

    await storage.publish("app-1", bundle_files=files, progress_callback=concurrent_preview)
    actual = {key.rsplit("/", 1)[-1]: data for key, data in client.put_calls}
    assert actual == retained
    assert client.put_calls[-1][0].endswith("/manifest.json")
    assert client.copy_calls == []
    assert all("/preview/" not in key for call in client.delete_calls for key in call)


@pytest.mark.asyncio
async def test_changed_output_is_rejected_before_live_writes():
    client = _S3Client({})
    files = _bundle()
    files["entry-new.js"] = b"different bytes"
    with pytest.raises(ValueError, match="output hashes"):
        await _storage(client).publish("app-1", bundle_files=files)
    assert client.put_calls == client.copy_calls == client.delete_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["duplicate-output", "missing-css", "invalid-css", "missing-evidence", "unsafe-path"])
async def test_invalid_publication_manifest_is_rejected_before_any_write(change):
    client = _S3Client({})
    files = _bundle()
    manifest = json.loads(files["manifest.json"])
    if change == "duplicate-output":
        manifest["outputs"].append("entry-new.js")
    elif change == "missing-css":
        manifest["css"] = "missing.css"
    elif change == "invalid-css":
        manifest["css"] = []
    elif change == "missing-evidence":
        del manifest["build_evidence"]
    else:
        files["../entry-new.js"] = files.pop("entry-new.js")
        manifest["entry"] = "../entry-new.js"
        manifest["outputs"][0] = "../entry-new.js"
    files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(ValueError):
        await _storage(client).publish("app-1", bundle_files=files)
    assert client.put_calls == client.copy_calls == client.delete_calls == []


@pytest.mark.asyncio
async def test_output_failure_preserves_prior_live_manifest():
    client = _S3Client({})
    client.put_object = AsyncMock(side_effect=RuntimeError("storage failed"))
    storage = _storage(client)
    with pytest.raises(RuntimeError, match="storage failed"):
        await storage.publish("app-1", bundle_files=_bundle())
    assert all(not call.kwargs["Key"].endswith("/manifest.json")
               for call in client.put_object.await_args_list)
    assert client.delete_calls == []
    storage.invalidate_render_cache.assert_not_awaited()
