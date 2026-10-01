import importlib
import hashlib
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


def _module_cache_sync() -> Any:
    return cast(Any, importlib.import_module("src.core.module_cache_sync"))


@pytest.fixture(autouse=True)
def stable_workspace_generation(monkeypatch):
    monkeypatch.setattr(
        _module_cache_sync(),
        "workspace_generation_for_import",
        lambda: "generation-1",
    )


def test_s3_client_disabled_when_provider_is_azure_blob(monkeypatch):
    monkeypatch.setenv("BIFROST_OBJECT_STORAGE_PROVIDER", "azure_blob")
    monkeypatch.setenv("BIFROST_S3_ACCESS_KEY", "access")
    monkeypatch.setenv("BIFROST_S3_SECRET_KEY", "secret")

    module_cache_sync = _module_cache_sync()
    module_cache_sync._s3_available = None
    module_cache_sync._s3_client = None

    assert module_cache_sync._get_s3_client() is None


def test_object_storage_provider_prefers_blob_when_blob_configured(monkeypatch):
    monkeypatch.delenv("BIFROST_OBJECT_STORAGE_PROVIDER", raising=False)
    monkeypatch.setenv(
        "BIFROST_AZURE_BLOB_ACCOUNT_URL", "https://example.blob.core.windows.net"
    )
    monkeypatch.setenv("BIFROST_AZURE_BLOB_CONTAINER", "bifrost-objects")

    module_cache_sync = _module_cache_sync()

    assert module_cache_sync._object_storage_provider() == "azure_blob"


def test_get_engine_credentials_reads_credentials(monkeypatch):
    module_cache_sync = _module_cache_sync()

    monkeypatch.setattr(
        "bifrost.credentials.get_credentials",
        lambda: {
            "api_url": "https://bifrost.example.com/",
            "access_token": "token-1",
        },
    )

    assert module_cache_sync._get_engine_credentials() == (
        "https://bifrost.example.com",
        "token-1",
    )


def test_get_engine_credentials_returns_none_when_unavailable(monkeypatch):
    module_cache_sync = _module_cache_sync()

    def raise_missing():
        raise RuntimeError("no credentials")

    monkeypatch.setattr("bifrost.credentials.get_credentials", raise_missing)

    assert module_cache_sync._get_engine_credentials() is None


def test_fetch_module_from_api_handles_success_and_http_statuses(monkeypatch):
    module_cache_sync = _module_cache_sync()
    monkeypatch.setattr(
        module_cache_sync,
        "_get_engine_credentials",
        lambda: ("https://api.example", "token"),
    )
    calls: list[tuple[str, dict]] = []

    class Httpx:
        def __init__(self, status_code: int, body: dict | None = None):
            self.status_code = status_code
            self.body = body or {}

        def get(self, url, headers, **_kwargs):
            calls.append((url, headers))
            return SimpleNamespace(
                status_code=self.status_code,
                json=lambda: self.body,
            )

    monkeypatch.setattr(
        module_cache_sync,
        "_get_http_client",
        lambda: Httpx(
            200,
            {"content": "print(1)", "path": "modules/a.py", "hash": "h"},
        ),
    )

    assert module_cache_sync._fetch_module_from_api("modules/a.py") == {
        "content": "print(1)",
        "path": "modules/a.py",
        "hash": "h",
    }
    assert calls[-1] == (
        "https://api.example/api/sdk/modules/modules/a.py",
        {"Authorization": "Bearer token"},
    )

    monkeypatch.setattr(module_cache_sync, "_get_http_client", lambda: Httpx(404))
    assert module_cache_sync._fetch_module_from_api("missing.py") is None

    monkeypatch.setattr(module_cache_sync, "_get_http_client", lambda: Httpx(500))
    assert module_cache_sync._fetch_module_from_api("error.py") is None


def test_fetch_module_index_from_api_and_requirements(monkeypatch):
    module_cache_sync = _module_cache_sync()
    monkeypatch.setattr(
        module_cache_sync,
        "_get_engine_credentials",
        lambda: ("https://api.example", "token"),
    )

    class Httpx:
        def get(self, url, headers, **_kwargs):
            if url.endswith("/api/sdk/modules-index"):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"paths": ["modules/a.py", "workflows/b.py"]},
                )
            if url.endswith("/api/sdk/requirements"):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"content": "requests==2.32.0\n"},
                )
            raise AssertionError(url)

    monkeypatch.setattr(module_cache_sync, "_get_http_client", lambda: Httpx())

    assert module_cache_sync._fetch_module_index_from_api() == {
        "modules/a.py",
        "workflows/b.py",
    }
    assert module_cache_sync._fetch_requirements_from_api() == (
        True,
        "requests==2.32.0\n",
    )


def test_fetch_api_helpers_return_empty_on_missing_credentials(monkeypatch):
    module_cache_sync = _module_cache_sync()
    monkeypatch.setattr(module_cache_sync, "_get_engine_credentials", lambda: None)

    assert module_cache_sync._fetch_module_from_api("modules/a.py") is None
    assert module_cache_sync._fetch_module_index_from_api() == set()
    assert module_cache_sync._fetch_requirements_from_api() == (False, None)


def test_storage_path_to_object_key_handles_repo_and_solution_paths():
    module_cache_sync = _module_cache_sync()

    assert (
        module_cache_sync._storage_path_to_s3_key("modules/a.py")
        == "_repo/modules/a.py"
    )
    assert (
        module_cache_sync._storage_path_to_s3_key("_solutions/sol-1/modules/a.py")
        == "_solutions/sol-1/modules/a.py"
    )
    release_path = "_workspace_releases/org-1/release-1/files/modules/a.py"
    assert module_cache_sync._storage_path_to_s3_key(release_path) == release_path


def test_list_blob_modules_returns_python_paths(monkeypatch):
    module_cache_sync = _module_cache_sync()
    client = MagicMock()
    client.list_blobs.return_value = [
        SimpleNamespace(name="_repo/modules/a.py"),
        SimpleNamespace(name="_repo/modules/readme.md"),
        SimpleNamespace(name="_repo/workflows/b.py"),
    ]
    monkeypatch.setattr(module_cache_sync, "_get_blob_container_client", lambda: client)

    assert module_cache_sync._list_blob_modules() == {
        "modules/a.py",
        "workflows/b.py",
    }


def test_solution_has_submodules_uses_s3_when_solution_context_active(monkeypatch):
    module_cache_sync = _module_cache_sync()
    solution_id = uuid4()
    client = MagicMock()
    client.list_objects_v2.return_value = {"KeyCount": 1}

    monkeypatch.setenv("BIFROST_OBJECT_STORAGE_PROVIDER", "s3")
    monkeypatch.setenv("BIFROST_S3_BUCKET", "bucket")
    monkeypatch.setattr(module_cache_sync, "_get_s3_client", lambda: client)

    module_cache_sync.set_solution_context(solution_id, global_repo_access=False)
    try:
        assert module_cache_sync.solution_has_submodules("modules") is True
    finally:
        module_cache_sync.clear_solution_context()

    client.list_objects_v2.assert_called_once_with(
        Bucket="bucket",
        Prefix=f"_solutions/{solution_id}/modules/",
        MaxKeys=1,
    )


def test_solution_has_submodules_uses_blob_when_configured(monkeypatch):
    module_cache_sync = _module_cache_sync()
    solution_id = uuid4()
    client = MagicMock()
    client.list_blobs.return_value = iter([SimpleNamespace(name="x")])

    monkeypatch.setenv("BIFROST_OBJECT_STORAGE_PROVIDER", "azure_blob")
    monkeypatch.setattr(module_cache_sync, "_get_blob_container_client", lambda: client)

    module_cache_sync.set_solution_context(solution_id, global_repo_access=False)
    try:
        assert module_cache_sync.solution_has_submodules("modules") is True
    finally:
        module_cache_sync.clear_solution_context()

    client.list_blobs.assert_called_once_with(
        name_starts_with=f"_solutions/{solution_id}/modules/"
    )


def test_candidate_paths_respect_solution_context():
    module_cache_sync = _module_cache_sync()
    solution_id = uuid4()

    assert module_cache_sync.candidate_module_paths("modules/a.py") == ["modules/a.py"]
    assert module_cache_sync.candidate_index_prefixes("modules") == ["modules/"]

    module_cache_sync.set_solution_context(solution_id, global_repo_access=False)
    try:
        assert module_cache_sync.candidate_module_paths("modules/a.py") == [
            f"_solutions/{solution_id}/modules/a.py"
        ]
        assert module_cache_sync.candidate_index_prefixes("modules") == [
            f"_solutions/{solution_id}/modules/"
        ]
    finally:
        module_cache_sync.clear_solution_context()

    deployment_id = uuid4()
    module_cache_sync.set_solution_context(
        solution_id,
        global_repo_access=False,
        runtime_storage_prefix=f"_solutions/{solution_id}/{deployment_id}/",
    )
    try:
        assert module_cache_sync.candidate_module_paths("modules/a.py") == [
            f"_solutions/{solution_id}/{deployment_id}/modules/a.py"
        ]
    finally:
        module_cache_sync.clear_solution_context()

    module_cache_sync.set_solution_context(solution_id, global_repo_access=True)
    try:
        assert module_cache_sync.candidate_module_paths("/modules/a.py") == [
            f"_solutions/{solution_id}/modules/a.py",
            "/modules/a.py",
        ]
        assert module_cache_sync.candidate_index_prefixes("modules/") == [
            f"_solutions/{solution_id}/modules/",
            "modules/",
        ]
    finally:
        module_cache_sync.clear_solution_context()


def test_candidate_paths_are_fail_closed_to_workspace_release_manifest():
    module_cache_sync = _module_cache_sync()
    prefix = "_workspace_releases/org-1/release-1/files/"
    module_cache_sync.set_workspace_release_context(
        "sha256:" + "a" * 64,
        runtime_storage_prefix=prefix,
        source_hashes={"modules/a.py": "b" * 64},
    )
    try:
        assert module_cache_sync.candidate_module_paths("modules/a.py") == [
            f"{prefix}modules/a.py"
        ]
        assert module_cache_sync.candidate_index_prefixes("modules") == [
            f"{prefix}modules/"
        ]
        assert module_cache_sync.get_module_index_sync() == {
            f"{prefix}modules/a.py"
        }
    finally:
        module_cache_sync.clear_workspace_release_context()


def test_workspace_release_context_rejects_solution_overlap():
    module_cache_sync = _module_cache_sync()
    module_cache_sync.set_solution_context(uuid4(), global_repo_access=False)
    module_cache_sync.set_workspace_release_context(
        "sha256:" + "a" * 64,
        runtime_storage_prefix="_workspace_releases/org-1/release-1/files/",
        source_hashes={"modules/a.py": "b" * 64},
    )
    try:
        with pytest.raises(RuntimeError, match="cannot overlap"):
            module_cache_sync.candidate_module_paths("modules/a.py")
    finally:
        module_cache_sync.clear_workspace_release_context()
        module_cache_sync.clear_solution_context()


def test_workspace_release_cache_hit_must_match_manifest(monkeypatch):
    module_cache_sync = _module_cache_sync()
    content = "VALUE = 'wrong release'"
    redis_client = MagicMock()
    redis_client.get.return_value = json.dumps(
        {
            "content": content,
            "path": "modules/a.py",
            "hash": hashlib.sha256(content.encode()).hexdigest(),
        }
    )
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    module_cache_sync.set_workspace_release_context(
        "sha256:" + "a" * 64,
        runtime_storage_prefix="_workspace_releases/org-1/release-1/files/",
        source_hashes={"modules/a.py": "b" * 64},
    )
    try:
        with pytest.raises(RuntimeError, match="import integrity mismatch"):
            module_cache_sync.get_module_sync("modules/a.py")
    finally:
        module_cache_sync.clear_workspace_release_context()


def test_workspace_release_corrupt_cache_label_is_deleted_and_refetched(monkeypatch):
    module_cache_sync = _module_cache_sync()
    reviewed = "VALUE = 'reviewed'\n"
    reviewed_hash = hashlib.sha256(reviewed.encode()).hexdigest()
    storage_path = (
        "_workspace_releases/org-1/release-1/files/modules/a.py"
    )
    redis_client = MagicMock()
    redis_client.get.return_value = json.dumps(
        {
            "content": "VALUE = 'corrupt'\n",
            "path": "modules/a.py",
            # The label alone is not evidence that these are reviewed bytes.
            "hash": reviewed_hash,
        }
    )
    fetch_api = MagicMock(
        return_value={
            "content": reviewed,
            "path": "modules/a.py",
            "hash": reviewed_hash,
        }
    )
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(module_cache_sync, "_fetch_module_from_api", fetch_api)
    module_cache_sync.set_workspace_release_context(
        "sha256:" + "a" * 64,
        runtime_storage_prefix="_workspace_releases/org-1/release-1/files/",
        source_hashes={"modules/a.py": reviewed_hash},
    )
    try:
        result = module_cache_sync.get_module_sync("modules/a.py")
    finally:
        module_cache_sync.clear_workspace_release_context()

    assert result == {
        "content": reviewed,
        "path": "modules/a.py",
        "hash": reviewed_hash,
    }
    redis_client.delete.assert_called_once_with(
        f"{module_cache_sync.MODULE_KEY_PREFIX}{storage_path}"
    )
    fetch_api.assert_called_once_with(storage_path)
    redis_client.setex.assert_called_once()


def test_workspace_release_generation_is_not_mutable_repo_generation(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.get.return_value = "repo-generation-that-must-not-win"
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    release_id = "sha256:" + "a" * 64
    module_cache_sync.set_workspace_release_context(
        release_id,
        runtime_storage_prefix="_workspace_releases/org-1/release-1/files/",
        source_hashes={"modules/a.py": "b" * 64},
    )
    try:
        assert module_cache_sync.get_workspace_generation_sync() == release_id
        assert module_cache_sync.assert_workspace_generation(release_id) == release_id
        with pytest.raises(
            module_cache_sync.WorkspaceGenerationChangedError,
            match="stale import closure was not executed",
        ):
            module_cache_sync.assert_workspace_generation(
                "sha256:" + "c" * 64
            )
        redis_client.get.assert_not_called()
    finally:
        module_cache_sync.clear_workspace_release_context()


def test_get_module_sync_returns_redis_hit_without_fallbacks(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    content = "print(1)"
    module = {
        "content": content,
        "path": "modules/a.py",
        "hash": hashlib.sha256(content.encode()).hexdigest(),
        "generation": "generation-1",
    }
    redis_client.get.return_value = json.dumps(module)
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(
        module_cache_sync,
        "_fetch_module_from_api",
        MagicMock(side_effect=AssertionError("should not fetch API")),
    )

    assert module_cache_sync.get_module_sync("modules/a.py") == module
    redis_client.get.assert_called_once_with(
        f"{module_cache_sync.MODULE_KEY_PREFIX}modules/a.py"
    )


def test_get_module_sync_recaches_api_fallback(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.get.return_value = None
    content = "print(2)"
    module = {
        "content": content,
        "path": "modules/a.py",
        "hash": hashlib.sha256(content.encode()).hexdigest(),
        "generation": "generation-1",
    }
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(
        module_cache_sync, "_fetch_module_from_api", lambda path: module
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_object_storage_module",
        MagicMock(side_effect=AssertionError("API hit should stop fallback")),
    )

    assert module_cache_sync.get_module_sync("modules/a.py") == module
    redis_client.setex.assert_called_once_with(
        f"{module_cache_sync.MODULE_KEY_PREFIX}modules/a.py",
        module_cache_sync.MODULE_CACHE_TTL,
        json.dumps(module),
    )
    redis_client.sadd.assert_called_once_with(
        module_cache_sync.MODULE_INDEX_KEY,
        "modules/a.py",
    )


def test_get_module_sync_builds_module_from_object_storage(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.get.return_value = None
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(module_cache_sync, "_fetch_module_from_api", lambda path: None)
    monkeypatch.setattr(
        module_cache_sync,
        "_get_object_storage_module",
        lambda path: b"print('storage')",
    )

    result = module_cache_sync.get_module_sync("modules/a.py")

    assert result["content"] == "print('storage')"
    assert result["path"] == "modules/a.py"
    assert len(result["hash"]) == 64
    redis_client.setex.assert_called_once()
    redis_client.sadd.assert_called_once_with(
        module_cache_sync.MODULE_INDEX_KEY,
        "modules/a.py",
    )


def test_deployment_import_rejects_manifest_hash_mismatch(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.get.return_value = json.dumps(
        {"content": "tampered", "path": "modules/a.py", "hash": "bad"}
    )
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    module_cache_sync.set_solution_context(
        "solution",
        False,
        runtime_storage_prefix="_solutions/solution/deployment/",
        source_hashes={"modules/a.py": "sha256:expected"},
    )
    try:
        with pytest.raises(RuntimeError, match="import integrity mismatch"):
            module_cache_sync.get_module_sync("modules/a.py")
    finally:
        module_cache_sync.clear_solution_context()


def test_deployment_namespace_comes_from_pinned_source_manifest_not_mutable_install_root(
    monkeypatch,
):
    module_cache_sync = _module_cache_sync()
    solution_id = str(uuid4())
    deployment_id = str(uuid4())
    module_cache_sync.set_solution_context(
        solution_id,
        False,
        runtime_storage_prefix=f"_solutions/{solution_id}/{deployment_id}/",
        source_hashes={"modules/cipp.py": "sha256:" + "a" * 64},
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_cached_module_resolution",
        lambda _name: pytest.fail("mutable cached namespace must not shadow pinned sources"),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_exact_scoped_module",
        lambda _name: pytest.fail("mutable install-root lookup must not resolve a pinned namespace"),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_fetch_module_resolution_from_api",
        lambda _name: pytest.fail("mutable API resolver must not resolve a pinned namespace"),
    )
    try:
        result = module_cache_sync.resolve_module_sync("modules")
        assert result.kind == "namespace"
        assert result.path == "modules"
    finally:
        module_cache_sync.clear_solution_context()


@pytest.mark.parametrize(
    ("source_path", "import_name", "kind"),
    [
        ("modules/cipp.py", "modules.cipp", "module"),
        ("modules/cipp/__init__.py", "modules.cipp", "package"),
    ],
)
def test_deployment_concrete_import_uses_pinned_bytes_and_ignores_mutable_shadow(
    monkeypatch, source_path, import_name, kind
):
    module_cache_sync = _module_cache_sync()
    solution_id = str(uuid4())
    deployment_id = str(uuid4())
    content = "VALUE = 'pinned deployment source'\n"
    expected_hash = hashlib.sha256(content.encode()).hexdigest()
    pinned_prefix = f"_solutions/{solution_id}/{deployment_id}/"
    module_cache_sync.set_solution_context(
        solution_id,
        False,
        runtime_storage_prefix=pinned_prefix,
        source_hashes={source_path: "sha256:" + expected_hash},
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_cached_module_resolution",
        lambda _name: pytest.fail("mutable resolution cache must not shadow pinned source"),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_exact_scoped_module",
        lambda _name: pytest.fail("mutable install-root module must not shadow pinned source"),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_fetch_module_resolution_from_api",
        lambda _name: pytest.fail("mutable API module must not shadow pinned source"),
    )
    fetched_paths = []

    def fetch(path):
        fetched_paths.append(path)
        return {"content": content, "path": path, "hash": expected_hash}

    monkeypatch.setattr(module_cache_sync, "get_module_sync", fetch)
    try:
        result = module_cache_sync.resolve_module_sync(import_name)
        assert result.kind == kind
        assert result.content == content
        assert result.hash == expected_hash
        assert result.storage_path == pinned_prefix + source_path
        assert fetched_paths == [source_path]
    finally:
        module_cache_sync.clear_solution_context()


@pytest.mark.parametrize(
    ("content", "module_hash"),
    [
        ("VALUE = 'tampered'\n", "a" * 64),
        ("VALUE = 'pinned'\n", "b" * 64),
    ],
)
def test_deployment_concrete_import_rejects_wrongly_labelled_pinned_bytes(
    monkeypatch, content, module_hash
):
    module_cache_sync = _module_cache_sync()
    solution_id = str(uuid4())
    deployment_id = str(uuid4())
    source_path = "modules/cipp.py"
    expected_content = "VALUE = 'pinned'\n"
    expected_hash = hashlib.sha256(expected_content.encode()).hexdigest()
    module_cache_sync.set_solution_context(
        solution_id,
        False,
        runtime_storage_prefix=f"_solutions/{solution_id}/{deployment_id}/",
        source_hashes={source_path: "sha256:" + expected_hash},
    )
    monkeypatch.setattr(
        module_cache_sync,
        "get_module_sync",
        lambda path: {"content": content, "path": path, "hash": module_hash},
    )
    try:
        with pytest.raises(RuntimeError, match="import integrity mismatch"):
            module_cache_sync.resolve_module_sync("modules.cipp")
    finally:
        module_cache_sync.clear_solution_context()


def test_deployment_source_manifest_absence_does_not_fall_back_to_mutable_modules(
    monkeypatch,
):
    module_cache_sync = _module_cache_sync()
    solution_id = str(uuid4())
    deployment_id = str(uuid4())
    module_cache_sync.set_solution_context(
        solution_id,
        False,
        runtime_storage_prefix=f"_solutions/{solution_id}/{deployment_id}/",
        source_hashes={},
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_cached_module_resolution",
        lambda _name: pytest.fail("mutable cached sources must not satisfy absent pin"),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_exact_scoped_module",
        lambda _name: pytest.fail("mutable install sources must not satisfy absent pin"),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_fetch_module_resolution_from_api",
        lambda _name: pytest.fail("mutable API sources must not satisfy absent pin"),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "get_module_sync",
        lambda _path: pytest.fail("unlisted source must not be fetched from mutable storage"),
    )
    try:
        result = module_cache_sync.resolve_module_sync("modules.cipp")
        assert result.kind == "not_found"
        assert result.path == "modules/cipp"
    finally:
        module_cache_sync.clear_solution_context()


def test_deployment_missing_source_manifest_fails_closed(monkeypatch):
    module_cache_sync = _module_cache_sync()
    solution_id = str(uuid4())
    deployment_id = str(uuid4())
    module_cache_sync.set_solution_context(
        solution_id,
        False,
        runtime_storage_prefix=f"_solutions/{solution_id}/{deployment_id}/",
        source_hashes=None,
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_cached_module_resolution",
        lambda _name: pytest.fail("missing pin must not use mutable cache"),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_exact_scoped_module",
        lambda _name: pytest.fail("missing pin must not use mutable install source"),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_fetch_module_resolution_from_api",
        lambda _name: pytest.fail("missing pin must not use mutable API source"),
    )
    try:
        with pytest.raises(
            module_cache_sync.ModuleResolutionError,
            match="Immutable deployment source manifest unavailable",
        ):
            module_cache_sync.resolve_module_sync("modules.cipp")
    finally:
        module_cache_sync.clear_solution_context()


def test_get_module_index_sync_repopulates_from_api_then_storage(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.smembers.return_value = set()
    redis_client.get.return_value = None
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(
        module_cache_sync,
        "_fetch_module_index_from_api",
        lambda: {"modules/a.py", "workflows/b.py"},
    )

    assert module_cache_sync.get_module_index_sync() == {
        "modules/a.py",
        "workflows/b.py",
    }
    redis_client.sadd.assert_called_once()
    redis_client.expire.assert_called_once_with(
        module_cache_sync.MODULE_INDEX_KEY,
        module_cache_sync.MODULE_CACHE_TTL,
    )

    redis_client = MagicMock()
    redis_client.smembers.return_value = set()
    redis_client.get.return_value = None
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(module_cache_sync, "_fetch_module_index_from_api", set)
    monkeypatch.setattr(
        module_cache_sync,
        "_list_object_storage_modules",
        lambda: {"modules/storage.py"},
    )

    assert module_cache_sync.get_module_index_sync() == {"modules/storage.py"}
    redis_client.sadd.assert_called_once_with(
        module_cache_sync.MODULE_INDEX_KEY,
        "modules/storage.py",
    )


def test_get_module_index_sync_accepts_current_byte_generation(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.get.return_value = b"generation-1"
    redis_client.smembers.return_value = {b"modules/current.py"}
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)

    assert module_cache_sync.get_module_index_sync() == {"modules/current.py"}
