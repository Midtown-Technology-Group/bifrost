"""Real PostgreSQL must reject incomplete Solution accounting evidence."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.workspace_promotions import WorkspaceSourceRelease
from tests.e2e.platform.test_solution_source_revision import (
    _seed_adopted_revision,
    db_session as db_session,
)

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing_schema", "missing_commit", "missing_tree", "wrong_commit",
    "wrong_schema", "empty_paths", "missing_paths", "paths_not_object", "missing_resolution"])
async def test_solution_completion_constraint_requires_exact_nonempty_evidence(async_engine, platform_admin, fault):
    source_sha, tree_sha = uuid4().hex + "a" * 8, "b" * 40
    evidence = {"schema_version": "bifrost.solution-owned-source-completion/v1",
        "source_commit_sha": source_sha, "source_tree_sha": tree_sha,
        "paths": {"features/fixture.py": {"sha256": "c" * 64}}}
    if fault == "missing_schema":
        del evidence["schema_version"]
    elif fault == "missing_commit":
        del evidence["source_commit_sha"]
    elif fault == "missing_tree":
        del evidence["source_tree_sha"]
    elif fault == "wrong_commit":
        evidence["source_commit_sha"] = "f" * 40
    elif fault == "wrong_schema":
        evidence["schema_version"] = "unverified"
    elif fault == "empty_paths":
        evidence["paths"] = {}
    elif fault == "missing_paths":
        del evidence["paths"]
    elif fault == "paths_not_object":
        evidence["paths"] = []
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                db.add(WorkspaceSourceRelease(id=uuid4(), organization_id=PROVIDER_ORG_ID,
                    source_commit_sha=source_sha, source_tree_sha=tree_sha,
                    paths={"features/fixture.py": "c" * 64}, declaration_actor="platform_admin",
                    declared_disposition="pending", disposition="released", created_by=platform_admin.user_id,
                    release_row_id=None, completion_evidence=evidence,
                    resolved_at=None if fault == "missing_resolution" else datetime.now(UTC)))
                if fault is None:
                    await db.flush()
                else:
                    with pytest.raises(IntegrityError, match="ck_workspace_source_release_released_evidence"):
                        await db.flush()
        finally:
            await transaction.rollback()


@pytest_asyncio.fixture
async def accounting_install(db_session, platform_admin, monkeypatch):
    """Exclusive synthetic consumers inside an outer rollback transaction."""
    from src import config
    from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
    from src.models.orm.executions import Execution
    from src.models.orm.execution_attempts import ExecutionAttempt
    from src.models.orm.operation_receipts import OperationReceipt
    from src.models.orm.solutions import Solution
    from src.models.orm.workflows import Workflow
    from src.models.enums import ExecutionStatus
    from src.services import solution_source_accountability as accounting
    from src.services.operation_receipts import canonical_operation_scope_key, canonical_request_fingerprint
    from src.services.solutions import source_revision

    assert await db_session.scalar(text("SELECT current_database()")) == "bifrost_test"
    # Other E2E fixtures are not evidence for this synthetic installation. These
    # changes are confined to the fixture's outer transaction and rolled back.
    await db_session.execute(update(Solution).values(status="inactive"))
    await db_session.execute(update(Workflow).values(is_active=False))
    await db_session.execute(update(Execution).values(status=ExecutionStatus.SUCCESS))
    await db_session.execute(update(ExecutionAttempt).values(completed_at=datetime.now(UTC)))
    commit, tree = "a" * 40, "b" * 40
    f = await _seed_adopted_revision(db_session, platform_admin, monkeypatch, source_commit_sha=commit)
    f.objects[(str(f.base_id), f.path)] = f.old_source
    monkeypatch.setattr(accounting, "SolutionDeploymentStorage", source_revision.SolutionDeploymentStorage)
    policy = SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace", repository_id=1197464564,
        repository_owner_id=87775189, organization_id=PROVIDER_ORG_ID,
        workflow_path=".github/workflows/deliver-solutions.yml", ci_workflow_path=".github/workflows/ci.yml",
        ci_workflow_id=257449914, solutions={f.solution_id: "config/solution-delivery/fixture.json"})
    settings = SimpleNamespace(solution_git_delivery_policy=policy,
        workspace_source_release_oidc_organization_id=str(PROVIDER_ORG_ID),
        workspace_source_release_oidc_repository=policy.repository,
        workspace_source_release_oidc_repository_id=policy.repository_id,
        workspace_source_release_oidc_repository_owner_id=policy.repository_owner_id)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(accounting, "get_settings", lambda: settings)
    manifest = f.manifest
    identity = {"repository_id": policy.repository_id, "solution_id": str(f.solution_id),
        "source_commit_sha": commit, "artifact_digest": "sha256:" + "c" * 64,
        "ci_run_id": 123, "ci_run_attempt": 1, "producer_run_id": "456", "producer_run_attempt": 1}
    now = datetime.now(UTC)
    receipt = OperationReceipt(id=uuid4(), namespace="solution.git-source-delivery", status="succeeded",
        scope_key=canonical_operation_scope_key(identity), request_fingerprint=canonical_request_fingerprint(identity),
        response={}, completed_at=now, expires_at=now + timedelta(days=1))
    db_session.add(receipt)
    await db_session.flush()
    digest = manifest.workflows[f"{f.path}::run"].source_hash
    proof = {**identity, "repository": policy.repository, "repository_owner_id": policy.repository_owner_id,
        "solution_id": str(f.solution_id), "organization_id": str(PROVIDER_ORG_ID), "commit_sha": commit,
        "tree_sha": tree, "receipt_id": str(receipt.id), "source_mapping_schema": accounting.MAPPING_SCHEMA,
        "repository_paths": {f.path: f.path}, "runtime_hashes": {f.path: digest},
        "control_hashes": {}, "recipe_path": policy.solutions[f.solution_id]}
    f.policy, f.proof, f.receipt, f.commit, f.tree, f.digest = policy, proof, receipt, commit, tree, digest
    return f


async def _attach_accounting_proof(db, f):
    from src.models.orm.solution_deployments import SolutionDeployment
    await db.execute(update(SolutionDeployment).where(SolutionDeployment.id == f.base_id).values(
        validation_result={"schema_version": "bifrost.workspace-live-handoff/v1", "github_delivery": f.proof})
        .execution_options(synchronize_session=False))


@pytest.mark.asyncio
async def test_late_declaration_replay_settles_from_installed_readback_without_manual_disposition(
    db_session, platform_admin, accounting_install
):
    from src.models.contracts.workspace_promotions import WorkspaceSourceReleaseDeclareRequest
    from src.services.workspace_source_releases import WorkspaceSourceReleaseService
    f = accounting_install
    service = WorkspaceSourceReleaseService(db_session, PROVIDER_ORG_ID)
    request = WorkspaceSourceReleaseDeclareRequest(source_commit_sha=f.commit, source_tree_sha=f.tree,
        paths={f.path: f.digest.removeprefix("sha256:")}, disposition="pending")
    declared = await service.declare(request, created_by=platform_admin.user_id)
    assert declared.disposition == "pending"
    record = await db_session.get(WorkspaceSourceRelease, declared.id)
    record.disposition, record.reason, record.due_at = "attention_required", "release deadline elapsed", datetime.now(UTC) - timedelta(hours=1)
    original_digest = record.declaration_digest
    await db_session.commit()
    await _attach_accounting_proof(db_session, f)
    observed = await service.declare(request, created_by=platform_admin.user_id)
    assert observed.id == declared.id and observed.disposition == "released"
    assert observed.release_row_id is None
    assert observed.completion_evidence["paths"][f.path]["consumers"][0]["deployment_id"] == str(f.base_id)
    assert record.declaration_digest == original_digest and record.reason is None
    assert (await service.declare(request, created_by=platform_admin.user_id)).completion_evidence == observed.completion_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["bytes", "mapping", "receipt", "registration", "mutable", "legacy_execution"])
async def test_database_consumer_drift_keeps_source_unresolved(db_session, platform_admin, accounting_install, fault):
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution
    from src.models.orm.solutions import Solution
    from src.models.orm.workflows import Workflow
    from src.services.solution_source_accountability import reconcile_solution_owned_source
    f = accounting_install
    if fault == "bytes":
        f.objects[(str(f.base_id), f.path)] = f.new_source
    elif fault == "mapping":
        f.proof["repository_paths"] = {}
    elif fault == "receipt":
        f.receipt.request_fingerprint = "f" * 64
    elif fault == "registration":
        await db_session.execute(update(Workflow).where(Workflow.id == f.workflow_id).values(name="stale registration"))
    elif fault == "mutable":
        await db_session.execute(update(Solution).where(Solution.id == f.solution_id).values(execution_runtime_mode="legacy"))
    else:
        db_session.add(Execution(id=uuid4(), workflow_name="unproven accepted work", executed_by_name="fixture",
            workflow_id=f.workflow_id, status=ExecutionStatus.PENDING))
    await _attach_accounting_proof(db_session, f)
    record = WorkspaceSourceRelease(id=uuid4(), organization_id=PROVIDER_ORG_ID,
        source_commit_sha=f.commit, source_tree_sha=f.tree, paths={f.path: f.digest.removeprefix("sha256:")},
        disposition="pending", declared_disposition="pending", declaration_actor="platform_admin",
        created_by=platform_admin.user_id)
    db_session.add(record)
    await db_session.flush()
    assert await reconcile_solution_owned_source(db_session) == []
    assert record.disposition == "pending" and record.completion_evidence is None
