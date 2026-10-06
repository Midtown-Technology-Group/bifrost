from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
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
        self.objects: dict[str, bytes] = {}
        if manifest:
            self.objects["_apps/app-1/live/manifest.json"] = json.dumps(manifest).encode()
        self.exceptions = SimpleNamespace(NoSuchKey=KeyError)
        self.preconditions: list[dict] = []
        self.copy_calls: list[tuple[str, str]] = []
        self.put_calls: list[tuple[str, bytes]] = []
        self.delete_calls: list[list[str]] = []
        self.client_contexts: list[bool] = []

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
        if "/preview/" in Key:
            return {"Body": _Body(json.dumps(self.manifest).encode())}
        data = self.objects[Key]
        return {"Body": _Body(data), "ETag": hashlib.sha256(data).hexdigest()}

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
        self.preconditions.append({key: value for key, value in _kwargs.items() if key in {"IfMatch", "IfNoneMatch"}})
        if _kwargs.get("IfNoneMatch") == "*" and Key in self.objects:
            raise ValueError("precondition failed")
        if "IfMatch" in _kwargs and (
            Key not in self.objects or _kwargs["IfMatch"] != hashlib.sha256(self.objects[Key]).hexdigest()
        ):
            raise ValueError("precondition failed")
        self.objects[Key] = Body
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
    async def _get_client(*, single_attempt: bool = False):
        client.client_contexts.append(single_attempt)
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
    assert deleted == set()
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
@pytest.mark.parametrize("initially_published", [False, True])
async def test_manifest_changed_during_build_rejects_before_intent_or_output_write(initially_published):
    client = _S3Client({"entry": "old.js", "outputs": ["old.js"]} if initially_published else {})
    storage = _storage(client)
    before_build = await storage.live_manifest_revision("app-1")
    await storage.publish("app-1", bundle_files=_bundle())
    winner = client.objects["_apps/app-1/live/manifest.json"]
    client.put_calls.clear()
    checkpoint = AsyncMock()
    with pytest.raises(ValueError, match="changed during the captured source build"):
        await storage.publish("app-1", bundle_files=_bundle(), expected_revision=before_build,
            checkpoint_callback=checkpoint)
    assert client.put_calls == []
    assert client.objects["_apps/app-1/live/manifest.json"] == winner
    checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_unchanged_before_build_manifest_revision_is_carried_into_saved_intent():
    client = _S3Client({"entry": "old.js", "outputs": ["old.js"]})
    storage = _storage(client)
    before_build = await storage.live_manifest_revision("app-1")
    checkpoint = AsyncMock()
    await storage.publish("app-1", bundle_files=_bundle(), expected_revision=before_build,
        checkpoint_callback=checkpoint)
    assert checkpoint.await_args.args[0]["expected_live_etag"] == before_build.etag
    assert client.preconditions[-1] == {"IfMatch": before_build.etag}


@pytest.mark.asyncio
async def test_source_guard_refusal_after_output_creation_does_not_switch_manifest():
    client = _S3Client({"entry": "old.js", "outputs": ["old.js"]})
    prior = client.objects["_apps/app-1/live/manifest.json"]
    storage = _storage(client)
    checkpoint = AsyncMock()
    guard = AsyncMock(side_effect=ValueError("Protected Main superseded"))
    with pytest.raises(ValueError, match="Main superseded"):
        await storage.publish("app-1", bundle_files=_bundle(), checkpoint_callback=checkpoint,
            before_manifest_switch=guard)
    checkpoint.assert_awaited_once()
    guard.assert_awaited_once()
    assert client.objects["_apps/app-1/live/manifest.json"] == prior
    assert all(not key.endswith("/manifest.json") for key, _ in client.put_calls)
    assert client.delete_calls == []


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
async def test_publication_never_deletes_chunks_used_by_loaded_browsers():
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
    client.delete_objects.assert_not_awaited()
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


@pytest.mark.asyncio
async def test_checkpoint_precedes_effects_and_recovery_only_reads():
    client = _S3Client({})
    storage = _storage(client)
    saved = []

    async def checkpoint(intent):
        if intent["manifest_write_started"] is False:
            assert client.put_calls == client.preconditions == []
        else:
            assert intent["manifest_write_started"] is True
            assert {key for key, _ in client.put_calls} == {
                "_apps/app-1/live/entry-new.js", "_apps/app-1/live/chunk-new.js",
            }
            assert client.preconditions == [{"IfNoneMatch": "*"}] * 2
            assert "_apps/app-1/live/manifest.json" not in client.objects
        saved.append(intent)

    await storage.publish("app-1", bundle_files=_bundle(), checkpoint_callback=checkpoint)
    assert [intent["manifest_write_started"] for intent in saved] == [False, True]
    assert {key: value for key, value in saved[0].items() if key != "manifest_write_started"} == {
        key: value for key, value in saved[1].items() if key != "manifest_write_started"
    }
    assert client.client_contexts == [True]
    writes = list(client.put_calls)
    assert client.preconditions[-1] == {"IfNoneMatch": "*"}
    assert await storage.verify_publication("app-1", saved[-1]) == 3
    assert client.client_contexts == [True, False]
    assert client.put_calls == writes
    assert client.delete_calls == []


@pytest.mark.asyncio
async def test_checkpoint_failure_stops_before_any_output_or_manifest_write():
    client = _S3Client({})
    with pytest.raises(TimeoutError):
        await _storage(client).publish("app-1", bundle_files=_bundle(),
                                      checkpoint_callback=AsyncMock(side_effect=TimeoutError("checkpoint unavailable")))
    assert client.put_calls == client.preconditions == client.delete_calls == []


@pytest.mark.asyncio
async def test_late_publisher_cannot_replace_a_newer_live_manifest():
    client = _S3Client({"entry": "old.js", "outputs": ["old.js"]})
    later = json.dumps({"entry": "later.js", "outputs": ["later.js"]}).encode()

    async def replace_pointer(current, _total):
        if current == 1:
            client.objects["_apps/app-1/live/manifest.json"] = later

    with pytest.raises(ValueError, match="precondition"):
        await _storage(client).publish("app-1", bundle_files=_bundle(), progress_callback=replace_pointer)
    assert client.objects["_apps/app-1/live/manifest.json"] == later
    assert client.delete_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing-output", "changed-output", "changed-manifest", "cross-app", "unsafe-path"])
async def test_incomplete_or_changed_recovery_never_writes(damage):
    client = _S3Client({})
    storage = _storage(client)
    saved = []
    async def checkpoint(intent):
        saved.append(intent)
    await storage.publish("app-1", bundle_files=_bundle(), checkpoint_callback=checkpoint)
    intent = saved[-1]
    if damage == "missing-output":
        del client.objects["_apps/app-1/live/entry-new.js"]
    elif damage == "changed-output":
        client.objects["_apps/app-1/live/entry-new.js"] = b"changed"
    elif damage == "changed-manifest":
        client.objects["_apps/app-1/live/manifest.json"] = b"{}"
    elif damage == "cross-app":
        intent["application_id"] = "other"
    else:
        intent["artifact_hashes"]["../outside.js"] = "sha256:" + "a" * 64
    writes = list(client.put_calls)
    with pytest.raises((ValueError, KeyError)):
        await storage.verify_publication("app-1", intent)
    assert client.put_calls == writes
    assert client.delete_calls == []


@pytest.mark.asyncio
async def test_existing_output_with_different_bytes_is_not_overwritten():
    client = _S3Client({})
    key = "_apps/app-1/live/entry-new.js"
    client.objects[key] = b"old collision"
    with pytest.raises(ValueError, match="immutable storage"):
        await _storage(client).publish("app-1", bundle_files=_bundle())
    assert client.objects[key] == b"old collision"
    assert not any(path.endswith("manifest.json") for path, _ in client.put_calls)
