"""Checks for the data grants that gate a reviewed Root file adapter."""
import pytest
from pydantic import ValidationError

from bifrost.root_file_bindings import (
    BACKUP_PREFIX,
    RootFileBinding,
    require_root_file_bindings,
)


def binding(**changes):
    return RootFileBinding.model_validate({
        "location": "workspace",
        "path": "features/ninjaone/scripts/CiscoSecureClient.Migration.ps1",
        "operations": ["exists", "read"],
        "max_bytes": 65536,
        "expected_read_sha256": "sha256:" + "a" * 64,
        **changes,
    })


@pytest.mark.parametrize("path", [
    "../secret.json", "features/../secret.json", "features//data.json",
    "/features/data.json", "features\\data.json", "features/%2e/data.json",
    " features/data.json", "features/data.json\n", "C:/features/data.json",
    "features/.env", "features/.bifrost/auth.json", "features/code.py",
    ".bifrost/credentials.json", "features/data.json\x00",
])
def test_cannot_grant_unsafe_or_executable_workspace_path(path):
    with pytest.raises(ValidationError):
        binding(path=path)


@pytest.mark.parametrize("changes", [
    {"directory_prefix": True}, {"max_bytes": 0}, {"max_bytes": -1},
    {"max_bytes": 128 * 1024 * 1024 + 1}, {"max_bytes": True},
    {"max_url_ttl_seconds": 601}, {"max_url_ttl_seconds": 0},
    {"operations": ["read", "read"]}, {"operations": ["delete"]},
    {"operations": ["overwrite"]}, {"operations": ["signed_put"]},
    {"operations": []}, {"scope": "another-organization"},
    {"location": "custom"}, {"expected_read_sha256": None},
])
def test_invalid_grants_fail(changes):
    with pytest.raises(ValidationError):
        binding(**changes)


def test_workspace_read_is_exact_and_does_not_grant_signing():
    grant = binding()
    assert grant.permits(grant.path, "read")
    assert grant.permits(grant.path, "exists")
    assert not grant.permits(grant.path, "signed_get")
    assert not grant.permits(grant.path + ".other", "read")
    with pytest.raises(ValueError):
        grant.permits("features/../secret.json", "read")
    with pytest.raises(ValidationError):
        grant.max_bytes = 999999
    assert isinstance(grant.operations, tuple)


def test_workspace_download_requires_hash_check_before_signing():
    grant = binding(
        path="features/cisco_secure_client/assets/Midtown-WCM.xml",
        operations=["exists", "signed_get"],
    )
    assert grant.permits(grant.path, "signed_get")
    with pytest.raises(ValidationError):
        binding(operations=["signed_get"], expected_read_sha256=None)
    with pytest.raises(ValidationError):
        binding(operations=["exists"])


def test_mutable_json_data_can_be_read_without_freezing_its_contents():
    grant = binding(path="features/bsn/data/clients.json", expected_read_sha256=None)
    assert grant.permits(grant.path, "read")
    assert grant.expected_read_sha256 is None
    assert grant.max_bytes == 65536
    pinned = binding(path=grant.path)
    assert pinned.expected_read_sha256 == "sha256:" + "a" * 64


def test_upload_prefix_does_not_match_sibling_or_hidden_path():
    grant = binding(
        location="uploads", path="cisco-secure-client/packages",
        directory_prefix=True, operations=["exists", "signed_get"],
        expected_read_sha256=None,
    )
    assert grant.permits("cisco-secure-client/packages/installer.zip", "signed_get")
    assert not grant.permits("cisco-secure-client/packages-old/installer.zip", "signed_get")
    assert not grant.permits("cisco-secure-client/packages/.credentials.json", "signed_get")
    assert not grant.permits("cisco-secure-client/packages/installer.zip", "read")


@pytest.mark.parametrize("size", [135419864, 256 * 1024 * 1024])
def test_large_upload_download_is_bounded_without_granting_raw_reads(size):
    grant = binding(
        location="uploads", path="cisco-secure-client/packages",
        directory_prefix=True, operations=["exists", "signed_get"],
        expected_read_sha256=None, max_bytes=size,
    )
    assert grant.permits("cisco-secure-client/packages/installer.zip", "signed_get")
    assert not grant.permits("cisco-secure-client/packages/installer.zip", "read")
    assert not grant.permits("other-packages/installer.zip", "signed_get")


@pytest.mark.parametrize("operations,size", [
    (["exists", "signed_get"], 256 * 1024 * 1024 + 1),
    (["read"], 128 * 1024 * 1024 + 1),
    (["read", "signed_get"], 128 * 1024 * 1024 + 1),
    (["exists"], 128 * 1024 * 1024 + 1),
])
def test_large_grant_cannot_expand_into_raw_reads_or_unbounded_downloads(operations, size):
    with pytest.raises(ValidationError):
        binding(location="uploads", path="packages/installer.zip",
                operations=operations, expected_read_sha256=None, max_bytes=size)


def backup_binding(**changes):
    return RootFileBinding.model_validate({
        "location": "workspace", "path": BACKUP_PREFIX,
        "directory_prefix": True, "operations": ["create"],
        "max_bytes": 65536, **changes,
    })


def test_backup_is_create_only_and_cannot_expose_credentials():
    grant = backup_binding()
    path = BACKUP_PREFIX + "/20260930T235900Z-" + "a" * 12 + ".json"
    assert grant.permits(path, "create")
    for action in ["read", "signed_get", "exists"]:
        assert not grant.permits(path, action)
    for filename in ["secrets.json", "20260930T235900Z-abcd.json", "nested/file.json"]:
        assert not grant.permits(BACKUP_PREFIX + "/" + filename, "create")


@pytest.mark.parametrize("changes", [
    {"path": ".artifacts/other"}, {"location": "uploads"},
    {"max_bytes": 65537}, {"directory_prefix": False},
    {"operations": ["create", "read"]},
    {"operations": ["create", "signed_get"]},
])
def test_backup_grant_cannot_expand(changes):
    with pytest.raises(ValidationError):
        backup_binding(**changes)


def test_ambiguous_selectors_fail_before_artifact_creation():
    grant = binding()
    with pytest.raises(ValueError):
        require_root_file_bindings({"one": grant, "two": grant})
    with pytest.raises(ValueError):
        require_root_file_bindings({"Caller_Chosen/Name": grant})
    prefix = binding(location="uploads", path="packages", directory_prefix=True,
                     operations=["signed_get"], expected_read_sha256=None)
    child = binding(location="uploads", path="packages/one.zip", operations=["signed_get"],
                    expected_read_sha256=None)
    with pytest.raises(ValueError):
        require_root_file_bindings({"all_packages": prefix, "one_package": child})
