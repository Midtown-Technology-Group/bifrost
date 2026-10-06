"""Native authored metadata and journal completion use actual PostgreSQL."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
import yaml
from bifrost.manifest import ManifestWorkflow
from sqlalchemy import null, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.constants import PROVIDER_ORG_ID
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.models.orm.solutions import Solution
from src.models.orm.workspace_promotions import (
    SolutionDeployObligation,
    WorkspaceSourceRelease,
)
from src.services.solution_deploy_obligations import solution_source_content_id
from src.services.solutions import native_authored_source, source_revision
from src.services.solutions.github_delivery_source import (
    VerifiedAuthoredSolution,
    VerifiedAuthoredSolutionFile,
    VerifiedGitSource,
)
from src.services.solutions.github_source_delivery import GitSourceDeliveryService

from tests.e2e.platform.test_solution_source_revision import _seed_adopted_revision
from tests.e2e.platform.test_solution_source_revision import committed_delivery_db as committed_delivery_db
from tests.e2e.platform.test_solution_source_revision import db_session as db_session

pytestmark = pytest.mark.e2e


def _source(f):
    root = f"solutions/{f.solution.slug}"
    descriptor = {key: getattr(f.solution, key) for key in (
        "slug", "name", "version", "repo_subpath", "allow_inbound_access", "allow_outbound_access",
        "git_connected", "git_repo_url", "git_ref")}
    fields = ManifestWorkflow.from_row(f.workflow, roles=[]).model_dump(mode="json", by_alias=True)
    files = {"bifrost.solution.yaml": yaml.safe_dump(descriptor).encode(),
        "README.md": b"Reviewed new investigation instructions\n",
        ".bifrost/workflows.yaml": yaml.safe_dump({"workflows": {str(f.workflow_id): fields}}).encode(),
        f.path: f.old_source, "shared/__init__.py": b""}
    entries = tuple(VerifiedAuthoredSolutionFile(path=root + "/" + path, mode="100644",
        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)) for path, raw in sorted(files.items()))
    source_files = [{"path": item.path, "mode": item.mode, "sha256": item.sha256, "size": item.size} for item in entries]
    return VerifiedAuthoredSolution(commit_sha="a" * 40, tree_sha="b" * 40, subtree_sha="c" * 40,
        solution_slug=f.solution.slug, repo_subpath=root, source_files=entries, files=MappingProxyType(files),
        source_content_id=solution_source_content_id(solution_slug=f.solution.slug, repo_subpath=root, source_files=source_files))


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [None, "same", "changed"], ids=["exact", "descendant-revert", "descendant-change"])
@pytest.mark.parametrize("unrelated_mutable", [False, True])
@pytest.mark.parametrize("recovery_fault", [None, "receipt", "readme", "membership", "lock_timeout", "cache_budget"])
async def test_successful_receipt_replay_then_late_declaration_settles_native_authored_source(
    committed_delivery_db, async_session_factory, platform_admin, monkeypatch, unrelated_mutable, recovery_fault, historical,
):
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from src import config
    from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
    from src.models.contracts.workspace_promotions import SolutionDeployObligationDeclare, WorkspaceSourceReleaseDeclareRequest
    from src.models.orm.operation_receipts import OperationReceipt
    from src.services import solution_source_accountability
    from src.services.operation_receipts import (
        OperationReceiptClaim, OperationReceiptDisposition,
        canonical_operation_scope_key, canonical_request_fingerprint,
    )
    from src.services.solutions import github_source_delivery, native_authored_accounting
    from src.services.solutions.authored_archive import _git_subtree_sha
    from src.services.solutions.github_delivery_source import GitDeliveryIdentity
    from src.services.workspace_source_releases import WorkspaceSourceReleaseService

    db_session = committed_delivery_db
    commit_sha = uuid4().hex + uuid4().hex[:8]
    f = await _seed_adopted_revision(db_session, platform_admin, monkeypatch, source_commit_sha=commit_sha)
    root = f"solutions/{f.solution.slug}"
    await db_session.execute(update(Solution).where(Solution.id == f.solution_id).values(repo_subpath=root, readme="Prior instructions\n"))
    if unrelated_mutable:
        db_session.add(Solution(id=uuid4(), slug=f"unrelated-{uuid4().hex[:10]}", name="Unrelated legacy app package",
            organization_id=PROVIDER_ORG_ID, execution_runtime_mode="repo-v1"))
    await db_session.commit()
    await db_session.refresh(f.solution)
    source = _source(f)
    source = replace(source, commit_sha=commit_sha,
        subtree_sha=_git_subtree_sha(dict(source.files), source.source_files, root + "/"))
    f.objects[(str(f.base_id), f.path)] = f.old_source
    monkeypatch.setattr(native_authored_source, "SolutionDeploymentStorage", source_revision.SolutionDeploymentStorage)
    policy = SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace", repository_id=1,
        repository_owner_id=1, organization_id=PROVIDER_ORG_ID, workflow_path=".github/workflows/deliver.yml",
        ci_workflow_path=".github/workflows/ci.yml", ci_workflow_id=1,
        solutions={f.solution_id: "config/solution-delivery/fixture.json"})
    settings = config.get_settings().model_copy(update={"solution_git_delivery_policy": policy,
        "workspace_source_release_oidc_organization_id": str(PROVIDER_ORG_ID)})
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(solution_source_accountability, "get_settings", lambda: settings)
    monkeypatch.setattr(native_authored_accounting, "get_settings", lambda: settings)
    digest = "sha256:" + "d" * 64
    desired = VerifiedGitSource(f.solution_id, source.commit_sha, source.tree_sha,
        policy.solutions[f.solution_id], {f.path: "sha256:" + hashlib.sha256(f.old_source).hexdigest()},
        {f.path: f.old_source}, digest, repository_paths={f.path: root + "/" + f.path},
        installation_registry={"path": "config/solution-delivery/installations.json", "target": "production",
            "installations": {str(f.solution_id): {"recipe_path": policy.solutions[f.solution_id],
                "organization_id": str(PROVIDER_ORG_ID), "repo_subpath": root, "package_subpaths": [root]}}})
    identity = {"repository_id": 1, "solution_id": str(f.solution_id), "source_commit_sha": source.commit_sha,
        "artifact_digest": digest, "ci_run_id": 1, "ci_run_attempt": 1, "producer_run_id": "2", "producer_run_attempt": 1}
    receipt = OperationReceipt(id=uuid4(), namespace="solution.git-source-delivery", status="succeeded",
        scope_key=canonical_operation_scope_key(identity), request_fingerprint=canonical_request_fingerprint(identity),
        response={}, completed_at=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(days=1))
    db_session.add(receipt)
    await db_session.commit()
    monkeypatch.setattr(github_source_delivery, "claim_operation_receipt", AsyncMock(return_value=
        OperationReceiptClaim(receipt_id=receipt.id, disposition=OperationReceiptDisposition.SUCCEEDED)))

    previous = replace(source, commit_sha=uuid4().hex + "a" * 8, tree_sha="e" * 40)
    if historical == "changed":
        old_files = {**source.files, "README.md": b"Older reviewed investigation instructions\n"}
        old_entries = tuple(replace(item,
            sha256=hashlib.sha256(old_files[item.path.removeprefix(root + "/")]).hexdigest(),
            size=len(old_files[item.path.removeprefix(root + "/")])) for item in source.source_files)
        previous = replace(previous, files=MappingProxyType(old_files), source_files=old_entries,
            subtree_sha=_git_subtree_sha(old_files, old_entries, root + "/"))
        previous = replace(previous, source_content_id=solution_source_content_id(
            solution_slug=previous.solution_slug, repo_subpath=root, source_files=previous.file_manifest()))
    declaration = previous if historical else source

    class Reader:
        async def verified_ancestors(self, _head, candidates):
            if candidates is None:
                return (previous.commit_sha,) if historical else ()
            return (previous.commit_sha,) if previous.commit_sha in candidates else ()

        async def verify_ci(self, *_args):
            return None

        async def source(self, *_args):
            return desired

        async def authored_source(self, commit, *_args, **_kwargs):
            return previous if commit == previous.commit_sha else source

    delivered = await GitSourceDeliveryService(db_session, policy, Reader()).deliver(f.solution_id,
        SolutionGitSourceDeliveryRequest(source_commit_sha=source.commit_sha, ci_run_id=1,
            ci_run_attempt=1, artifact_digest=digest), GitDeliveryIdentity("2", 1))
    assert delivered.state == "already_active" and delivered.authored_source_state == "verified"
    assert delivered.deployment_id == f.base_id
    assert f.base.validation_result["github_delivery"]["receipt_id"] == str(receipt.id)
    child = SolutionDeployObligationDeclare(solution_slug=declaration.solution_slug, repo_subpath=root,
        source_subtree_sha=declaration.subtree_sha, source_content_id=declaration.source_content_id,
        source_files=declaration.file_manifest(), changed_paths={root + "/README.md": hashlib.sha256(declaration.files["README.md"]).hexdigest()},
        disposition="solution_deploy_required")
    request = WorkspaceSourceReleaseDeclareRequest(source_commit_sha=declaration.commit_sha, source_tree_sha=declaration.tree_sha,
        paths={}, disposition="non_production", reason="All authored changes belong to the explicit Solution target",
        solution_deploy_obligations=[child])
    service = WorkspaceSourceReleaseService(db_session, PROVIDER_ORG_ID)
    from src.services.github_actions_oidc import WorkspaceSourceReleaseProducer
    producer = WorkspaceSourceReleaseProducer(organization_id=PROVIDER_ORG_ID,
        source_commit_sha=declaration.commit_sha, oidc_commit_sha=source.commit_sha,
        repository=policy.repository, workflow_ref=f"{policy.repository}/.github/workflows/declare.yml@refs/heads/main",
        run_id="9", event_name="workflow_run", triggering_workflow_run_id="8") if historical else None

    async def recover_history():
        if historical:
            await GitSourceDeliveryService(db_session, policy, Reader()).deliver(f.solution_id,
                SolutionGitSourceDeliveryRequest(source_commit_sha=source.commit_sha, ci_run_id=1,
                    ci_run_attempt=1, artifact_digest=digest), GitDeliveryIdentity("2", 1))

    original_evidence = native_authored_accounting._installed_evidence
    # Engine-bound factory uses independent PostgreSQL backends. A second
    # session bound to db_session.bind would share its existing connection.
    sessions = async_session_factory

    async def checked_evidence(*args, **kwargs):
        if kwargs.get("collected") is None:
            # Red-capable SQL observation: remote proof must not hold the
            # relation-wide lock that blocks unrelated Solution updates.
            held = await db_session.scalar(text("SELECT count(*) FROM pg_locks WHERE "
                "pid=pg_backend_pid() AND relation='solutions'::regclass "
                "AND mode='ShareLock' AND granted"))
            assert held == 0
        result = await original_evidence(*args, **kwargs)
        if kwargs.get("collected") is None and recovery_fault in {"receipt", "readme", "membership"}:
            async with sessions() as writer:
                if recovery_fault == "receipt":
                    await writer.execute(update(OperationReceipt).where(OperationReceipt.id == receipt.id)
                        .values(status="failed", response=null(), error={"code": "proof_changed"}))
                elif recovery_fault == "readme":
                    await writer.execute(update(Solution).where(Solution.id == f.solution_id).values(readme="Changed after proof"))
                else:
                    writer.add(Solution(id=uuid4(), slug=f.solution.slug, repo_subpath=root,
                        name="New same-family installation", organization_id=None, execution_runtime_mode="repo-v1"))
                await asyncio.wait_for(writer.commit(), timeout=2)
        return result

    monkeypatch.setattr(native_authored_accounting, "_installed_evidence", checked_evidence)
    if recovery_fault == "cache_budget":
        monkeypatch.setattr(native_authored_accounting, "MAX_CACHED_NATIVE_BYTES", 1)
    if recovery_fault == "lock_timeout":
        async with sessions() as writer:
            assert await writer.scalar(text("SELECT pg_backend_pid()")) != await db_session.scalar(
                text("SELECT pg_backend_pid()"))
            await writer.execute(text("LOCK TABLE solutions IN ROW EXCLUSIVE MODE"))
            declared = await asyncio.wait_for(service.declare(request, created_by=platform_admin.user_id, producer=producer), timeout=4)
            await recover_history()
            await writer.rollback()
    else:
        declared = await service.declare(request, created_by=platform_admin.user_id, producer=producer)
        await recover_history()
    assert declared.source_commit_sha == declaration.commit_sha
    assert declared.source_tree_sha == declaration.tree_sha
    assert declared.disposition == "non_production"
    child_row = (await db_session.get(WorkspaceSourceRelease, declared.id)).solution_deploy_obligations[0]
    if recovery_fault:
        assert child_row.disposition == "pending" and child_row.completion_evidence is None
        # The failure must release the table fence and leave a usable session.
        async with sessions() as writer:
            await writer.execute(update(Solution).where(Solution.id == f.solution_id).values(readme="Post-fence write"))
            await asyncio.wait_for(writer.commit(), timeout=2)
        return
    assert child_row.disposition == ("superseded" if historical else "released") and child_row.deploy_job_id is None
    if historical:
        assert child_row.completion_evidence["superseded_source_commit_sha"] == previous.commit_sha
        assert child_row.completion_evidence["superseded_source_files"] == previous.file_manifest()
    assert child_row.completion_evidence["source_content_id"] == source.source_content_id
    original = child_row.completion_evidence
    replay = await service.declare(request, created_by=platform_admin.user_id)
    assert replay.id == declared.id
    assert child_row.completion_evidence == original
    await db_session.refresh(f.solution)
    assert f.solution.readme == source.files["README.md"].decode()
    assert f.solution.active_deployment_id == f.base_id


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "descriptor", "table_component", "runtime_bytes"])
async def test_authored_readme_commits_only_with_full_native_component_readback(db_session, platform_admin, monkeypatch, fault):
    f = await _seed_adopted_revision(db_session, platform_admin, monkeypatch)
    root = f"solutions/{f.solution.slug}"
    await db_session.execute(update(Solution).where(Solution.id == f.solution_id)
        .values(repo_subpath=root, readme="Prior instructions\n"))
    await db_session.commit()
    await db_session.refresh(f.solution)
    source = _source(f)
    if fault == "descriptor":
        await db_session.execute(update(Solution).where(Solution.id == f.solution_id).values(name="Other name"))
    if fault == "table_component":
        # An unrepresented owned resource must prevent the README mutation.
        await db_session.execute(update(type(f.table)).where(type(f.table).id == f.table.id).values(solution_id=f.solution_id))
    if fault == "runtime_bytes":
        f.objects[(str(f.base_id), f.path)] = f.new_source
    else:
        f.objects[(str(f.base_id), f.path)] = f.old_source
    await db_session.commit()
    monkeypatch.setattr(native_authored_source, "SolutionDeploymentStorage", source_revision.SolutionDeploymentStorage)
    policy = SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace", repository_id=1,
        repository_owner_id=1, organization_id=PROVIDER_ORG_ID, workflow_path=".github/workflows/deliver.yml",
        ci_workflow_path=".github/workflows/ci.yml", ci_workflow_id=1,
        solutions={f.solution_id: "config/solution-delivery/fixture.json"})
    runtime = VerifiedGitSource(f.solution_id, source.commit_sha, source.tree_sha,
        policy.solutions[f.solution_id], {}, {}, "sha256:" + "e" * 64)
    before_pointer, before_bytes = f.solution.active_deployment_id, f.objects[(str(f.base_id), "source")]
    result = await GitSourceDeliveryService(db_session, policy, None)._deliver_authored_readme(f.base, runtime, source)
    await db_session.commit()
    await db_session.refresh(f.solution)
    assert result == ("verified" if fault is None else "attention_required")
    assert f.solution.readme == ("Reviewed new investigation instructions\n" if fault is None else "Prior instructions\n")
    assert f.solution.active_deployment_id == before_pointer
    assert f.objects[(str(f.base_id), "source")] == before_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing_subtree", "empty_installs", "missing_targets", "wrong_digest", "wrong_commit", "generic_missing_job"])
async def test_native_completion_constraint_requires_its_actual_source_branch(async_engine, platform_admin, fault):
    source_id, solution_id = uuid4(), uuid4()
    archive = "d" * 64
    evidence = {"schema_version": "bifrost.native-solution-deploy-completion/v1",
        "source_commit_sha": "a" * 40, "source_tree_sha": "b" * 40, "source_subtree_sha": "c" * 40,
        "source_content_id": "sha256:" + "e" * 64, "solution_id": str(solution_id),
        "candidate_id": "sha256:" + archive, "source_artifact_sha256": archive,
        "target_installs": {str(solution_id): None}, "installations": {str(solution_id): {"receipt_id": str(uuid4())}}}
    if fault == "missing_subtree":
        del evidence["source_subtree_sha"]
    elif fault == "empty_installs":
        evidence["installations"] = {}
    elif fault == "missing_targets":
        del evidence["target_installs"]
    elif fault == "wrong_digest":
        evidence["source_artifact_sha256"] = "f" * 64
    elif fault == "wrong_commit":
        evidence["source_commit_sha"] = "f" * 40
    elif fault == "generic_missing_job":
        evidence["schema_version"] = "bifrost.solution-deploy-completion/v1"
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                db.add(WorkspaceSourceRelease(id=source_id, organization_id=PROVIDER_ORG_ID,
                    source_commit_sha=uuid4().hex + "a" * 8, source_tree_sha="b" * 40, paths={},
                    declaration_actor="platform_admin", declared_disposition="non_production", disposition="non_production",
                    reason="Synthetic native completion constraint proof", resolved_at=datetime.now(UTC), created_by=platform_admin.user_id))
                await db.flush()
                db.add(SolutionDeployObligation(id=uuid4(), source_release_id=source_id,
                    organization_id=PROVIDER_ORG_ID, source_commit_sha="a" * 40, source_tree_sha="b" * 40,
                    source_subtree_sha="c" * 40, source_content_id="sha256:" + "e" * 64,
                    solution_slug="fixture", repo_subpath="solutions/fixture", source_files=[], changed_paths={},
                    declared_disposition="solution_deploy_required", disposition="released", solution_id=solution_id,
                    candidate_id="sha256:" + archive, source_artifact_sha256=archive,
                    completion_evidence=evidence, resolved_at=datetime.now(UTC)))
                if fault is None:
                    await db.flush()
                else:
                    with pytest.raises(IntegrityError, match="ck_solution_deploy_obligation_released_evidence"):
                        await db.flush()
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_solution_membership_fence_preserves_plain_reads_and_blocks_membership_writes(async_engine):
    from sqlalchemy.exc import DBAPIError
    async with async_engine.connect() as first, async_engine.connect() as second:
        a, b = await first.begin(), await second.begin()
        try:
            await first.execute(text("LOCK TABLE solutions IN SHARE MODE"))
            await second.execute(text("SELECT id FROM solutions LIMIT 1"))
            # Admission/FK reads and install row-lock readers remain available.
            # The accounting census uses plain reads, so it never waits on these.
            await second.execute(text("SELECT id FROM solutions LIMIT 1 FOR KEY SHARE"))
            await second.execute(text("SELECT id FROM solutions LIMIT 1 FOR UPDATE"))
            await second.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                await second.execute(text("UPDATE solutions SET status = status WHERE false"))
        finally:
            await b.rollback()
            await a.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("unprovable_prefix", [0, 100])
async def test_alternating_install_replays_converge_on_immutable_historical_tranches(
    committed_delivery_db, async_session_factory, platform_admin, monkeypatch, unprovable_prefix,
):
    """Two installs must retain the same bounded history tranche until both prove it."""
    from contextlib import asynccontextmanager
    from datetime import timedelta

    from src import config
    from src.models.contracts.solution_deployments import (
        SolutionGitSourceDeliveryRequest,
    )
    from src.models.orm.operation_receipts import OperationReceipt
    from src.models.orm.organizations import Organization
    from src.services import operation_receipts
    from src.services.solutions import native_authored_accounting
    from src.services.solutions.authored_archive import _git_subtree_sha
    from src.services.solutions.deploy import solution_entity_id
    from src.services.solutions.deployment_manifest import sha256_digest
    from src.services.solutions.deployment_storage import SOURCE_ARTIFACTS_ROOT
    from src.services.solutions.github_delivery_source import GitDeliveryIdentity

    db = committed_delivery_db
    head = uuid4().hex + uuid4().hex[:8]
    current_tree = uuid4().hex + uuid4().hex[:8]
    tracking_org_id = uuid4()
    db.add(Organization(id=tracking_org_id, name=f"native-history-{tracking_org_id.hex[:10]}",
        created_by=str(platform_admin.user_id)))
    await db.flush()
    first = await _seed_adopted_revision(db, platform_admin, monkeypatch,
        source_commit_sha=head, organization_id=tracking_org_id)
    global_solution_id = uuid4()
    second = await _seed_adopted_revision(db, platform_admin, monkeypatch,
        source_commit_sha=head, organization_id=None, solution_id=global_solution_id,
        workflow_id=solution_entity_id(global_solution_id, first.workflow_id),
        source_path=first.path, solution_slug=first.solution.slug)
    slug = first.solution.slug
    root = f"solutions/{slug}"
    await db.execute(update(Solution).where(Solution.id == first.solution_id).values(repo_subpath=root))
    await db.execute(update(Solution).where(Solution.id == second.solution_id).values(
        repo_subpath=root))
    objects_by_deployment = {}
    installs = (first, second)
    for fixture in installs:
        fixture.objects[(str(fixture.base_id), first.path)] = fixture.old_source
        objects_by_deployment[str(fixture.base_id)] = fixture.objects
    await db.commit()
    for fixture in installs:
        await db.refresh(fixture.solution)
        await db.refresh(fixture.base)

    class Storage:
        def __init__(self, _solution_id, deployment_id):
            self.solution_id = str(_solution_id)
            self.deployment_id = str(deployment_id)
            self.objects = objects_by_deployment[self.deployment_id]

        def authored_artifact_key(self, source_content_id):
            digest = source_content_id.removeprefix("sha256:")
            return f"{SOURCE_ARTIFACTS_ROOT}/{self.solution_id}/{self.deployment_id}/authored/{digest}.zip"

        async def read_source_artifact(self):
            return self.objects[(self.deployment_id, "source")]

        async def write_source_artifact(self, content, *, idempotent=False):
            key = (self.deployment_id, "source")
            assert key not in self.objects or (idempotent and self.objects[key] == content)
            self.objects[key] = content

        async def read_runtime_file(self, path):
            return self.objects[(self.deployment_id, path)]

        async def write_authored_artifact(self, source_content_id, content, *, idempotent=False):
            key = (self.deployment_id, "authored:" + source_content_id)
            assert key not in self.objects or (idempotent and self.objects[key] == content)
            self.objects[key] = content

        async def read_authored_artifact(self, source_content_id):
            return self.objects[(self.deployment_id, "authored:" + source_content_id)]

    monkeypatch.setattr(source_revision, "SolutionDeploymentStorage", Storage)
    monkeypatch.setattr(native_authored_source, "SolutionDeploymentStorage", Storage)
    monkeypatch.setattr(native_authored_accounting, "SolutionDeploymentStorage", Storage)
    monkeypatch.setattr("src.services.solutions.deployment_storage.SolutionDeploymentStorage", Storage)

    files = {
        "bifrost.solution.yaml": yaml.safe_dump({key: getattr(first.solution, key) for key in (
            "slug", "name", "version", "repo_subpath", "allow_inbound_access", "allow_outbound_access",
            "git_connected", "git_repo_url", "git_ref")}).encode(),
        "README.md": b"Shared authored-family history\n",
        ".bifrost/workflows.yaml": yaml.safe_dump({"workflows": {
            str(first.workflow_id): {key: value for key, value in
                ManifestWorkflow.from_row(first.workflow, roles=[]).model_dump(mode="json", by_alias=True).items()
                if key != "organization_id"}}}).encode(),
        first.path: first.old_source,
        "shared/__init__.py": b"",
    }
    entries = tuple(VerifiedAuthoredSolutionFile(path=root + "/" + path, mode="100644",
        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)) for path, raw in sorted(files.items()))
    manifest_files = [{"path": entry.path, "mode": entry.mode, "sha256": entry.sha256, "size": entry.size}
        for entry in entries]
    content_id = solution_source_content_id(solution_slug=first.solution.slug,
        repo_subpath=root, source_files=manifest_files)
    subtree_sha = _git_subtree_sha(files, entries, root + "/")
    authored_head = VerifiedAuthoredSolution(commit_sha=head, tree_sha=current_tree,
        subtree_sha=subtree_sha, solution_slug=first.solution.slug, repo_subpath=root,
        source_files=entries, files=MappingProxyType(files), source_content_id=content_id)
    old_authored = {}

    # Seed older unprovable children first, then 200 eligible OIDC parent/child
    # declarations. A bounded Git ancestry lookup must filter by its verified
    # chain before taking the historical tranche limit.
    origin_time = datetime.now(UTC) - timedelta(days=3)
    child_id_start = (uuid4().int >> 16) << 16
    commit_prefix, tree_prefix = uuid4().hex, uuid4().hex
    unprovable_commit_prefix, unprovable_tree_prefix = uuid4().hex, uuid4().hex
    for index in range(unprovable_prefix + 200):
        eligible = index >= unprovable_prefix
        anchor_index = index - unprovable_prefix + 1 if eligible else index + 1
        commit = (commit_prefix if eligible else unprovable_commit_prefix) + f"{anchor_index:08x}"
        tree = (tree_prefix if eligible else unprovable_tree_prefix) + f"{anchor_index:08x}"
        if eligible:
            prior = replace(authored_head, commit_sha=commit, tree_sha=tree)
            old_authored[commit] = prior
        release = WorkspaceSourceRelease(
            id=uuid4(), organization_id=tracking_org_id, source_commit_sha=commit,
            source_tree_sha=tree, paths={}, declaration_actor="github_actions_oidc",
            producer_oidc_commit_sha=commit, producer_event_name="push", producer_run_id=str(index + 1),
            disposition="pending", declared_disposition="pending", created_by=platform_admin.user_id,
            created_at=origin_time + timedelta(seconds=index),
        )
        child = SolutionDeployObligation(
            id=UUID(int=child_id_start + index), source_release=release, organization_id=tracking_org_id,
            source_commit_sha=commit, source_tree_sha=tree, solution_slug=first.solution.slug,
            repo_subpath=root, source_subtree_sha=subtree_sha, source_content_id=content_id,
            source_files=manifest_files, changed_paths={}, declared_disposition="solution_deploy_required",
            disposition="pending", created_at=origin_time + timedelta(seconds=index), updated_at=origin_time,
        )
        db.add(release)
        db.add(child)
    await db.commit()
    for fixture in installs:
        await db.refresh(fixture.solution)
        await db.refresh(fixture.base)

    registry = {"path": "config/solution-delivery/installations.json", "target": "production",
        "installations": {str(f.solution_id): {"recipe_path": "config/solution-delivery/family.json",
            "organization_id": str(f.solution.organization_id) if f.solution.organization_id else None,
            "repo_subpath": root, "package_subpaths": [root]} for f in installs}}
    recipe_by_solution = {f.solution_id: "config/solution-delivery/family.json" for f in installs}
    policy = SolutionGitDeliveryPolicy(
        repository="MTG-Thomas/bifrost-workspace", repository_id=1, repository_owner_id=1,
        organization_id=tracking_org_id, workflow_path=".github/workflows/deliver.yml",
        ci_workflow_path=".github/workflows/ci.yml", ci_workflow_id=1,
        solutions=recipe_by_solution,
        solution_organization_ids={f.solution_id: f.solution.organization_id for f in installs},
    )
    settings = config.get_settings().model_copy(update={"solution_git_delivery_policy": policy,
        "workspace_source_release_oidc_organization_id": str(tracking_org_id)})
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    @asynccontextmanager
    async def receipt_context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr(operation_receipts, "get_db_context", receipt_context)
    evidence_calls = {"prefence": 0, "fence": 0, "errors": {}}
    original_installed_evidence = native_authored_accounting._installed_evidence

    async def observe_installed_evidence(*args, collected=None, **kwargs):
        phase = "fence" if collected is not None else "prefence"
        evidence_calls[phase] += 1
        try:
            return await original_installed_evidence(*args, collected=collected, **kwargs)
        except Exception as exc:
            name = type(exc).__name__
            evidence_calls["errors"][name] = evidence_calls["errors"].get(name, 0) + 1
            raise

    completions = []
    original_complete = native_authored_accounting._complete_native_obligations

    async def observe_completion(*args, **kwargs):
        try:
            result = await original_complete(*args, **kwargs)
        except asyncio.CancelledError:
            completions.append({"cancelled": True})
            raise
        completions.append({"cancelled": False, "settled": len(result)})
        return result

    monkeypatch.setattr(native_authored_accounting, "_installed_evidence", observe_installed_evidence)
    monkeypatch.setattr(native_authored_accounting, "_complete_native_obligations", observe_completion)
    current_git = VerifiedGitSource(
        first.solution_id, head, current_tree, "config/solution-delivery/family.json",
        {first.path: sha256_digest(first.old_source)}, {first.path: first.old_source},
        "sha256:" + "d" * 64,
        repository_paths={first.path: root + "/" + first.path},
        installation_registry=registry,
    )

    class Reader:
        async def verify_ci(self, *_args):
            return None

        async def source(self, solution_id, *_args):
            return replace(current_git, solution_id=solution_id,
                recipe_path=recipe_by_solution[solution_id])

        async def verified_ancestors(self, _head, candidates):
            if candidates is None:
                return tuple(sorted(old_authored))
            return tuple(sorted(set(candidates) & set(old_authored)))

        async def authored_source(self, commit, *_args, **_kwargs):
            return old_authored.get(commit, authored_head)

    reader = Reader()
    request = SolutionGitSourceDeliveryRequest(source_commit_sha=head, ci_run_id=1,
        ci_run_attempt=1, artifact_digest="sha256:" + "d" * 64)
    identities = {first.solution_id: GitDeliveryIdentity("family-A", 1),
        second.solution_id: GitDeliveryIdentity("family-B", 1)}
    for fixture in installs:
        replay = await GitSourceDeliveryService(db, policy, reader).deliver(
            fixture.solution_id, request, identities[fixture.solution_id])
        assert replay.state == "already_active" and replay.authored_source_state == "verified"

    # The synthetic Git reader supplies CI/ancestry evidence; each replay
    # records durable receipts and checks the actual active pointer and archive.
    # Alternating targets must keep the same first
    # tranche selected until its aggregate two-install proof is complete.
    for fixture in installs + installs[:1]:
        replay = await GitSourceDeliveryService(db, policy, reader).deliver(
            fixture.solution_id, request, identities[fixture.solution_id])
        assert replay.state == "already_active" and replay.authored_source_state == "verified"

    await db.flush()
    rows = list((await db.scalars(select(SolutionDeployObligation).where(
        SolutionDeployObligation.solution_slug == slug,
        SolutionDeployObligation.repo_subpath == root).order_by(SolutionDeployObligation.created_at,
            SolutionDeployObligation.id))).all())
    assert len(rows) == unprovable_prefix + 200
    eligible_rows = rows[unprovable_prefix:]
    unprovable_rows = rows[:unprovable_prefix]
    disposition_counts = {state: sum(row.disposition == state for row in eligible_rows)
        for state in ("superseded", "pending", "attention_required", "released")}
    cancellation_count = sum(item["cancelled"] for item in completions)
    unresolved_sample = [{"id": str(row.id), "commit": row.source_commit_sha[:12],
        "disposition": row.disposition, "has_evidence": row.completion_evidence is not None}
        for row in eligible_rows if row.disposition != "superseded"][:10]
    assert all(row.disposition == "superseded" for row in eligible_rows), (
        f"Eligible disposition counts: {disposition_counts}; unresolved sample: {unresolved_sample}; "
        f"completions: {completions}; installed-evidence calls: {evidence_calls}")
    assert completions, "Native accounting did not call the real tranche completion path"
    assert cancellation_count == 0, f"Completion fence cancellations: {completions}"
    assert evidence_calls["fence"] <= len(completions) * len(installs), (
        f"Fence readbacks were not bounded per install: {evidence_calls}; completions: {completions}")
    assert all(row.disposition == "pending" and row.completion_evidence is None
        for row in unprovable_rows)
    expected_targets = {str(f.solution_id) for f in installs}
    assert all(set(row.completion_evidence["installations"]) == expected_targets for row in eligible_rows)
    for row in eligible_rows:
        for fixture in installs:
            proof = row.completion_evidence["installations"][str(fixture.solution_id)]
            assert proof["source_commit_sha"] == head
            assert proof["historical_authored_source"]["commit_sha"] == row.source_commit_sha
            assert proof["readback"]["solution_id"] == str(fixture.solution_id)
    for fixture in installs:
        deployment = await db.get(type(fixture.base), fixture.base_id, populate_existing=True)
        proof = deployment.validation_result["github_delivery"]
        assert proof["authored_source"]["source_content_id"] == content_id
        receipt_id = UUID(proof["receipt_id"])
        receipt = await db.get(OperationReceipt, receipt_id, populate_existing=True)
        assert receipt is not None and receipt.status == "succeeded"
