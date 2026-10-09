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


def supersession_fixture():
    from dataclasses import replace

    old, record, targets, installations = fixture()
    current = replace(authored(**{"README.md": b"Current reviewed instructions\n"}),
        commit_sha="d" * 40, tree_sha="e" * 40)
    history = {"commit_sha": old.commit_sha, "tree_sha": old.tree_sha,
        "subtree_sha": old.subtree_sha, "source_content_id": old.source_content_id,
        "solution_slug": old.solution_slug, "repo_subpath": old.repo_subpath,
        "source_files": old.file_manifest()}
    for proof in installations.values():
        proof.update(source_commit_sha=current.commit_sha, source_tree_sha=current.tree_sha,
            ancestor_commit_shas=[old.commit_sha], historical_authored_source=deepcopy(history))
        proof["readback"]["source_content_id"] = current.source_content_id
        proof["readback"]["evidence_id"] = canonical_digest({k: v for k, v in proof["readback"].items() if k != "evidence_id"})
        proof["authored_source"]["source_content_id"] = current.source_content_id
    return current, record, targets, installations


def test_verified_descendant_supersedes_old_full_inventory_without_claiming_old_release():
    from src.services.solutions.native_authored_accounting import native_supersession_evidence

    current, record, targets, installations = supersession_fixture()
    result = native_supersession_evidence(record, current, target_installs=targets,
        installations=installations, verified_at=datetime.now(UTC))
    assert result["schema_version"] == "bifrost.native-solution-deploy-supersession/v1"
    assert result["superseded_source_commit_sha"] == record.source_commit_sha
    assert result["superseded_source_files"] == record.source_files
    assert result["source_commit_sha"] == current.commit_sha
    assert result["source_content_id"] != record.source_content_id
    assert set(result["installations"]) == set(targets)
    assert result["evidence_id"] == canonical_digest({k: v for k, v in result.items() if k != "evidence_id"})
    with pytest.raises(NativeAuthoredSourceMismatch):
        native_completion_evidence(record, current, target_installs=targets,
            installations=installations, verified_at=datetime.now(UTC))


@pytest.mark.parametrize("fault", ["ancestry", "archive", "old_manifest", "old_tree", "old_content", "old_package",
    "current_package", "missing_target", "wrong_scope", "current_archive", "current_readback"])
def test_native_supersession_keeps_incomplete_or_ambiguous_historical_proof_open(fault):
    from dataclasses import replace
    from src.services.solutions.native_authored_accounting import native_supersession_evidence

    current, record, targets, installations = supersession_fixture()
    proof = next(iter(installations.values()))
    if fault == "ancestry":
        proof["ancestor_commit_shas"] = []
    elif fault == "archive":
        proof["historical_authored_source"] = None
    elif fault == "old_manifest":
        proof["historical_authored_source"]["source_files"].pop()
    elif fault == "old_tree":
        proof["historical_authored_source"]["tree_sha"] = "f" * 40
    elif fault == "old_content":
        proof["historical_authored_source"]["source_content_id"] = "sha256:" + "f" * 64
    elif fault == "old_package":
        proof["historical_authored_source"]["repo_subpath"] = "solutions/other"
    elif fault == "current_package":
        current = replace(current, solution_slug="other", repo_subpath="solutions/other")
    elif fault == "missing_target":
        installations.pop(next(reversed(installations)))
    elif fault == "wrong_scope":
        proof["readback"]["organization_id"] = str(uuid4())
    elif fault == "current_archive":
        proof["authored_source"]["archive_sha256"] = "e" * 64
    else:
        proof["readback"]["source_content_id"] = record.source_content_id
    with pytest.raises(NativeAuthoredSourceMismatch):
        native_supersession_evidence(record, current, target_installs=targets,
            installations=installations, verified_at=datetime.now(UTC))


@pytest.mark.parametrize("fault", [None, "missing", "other_parent", "manual", "other_org", "other_commit", "other_tree"])
def test_historical_native_origin_is_the_exact_original_producer_row(fault):
    from src.services.solutions.native_authored_accounting import _require_history_origin

    _, record, _, _ = fixture()
    record.source_release_id, record.organization_id = uuid4(), uuid4()
    origin = SimpleNamespace(id=record.source_release_id, organization_id=record.organization_id,
        source_commit_sha=record.source_commit_sha, source_tree_sha=record.source_tree_sha,
        declaration_actor="github_actions_oidc")
    if fault == "missing":
        origin = None
    elif fault == "other_parent":
        origin.id = uuid4()
    elif fault == "manual":
        origin.declaration_actor = "platform_admin"
    elif fault == "other_org":
        origin.organization_id = uuid4()
    elif fault == "other_commit":
        origin.source_commit_sha = "e" * 40
    elif fault == "other_tree":
        origin.source_tree_sha = "e" * 40
    if fault is None:
        _require_history_origin(record, origin)
    else:
        with pytest.raises(NativeAuthoredSourceMismatch):
            _require_history_origin(record, origin)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "git_unavailable", "timeout", "wrong_manifest"])
async def test_unavailable_historical_input_never_certifies_history_or_stops_current_source(fault):
    from unittest.mock import AsyncMock
    import httpx
    from src.services.solutions.github_delivery_source import GitDeliverySourceError
    from src.services.solutions.github_source_delivery import _historical_authored_source

    source, record, _, _ = fixture()
    reader = SimpleNamespace(authored_source=AsyncMock(return_value=source))
    if fault == "git_unavailable":
        reader.authored_source.side_effect = GitDeliverySourceError("Protected Git read returned HTTP 503")
    elif fault == "timeout":
        reader.authored_source.side_effect = httpx.ReadTimeout("Old subtree read timed out")
    elif fault == "wrong_manifest":
        record.source_files = []
    result = await _historical_authored_source(reader, record)
    assert result == (source if fault is None else None)
