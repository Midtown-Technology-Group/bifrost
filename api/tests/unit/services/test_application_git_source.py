"""App enrollment, OIDC and complete Git capture cannot borrow Solution authority."""

import copy
from uuid import UUID

import httpx
import jwt
import pytest
from bifrost.workspace_release import canonical_digest
from pydantic import ValidationError

from src.core.application_delivery_policy import InlineAppGitDeliveryPolicy
from src.services.application_git_source import (
    app_delivery_audience,
    app_source_evidence,
    authenticate_app_git_delivery,
    read_app_git_source,
)
from src.services.solutions.github_delivery_source import GitDeliverySourceError, ProtectedGitReader
from tests.unit.services.solutions.test_github_delivery_source import (
    SHA,
    SID,
    TREE,
    claims,
    inline_app_fixture,
    policy,
    signing as signing,
    transport,
)


def app_policy():
    values = policy().model_dump(exclude={"solutions", "solution_organization_ids"})
    return InlineAppGitDeliveryPolicy(**values, applications={SID: {
        "organization_id": values["organization_id"], "repo_subpath": "apps/fixture",
    }})


@pytest.mark.parametrize("changes", [
    {"applications": {}},
    {"applications": {SID: {"repo_subpath": "apps/fixture"}}},
    {"applications": {SID: {"repo_subpath": "solutions/fixture", "organization_id": None}}},
    {"applications": {SID: {"repo_subpath": "apps/../fixture", "organization_id": None}}},
    {"applications": {SID: {"repo_subpath": "apps/fixture", "organization_id": None, "access_level": "public"}}},
    {"applications": {SID: {"repo_subpath": "apps/fixture", "organization_id": None},
        UUID(int=20): {"repo_subpath": "apps/fixture", "organization_id": None}}},
    {"workflow_path": "../deliver.yml"}, {"repository_id": 0},
])
def test_app_policy_requires_exact_scope_canonical_unique_subtree_without_control_grants(changes):
    values = app_policy().model_dump()
    values.update(changes)
    with pytest.raises(ValidationError):
        InlineAppGitDeliveryPolicy.model_validate(values)


def test_global_enrollment_is_explicit_and_an_unknown_app_is_not_global():
    values = app_policy().model_dump()
    values["applications"][SID]["organization_id"] = None
    configured = InlineAppGitDeliveryPolicy.model_validate(values)
    assert configured.enrollment_for(SID).organization_id is None
    with pytest.raises(ValueError, match="allowlist"):
        configured.enrollment_for(UUID(int=20))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "solution_audience", "other_app", "artifact",
    "repository_id", "workflow_ref", "ref", "sha", "run_attempt"])
async def test_app_oidc_requires_its_own_resource_bound_audience_and_pinned_producer(signing, fault):
    digest = "sha256:" + "c" * 64
    values = claims(digest)
    if fault != "solution_audience":
        values["aud"] = app_delivery_audience(SID, SHA, 123, 2, digest)
    changes = {"repository_id": "2", "workflow_ref": "attacker/reusable@refs/heads/main",
        "ref": "refs/pull/1/merge", "sha": "d" * 40, "run_attempt": "0"}
    if fault in changes:
        values[fault] = changes[fault]
    private, jwks = signing
    token = jwt.encode(values, private, algorithm="RS256", headers={"kid": "delivery-test"})
    arguments = dict(policy=app_policy(), application_id=UUID(int=20) if fault == "other_app" else SID,
        commit_sha=SHA, ci_run_id=123, ci_run_attempt=2, jwks=jwks,
        artifact_digest="sha256:" + "d" * 64 if fault == "artifact" else digest)
    if fault is None:
        identity = await authenticate_app_git_delivery(token, **arguments)
        assert identity.run_id == "456" and identity.run_attempt == 1
        assert not hasattr(identity, "is_superuser")
    else:
        with pytest.raises(GitDeliverySourceError):
            await authenticate_app_git_delivery(token, **arguments)


@pytest.mark.asyncio
async def test_app_source_binds_complete_bytes_scope_modes_and_ci_without_app_metadata_authority():
    documents, files = inline_app_fixture()
    configured = app_policy()
    calls = []
    async with httpx.AsyncClient(transport=transport(documents, calls)) as client:
        reader = ProtectedGitReader(configured, "ephemeral-token", client)
        captured = await reader.inline_app_source(SHA, "apps/fixture", TREE)
        evidence = app_source_evidence(SID, configured, captured)
        result = await read_app_git_source(reader, policy=configured, application_id=SID,
            commit_sha=SHA, ci_run_id=123, ci_run_attempt=2, artifact_digest=canonical_digest(evidence))
        assert dict(result.snapshot.files) == files
        assert evidence["organization_id"] == str(configured.organization_id)
        assert evidence["metadata_mode"] == "preserve_installed_controls"
        assert "access_level" not in evidence
        assert calls.count("branches/main") == 2
        with pytest.raises(GitDeliverySourceError, match="own installed recipe policy"):
            await reader.source(SID, SHA, "sha256:" + "c" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["artifact", "scope", "subtree", "superseded_during_read", "ci"])
async def test_app_source_drift_cannot_produce_an_authorized_capture(fault):
    documents, _ = inline_app_fixture()
    configured = app_policy()
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        captured = await ProtectedGitReader(configured, "token", client).inline_app_source(SHA, "apps/fixture", TREE)
    evidence = app_source_evidence(SID, configured, captured)
    if fault in {"scope", "subtree"}:
        evidence["organization_id" if fault == "scope" else "repo_subpath"] = "different"
    digest = "sha256:" + "c" * 64 if fault == "artifact" else canonical_digest(evidence)
    branch_reads = 0
    normal = transport(documents, [])

    def source_transport(request):
        nonlocal branch_reads
        if request.url.path.endswith("/branches/main"):
            branch_reads += 1
            if fault == "superseded_during_read" and branch_reads == 2:
                branch = copy.deepcopy(documents["branches/main"])
                branch["commit"]["sha"] = "d" * 40
                return httpx.Response(200, json=branch)
        if fault == "ci" and request.url.path.endswith("/actions/runs/123"):
            run = copy.deepcopy(documents["actions/runs/123"])
            run["conclusion"] = "failure"
            return httpx.Response(200, json=run)
        return normal.handle_request(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(source_transport)) as client:
        with pytest.raises(GitDeliverySourceError):
            await read_app_git_source(ProtectedGitReader(configured, "token", client), policy=configured,
                application_id=SID, commit_sha=SHA, ci_run_id=123, ci_run_attempt=2, artifact_digest=digest)
