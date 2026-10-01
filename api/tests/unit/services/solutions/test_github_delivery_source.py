"""Verify producer scope and protected Git provenance without executing source."""
import base64
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from bifrost.workspace_release import canonical_digest
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.services.github_actions_oidc import GITHUB_ACTIONS_ISSUER
from src.services.solutions.github_delivery_source import (
    GitDeliverySourceError, ProtectedGitReader, RECIPE_SCHEMA,
    authenticate_git_delivery, delivery_audience,
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
    "git/blobs/../../other", "branches/main?redirect=attacker", "actions/runs/1/../../other"])
@pytest.mark.asyncio
async def test_unapproved_endpoint_never_sends_job_credentials(suffix):
    calls = []
    async with httpx.AsyncClient(transport=transport({}, calls)) as client:
        with pytest.raises(GitDeliverySourceError, match="allowlist"):
            await ProtectedGitReader(policy(), "ephemeral-job-token", client).document(suffix)
    assert calls == []
