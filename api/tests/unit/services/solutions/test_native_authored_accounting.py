"""One runtime cannot settle a complete multi-install authored declaration."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bifrost.workspace_release import canonical_digest
from src.services.solutions.native_authored_accounting import (
    _cached_native_evidence_size,
    intended_native_targets,
    native_completion_evidence,
)
from src.services.solutions.native_authored_source import NativeAuthoredSourceMismatch
from tests.unit.services.solutions.test_native_authored_source import authored


def test_multi_install_cache_budget_counts_resource_path_and_bytes():
    from types import MappingProxyType
    from src.services.solutions.native_authored_source import _VerifiedNativeRuntime

    proof = {"receipt": "a" * 36}
    runtime = _VerifiedNativeRuntime(uuid4(), "manifest", "resolution", "archive",
        MappingProxyType({"flow.py": b"x"}), MappingProxyType({"settings.json": b"resource-bytes"}))
    source = SimpleNamespace(files={"flow.py": b"x"})
    size_with_resource = _cached_native_evidence_size(source, proof, runtime)
    runtime_without_resource = _VerifiedNativeRuntime(runtime.deployment_id, runtime.manifest_hash,
        runtime.resolution_hash, runtime.archive_sha256, runtime.files, MappingProxyType({}))
    size_without_resource = _cached_native_evidence_size(source, proof, runtime_without_resource)
    assert size_with_resource - size_without_resource == len("settings.json") + len(b"resource-bytes")
    # One package fits; adding the second package's resource proof exceeds the
    # family-wide budget, so the complete multi-install readback cannot be cached.
    limit = size_without_resource + size_with_resource - 1
    assert size_without_resource <= limit
    assert size_without_resource + size_with_resource > limit
    authored_source, record, targets, installations = fixture()
    first = next(iter(targets))
    with pytest.raises(NativeAuthoredSourceMismatch, match="Every intended installation"):
        native_completion_evidence(record, authored_source, target_installs=targets,
            installations={first: installations[first]}, verified_at=datetime.now(UTC))


def fixture():
    source = authored()
    record = SimpleNamespace(source_commit_sha=source.commit_sha, source_tree_sha=source.tree_sha,
        source_subtree_sha=source.subtree_sha, source_content_id=source.source_content_id,
        solution_slug=source.solution_slug, repo_subpath=source.repo_subpath, source_files=source.file_manifest())
    global_id, provider_id, scope = str(uuid4()), str(uuid4()), str(uuid4())
    targets = {global_id: None, provider_id: scope}
    installations = {}
    for identity, organization in targets.items():
        readback = {"schema_version": "bifrost.native-solution-authored-readback/v1",
            "solution_id": identity, "organization_id": organization,
            "deployment_id": str(uuid4()), "source_content_id": source.source_content_id,
            "runtime_paths": ["functions/probe.py", "modules/runtime.py"],
            "omitted_empty_initializers": ["shared/__init__.py", "shared/microsoft/__init__.py"]}
        readback["evidence_id"] = canonical_digest(readback)
        installations[identity] = {"readback": readback, "source_commit_sha": source.commit_sha,
            "source_tree_sha": source.tree_sha, "receipt_id": str(uuid4()),
            "authored_source": {"source_content_id": source.source_content_id, "archive_sha256": "f" * 64}}
    return source, record, targets, installations


def test_protected_targets_retain_missing_provider_and_mixed_source_consumers():
    global_id, provider_id, unrelated_id = str(uuid4()), str(uuid4()), str(uuid4())
    scope = str(uuid4())
    registry = {"target": "production", "installations": {
        global_id: {"organization_id": None, "repo_subpath": "solutions/fixture", "package_subpaths": ["solutions/fixture"]},
        provider_id: {"organization_id": scope, "repo_subpath": None, "package_subpaths": ["solutions/fixture"]},
        unrelated_id: {"organization_id": scope, "repo_subpath": "solutions/other", "package_subpaths": ["solutions/other"]}}}
    assert intended_native_targets(registry, "solutions/fixture") == {global_id: None, provider_id: scope}
    # Membership has no DB-active input: removing a DB row cannot remove its
    # intent, and mixed Root/native mappings remain blockers for this package.
    registry["installations"][provider_id].pop("package_subpaths")
    with pytest.raises(NativeAuthoredSourceMismatch):
        intended_native_targets(registry, "solutions/fixture")


def test_native_completion_retains_all_authored_files_and_each_scope_without_fake_deploy_job():
    source, record, targets, installations = fixture()
    result = native_completion_evidence(record, source, target_installs=targets,
        installations=installations, verified_at=datetime.now(UTC))
    assert result["target_installs"] == targets
    assert set(result["installations"]) == set(targets)
    assert result["source_content_id"] == record.source_content_id
    assert len(record.source_files) == 8
    assert all(len(p["readback"]["runtime_paths"]) == 2 for p in result["installations"].values())
    assert "deploy_job_id" not in result


@pytest.mark.parametrize("fault", ["missing_provider", "extra_install", "wrong_scope", "wrong_runtime",
    "wrong_receipt", "archive_disagreement", "omitted_readme", "wrong_commit", "wrong_tree", "wrong_mode"])
def test_incomplete_or_different_evidence_does_not_settle_source(fault):
    source, record, targets, installations = fixture()
    identity = next(iter(installations))
    if fault == "missing_provider":
        installations.pop(next(reversed(installations)))
    elif fault == "extra_install":
        installations[str(uuid4())] = deepcopy(installations[identity])
    elif fault == "wrong_scope":
        installations[identity]["readback"]["organization_id"] = str(uuid4())
    elif fault == "wrong_runtime":
        installations[identity]["readback"]["deployment_id"] = str(uuid4())
    elif fault == "wrong_receipt":
        installations[identity]["receipt_id"] = ""
    elif fault == "archive_disagreement":
        installations[identity]["authored_source"]["archive_sha256"] = "e" * 64
    elif fault == "omitted_readme":
        record.source_files = [f for f in record.source_files if not f["path"].endswith("/README.md")]
    elif fault == "wrong_commit":
        record.source_commit_sha = "f" * 40
    elif fault == "wrong_tree":
        record.source_tree_sha = "f" * 40
    else:
        record.source_files = deepcopy(record.source_files)
        record.source_files[0]["mode"] = "100755"
    with pytest.raises(NativeAuthoredSourceMismatch):
        native_completion_evidence(record, source, target_installs=targets,
            installations=installations, verified_at=datetime.now(UTC))
