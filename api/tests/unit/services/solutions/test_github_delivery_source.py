"""Verify producer scope and protected Git provenance without executing source."""
import base64
import copy
import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from operator import setitem
from typing import Any, cast
from uuid import UUID

import httpx
import jwt
import pytest
from bifrost.workspace_release import canonical_digest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.services.github_actions_oidc import GITHUB_ACTIONS_ISSUER
from src.services.solutions.github_delivery_source import (
    RECIPE_SCHEMA,
    GitDeliverySourceError,
    ProtectedGitReader,
    authenticate_git_delivery,
    delivery_audience,
)

SID = UUID("00000000-0000-0000-0000-000000000010")
SHA, TREE = "a" * 40, "b" * 40
RECIPE = "config/solution-delivery/fixture.json"


def policy():
    return SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace",
        repository_id=1197464564, repository_owner_id=87775189,
        organization_id=UUID("00000000-0000-0000-0000-000000000002"),
        workflow_path=".github/workflows/deliver-solutions.yml",
        ci_workflow_path=".github/workflows/ci.yml", ci_workflow_id=257449914,
        solutions={SID: RECIPE})


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_protected_git_transport_failure_reaches_receipt_readback_handler(error_type):
    def fail(request):
        raise error_type("GitHub transport unavailable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(error_type):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).verify_ci(SHA, 123, 2)


@pytest.fixture(scope="module")
def signing():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    public["kid"] = "delivery-test"
    return private, {"keys": [public]}


def claims(digest):
    now = datetime.now(UTC)
    p = policy()
    return {"iss": GITHUB_ACTIONS_ISSUER, "aud": delivery_audience(SID, SHA, 123, 2, digest),
        "iat": now, "nbf": now - timedelta(seconds=1), "exp": now + timedelta(minutes=5),
        "jti": "scope-bound-job", "repository": p.repository, "repository_id": str(p.repository_id),
        "repository_owner_id": str(p.repository_owner_id), "ref": "refs/heads/main", "ref_type": "branch",
        "sha": SHA, "workflow_sha": SHA, "workflow_ref": f"{p.repository}/{p.workflow_path}@refs/heads/main",
        "event_name": "workflow_run", "run_id": "456", "run_attempt": "1"}


async def authenticate(values, signing, **overrides):
    private, jwks = signing
    token = jwt.encode(values, private, algorithm="RS256", headers={"kid": "delivery-test"})
    return await authenticate_git_delivery(token, policy=policy(), solution_id=overrides.get("solution_id", SID),
        commit_sha=SHA, ci_run_id=123, ci_run_attempt=2,
        artifact_digest=overrides.get("artifact_digest", "sha256:" + "c" * 64), jwks=jwks)


@pytest.mark.asyncio
async def test_valid_producer_has_job_identity_and_no_admin_identity(signing):
    result = await authenticate(claims("sha256:" + "c" * 64), signing)
    assert result.run_id == "456" and result.run_attempt == 1
    assert not hasattr(result, "is_superuser")


@pytest.mark.parametrize("field,value", [
    ("repository", "attacker/fork"), ("repository_id", "2"), ("repository_owner_id", "2"),
    ("ref", "refs/pull/1/merge"), ("ref_type", "tag"), ("sha", "d" * 40),
    ("workflow_sha", "d" * 40), ("workflow_ref", "attacker/reusable@refs/heads/main"),
    ("event_name", "pull_request_target"), ("run_id", "../other"), ("run_attempt", "0"),
    ("run_id", "１２３"),
    ("aud", "bifrost-workspace-source-release"), ("iss", "https://attacker.invalid"),
])
@pytest.mark.asyncio
async def test_producer_claim_drift_is_rejected(signing, field, value):
    values = claims("sha256:" + "c" * 64)
    values[field] = value
    with pytest.raises(GitDeliverySourceError):
        await authenticate(values, signing)


@pytest.mark.asyncio
async def test_audience_cannot_authorize_other_artifact_or_install(signing):
    values = claims("sha256:" + "c" * 64)
    with pytest.raises(GitDeliverySourceError):
        await authenticate(values, signing, artifact_digest="sha256:" + "d" * 64)
    with pytest.raises(GitDeliverySourceError, match="allowlist"):
        await authenticate(values, signing, solution_id=UUID(int=20))


@pytest.mark.asyncio
async def test_expired_token_and_missing_claims_fail(signing):
    values = claims("sha256:" + "c" * 64)
    values["exp"] = datetime.now(UTC) - timedelta(seconds=10)
    with pytest.raises(GitDeliverySourceError):
        await authenticate(values, signing)
    del values["exp"]
    with pytest.raises(GitDeliverySourceError):
        await authenticate(values, signing)


@pytest.mark.parametrize("changes", [
    {"repository": "../other"}, {"workflow_path": "../deliver.yml"},
    {"ci_workflow_path": "https://attacker/ci.yml"}, {"solutions": {SID: "config/../recipe.json"}},
    {"solutions": {}}, {"ci_workflow_id": 0}, {"organization_id": "invalid"},
])
def test_policy_fails_closed_for_partial_or_unsafe_configuration(changes):
    values = policy().model_dump()
    values.update(changes)
    with pytest.raises(ValidationError):
        SolutionGitDeliveryPolicy.model_validate(values)


def test_legacy_policy_keeps_exact_scoped_install_and_rejects_unknown_install():
    configured = policy()
    assert configured.organization_id_for(SID) == configured.organization_id
    with pytest.raises(ValueError, match="allowlist"):
        configured.organization_id_for(UUID(int=20))


def test_explicit_install_scopes_distinguish_global_and_scoped_solutions():
    values = policy().model_dump()
    scoped_id = UUID(int=20)
    values.update(solutions={SID: RECIPE, scoped_id: "config/solution-delivery/scoped.json"},
                  solution_organization_ids={SID: None, scoped_id: values["organization_id"]})
    configured = SolutionGitDeliveryPolicy.model_validate(values)
    assert configured.organization_id_for(SID) is None
    assert configured.organization_id_for(scoped_id) == configured.organization_id
    with pytest.raises(ValueError, match="allowlist"):
        configured.organization_id_for(UUID(int=30))


@pytest.mark.parametrize("scopes", [{}, {UUID(int=20): None}, {SID: None, UUID(int=20): None},
                                   {SID: "invalid"}])
def test_explicit_scope_map_requires_exact_allowlist_coverage(scopes):
    values = policy().model_dump()
    values["solution_organization_ids"] = scopes
    with pytest.raises(ValidationError):
        SolutionGitDeliveryPolicy.model_validate(values)


def fixture(workflow_recipe=None, resource_files=None):
    files = {"fixture.py": b"from helper import value\nasync def fixture():\n    return value\n",
             "helper.py": b"value = 'merged-earlier'\n"}
    recipe = {"schema_version": RECIPE_SCHEMA, "solution_id": str(SID),
              "files": {path: "solutions/fixture/" + path for path in files}}
    if workflow_recipe is not None:
        recipe = {**recipe, **workflow_recipe}
    resource_files = resource_files or {}
    contents = {RECIPE: json.dumps(recipe).encode(), **{"solutions/fixture/" + path: content for path, content in files.items()},
        **{recipe["resources"][path]: content for path, content in resource_files.items()}}
    entries, blobs = [], {}
    for path, content in contents.items():
        sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False).hexdigest()
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha, "size": len(content)})
        blobs["git/blobs/" + sha] = {"sha": sha, "encoding": "base64", "content": base64.b64encode(content).decode()}
    digest = canonical_digest({"schema_version": recipe["schema_version"], "solution_id": str(SID),
        "source_commit_sha": SHA, "source_tree_sha": TREE, "recipe_path": RECIPE,
        "source_hashes": {path: "sha256:" + hashlib.sha256(value).hexdigest() for path, value in {**files, **resource_files}.items()},
        **({"reviewed_recipe": recipe} if workflow_recipe is not None else {})})
    documents = {"": {"id": policy().repository_id, "owner": {"id": policy().repository_owner_id},
        "full_name": policy().repository, "default_branch": "main"},
        "branches/main": {"protected": True, "commit": {"sha": SHA}},
        "actions/runs/123": {"id": 123, "run_attempt": 2, "head_sha": SHA, "head_branch": "main",
            "workflow_id": policy().ci_workflow_id, "path": policy().ci_workflow_path,
            "status": "completed", "conclusion": "success", "event": "workflow_dispatch",
            "repository": {"id": policy().repository_id}, "head_repository": {"id": policy().repository_id}},
        "git/commits/" + SHA: {"sha": SHA, "tree": {"sha": TREE}},
        "git/trees/" + TREE + "?recursive=1": {"sha": TREE, "truncated": False, "tree": entries}, **blobs}
    return documents, files, digest


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "symlink", "oversize", "missing", "changed_bytes"])
async def test_protected_git_resources_are_hash_bound_and_regular_bounded_blobs(damage):
    recipe = {"schema_version": "bifrost.solution-workflow-delivery/v1", "resources": {"rates.json": "data/rates.json"},
        "workflows": [{"id": str(UUID(int=100)), "path": "fixture.py", "function_name": "fixture",
            "organization_id": None, "runtime_bounds": {"max_duration_seconds": 30, "max_external_calls": 10,
                "max_records_read": 100, "max_output_bytes": 4096}, "controls": {}}]}
    resources = {"rates.json": b'{"rate":1}'}
    documents, _, digest = fixture(recipe, resources)
    entry = next(row for row in documents["git/trees/" + TREE + "?recursive=1"]["tree"] if row["path"] == "data/rates.json")
    if damage == "symlink":
        entry["mode"] = "120000"
    elif damage == "oversize":
        entry["size"] = 2 * 1024 * 1024 + 1
    elif damage == "missing":
        documents["git/trees/" + TREE + "?recursive=1"]["tree"].remove(entry)
    elif damage == "changed_bytes":
        documents, _, _ = fixture(recipe, {"rates.json": b'{"rate":2}'})
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        if damage:
            with pytest.raises(GitDeliverySourceError):
                await reader.source(SID, SHA, digest)
        else:
            source = await reader.source(SID, SHA, digest)
            assert source.resources == resources
            assert source.source_hashes["rates.json"] == "sha256:" + hashlib.sha256(resources["rates.json"]).hexdigest()
            assert source.repository_paths["rates.json"] == "data/rates.json"
            assert source.repository_paths["fixture.py"] == "solutions/fixture/fixture.py"
            assert source.control_hashes[RECIPE] == hashlib.sha256(json.dumps({
                "schema_version": RECIPE_SCHEMA, "solution_id": str(SID),
                "files": {path: "solutions/fixture/" + path for path in source.files}, **recipe}).encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "extra_install", "missing_install", "duplicate", "scope_target"])
async def test_protected_registry_is_bound_to_exact_configured_installations(fault):
    documents, _, digest = fixture()
    path = "config/solution-delivery/installations.json"
    rows = [{"target": "production", "recipe": RECIPE}]
    if fault == "extra_install":
        rows.append({"target": "production", "recipe": "config/solution-delivery/other.json"})
    elif fault == "missing_install":
        rows[0]["recipe"] = "config/solution-delivery/other.json"
    elif fault == "duplicate":
        rows.append(dict(rows[0]))
    elif fault == "scope_target":
        rows.append({"target": "canary", "recipe": RECIPE})
    content = json.dumps({"schema_version": "bifrost.solution-delivery-installations/v1", "installations": rows}).encode()
    sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False).hexdigest()
    documents["git/trees/" + TREE + "?recursive=1"]["tree"].append({"path": path,
        "mode": "100644", "type": "blob", "sha": sha, "size": len(content)})
    documents["git/blobs/" + sha] = {"sha": sha, "encoding": "base64", "content": base64.b64encode(content).decode()}
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        if fault:
            with pytest.raises(GitDeliverySourceError, match="registry"):
                await reader.source(SID, SHA, digest)
        else:
            source = await reader.source(SID, SHA, digest)
            assert source.control_hashes[path] == hashlib.sha256(content).hexdigest()
            assert source.installation_registry["installations"] == {str(SID): {
                "recipe_path": RECIPE, "organization_id": str(policy().organization_id),
                "repo_subpath": "solutions/fixture", "package_subpaths": ["solutions/fixture"]}}


def add_git_document(documents, path, content):
    sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content,
        usedforsecurity=False).hexdigest()
    entries = documents["git/trees/" + TREE + "?recursive=1"]["tree"]
    entries[:] = [row for row in entries if row["path"] != path]
    entries.append({"path": path, "type": "blob", "mode": "100644", "sha": sha, "size": len(content)})
    documents["git/blobs/" + sha] = {"sha": sha, "encoding": "base64", "content": base64.b64encode(content).decode()}


def family_registry_fixture(*, root_mapped=False, resource_mapping=None):
    documents, _, digest = fixture()
    peer_id, root_id = UUID(int=20), UUID(int=30)
    peer_path, root_path = "config/solution-delivery/peer.json", "config/solution-delivery/root.json"
    peer_files = {"fixture.py": "solutions/fixture/fixture.py", "helper.py": "solutions/fixture/helper.py"}
    if root_mapped:
        peer_files["helper.py"] = "features/helpers/runtime.py"
        add_git_document(documents, "features/helpers/runtime.py", b"value = 1\n")
    peer = {"schema_version": RECIPE_SCHEMA, "solution_id": str(peer_id), "files": peer_files}
    if resource_mapping is not None:
        peer.update(schema_version="bifrost.solution-workflow-delivery/v1", resources=resource_mapping,
            workflows=[{"id": str(UUID(int=100)), "path": "fixture.py", "function_name": "fixture",
                "organization_id": None, "runtime_bounds": {"max_duration_seconds": 30,
                    "max_external_calls": 10, "max_records_read": 100, "max_output_bytes": 4096}, "controls": {}}])
        for path in resource_mapping.values():
            add_git_document(documents, path, b'{"rate":1}')
    add_git_document(documents, peer_path, json.dumps(peer).encode())
    add_git_document(documents, root_path, json.dumps({"schema_version": RECIPE_SCHEMA,
        "solution_id": str(root_id), "files": {"root.py": "features/root.py"}}).encode())
    add_git_document(documents, "features/root.py", b"value = 1\n")
    add_git_document(documents, "config/solution-delivery/installations.json", json.dumps({
        "schema_version": "bifrost.solution-delivery-installations/v1", "installations": [
            {"target": "production", "recipe": path} for path in (RECIPE, peer_path, root_path)]}).encode())
    configured = policy().model_copy(update={"solutions": {SID: RECIPE, peer_id: peer_path, root_id: root_path},
        "solution_organization_ids": {SID: None, peer_id: policy().organization_id, root_id: None}})
    return documents, digest, configured, peer, peer_id, root_id


@pytest.mark.asyncio
async def test_registry_target_family_is_derived_from_all_protected_recipes_without_database_rows():
    documents, digest, configured, _, peer_id, root_id = family_registry_fixture()
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        source = await ProtectedGitReader(configured, "ephemeral-job-token", client).source(SID, SHA, digest)
    targets = source.installation_registry["installations"]
    assert set(targets) == {str(SID), str(peer_id), str(root_id)}
    assert targets[str(SID)] == {"recipe_path": RECIPE, "organization_id": None,
        "repo_subpath": "solutions/fixture", "package_subpaths": ["solutions/fixture"]}
    assert targets[str(peer_id)]["organization_id"] == str(policy().organization_id)
    assert targets[str(peer_id)]["repo_subpath"] == "solutions/fixture"
    assert targets[str(root_id)]["repo_subpath"] is None
    assert targets[str(root_id)]["package_subpaths"] == []
    # Reading another recipe proves its association, not its installed controls.
    assert "config/solution-delivery/peer.json" not in source.control_hashes


@pytest.mark.asyncio
@pytest.mark.parametrize("root_mapped,resources,expected,packages", [
    (True, None, None, ["solutions/fixture"]),
    (False, {"rates.json": "solutions/fixture/data/rates.json"}, "solutions/fixture", ["solutions/fixture"]),
    (False, {"rates.json": "data/rates.json"}, None, ["solutions/fixture"]),
    (False, {"rates.json": "solutions/other/data/rates.json"}, None, ["solutions/fixture", "solutions/other"]),
])
async def test_registry_family_retains_every_package_consumed_by_mixed_sources_or_resources(root_mapped, resources, expected, packages):
    documents, digest, configured, _, peer_id, _ = family_registry_fixture(
        root_mapped=root_mapped, resource_mapping=resources)
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        source = await ProtectedGitReader(configured, "ephemeral-job-token", client).source(SID, SHA, digest)
    assert source.installation_registry["installations"][str(peer_id)]["repo_subpath"] == expected
    assert source.installation_registry["installations"][str(peer_id)]["package_subpaths"] == packages
    fixture_targets = {identity for identity, entry in source.installation_registry["installations"].items()
        if "solutions/fixture" in entry["package_subpaths"]}
    # No install rows are consulted: a missing/inactive mixed peer remains
    # independently declared as a consumer of the fixture package.
    assert fixture_targets == {str(SID), str(peer_id)}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_recipe", "recipe_symlink", "recipe_blob_drift", "recipe_id",
    "recipe_schema", "recipe_duplicate", "unsafe_source", "missing_source", "recipe_bytes", "aggregate_bytes"])
async def test_other_registry_target_recipe_is_verified_and_bounded_before_association(fault, monkeypatch):
    from src.services.solutions import github_delivery_source

    documents, digest, configured, peer, _, _ = family_registry_fixture()
    path = "config/solution-delivery/peer.json"
    entries = documents["git/trees/" + TREE + "?recursive=1"]["tree"]
    entry = next(row for row in entries if row["path"] == path)
    if fault == "missing_recipe":
        entries.remove(entry)
    elif fault == "recipe_symlink":
        entry["mode"] = "120000"
    elif fault == "recipe_blob_drift":
        documents["git/blobs/" + entry["sha"]]["content"] = base64.b64encode(b"forged").decode()
    elif fault == "recipe_id":
        peer["solution_id"] = str(UUID(int=99))
        add_git_document(documents, path, json.dumps(peer).encode())
    elif fault == "recipe_schema":
        peer["schema_version"] = "unreviewed/v1"
        add_git_document(documents, path, json.dumps(peer).encode())
    elif fault == "recipe_duplicate":
        raw = json.dumps(peer).encode().replace(b'"files": {', b'"files": {}, "files": {')
        add_git_document(documents, path, raw)
    elif fault == "unsafe_source":
        peer["files"]["helper.py"] = "solutions/fixture/../other.py"
        add_git_document(documents, path, json.dumps(peer).encode())
    elif fault == "missing_source":
        peer["files"]["helper.py"] = "solutions/fixture/missing.py"
        add_git_document(documents, path, json.dumps(peer).encode())
    elif fault == "recipe_bytes":
        entry["size"] = 128 * 1024 + 1
    elif fault == "aggregate_bytes":
        monkeypatch.setattr(github_delivery_source, "MAX_METADATA_BYTES", 1)
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        with pytest.raises(GitDeliverySourceError):
            await ProtectedGitReader(configured, "ephemeral-job-token", client).source(SID, SHA, digest)


def commit_pages(chain):
    rows = [{"sha": identity, "parents": [{"sha": chain[index + 1]}] if index + 1 < len(chain) else []}
            for index, identity in enumerate(chain)]
    return {f"commits?sha={chain[0]}&per_page=100&page={page + 1}": rows[index:index + 100]
            for page, index in enumerate(range(0, len(rows), 100))}


@pytest.mark.asyncio
async def test_delayed_source_ancestry_reaches_268_commits_in_three_bounded_reads():
    chain = [SHA] + [f"{index:040x}" for index in range(1, 269)]
    calls = []
    documents = commit_pages(chain)
    documents.update({"git/commits/" + identity: {"sha": identity, "parents": [{"sha": chain[index + 1]}]}
                      for index, identity in enumerate(chain[:-1])})
    async with httpx.AsyncClient(transport=transport(documents, calls)) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        assert await reader.verified_ancestors(SHA, {chain[-1]}) == (chain[-1],)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_native_history_eligibility_uses_only_bounded_first_parent_chain():
    parent, grandparent, branch = "d" * 40, "e" * 40, "f" * 40
    documents = commit_pages([SHA, parent, grandparent])
    rows = documents[f"commits?sha={SHA}&per_page=100&page=1"]
    rows.insert(1, {"sha": branch, "parents": []})
    rows[0]["parents"].append({"sha": branch})
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        assert await reader.verified_ancestors(SHA, None) == (parent, grandparent)
        assert await reader.verified_ancestors(SHA, None, limit=1) == (parent,)

    chain = [SHA] + [f"{index:040x}" for index in range(1, 1002)]
    calls = []
    async with httpx.AsyncClient(transport=transport(commit_pages(chain), calls)) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        assert await reader.verified_ancestors(SHA, None, limit=10000) == tuple(chain[1:1000])
    assert len(calls) == 10


@pytest.mark.asyncio
async def test_supersession_ancestry_is_first_parent_git_proof_with_a_walk_bound():
    parent, grandparent = "d" * 40, "e" * 40
    documents = commit_pages([SHA, parent, grandparent])
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        assert await reader.verified_ancestors(SHA, {grandparent}, limit=1) == ()
        assert await reader.verified_ancestors(SHA, {parent, grandparent}) == (parent, grandparent)
        assert await reader.verified_ancestors(SHA, {"f" * 40}) == ()
    # REST history order can contain a merged branch. Only the persisted first
    # parent edge proves the protected-main lineage, never adjacent rows.
    branch = "f" * 40
    documents[f"commits?sha={SHA}&per_page=100&page=1"].insert(1, {"sha": branch, "parents": []})
    documents[f"commits?sha={SHA}&per_page=100&page=1"][0]["parents"].append({"sha": branch})
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        assert await reader.verified_ancestors(SHA, {branch, parent, grandparent}) == (parent, grandparent)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_head", "missing_parent", "malformed_parent", "cycle", "duplicate"])
async def test_incomplete_or_ambiguous_history_cannot_prove_an_older_source(fault):
    parent, grandparent = "d" * 40, "e" * 40
    rows = commit_pages([SHA, parent, grandparent])[f"commits?sha={SHA}&per_page=100&page=1"]
    if fault == "missing_head":
        rows.pop(0)
    elif fault == "missing_parent":
        rows.pop(1)
    elif fault == "malformed_parent":
        rows[0]["parents"] = [{"sha": "../other"}]
    elif fault == "cycle":
        rows[1]["parents"] = [{"sha": SHA}]
    else:
        rows.append(rows[0])
    documents = {f"commits?sha={SHA}&per_page=100&page=1": rows}
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        assert await reader.verified_ancestors(SHA, {grandparent}) == ()


@pytest.mark.asyncio
async def test_history_page_bound_does_not_invent_ancestry_after_truncation():
    chain = [SHA] + [f"{index:040x}" for index in range(1, 1002)]
    calls = []
    async with httpx.AsyncClient(transport=transport(commit_pages(chain), calls)) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        assert await reader.verified_ancestors(SHA, {chain[-1]}, limit=10000) == ()
    assert len(calls) == 10


@pytest.mark.asyncio
async def test_protected_recipe_binds_registration_controls_and_table_metadata_to_oidc_digest():
    from src.services.solutions.workflow_revision_recipe import WORKFLOW_RECIPE_SCHEMA
    recipe = {"schema_version": WORKFLOW_RECIPE_SCHEMA, "workflows": [{"id": str(UUID(int=100)),
        "path": "fixture.py", "function_name": "fixture", "organization_id": None,
        "runtime_bounds": {"max_duration_seconds": 30, "max_external_calls": 10,
            "max_records_read": 100, "max_output_bytes": 4096}, "controls": {}}],
        "shared_tables": {"state": {"table_id": str(UUID(int=200)),
            "metadata_hash": "sha256:" + "f" * 64, "access": "read"}}}
    documents, _, digest = fixture(recipe)
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        result = await ProtectedGitReader(policy(), "ephemeral-job-token", client).source(SID, SHA, digest)
        assert result.workflow_recipe is not None
        assert result.workflow_recipe.workflows[0].id == UUID(int=100)
    recipe["shared_tables"]["state"]["access"] = "read-write"
    changed_documents, _, changed_digest = fixture(recipe)
    assert changed_digest != digest
    async with httpx.AsyncClient(transport=transport(changed_documents, [])) as client:
        with pytest.raises(GitDeliverySourceError, match="digest"):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).source(SID, SHA, digest)


def transport(documents, calls):
    def respond(request):
        assert request.method == "GET" and request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer ephemeral-job-token"
        prefix = "/repos/" + policy().repository
        assert request.url.path == prefix or request.url.path.startswith(prefix + "/")
        suffix = request.url.path[len(prefix):].removeprefix("/") + ("?" + request.url.query.decode() if request.url.query else "")
        calls.append(suffix)
        response = documents[suffix]
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)
    return httpx.MockTransport(respond)


@pytest.mark.asyncio
async def test_full_snapshot_is_independently_derived_from_protected_blobs():
    documents, files, digest = fixture()
    calls = []
    async with httpx.AsyncClient(transport=transport(documents, calls)) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        await reader.verify_ci(SHA, 123, 2)
        result = await reader.source(SID, SHA, digest)
        assert result.files == files and result.artifact_digest == digest
        assert result.files["helper.py"] == b"value = 'merged-earlier'\n"
        assert result.tree_sha == TREE
        documents["branches/main"]["commit"]["sha"] = "d" * 40
        with pytest.raises(GitDeliverySourceError, match="superseded"):
            await reader.require_current_main(SHA)


@pytest.mark.parametrize("key,value", [
    ("run_attempt", 3), ("conclusion", "failure"), ("status", "in_progress"),
    ("head_sha", "d" * 40), ("head_branch", "review"), ("event", "pull_request"),
    ("workflow_id", 2), ("path", ".github/workflows/unreviewed.yml"),
    ("head_repository", {"id": 2}),
])
@pytest.mark.asyncio
async def test_wrong_or_incomplete_ci_never_authorizes_source(key, value):
    documents, _, _ = fixture()
    documents["actions/runs/123"][key] = value
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        with pytest.raises(GitDeliverySourceError, match="CI"):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).verify_ci(SHA, 123, 2)


@pytest.mark.parametrize("damage", ["symlink", "truncated", "duplicate", "duplicate_recipe", "blob_bytes", "wrong_digest", "wrong_install", "oversize"])
@pytest.mark.asyncio
async def test_unsafe_or_forged_artifact_is_rejected(damage):
    documents, _, digest = fixture()
    tree = documents["git/trees/" + TREE + "?recursive=1"]
    entry = tree["tree"][1]
    if damage == "symlink":
        entry["mode"] = "120000"
    elif damage == "truncated":
        tree["truncated"] = True
    elif damage == "duplicate":
        tree["tree"].append(copy.copy(entry))
    elif damage == "duplicate_recipe":
        recipe = tree["tree"][0]
        content = base64.b64decode(documents["git/blobs/" + recipe["sha"]]["content"])
        content = content.replace(b'"files": {', b'"files": {}, "files": {')
        sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content,
                           usedforsecurity=False).hexdigest()
        recipe.update(sha=sha, size=len(content))
        documents["git/blobs/" + sha] = {"sha": sha, "encoding": "base64",
                                       "content": base64.b64encode(content).decode()}
    elif damage == "blob_bytes":
        documents["git/blobs/" + entry["sha"]]["content"] = base64.b64encode(b"forged").decode()
    elif damage == "wrong_digest":
        digest = "sha256:" + "d" * 64
    elif damage == "wrong_install":
        recipe = tree["tree"][0]
        documents["git/blobs/" + recipe["sha"]]["sha"] = "d" * 40
    elif damage == "oversize":
        entry["size"] = 11 * 1024 * 1024
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        with pytest.raises(GitDeliverySourceError):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).source(SID, SHA, digest)


@pytest.mark.asyncio
async def test_redirect_does_not_forward_job_token():
    documents, _, _ = fixture()
    documents[""] = httpx.Response(302, headers={"Location": "https://attacker.invalid/token"})
    calls = []
    async with httpx.AsyncClient(transport=transport(documents, calls), follow_redirects=True) as client:
        with pytest.raises(GitDeliverySourceError, match="HTTP 302"):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).verify_ci(SHA, 123, 2)
    assert calls == [""]


@pytest.mark.parametrize("suffix", ["https://attacker.invalid", "//attacker.invalid", "../other",
    "git/blobs/../../other", "branches/main?redirect=attacker", "actions/runs/1/../../other",
    "commits?sha=main&per_page=100&page=1",
    f"commits?sha={SHA}&per_page=100&page=0",
    f"commits?sha={SHA}&per_page=100&page=11",
    f"commits?sha={SHA}&per_page=100&page=1&repository=other"])
@pytest.mark.asyncio
async def test_unapproved_endpoint_never_sends_job_credentials(suffix):
    calls = []
    async with httpx.AsyncClient(transport=transport({}, calls)) as client:
        with pytest.raises(GitDeliverySourceError, match="allowlist"):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).document(suffix)
    assert calls == []


def authored_fixture():
    """Fourteen authored files, of which only seven belong to the recipe."""
    runtime_paths = ["functions/audit.py", "functions/recall.py", "modules/__init__.py",
        "modules/audit_search.py", "modules/message_recall.py", "modules/microsoft/auth.py",
        "shared/microsoft/tenant_identity.py"]
    runtime = {path: b"" if path.endswith("__init__.py") else b"value = 1\n" for path in runtime_paths}
    authored = {**runtime, "modules/microsoft/__init__.py": b"", "shared/__init__.py": b"",
        "shared/microsoft/__init__.py": b"", "README.md": b"# Authored documentation\n",
        "bifrost.solution.yaml": b"slug: fixture\nname: Fixture\n",
        ".bifrost/workflows.yaml": b"workflows: {}\n", ".bifrost/tables.yaml": b"tables: {}\n"}
    recipe = {"schema_version": RECIPE_SCHEMA, "solution_id": str(SID),
        "files": {path: "solutions/fixture/" + path for path in runtime}}
    documents, _, _ = fixture()
    entries, blobs = [], {}
    contents = {RECIPE: json.dumps(recipe).encode(), **{"solutions/fixture/" + path: value
        for path, value in authored.items()}}
    for path, content in contents.items():
        sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content,
            usedforsecurity=False).hexdigest()
        entries.append({"path": path, "type": "blob", "mode": "100755" if path.endswith("auth.py")
            else "100644", "sha": sha, "size": len(content)})
        blobs["git/blobs/" + sha] = {"sha": sha, "encoding": "base64",
            "content": base64.b64encode(content).decode()}
    subtree_sha = "c" * 40
    directories = sorted({"/".join(path.split("/")[:depth]) for path in authored
        for depth in range(1, len(path.split("/")))})
    subtree_entries = [{**row, "path": row["path"].removeprefix("solutions/fixture/")}
        for row in entries if row["path"].startswith("solutions/fixture/")]
    for path in directories:
        sha = hashlib.sha1(path.encode(), usedforsecurity=False).hexdigest()
        subtree_entries.append({"path": path, "type": "tree", "mode": "040000", "sha": sha})
        entries.append({"path": "solutions/fixture/" + path, "type": "tree", "mode": "040000", "sha": sha})
    entries.extend([{"path": "solutions", "type": "tree", "mode": "040000", "sha": "e" * 40},
        {"path": "solutions/fixture", "type": "tree", "mode": "040000", "sha": subtree_sha}])
    documents["git/trees/" + TREE + "?recursive=1"]["tree"] = entries
    documents["git/trees/" + subtree_sha + "?recursive=1"] = {
        "sha": subtree_sha, "truncated": False, "tree": subtree_entries}
    documents.update(blobs)
    digest = canonical_digest({"schema_version": RECIPE_SCHEMA, "solution_id": str(SID),
        "source_commit_sha": SHA, "source_tree_sha": TREE, "recipe_path": RECIPE,
        "source_hashes": {path: "sha256:" + hashlib.sha256(value).hexdigest()
            for path, value in runtime.items()}})
    return documents, authored, runtime, digest


@pytest.mark.asyncio
async def test_authored_inventory_is_complete_immutable_and_independent_of_runtime_recipe():
    from src.services.solution_deploy_obligations import solution_source_content_id

    documents, authored, runtime, digest = authored_fixture()
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        evidence = await reader.authored_source(SHA, "solutions/fixture", TREE)
        native = await reader.source(SID, SHA, digest)
    assert len(evidence.source_files) == 14
    assert len(native.files) == 7
    assert evidence.files == authored
    assert native.files == runtime
    assert evidence.commit_sha == SHA
    assert evidence.tree_sha == TREE
    assert evidence.subtree_sha == "c" * 40
    assert evidence.solution_slug == "fixture"
    assert evidence.repo_subpath == "solutions/fixture"
    assert list(evidence.files) == sorted(authored)
    manifest = evidence.file_manifest()
    assert [row["path"] for row in manifest] == ["solutions/fixture/" + path for path in sorted(authored)]
    assert evidence.source_content_id == solution_source_content_id(solution_slug="fixture",
        repo_subpath="solutions/fixture", source_files=manifest)
    executable = next(row for row in manifest if row["path"].endswith("auth.py"))
    assert executable["mode"] == "100755"
    assert executable["sha256"] == hashlib.sha256(authored["modules/microsoft/auth.py"]).hexdigest()
    assert executable["size"] == len(authored["modules/microsoft/auth.py"])
    with pytest.raises(TypeError):
        setitem(evidence.files, "README.md", b"forged")
    with pytest.raises(FrozenInstanceError):
        cast(Any, evidence.source_files[0]).size = 1
    manifest[0]["size"] = 123
    assert evidence.file_manifest()[0]["size"] != 123


@pytest.mark.asyncio
@pytest.mark.parametrize("commit,path,tree", [
    ("A" * 40, "solutions/fixture", TREE), ("a" * 39, "solutions/fixture", TREE),
    (SHA, "solutions/fixture", "B" * 40), (SHA, "solutions/fixture", "b" * 41),
    (SHA, "solutions/fixture/nested", TREE), (SHA, "solutions/../fixture", TREE),
    (SHA, "solutions/fixture/", TREE), (SHA, "solutions/Fixture", TREE),
    (SHA, "solutions/fixture_name", TREE), (SHA, "other/fixture", TREE),
])
async def test_authored_inventory_rejects_noncanonical_request_before_transport(commit, path, tree):
    calls = []
    async with httpx.AsyncClient(transport=transport({}, calls)) as client:
        with pytest.raises(GitDeliverySourceError):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).authored_source(commit, path, tree)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unrelated_path", ["notes/other:operator.txt", "windows\\other.txt"])
async def test_authored_inventory_does_not_apply_delivery_path_rules_to_unrelated_git_files(unrelated_path):
    documents, _, _, _ = authored_fixture()
    documents["git/trees/" + TREE + "?recursive=1"]["tree"].append({"path": unrelated_path,
        "mode": "100644", "type": "blob", "sha": "d" * 40, "size": 1})
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        source = await ProtectedGitReader(policy(), "ephemeral-job-token", client).authored_source(SHA,
            "solutions/fixture", TREE)
    assert unrelated_path not in source.files


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["commit", "commit_tree", "root_identity", "root_truncated",
    "subtree_identity", "subtree_truncated", "missing_subtree", "invalid_subtree_sha",
    "duplicate_root", "duplicate_subtree", "unsafe", "root_disagreement", "missing_parent",
    "symlink", "gitlink", "invalid_mode", "invalid_blob_sha", "bool_size", "blob_bytes", "blob_identity"])
async def test_authored_inventory_rejects_incomplete_or_forged_git_transport(fault):
    documents, _, _, _ = authored_fixture()
    root = documents["git/trees/" + TREE + "?recursive=1"]
    subtree = documents["git/trees/" + "c" * 40 + "?recursive=1"]
    entry = next(row for row in subtree["tree"] if row["path"] == "README.md")
    root_entry = next(row for row in root["tree"] if row["path"] == "solutions/fixture/README.md")
    if fault == "commit":
        documents["git/commits/" + SHA]["sha"] = "d" * 40
    elif fault == "commit_tree":
        documents["git/commits/" + SHA]["tree"]["sha"] = "d" * 40
    elif fault == "root_identity":
        root["sha"] = "d" * 40
    elif fault == "root_truncated":
        root["truncated"] = True
    elif fault == "subtree_identity":
        subtree["sha"] = "d" * 40
    elif fault == "subtree_truncated":
        subtree["truncated"] = True
    elif fault == "missing_subtree":
        root["tree"] = [row for row in root["tree"] if row["path"] != "solutions/fixture"]
    elif fault == "invalid_subtree_sha":
        next(row for row in root["tree"] if row["path"] == "solutions/fixture")["sha"] = "G" * 40
    elif fault == "duplicate_root":
        root["tree"].append(copy.copy(root_entry))
    elif fault == "duplicate_subtree":
        subtree["tree"].append(copy.copy(entry))
    elif fault == "unsafe":
        entry["path"] = "../README.md"
    elif fault == "root_disagreement":
        root_entry["size"] += 1
    elif fault == "missing_parent":
        subtree["tree"] = [row for row in subtree["tree"] if row["path"] != "modules"]
        root["tree"] = [row for row in root["tree"] if row["path"] != "solutions/fixture/modules"]
    elif fault in {"symlink", "gitlink", "invalid_mode", "invalid_blob_sha", "bool_size"}:
        changes = {"symlink": {"mode": "120000"}, "gitlink": {"mode": "160000", "type": "commit"},
            "invalid_mode": {"mode": "100664"}, "invalid_blob_sha": {"sha": "g" * 40}, "bool_size": {"size": True}}
        entry.update(changes[fault])
        root_entry.update(changes[fault])
    elif fault == "blob_bytes":
        documents["git/blobs/" + entry["sha"]]["content"] = base64.b64encode(b"forged").decode()
    elif fault == "blob_identity":
        documents["git/blobs/" + entry["sha"]]["sha"] = "d" * 40
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        with pytest.raises(GitDeliverySourceError):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).authored_source(SHA, "solutions/fixture", TREE)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["count", "single_bytes", "total_bytes", "empty"])
async def test_authored_inventory_bounds_are_checked_before_blob_transport(fault):
    documents, _, _, _ = authored_fixture()
    root = documents["git/trees/" + TREE + "?recursive=1"]
    subtree = documents["git/trees/" + "c" * 40 + "?recursive=1"]
    if fault == "count":
        for number in range(1001):
            entry = {"path": f"extra-{number}.txt", "type": "blob", "mode": "100644",
                "sha": "d" * 40, "size": 0}
            subtree["tree"].append(entry)
            root["tree"].append({**entry, "path": "solutions/fixture/" + entry["path"]})
    elif fault == "empty":
        subtree["tree"] = []
        root["tree"] = [row for row in root["tree"] if not row["path"].startswith("solutions/fixture/")]
    else:
        selected = [row for row in subtree["tree"] if row["type"] == "blob"]
        for entry in selected[:1 if fault == "single_bytes" else 2]:
            size = 10 * 1024 * 1024 + 1 if fault == "single_bytes" else 6 * 1024 * 1024
            entry["size"] = size
            next(row for row in root["tree"] if row["path"] == "solutions/fixture/" + entry["path"])["size"] = size
    calls = []
    async with httpx.AsyncClient(transport=transport(documents, calls)) as client:
        with pytest.raises(GitDeliverySourceError):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).authored_source(SHA, "solutions/fixture", TREE)
    assert not any(path.startswith("git/blobs/") for path in calls)


def inline_app_fixture():
    files = {"app.yaml": b"scope: global\naccess_level: public\n",
        "pages/index.tsx": b"export default () => null;\n", "assets/empty.svg": b""}
    documents, _, _ = fixture()
    entries = [{"path": "pages", "type": "tree", "mode": "040000", "sha": "d" * 40},
        {"path": "assets", "type": "tree", "mode": "040000", "sha": "e" * 40}]
    for path, content in files.items():
        blob_sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content,
            usedforsecurity=False).hexdigest()
        entries.append({"path": path, "type": "blob", "mode": "100644", "sha": blob_sha,
            "size": len(content)})
        documents["git/blobs/" + blob_sha] = {"sha": blob_sha, "encoding": "base64",
            "content": base64.b64encode(content).decode()}
    documents["git/trees/" + TREE + "?recursive=1"]["tree"] = [
        {"path": "apps", "type": "tree", "mode": "040000", "sha": "f" * 40},
        {"path": "apps/fixture", "type": "tree", "mode": "040000", "sha": "c" * 40},
        *[{**entry, "path": "apps/fixture/" + entry["path"]} for entry in entries],
    ]
    documents["git/trees/" + "c" * 40 + "?recursive=1"] = {
        "sha": "c" * 40, "truncated": False, "tree": entries,
    }
    return documents, files


@pytest.mark.asyncio
async def test_inline_app_content_read_proves_complete_blobs_without_solution_or_publish_authority():
    documents, files = inline_app_fixture()
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        reader = ProtectedGitReader(policy(), "ephemeral-job-token", client)
        await reader.verify_ci(SHA, 123, 2)
        source = await reader.inline_app_source(SHA, "apps/fixture", TREE)
    assert source.commit_sha == SHA and source.tree_sha == TREE and source.subtree_sha == "c" * 40
    assert source.repo_subpath == "apps/fixture"
    assert dict(source.snapshot.files) == files
    assert source.snapshot.hashes() == {path: "sha256:" + hashlib.sha256(content).hexdigest()
        for path, content in sorted(files.items())}
    assert dict(source.file_modes) == {path: "100644" for path in files}
    assert not hasattr(source, "solution_id") and not hasattr(source, "application_id")
    with pytest.raises(TypeError):
        setitem(source.snapshot.files, "app.yaml", b"forged")
    with pytest.raises(TypeError):
        setitem(source.file_modes, "app.yaml", "120000")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["solutions/fixture", "apps/../fixture", "apps/fixture/",
    "apps/fixture/nested", "apps/Fixture", "apps/fixture_name", "https://attacker.invalid"])
async def test_inline_app_subtree_path_is_checked_before_git_transport(path):
    calls = []
    async with httpx.AsyncClient(transport=transport({}, calls)) as client:
        with pytest.raises(GitDeliverySourceError, match="canonical inline App"):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).inline_app_source(SHA, path, TREE)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_metadata", "generated_entry", "blob_bytes",
    "root_disagreement", "subtree_truncated", "symlink"])
async def test_inline_app_content_rejects_invalid_git_or_capture_input(fault):
    documents, _ = inline_app_fixture()
    root = documents["git/trees/" + TREE + "?recursive=1"]
    subtree = documents["git/trees/" + "c" * 40 + "?recursive=1"]
    entry = next(row for row in subtree["tree"] if row["path"] == "app.yaml")
    root_entry = next(row for row in root["tree"] if row["path"] == "apps/fixture/app.yaml")
    if fault == "missing_metadata":
        subtree["tree"].remove(entry)
        root["tree"].remove(root_entry)
    elif fault == "generated_entry":
        generated = {**entry, "path": "_entry.tsx"}
        subtree["tree"].append(generated)
        root["tree"].append({**generated, "path": "apps/fixture/_entry.tsx"})
    elif fault == "blob_bytes":
        documents["git/blobs/" + entry["sha"]]["content"] = base64.b64encode(b"forged").decode()
    elif fault == "root_disagreement":
        root_entry["size"] += 1
    elif fault == "subtree_truncated":
        subtree["truncated"] = True
    else:
        entry["mode"] = root_entry["mode"] = "120000"
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        with pytest.raises(GitDeliverySourceError):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).inline_app_source(SHA, "apps/fixture", TREE)


@pytest.mark.asyncio
async def test_inline_app_file_bound_precedes_blob_reads():
    documents, _ = inline_app_fixture()
    root = documents["git/trees/" + TREE + "?recursive=1"]
    subtree = documents["git/trees/" + "c" * 40 + "?recursive=1"]
    for number in range(256):
        entry = {"path": f"extra-{number}.ts", "type": "blob", "mode": "100644", "sha": "d" * 40, "size": 0}
        subtree["tree"].append(entry)
        root["tree"].append({**entry, "path": "apps/fixture/" + entry["path"]})
    calls = []
    async with httpx.AsyncClient(transport=transport(documents, calls)) as client:
        with pytest.raises(GitDeliverySourceError, match="inventory bound"):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).inline_app_source(SHA, "apps/fixture", TREE)
    assert not any(path.startswith("git/blobs/") for path in calls)


@pytest.mark.asyncio
async def test_solution_delivery_in_mixed_registry_does_not_borrow_package_or_app_authority():
    documents, _, digest = fixture()
    registry_path = "config/solution-delivery/installations.json"
    rows = [
        {"kind": "solution", "target": "production", "recipe": RECIPE},
        {"kind": "inline_app", "target": "production", "recipe": "config/app-delivery/fixture.json"},
        {"kind": "solution_package", "target": "production", "recipe": "config/solution-package-delivery/fixture.json"},
    ]
    content = json.dumps({"schema_version": "bifrost.package-delivery-installations/v1", "installations": rows}).encode()
    add_git_document(documents, registry_path, content)
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        source = await ProtectedGitReader(policy(), "ephemeral-job-token", client).source(SID, SHA, digest)
    assert set(source.installation_registry["installations"]) == {str(SID)}
    assert source.installation_registry["application_recipes"] == ["config/app-delivery/fixture.json"]
    assert source.control_hashes[registry_path] == hashlib.sha256(content).hexdigest()
