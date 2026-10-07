"""An App ledger anchor needs exact Git metadata and original published bytes."""

import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from typing import Any
from uuid import UUID

import httpx
import pytest

from bifrost.workspace_release import canonical_digest
from src.core.application_delivery_policy import InlineAppGitDeliveryPolicy
from src.core.constants import SYSTEM_USER_UUID
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.models.contracts.applications import ApplicationGitSourcePublicationRequest
from src.services import application_source_accountability as accounting
from src.services.solutions.github_delivery_source import GitDeliveryIdentity, ProtectedGitReader

APP, JOB, SID, ORG = (UUID(int=value) for value in (1, 2, 3, 4))
SHA, TREE, HASH = "a" * 40, "b" * 40, "sha256:" + "c" * 64
RECIPE = "config/solution-delivery/fixture.json"
APP_RECIPE = "config/app-delivery/fixture.json"


def policies() -> tuple[SolutionGitDeliveryPolicy, InlineAppGitDeliveryPolicy]:
    trust: dict[str, Any] = {"repository": "MTG-Thomas/bifrost-workspace", "repository_id": 1197464564,
        "repository_owner_id": 87775189, "organization_id": ORG,
        "workflow_path": ".github/workflows/deliver-solutions.yml",
        "ci_workflow_path": ".github/workflows/ci.yml", "ci_workflow_id": 257449914}
    return (SolutionGitDeliveryPolicy(**trust, solutions={SID: RECIPE}),
        InlineAppGitDeliveryPolicy(**trust, applications={APP: {
            "organization_id": ORG, "repo_subpath": "apps/fixture"}}))


def source() -> dict[str, Any]:
    return {"application_id": str(APP), "organization_id": str(ORG),
        "repository": "MTG-Thomas/bifrost-workspace", "repository_id": 1197464564,
        "repository_owner_id": 87775189, "source_commit_sha": SHA, "source_tree_sha": TREE,
        "repo_subpath": "apps/fixture", "artifact_digest": HASH}


def metadata_files() -> dict[str, dict[str, Any]]:
    return {RECIPE: {"schema_version": "bifrost.solution-source-delivery/v1", "solution_id": str(SID),
            "files": {"entry.py": "features/entry.py"}},
        APP_RECIPE: {"schema_version": accounting.APP_RECIPE_SCHEMA, "application_id": str(APP),
            "organization_id": str(ORG), "repo_subpath": "apps/fixture"},
        accounting.REGISTRY_PATH: {"schema_version": "bifrost.package-delivery-installations/v1",
            "installations": [{"kind": "solution", "target": "production", "recipe": RECIPE},
                {"kind": "inline_app", "target": "production", "recipe": APP_RECIPE},
                {"kind": "solution_package", "target": "production",
                    "recipe": "config/solution-package-delivery/fixture.json"}]}}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "tree", "truncated", "duplicate_path", "symlink", "wrong_scope",
    "wrong_root", "duplicate_app", "missing_app", "wrong_solution", "wrong_producer", "wrong_repo"])
async def test_accounting_reads_exact_historical_tree_and_complete_target_registry(fault):
    policy, app_policy = policies()
    authored = source()
    files = metadata_files()
    if fault == "wrong_scope":
        files[APP_RECIPE]["organization_id"] = None
    elif fault == "wrong_root":
        files[APP_RECIPE]["repo_subpath"] = "apps/other"
    elif fault == "duplicate_app":
        files["config/app-delivery/second.json"] = files[APP_RECIPE]
        files[accounting.REGISTRY_PATH]["installations"].append({
            "kind": "inline_app", "target": "production", "recipe": "config/app-delivery/second.json"})
    elif fault == "missing_app":
        files[accounting.REGISTRY_PATH]["installations"] = [
            row for row in files[accounting.REGISTRY_PATH]["installations"] if row["kind"] != "inline_app"]
    elif fault == "wrong_solution":
        files[RECIPE]["solution_id"] = str(APP)
    elif fault == "wrong_producer":
        app_policy = app_policy.model_copy(update={"workflow_path": ".github/workflows/other.yml"})
    elif fault == "wrong_repo":
        authored["repository_id"] = 1
    raw = {path: json.dumps(value, sort_keys=True).encode() for path, value in files.items()}
    # Real ProtectedGitReader.blob validates the Git object ID independently.
    entries = [{"path": path, "type": "blob", "mode": "100644", "size": len(value),
        "sha": hashlib.sha1(f"blob {len(value)}\0".encode() + value).hexdigest()}
        for path, value in raw.items()]
    if fault == "duplicate_path":
        entries.append(entries[0])
    elif fault == "symlink":
        entries[0]["mode"] = "120000"
    by_sha = {entry["sha"]: raw[entry["path"]] for entry in entries}
    calls = []

    def respond(request):
        import base64
        calls.append(request.url.path)
        assert "branches/main" not in str(request.url) and "/actions/" not in str(request.url)
        if f"git/commits/{SHA}" in request.url.path:
            return httpx.Response(200, json={"sha": SHA, "tree": {"sha": "f" * 40 if fault == "tree" else TREE}})
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={"sha": TREE, "truncated": fault == "truncated", "tree": entries})
        identity = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json={"sha": identity, "encoding": "base64", "size": len(by_sha[identity]),
            "content": base64.b64encode(by_sha[identity]).decode()})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        reader = ProtectedGitReader(app_policy, "fake-read-token", client)
        if fault:
            with pytest.raises(ValueError):
                await accounting.read_app_accounting_metadata(reader, policy=policy, app_policy=app_policy,
                    application_id=APP, source=authored)
        else:
            proof = await accounting.read_app_accounting_metadata(reader, policy=policy, app_policy=app_policy,
                application_id=APP, source=authored)
            assert proof["commit_sha"] == SHA and proof["tree_sha"] == TREE
            assert proof["control_hashes"][APP_RECIPE] == hashlib.sha256(raw[APP_RECIPE]).hexdigest()
            assert proof["control_hashes"][accounting.REGISTRY_PATH] == hashlib.sha256(raw[accounting.REGISTRY_PATH]).hexdigest()
            assert proof["package_registry_requirements"]["installations"] == {
                str(SID): {"organization_id": str(ORG), "recipe_path": RECIPE}}
            assert proof["package_registry_requirements"]["application_recipes"] == [APP_RECIPE]
            assert len(calls) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "ownership", "scope", "latest_job", "status", "intent", "controls", "pin", "bytes", "snapshot"])
async def test_final_readback_never_replays_publication_or_settles_uncertain_evidence(monkeypatch, fault):
    application = SimpleNamespace(id=APP, organization_id=ORG, repo_path="apps/fixture", solution_id=None,
        app_model="inline_v1", published_snapshot={"entry.js": "", "manifest.json": ""}, published_at="now")
    pin: dict[str, Any] = {"manifest_hash": HASH, "runtime_pin_hash": HASH, "output_hashes": {"entry.js": HASH},
        "source": {**source(), "source_hashes": {"app.yaml": HASH, "page.tsx": HASH}}}
    job = SimpleNamespace(id=JOB, status="succeeded", organization_id=ORG,
        requested_by_user_id=str(SYSTEM_USER_UUID), payload={"protected_git": {"source_commit_sha": SHA}},
        result={"publication_intent": {"controls_hash": HASH,
            "artifact_hashes": {"entry.js": HASH, "manifest.json": HASH}}, "runtime_pin": pin})
    prepared = accounting.PreparedAppAccounting(APP, JOB, ORG, "apps/fixture",
        canonical_digest({"payload": job.payload, "result": job.result}), copy.deepcopy(pin),
        {"commit_sha": SHA, "tree_sha": TREE, "artifact_digest": HASH})
    if fault == "ownership":
        application.solution_id = SID
    elif fault == "scope":
        application.organization_id = None
    elif fault == "latest_job":
        job.id = SID
    elif fault == "status":
        job.status = "requires_action"
    elif fault == "intent":
        job.result["publication_intent"]["extra"] = "changed"
    elif fault == "snapshot":
        application.published_snapshot = {"other.js": ""}
    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [application])))
    storage = SimpleNamespace(verify_publication=AsyncMock(side_effect=ValueError("byte drift") if fault == "bytes" else None))
    monkeypatch.setattr(accounting, "AppStorageService", lambda: storage)
    monkeypatch.setattr(accounting, "_latest_publication", AsyncMock(return_value=job))
    monkeypatch.setattr(accounting, "publication_controls_hash", AsyncMock(return_value="changed" if fault == "controls" else HASH))
    monkeypatch.setattr(accounting, "read_app_publication_runtime_pin", AsyncMock(return_value={} if fault == "pin" else pin))
    before = copy.deepcopy(job.result)
    consumers = await accounting.verify_app_accounting(db, [prepared])
    assert job.result == before  # No mutation of the original job/checkpoint.
    if fault:
        assert consumers == []
    else:
        assert consumers[0].publication_job_id == str(JOB)
        assert consumers[0].sources["apps/fixture/page.tsx"] == {"page.tsx": HASH.removeprefix("sha256:")}
        assert consumers[0].output_hashes == {"entry.js": HASH}
        storage.verify_publication.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "manual", "unknown_job_id", "scope", "partial", "no_verified_result", "payload_app"])
async def test_preparation_requires_original_system_admission_and_keeps_ancestry(monkeypatch, fault):
    policy, app_policy = policies()
    request = ApplicationGitSourcePublicationRequest(source_commit_sha=SHA, ci_run_id=123,
        ci_run_attempt=1, artifact_digest=HASH)
    producer = GitDeliveryIdentity("456", 1)
    original_id = accounting.app_git_job_id(APP, request, producer)
    application = SimpleNamespace(id=APP, organization_id=ORG, repo_path="apps/fixture",
        published_snapshot={"entry.js": ""})
    job = SimpleNamespace(id=original_id, status="succeeded", organization_id=ORG,
        requested_by_user_id=str(SYSTEM_USER_UUID), payload={"application_id": str(APP), "protected_git": {
            **request.model_dump(), "expected_controls_hash": HASH,
            "producer_run_id": producer.run_id, "producer_run_attempt": producer.run_attempt}},
        result={"publication_verified": True, "runtime_pin": {"source": source()}})
    if fault == "manual":
        job.requested_by_user_id = str(SID)
    elif fault == "unknown_job_id":
        job.id = JOB
    elif fault == "scope":
        job.organization_id = None
    elif fault == "partial":
        job.status = "requires_action"
    elif fault == "no_verified_result":
        job.result["publication_verified"] = False
    elif fault == "payload_app":
        job.payload["application_id"] = str(SID)
    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [application])))
    monkeypatch.setattr(accounting, "get_settings", lambda: SimpleNamespace(inline_app_git_delivery_policy=app_policy))
    monkeypatch.setattr(accounting, "get_github_config", AsyncMock(return_value=SimpleNamespace(
        token="fake-read-token", repo_url=f"https://github.com/{policy.repository}")))
    monkeypatch.setattr(accounting, "_latest_publication", AsyncMock(return_value=job))
    metadata = AsyncMock(return_value={"commit_sha": SHA})
    ancestors = AsyncMock(return_value=("f" * 40,))
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    monkeypatch.setattr(accounting.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(accounting, "read_app_accounting_metadata", metadata)
    monkeypatch.setattr(ProtectedGitReader, "verified_ancestors", ancestors)
    prepared = await accounting.prepare_app_accounting(db, policy)
    if fault:
        assert prepared == []
        metadata.assert_not_awaited()
        ancestors.assert_not_awaited()
    else:
        assert prepared[0].publication_job_id == original_id
        assert prepared[0].proof["ancestor_commit_shas"] == ["f" * 40]
        assert prepared[0].original_evidence_hash == canonical_digest({"payload": job.payload, "result": job.result})
        ancestors.assert_awaited_once_with(SHA, None)


@pytest.mark.parametrize("fault", [None, "no_declaration", "manual", "tree", "pending", "old_job",
    "old_pin", "source_bytes", "solution_only", "corrupt_evidence"])
def test_read_only_accounting_projection_requires_the_same_actual_publication(fault):
    pin: dict[str, Any] = {"application_id": str(APP), "publication_job_id": str(JOB), "runtime_pin_hash": HASH,
        "source": {**source(), "source_hashes": {"page.tsx": HASH}}}
    path = "apps/fixture/page.tsx"
    anchor = {"kind": "inline_app", "application_id": str(APP), "publication_job_id": str(JOB),
        "runtime_pin_hash": HASH}
    evidence: dict[str, Any] = {"schema_version": "bifrost.package-owned-source-completion/v1",
        "source_commit_sha": SHA, "source_tree_sha": TREE,
        "paths": {path: {"sha256": HASH.removeprefix("sha256:"), "protected_git_anchors": [anchor]}}}
    record = SimpleNamespace(id=SID, source_commit_sha=SHA, source_tree_sha=TREE,
        declaration_actor="github_actions_oidc", paths={path: HASH.removeprefix("sha256:")},
        disposition="released", resolved_at="now", completion_evidence=evidence)
    if fault == "no_declaration":
        record = None
    elif fault == "manual":
        record.declaration_actor = "platform_admin"
    elif fault == "tree":
        record.source_tree_sha = "f" * 40
    elif fault == "pending":
        record.disposition = "pending"
    elif fault == "old_job":
        anchor["publication_job_id"] = str(SID)
    elif fault == "old_pin":
        anchor["runtime_pin_hash"] = "sha256:" + "f" * 64
    elif fault == "source_bytes":
        pin["source"]["source_hashes"]["page.tsx"] = "sha256:" + "f" * 64
    elif fault == "solution_only":
        evidence["schema_version"] = "bifrost.solution-owned-source-completion/v1"
    evidence["evidence_id"] = canonical_digest(evidence)
    if fault == "corrupt_evidence":
        evidence["evidence_id"] = HASH
    readback = accounting.app_accounting_readback(record, pin)
    assert readback["verified"] is (fault is None)
    assert readback["publication_job_id"] == str(JOB)
    assert readback["runtime_pin_hash"] == HASH


def test_other_package_debt_is_not_this_apps_obligation():
    pin = {"application_id": str(APP), "publication_job_id": str(JOB), "runtime_pin_hash": HASH,
        "source": source()}
    record = SimpleNamespace(id=SID, source_commit_sha=SHA, source_tree_sha=TREE,
        declaration_actor="github_actions_oidc", paths={"features/unrelated.py": "c" * 64})
    readback = accounting.app_accounting_readback(record, pin)
    assert readback["verified"] is True
    assert readback["state"] == "no_app_source_obligation"
