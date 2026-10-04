"""Native authored metadata and journal completion use actual PostgreSQL."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import uuid4

import pytest
import yaml
from bifrost.manifest import ManifestWorkflow
from sqlalchemy import null, text, update
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
@pytest.mark.parametrize("unrelated_mutable", [False, True])
@pytest.mark.parametrize("recovery_fault", [None, "receipt", "readme", "membership", "lock_timeout", "cache_budget"])
async def test_successful_receipt_replay_then_late_declaration_settles_native_authored_source(
    committed_delivery_db, async_session_factory, platform_admin, monkeypatch, unrelated_mutable, recovery_fault,
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

    class Reader:
        async def verify_ci(self, *_args):
            return None

        async def source(self, *_args):
            return desired

        async def authored_source(self, *_args, **_kwargs):
            return source

    delivered = await GitSourceDeliveryService(db_session, policy, Reader()).deliver(f.solution_id,
        SolutionGitSourceDeliveryRequest(source_commit_sha=source.commit_sha, ci_run_id=1,
            ci_run_attempt=1, artifact_digest=digest), GitDeliveryIdentity("2", 1))
    assert delivered.state == "already_active" and delivered.authored_source_state == "verified"
    assert delivered.deployment_id == f.base_id
    assert f.base.validation_result["github_delivery"]["receipt_id"] == str(receipt.id)
    child = SolutionDeployObligationDeclare(solution_slug=source.solution_slug, repo_subpath=root,
        source_subtree_sha=source.subtree_sha, source_content_id=source.source_content_id,
        source_files=source.file_manifest(), changed_paths={root + "/README.md": hashlib.sha256(source.files["README.md"]).hexdigest()},
        disposition="solution_deploy_required")
    request = WorkspaceSourceReleaseDeclareRequest(source_commit_sha=source.commit_sha, source_tree_sha=source.tree_sha,
        paths={}, disposition="non_production", reason="All authored changes belong to the explicit Solution target",
        solution_deploy_obligations=[child])
    service = WorkspaceSourceReleaseService(db_session, PROVIDER_ORG_ID)
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
            declared = await asyncio.wait_for(service.declare(request, created_by=platform_admin.user_id), timeout=4)
            await writer.rollback()
    else:
        declared = await service.declare(request, created_by=platform_admin.user_id)
    assert declared.source_commit_sha == commit_sha
    assert declared.source_tree_sha == source.tree_sha
    assert declared.disposition == "non_production"
    child_row = (await db_session.get(WorkspaceSourceRelease, declared.id)).solution_deploy_obligations[0]
    if recovery_fault:
        assert child_row.disposition == "pending" and child_row.completion_evidence is None
        # The failure must release the table fence and leave a usable session.
        async with sessions() as writer:
            await writer.execute(update(Solution).where(Solution.id == f.solution_id).values(readme="Post-fence write"))
            await asyncio.wait_for(writer.commit(), timeout=2)
        return
    assert child_row.disposition == "released" and child_row.deploy_job_id is None
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
