"""An App pin cannot be built from a green job or incomplete/stale bytes."""

import copy
import hashlib
import json
from uuid import UUID

import pytest
from bifrost.workspace_release import canonical_digest

from src.services.application_publication_evidence import app_publication_runtime_pin

APP = UUID(int=1)
JOB = UUID(int=2)
ORG = UUID(int=3)
HASH = "sha256:" + "a" * 64


def fixture():
    source = {"schema_version": "bifrost.inline-app-git-source/v1", "application_id": str(APP),
        "organization_id": str(ORG), "metadata_mode": "preserve_installed_controls",
        "source_commit_sha": "b" * 40, "source_tree_sha": "c" * 40, "source_subtree_sha": "d" * 40,
        "repository": "example/workspace", "repository_id": 5, "repository_owner_id": 6,
        "repo_subpath": "apps/fixture", "source_hashes": {"app.yaml": HASH, "page.tsx": HASH},
        "file_modes": {"app.yaml": "100644", "page.tsx": "100644"}}
    request = {"source_commit_sha": source["source_commit_sha"], "artifact_digest": canonical_digest(source),
        "ci_run_id": 7, "ci_run_attempt": 1, "producer_run_id": "8", "producer_run_attempt": 1,
        "expected_controls_hash": HASH}
    source.update({key: value for key, value in request.items() if key != "expected_controls_hash"})
    manifest = {"entry": "entry.js", "css": None, "outputs": ["entry.js"], "dependencies": {},
        "git_source_evidence": source, "build_evidence": {"schema_version": "bifrost.inline-app-build/v1",
            "materialized_source_hashes": {"page.tsx": HASH}, "output_hashes": {"entry.js": HASH}},
        "source_snapshot_evidence": {"schema_version": "bifrost.inline-app-source-snapshot/v1",
            "authored_source_hashes": {"app.yaml": HASH, "page.tsx": HASH},
            "compiler_source_hashes": {"page.tsx": HASH}, "migration_changed_paths": [],
            "metadata_not_applied": ["app.yaml"]}}
    return request, manifest


def pin(request, manifest, **changes):
    raw = json.dumps(manifest).encode()
    intent = {"application_id": str(APP), "controls_hash": HASH,
        "artifact_hashes": {"entry.js": HASH, "manifest.json": "sha256:" + hashlib.sha256(raw).hexdigest()}}
    return app_publication_runtime_pin(application_id=APP, publication_job_id=JOB, organization_id=ORG,
        intent=intent, manifest_bytes=raw, protected_git=request, **changes)


def test_exact_capture_has_a_stable_app_pin_without_solution_identity():
    request, manifest = fixture()
    actual = pin(request, manifest)
    assert actual == pin(request, copy.deepcopy(manifest))
    assert actual["application_id"] == str(APP) and actual["publication_job_id"] == str(JOB)
    assert not {"solution_id", "deployment_id"}.intersection(actual)
    assert actual["runtime_pin_hash"] == canonical_digest({k: v for k, v in actual.items() if k != "runtime_pin_hash"})


@pytest.mark.parametrize("fault", ["scope", "source", "artifact", "controls", "capture", "compiler", "migration",
    "output", "missing_output", "duplicate_output", "metadata_applied"])
def test_pin_rejects_wrong_source_scope_controls_or_incomplete_compile_evidence(fault):
    request, manifest = fixture()
    if fault == "scope":
        manifest["git_source_evidence"]["organization_id"] = None
    elif fault == "source":
        request["source_commit_sha"] = "e" * 40
    elif fault == "artifact":
        request["artifact_digest"] = "sha256:" + "e" * 64
    elif fault == "controls":
        request["expected_controls_hash"] = "sha256:" + "e" * 64
    elif fault == "capture":
        manifest["source_snapshot_evidence"]["authored_source_hashes"]["page.tsx"] = "sha256:" + "e" * 64
    elif fault == "compiler":
        manifest["source_snapshot_evidence"]["compiler_source_hashes"] = {"another.tsx": HASH}
    elif fault == "migration":
        manifest["source_snapshot_evidence"]["migration_changed_paths"] = ["unreviewed.tsx"]
    elif fault == "output":
        manifest["build_evidence"]["output_hashes"]["entry.js"] = "sha256:" + "e" * 64
    elif fault == "missing_output":
        manifest["outputs"] = []
    elif fault == "duplicate_output":
        manifest["outputs"].append("entry.js")
    else:
        manifest["source_snapshot_evidence"]["metadata_not_applied"] = []
    with pytest.raises(ValueError):
        pin(request, manifest)
