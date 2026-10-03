"""Native delivery replay and policy attention retain their durable boundaries."""

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from uuid import uuid4

import pytest
import yaml
from bifrost.manifest import ManifestTable, ManifestWorkflow
from sqlalchemy import func, select, update

from src.core.constants import PROVIDER_ORG_ID
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
from src.models.orm.operation_receipts import OperationReceipt
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workspace_promotions import SolutionDeployObligation, WorkspaceSourceRelease
from src.services import operation_receipts
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solution_deploy_obligations import solution_source_content_id
from src.services.solutions import native_authored_source, source_revision
from src.services.solutions.authored_archive import _git_subtree_sha
from src.services.solutions.deployment_manifest import sha256_digest
from src.services.solutions.github_delivery_source import (
    GitDeliveryIdentity, VerifiedAuthoredSolution, VerifiedAuthoredSolutionFile, VerifiedGitSource,
)
from src.services.solutions.github_source_delivery import GitSourceDeliveryService
from src.services.solutions.native_authored_accounting import reconcile_native_solution_deploy_obligations
from src.services.solutions.reviewed_workflow_artifact import build_reviewed_artifact, compile_reviewed_workflows
from src.services.solutions.workflow_revision import project_workflow_registrations
from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe

from tests.e2e.platform.test_solution_source_revision import (
    _seed_adopted_revision,
    committed_delivery_db as committed_delivery_db,
)

pytestmark = pytest.mark.e2e


def _policy(*fixtures):
    return SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace", repository_id=1,
        repository_owner_id=1, organization_id=PROVIDER_ORG_ID, workflow_path=".github/workflows/deliver.yml",
        ci_workflow_path=".github/workflows/ci.yml", ci_workflow_id=1,
        solutions={f.solution_id: f"config/solution-delivery/{f.solution_id}.json" for f in fixtures})


def _receipt_sessions(monkeypatch, factory):
    @asynccontextmanager
    async def context():
        async with factory() as db:
            yield db
    monkeypatch.setattr(operation_receipts, "get_db_context", context)


class _Reader:
    def __init__(self, runtime, authored=None):
        self.runtime, self.authored = runtime, authored

    async def verify_ci(self, *_args):
        return None

    async def source(self, *_args):
        return self.runtime

    async def authored_source(self, *_args, **_kwargs):
        return self.authored


def _request(runtime):
    return SolutionGitSourceDeliveryRequest(source_commit_sha=runtime.commit_sha,
        ci_run_id=1, ci_run_attempt=1, artifact_digest=runtime.artifact_digest)


@pytest.mark.asyncio
async def test_adopted_legacy_name_first_delivery_exact_replay_and_fresh_attempt_keep_deployment(
    committed_delivery_db, platform_admin, monkeypatch, async_session_factory,
):
    db = committed_delivery_db
    commit = uuid4().hex + "a" * 8
    raw = b"from bifrost import workflow, tables\n@workflow(name='Friendly task', effects=[])\nasync def run():\n    return 1\n"
    f = await _seed_adopted_revision(db, platform_admin, monkeypatch,
        source_pair=(raw, raw), source_commit_sha=commit)
    recipe = ReviewedWorkflowRecipe.model_validate({
        "schema_version": "bifrost.solution-workflow-delivery/v1", "solution_id": str(f.solution_id),
        "files": {f.path: f"solutions/{f.solution.slug}/{f.path}"},
        "shared_tables": {name: item.model_dump(mode="json") for name, item in f.bindings.items()},
        "workflows": [{"id": str(f.workflow_id), "path": f.path, "function_name": "run",
            "organization_id": str(PROVIDER_ORG_ID), "controls": {}, "runtime_bounds": {
                "max_duration_seconds": 20, "max_external_calls": 10,
                "max_records_read": 100, "max_output_bytes": 4096}}],
    })
    entities = compile_reviewed_workflows(recipe, {f.path: raw}, {}, WorkflowIndexer(db),
        legacy_registration_names={f.workflow_id: "run"})
    manifest, resolution = build_reviewed_artifact(f.solution_id, f.base_id, recipe,
        {f.path: raw}, {}, entities, commit, "bifrost.repo-workflow-adoption/v1")
    # Install the reviewed adoption fixture with its sealed caller binding.
    await project_workflow_registrations(db, f.solution_id, entities, {f.workflow_id})
    await db.execute(update(SolutionDeployment).where(SolutionDeployment.id == f.base_id).values(
        compiled_manifest=manifest.model_dump(mode="json", exclude_none=True),
        compiled_manifest_hash=manifest.content_hash(), bundle_hash=manifest.bundle_hash,
        resolution_map=resolution.model_dump(mode="json", exclude_none=True),
        resolution_map_hash=manifest.resolution_map_hash, git_commit_sha=commit))
    await db.commit()
    f.objects[(str(f.base_id), f.path)] = raw
    _receipt_sessions(monkeypatch, async_session_factory)
    policy = _policy(f)
    runtime = VerifiedGitSource(f.solution_id, commit, "b" * 40, policy.solutions[f.solution_id],
        {f.path: sha256_digest(raw)}, {f.path: raw}, "sha256:" + "c" * 64, recipe)
    service = GitSourceDeliveryService(db, policy, _Reader(runtime))
    objects_before = dict(f.objects)
    results = []
    for attempt in (1, 1, 2):
        results.append(await service.deliver(f.solution_id, _request(runtime), GitDeliveryIdentity("2", attempt)))
    assert all(result.state == "already_active" and result.deployment_id == f.base_id for result in results)
    assert all(result.compiled_manifest_hash == manifest.content_hash() for result in results)
    assert results[0].receipt_id == results[1].receipt_id
    assert results[2].receipt_id != results[0].receipt_id
    await db.refresh(f.workflow)
    assert f.workflow.name == "run"
    assert f.objects == objects_before
    async with async_session_factory() as observed:
        assert await observed.scalar(select(Solution.active_deployment_id).where(Solution.id == f.solution_id)) == f.base_id
        assert await observed.scalar(select(func.count()).select_from(SolutionDeployment)
            .where(SolutionDeployment.solution_id == f.solution_id)) == 1
        statuses = (await observed.scalars(select(OperationReceipt.status).where(
            OperationReceipt.id.in_([result.receipt_id for result in results])))).all()
        assert statuses == ["succeeded", "succeeded"]


def _authored(f, commit, owned_table=None):
    root = f"solutions/{f.solution.slug}"
    descriptor = {key: getattr(f.solution, key) for key in (
        "slug", "name", "version", "repo_subpath", "allow_inbound_access", "allow_outbound_access",
        "git_connected", "git_repo_url", "git_ref")}
    workflow = ManifestWorkflow.from_row(f.workflow, roles=[]).model_dump(mode="json", by_alias=True)
    files = {"bifrost.solution.yaml": yaml.safe_dump(descriptor).encode(),
        "README.md": b"Reviewed new instructions\n",
        ".bifrost/workflows.yaml": yaml.safe_dump({"workflows": {str(f.workflow_id): workflow}}).encode(),
        f.path: f.old_source}
    if owned_table is not None:
        fields = ManifestTable.from_row(owned_table).model_dump(mode="json", by_alias=True)
        files[".bifrost/tables.yaml"] = yaml.safe_dump({"tables": {str(owned_table.id): fields}}).encode()
    entries = tuple(VerifiedAuthoredSolutionFile(path=root + "/" + path, mode="100644",
        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)) for path, raw in sorted(files.items()))
    inventory = [{"path": item.path, "mode": item.mode, "sha256": item.sha256, "size": item.size} for item in entries]
    return VerifiedAuthoredSolution(commit_sha=commit, tree_sha="b" * 40,
        subtree_sha=_git_subtree_sha(files, entries, root + "/"), solution_slug=f.solution.slug,
        repo_subpath=root, source_files=entries, files=MappingProxyType(files),
        source_content_id=solution_source_content_id(solution_slug=f.solution.slug,
            repo_subpath=root, source_files=inventory))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "wrong_domain"])
async def test_table_policy_attention_preserves_delivery_and_rotates_to_another_native_child(
    committed_delivery_db, platform_admin, monkeypatch, async_session_factory, fault,
):
    db = committed_delivery_db
    commit = uuid4().hex + "a" * 8
    bad = await _seed_adopted_revision(db, platform_admin, monkeypatch, source_commit_sha=commit)
    bad_storage = source_revision.SolutionDeploymentStorage
    good = await _seed_adopted_revision(db, platform_admin, monkeypatch, source_commit_sha=commit)
    good_storage = source_revision.SolutionDeploymentStorage
    factories = {bad.solution_id: bad_storage, good.solution_id: good_storage}

    def runtime_storage(solution_id, deployment_id):
        return factories[solution_id](solution_id, deployment_id)

    monkeypatch.setattr(source_revision, "SolutionDeploymentStorage", runtime_storage)
    monkeypatch.setattr(native_authored_source, "SolutionDeploymentStorage", runtime_storage)
    rule_name = "authored-policy-" + uuid4().hex
    owned_table = Table(id=uuid4(), name="authored_evidence_" + uuid4().hex, organization_id=PROVIDER_ORG_ID,
        solution_id=bad.solution_id, access={"policies": [{"$ref": rule_name}]})
    db.add(owned_table)
    if fault == "wrong_domain":
        db.add(PolicyRule(name=rule_name, domain="file", organization_id=PROVIDER_ORG_ID,
            body={"actions": ["read"], "when": None}))
    for f in (bad, good):
        await db.execute(update(Solution).where(Solution.id == f.solution_id).values(
            repo_subpath=f"solutions/{f.solution.slug}", readme="Prior instructions\n"))
        f.objects[(str(f.base_id), f.path)] = f.old_source
    await db.commit()
    await db.refresh(owned_table)
    for f in (bad, good):
        await db.refresh(f.solution)
        await db.refresh(f.workflow)
    sources = {bad.solution_id: _authored(bad, commit, owned_table), good.solution_id: _authored(good, commit)}
    _receipt_sessions(monkeypatch, async_session_factory)
    policy = _policy(bad, good)
    before_archive = bad.objects[(str(bad.base_id), "source")]
    results = {}
    for f in (bad, good):
        source = sources[f.solution_id]
        root = source.repo_subpath
        runtime = VerifiedGitSource(f.solution_id, commit, source.tree_sha, policy.solutions[f.solution_id],
            {f.path: sha256_digest(f.old_source)}, {f.path: f.old_source}, "sha256:" + "c" * 64,
            repository_paths={f.path: root + "/" + f.path},
            installation_registry={"path": "config/solution-delivery/installations.json", "target": "production",
                "installations": {str(f.solution_id): {"recipe_path": policy.solutions[f.solution_id],
                    "organization_id": str(PROVIDER_ORG_ID), "repo_subpath": root, "package_subpaths": [root]}}})
        results[f.solution_id] = await GitSourceDeliveryService(db, policy, _Reader(runtime, source)).deliver(
            f.solution_id, _request(runtime), GitDeliveryIdentity("2", 1))
    assert results[bad.solution_id].authored_source_state == "attention_required"
    assert results[good.solution_id].authored_source_state == "verified"
    async with async_session_factory() as observed:
        installed = await observed.get(Solution, bad.solution_id)
        assert installed.readme == "Prior instructions\n" and installed.active_deployment_id == bad.base_id
        receipt = await observed.get(OperationReceipt, results[bad.solution_id].receipt_id)
        assert receipt.status == "succeeded" and receipt.response["deployment_id"] == str(bad.base_id)
    assert bad.objects[(str(bad.base_id), "source")] == before_archive

    # Isolate accounting's policy validation from the preceding README mismatch.
    await db.execute(update(Solution).where(Solution.id == bad.solution_id)
        .values(readme=sources[bad.solution_id].files["README.md"].decode()))
    release = WorkspaceSourceRelease(id=uuid4(), organization_id=PROVIDER_ORG_ID,
        source_commit_sha=commit, source_tree_sha="b" * 40, paths={}, declaration_actor="platform_admin",
        declared_disposition="non_production", disposition="non_production", reason="Native child fixtures",
        resolved_at=datetime.now(UTC), created_by=platform_admin.user_id)
    db.add(release)
    await db.flush()
    older = datetime.now(UTC) - timedelta(days=1)
    children = []
    for index, f in enumerate((bad, good)):
        source = sources[f.solution_id]
        child = SolutionDeployObligation(id=uuid4(), source_release_id=release.id,
            organization_id=PROVIDER_ORG_ID, source_commit_sha=commit, source_tree_sha=source.tree_sha,
            source_subtree_sha=source.subtree_sha, source_content_id=source.source_content_id,
            solution_slug=source.solution_slug, repo_subpath=source.repo_subpath, source_files=source.file_manifest(),
            changed_paths={}, declared_disposition="solution_deploy_required", disposition="pending",
            updated_at=older + timedelta(seconds=index))
        db.add(child)
        children.append(child)
    await db.commit()
    assert await reconcile_native_solution_deploy_obligations(db, policy=policy, source_release_id=release.id, limit=1) == []
    await db.refresh(children[0])
    assert children[0].disposition == "pending" and children[0].completion_evidence is None
    assert children[0].updated_at > children[1].updated_at
    assert await reconcile_native_solution_deploy_obligations(db, policy=policy,
        source_release_id=release.id, limit=1) == [children[1].id]
    async with async_session_factory() as observed:
        failed = await observed.get(SolutionDeployObligation, children[0].id)
        completed = await observed.get(SolutionDeployObligation, children[1].id)
        assert failed.disposition == "pending" and failed.completion_evidence is None
        assert completed.disposition == "released"
        assert completed.completion_evidence["source_content_id"] == sources[good.solution_id].source_content_id
