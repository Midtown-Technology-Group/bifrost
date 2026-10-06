"""Full-package Git proof cannot borrow another adapter's publication authority."""

import base64
import copy
import hashlib
import io
import json
import zipfile
from uuid import UUID

import httpx
import jwt
import pytest
from bifrost.workspace_release import canonical_digest
from pydantic import ValidationError

from src.core.solution_package_delivery_policy import SolutionPackageGitDeliveryPolicy
from src.services.solutions.github_delivery_source import (
    GitDeliverySourceError,
    ProtectedGitReader,
)
from src.services.solutions.package_git_source import (
    authenticate_package_git_delivery,
    package_delivery_audience,
    package_source_evidence,
    read_package_git_source,
)
from tests.unit.services.solutions.test_github_delivery_source import (
    SHA,
    SID,
    TREE,
    claims,
    fixture,
    policy,
    signing as signing,
    transport,
)
from tests.unit.test_solution_package_delivery import recipe, source

RECIPE_PATH = "config/solution-package-delivery/fixture.json"


def package_policy():
    values = policy().model_dump(exclude={"solutions", "solution_organization_ids"})
    return SolutionPackageGitDeliveryPolicy(
        **values,
        packages={
            SID: {
                "organization_id": None,
                "repo_subpath": "solutions/fixture",
                "recipe_path": RECIPE_PATH,
            }
        },
    )


def package_fixture():
    files = source()
    files["bifrost.solution.yaml"] = b"slug: fixture\nname: Fixture\n"
    contract = recipe(files)
    contract["repo_subpath"] = "solutions/fixture"
    raw_recipe = json.dumps(contract).encode()
    documents, _, _ = fixture()
    entries = []
    for path, raw in {
        RECIPE_PATH: raw_recipe,
        **{"solutions/fixture/" + path: content for path, content in files.items()},
    }.items():
        oid = hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
        ).hexdigest()
        entries.append(
            {
                "path": path,
                "type": "blob",
                "mode": "100644",
                "sha": oid,
                "size": len(raw),
            }
        )
        documents["git/blobs/" + oid] = {
            "sha": oid,
            "encoding": "base64",
            "content": base64.b64encode(raw).decode(),
        }
    subtree = [
        {**row, "path": row["path"].removeprefix("solutions/fixture/")}
        for row in entries
        if row["path"].startswith("solutions/fixture/")
    ]
    directories = sorted(
        {
            "/".join(path.split("/")[:depth])
            for path in files
            for depth in range(1, len(path.split("/")))
        }
    )
    for path in directories:
        row = {
            "path": path,
            "type": "tree",
            "mode": "040000",
            "sha": hashlib.sha1(path.encode(), usedforsecurity=False).hexdigest(),
        }
        subtree.append(row)
        entries.append({**row, "path": "solutions/fixture/" + path})
    entries.extend(
        [
            {"path": "solutions", "type": "tree", "mode": "040000", "sha": "e" * 40},
            {
                "path": "solutions/fixture",
                "type": "tree",
                "mode": "040000",
                "sha": "c" * 40,
            },
        ]
    )
    documents["git/trees/" + TREE + "?recursive=1"]["tree"] = entries
    documents["git/trees/" + "c" * 40 + "?recursive=1"] = {
        "sha": "c" * 40,
        "truncated": False,
        "tree": subtree,
    }
    return documents, files, raw_recipe


@pytest.mark.parametrize(
    "change",
    [
        {"packages": {}},
        {
            "packages": {
                SID: {"repo_subpath": "solutions/fixture", "recipe_path": RECIPE_PATH}
            }
        },
        {
            "packages": {
                SID: {
                    "organization_id": None,
                    "repo_subpath": "apps/fixture",
                    "recipe_path": RECIPE_PATH,
                }
            }
        },
        {
            "packages": {
                SID: {
                    "organization_id": None,
                    "repo_subpath": "solutions/fixture",
                    "recipe_path": "config/solution-delivery/fixture.json",
                }
            }
        },
        {
            "packages": {
                SID: {
                    "organization_id": None,
                    "repo_subpath": "solutions/fixture",
                    "recipe_path": RECIPE_PATH,
                    "force": True,
                }
            }
        },
    ],
)
def test_package_enrollment_requires_explicit_scope_and_separate_adapter(change):
    values = package_policy().model_dump()
    values.update(change)
    with pytest.raises(ValidationError):
        SolutionPackageGitDeliveryPolicy.model_validate(values)


def test_one_subtree_cannot_target_two_installs_and_unknown_is_not_global():
    configured = package_policy()
    assert configured.enrollment_for(SID).organization_id is None
    with pytest.raises(ValueError, match="allowlist"):
        configured.enrollment_for(UUID(int=20))
    values = configured.model_dump()
    values["packages"][UUID(int=20)] = values["packages"][SID]
    with pytest.raises(ValidationError):
        SolutionPackageGitDeliveryPolicy.model_validate(values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "workflow_audience", "inline_audience", "artifact", "other_target"]
)
async def test_package_oidc_is_resource_artifact_and_adapter_bound(signing, fault):
    digest = "sha256:" + "c" * 64
    values = claims(digest)
    if fault != "workflow_audience":
        values["aud"] = package_delivery_audience(SID, SHA, 123, 2, digest)
    if fault == "inline_audience":
        values["aud"] = f"bifrost-inline-app-git-delivery/v1:{SID}:{SHA}:123:2:{digest}"
    private, jwks = signing
    token = jwt.encode(
        values, private, algorithm="RS256", headers={"kid": "delivery-test"}
    )
    arguments = dict(
        policy=package_policy(),
        solution_id=UUID(int=20) if fault == "other_target" else SID,
        commit_sha=SHA,
        ci_run_id=123,
        ci_run_attempt=2,
        jwks=jwks,
        artifact_digest="sha256:" + "d" * 64 if fault == "artifact" else digest,
    )
    if fault is None:
        identity = await authenticate_package_git_delivery(token, **arguments)
        assert identity.run_id == "456" and not hasattr(identity, "is_superuser")
    else:
        with pytest.raises(GitDeliverySourceError):
            await authenticate_package_git_delivery(token, **arguments)


async def captured_evidence(documents, raw_recipe, configured):
    async with httpx.AsyncClient(transport=transport(documents, [])) as client:
        authored = await ProtectedGitReader(
            configured, "ephemeral-job-token", client
        ).authored_source(SHA, "solutions/fixture", TREE)
    return package_source_evidence(
        SID, configured, authored, raw_recipe, ci_run_id=123, ci_run_attempt=2
    )


@pytest.mark.asyncio
async def test_complete_protected_package_preserves_resources_archive_and_immutable_evidence():
    documents, files, raw_recipe = package_fixture()
    configured = package_policy()
    evidence = await captured_evidence(documents, raw_recipe, configured)
    calls = []
    async with httpx.AsyncClient(transport=transport(documents, calls)) as client:
        reader = ProtectedGitReader(configured, "ephemeral-job-token", client)
        result = await read_package_git_source(
            reader,
            policy=configured,
            solution_id=SID,
            commit_sha=SHA,
            ci_run_id=123,
            ci_run_attempt=2,
            artifact_digest=canonical_digest(evidence),
        )
        with pytest.raises(GitDeliverySourceError, match="own installed recipe policy"):
            await reader.source(SID, SHA, result.artifact_digest)
    assert calls.count("branches/main") == 2
    assert dict(result.authored.files) == files
    with zipfile.ZipFile(io.BytesIO(result.source_archive)) as archive:
        assert {path: archive.read(path) for path in archive.namelist()} == files
    assert (
        hashlib.sha256(result.source_archive).hexdigest()
        == evidence["package"]["source_archive_sha256"]
    )
    assert set(evidence["package"]["entities"]) == {
        "apps",
        "workflows",
        "tables",
        "file_locations",
    }
    assert evidence["package"]["runtime_verified"] is False
    assert evidence["package"]["installed_controls_verified"] is False
    changed = result.evidence()
    changed["package"]["entities"]["workflows"].clear()
    assert result.evidence() == evidence
    assert not hasattr(result, "deployment_id")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "artifact",
        "ci",
        "moved_main",
        "reader_policy",
        "missing_recipe",
        "duplicate_recipe",
        "truncated",
        "symlink_recipe",
        "recipe_scope",
        "recipe_target",
        "recipe_subtree",
        "recipe_manifest",
    ],
)
async def test_protected_package_capture_fails_closed_before_any_publication(fault):
    documents, _, raw_recipe = package_fixture()
    configured = package_policy()
    evidence = await captured_evidence(documents, raw_recipe, configured)
    root = documents["git/trees/" + TREE + "?recursive=1"]
    entry = next(row for row in root["tree"] if row["path"] == RECIPE_PATH)
    if fault == "missing_recipe":
        root["tree"].remove(entry)
    elif fault == "duplicate_recipe":
        root["tree"].append(dict(entry))
    elif fault == "truncated":
        root["truncated"] = True
    elif fault == "symlink_recipe":
        entry["mode"] = "120000"
    elif fault.startswith("recipe_"):
        changed = json.loads(raw_recipe)
        if fault == "recipe_scope":
            changed["organization_id"] = str(configured.organization_id)
        elif fault == "recipe_target":
            changed["solution_id"] = str(UUID(int=20))
        elif fault == "recipe_subtree":
            changed["repo_subpath"] = "solutions/other"
        else:
            changed["manifest_hashes"][".bifrost/tables.yaml"] = "sha256:" + "0" * 64
        raw = json.dumps(changed).encode()
        oid = hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
        ).hexdigest()
        entry.update(sha=oid, size=len(raw))
        documents["git/blobs/" + oid] = {
            "sha": oid,
            "encoding": "base64",
            "content": base64.b64encode(raw).decode(),
        }
    reads = 0
    normal = transport(documents, [])

    def response(request):
        nonlocal reads
        if request.url.path.endswith("/branches/main"):
            reads += 1
            if fault == "moved_main" and reads == 2:
                branch = copy.deepcopy(documents["branches/main"])
                branch["commit"]["sha"] = "d" * 40
                return httpx.Response(200, json=branch)
        if fault == "ci" and request.url.path.endswith("/actions/runs/123"):
            run = copy.deepcopy(documents["actions/runs/123"])
            run["conclusion"] = "failure"
            return httpx.Response(200, json=run)
        return normal.handle_request(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        reader = ProtectedGitReader(
            policy() if fault == "reader_policy" else configured, "ephemeral-job-token", client
        )
        with pytest.raises(GitDeliverySourceError):
            await read_package_git_source(
                reader,
                policy=configured,
                solution_id=SID,
                commit_sha=SHA,
                ci_run_id=123,
                ci_run_attempt=2,
                artifact_digest="sha256:" + "d" * 64
                if fault == "artifact"
                else canonical_digest(evidence),
            )
