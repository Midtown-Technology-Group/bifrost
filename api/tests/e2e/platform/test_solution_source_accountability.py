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
    from src.models.orm.executions import Execution, WorkflowExecutionAttempt
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
    await db_session.execute(update(WorkflowExecutionAttempt).values(status="succeeded",
        phase="terminal", completed_at=datetime.now(UTC)))
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
    from src.services.solution_source_accountability import _collect_consumers
    consumers, loose, uncertain = await _collect_consumers(db_session, f.policy)
    assert not loose and not uncertain and len(consumers) == 1
    observed = await service.declare(request, created_by=platform_admin.user_id)
    assert observed.id == declared.id and observed.disposition == "released"
    assert observed.release_row_id is None
    assert observed.completion_evidence["paths"][f.path]["consumers"][0]["deployment_id"] == str(f.base_id)
    assert record.declaration_digest == original_digest and record.reason is None
    assert (await service.declare(request, created_by=platform_admin.user_id)).completion_evidence == observed.completion_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["bytes", "mapping", "receipt", "registration", "mutable", "legacy_execution", "workflow_attempt"])
async def test_database_consumer_drift_keeps_source_unresolved(db_session, platform_admin, accounting_install, fault):
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution, WorkflowExecutionAttempt
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
    elif fault == "workflow_attempt":
        accepted = Execution(id=uuid4(), workflow_name="Terminal parent with accepted attempt",
            executed_by_name="fixture", workflow_id=f.workflow_id, status=ExecutionStatus.SUCCESS,
            completed_at=datetime.now(UTC))
        db_session.add(accepted)
        await db_session.flush()
        db_session.add(WorkflowExecutionAttempt(execution_id=accepted.id,
            attempt_number=1, claim_token=uuid4(), status="running", phase="execution",
            published_at=datetime.now(UTC), claimed_at=datetime.now(UTC), started_at=datetime.now(UTC)))
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


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_mode", ["repo-v1", "deployment-v1"])
async def test_later_unpinned_install_blocks_completion_before_any_bundle_read(
    db_session, platform_admin, accounting_install, monkeypatch, runtime_mode,
):
    """A valid earlier install must not trigger I/O ahead of a known blocker."""
    from unittest.mock import Mock
    from uuid import UUID
    from src.models.orm.solutions import Solution
    from src.services import solution_source_accountability as accounting

    f = accounting_install
    await _attach_accounting_proof(db_session, f)
    later_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    assert f.solution_id < later_id
    db_session.add(Solution(id=later_id, slug="accounting-unpinned-blocker",
        name="Unpinned accounting blocker", organization_id=PROVIDER_ORG_ID,
        status="active", execution_runtime_mode=runtime_mode, active_deployment_id=None))
    record = WorkspaceSourceRelease(id=uuid4(), organization_id=PROVIDER_ORG_ID,
        source_commit_sha=f.commit, source_tree_sha=f.tree,
        paths={f.path: f.digest.removeprefix("sha256:")}, disposition="pending",
        declared_disposition="pending", declaration_actor="platform_admin",
        created_by=platform_admin.user_id)
    db_session.add(record)
    await db_session.flush()
    storage = Mock(side_effect=AssertionError("Known blocker must precede bundle I/O"))
    monkeypatch.setattr(accounting, "SolutionDeploymentStorage", storage)

    assert await accounting.reconcile_solution_owned_source(db_session) == []
    storage.assert_not_called()
    assert record.disposition == "pending" and record.completion_evidence is None


@pytest.mark.asyncio
async def test_unknown_consumer_rotates_examined_obligations_without_claiming_completion(
    db_session, platform_admin, accounting_install,
):
    """A real mutable-install blocker must not freeze the oldest debt forever."""
    from src.models.orm.solutions import Solution
    from src.services.solution_source_accountability import reconcile_solution_owned_source

    f = accounting_install
    await _attach_accounting_proof(db_session, f)
    db_session.add(Solution(id=uuid4(), slug="accounting-rotation-blocker",
        name="Unproven mutable consumer", organization_id=PROVIDER_ORG_ID,
        status="active", execution_runtime_mode="repo-v1", active_deployment_id=None))
    records = [WorkspaceSourceRelease(id=uuid4(), organization_id=PROVIDER_ORG_ID,
        source_commit_sha=uuid4().hex + "a" * 8, source_tree_sha=f.tree,
        paths={f.path: f.digest.removeprefix("sha256:")}, disposition="pending",
        declared_disposition="pending", declaration_actor="platform_admin",
        created_by=platform_admin.user_id, created_at=datetime.now(UTC) + timedelta(seconds=i))
        for i in range(2)]
    db_session.add_all(records)
    await db_session.flush()
    assert await reconcile_solution_owned_source(db_session, limit=1) == []
    assert records[0].accounting_checked_at is not None
    assert records[1].accounting_checked_at is None
    await db_session.commit()
    assert await reconcile_solution_owned_source(db_session, limit=1) == []
    assert records[1].accounting_checked_at is not None
    assert all(row.disposition == "pending" and row.completion_evidence is None for row in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", ["rotation", "exact_replay"])
async def test_recovery_reaches_older_eligible_source_behind_100_blockers(
    db_session, platform_admin, accounting_install, recovery
):
    from src.models.contracts.workspace_promotions import WorkspaceSourceReleaseDeclareRequest
    from src.services.workspace_source_releases import WorkspaceSourceReleaseService
    from src.services.solution_source_accountability import reconcile_solution_owned_source
    f = accounting_install
    request = WorkspaceSourceReleaseDeclareRequest(source_commit_sha=f.commit, source_tree_sha=f.tree,
        paths={f.path: f.digest.removeprefix("sha256:")}, disposition="pending")
    service = WorkspaceSourceReleaseService(db_session, PROVIDER_ORG_ID)
    eligible = await service.declare(request, created_by=platform_admin.user_id)
    blockers = [WorkspaceSourceRelease(id=uuid4(), organization_id=PROVIDER_ORG_ID,
        source_commit_sha=uuid4().hex + "c" * 8, source_tree_sha="d" * 40,
        paths={"unmapped.py": "e" * 64}, disposition="pending", declared_disposition="pending",
        declaration_actor="platform_admin", created_by=platform_admin.user_id,
        created_at=datetime.now(UTC) + timedelta(seconds=i)) for i in range(100)]
    db_session.add_all(blockers)
    await db_session.commit()
    await _attach_accounting_proof(db_session, f)
    if recovery == "rotation":
        assert await reconcile_solution_owned_source(db_session) == []
        await db_session.commit()
        assert await reconcile_solution_owned_source(db_session) == [eligible.id]
    else:
        assert (await service.declare(request, created_by=platform_admin.user_id)).disposition == "released"
    assert (await db_session.get(WorkspaceSourceRelease, eligible.id)).disposition == "released"
    assert all(row.disposition == "pending" and row.completion_evidence is None for row in blockers)


@pytest.mark.asyncio
async def test_module_alias_is_unproven_through_real_loose_consumer_collection(
    db_session, platform_admin, accounting_install, monkeypatch
):
    import hashlib
    from unittest.mock import AsyncMock
    from src.models.orm.workflows import Workflow
    from src.services import solution_source_accountability as accounting
    f = accounting_install
    await _attach_accounting_proof(db_session, f)
    path = "features/aliased_loose.py"
    raw = b"import bifrost as bf\nasync def read():\n    return await bf.files.read('" + f.path.encode() + b"')\n"
    loose = Workflow(id=uuid4(), name="Aliased Root reader", path=path, function_name="read",
        type="workflow", organization_id=PROVIDER_ORG_ID, solution_id=None, is_active=True)
    db_session.add(loose)
    await db_session.flush()
    release = SimpleNamespace(governed_paths={path}, runtime_storage_prefix="fixture/",
        source_hashes={path: hashlib.sha256(raw).hexdigest()})
    monkeypatch.setattr(accounting, "global_active_workspace_release_descriptor", AsyncMock(return_value=release))
    monkeypatch.setattr(accounting, "inspect_workspace_release_registration_bindings",
        AsyncMock(return_value=[SimpleNamespace(workflow_id=loose.id, status="bound")]))
    monkeypatch.setattr(accounting, "WorkspaceReleaseStorage",
        lambda _: SimpleNamespace(read_many=AsyncMock(return_value={path: raw})))
    consumers, hashes, uncertain = await accounting._collect_consumers(db_session, f.policy)
    assert uncertain and f.path not in hashes
    record = SimpleNamespace(id=uuid4(), source_commit_sha=f.commit, source_tree_sha=f.tree,
        paths={f.path: f.digest.removeprefix("sha256:")}, disposition="pending")
    assert accounting.completion_for_source(record, consumers, loose_hashes=hashes,
        uncertain_loose=uncertain, verified_at=datetime.now(UTC)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_path", ["async", "scheduled"])
async def test_accounting_waits_for_admission_selected_before_pointer_activation(
    async_session_factory, platform_admin, monkeypatch, dispatch_path
):
    """Pause actual dispatch after D1 selection; D2 must not hide that pin."""
    import asyncio
    import base64
    from contextlib import asynccontextmanager
    from sqlalchemy import select
    from src.models.contracts.solution_deployments import (
        SolutionSourceFile, SolutionSourceRevisionRequest, SolutionSourceRevisionCommitRequest,
    )
    from src.models.orm.executions import Execution
    from src.models.orm.solutions import Solution
    from src.models.orm.workflows import Workflow
    from src.models.enums import ExecutionStatus
    from src.services.execution import async_executor, retry_policy
    from src.services.solutions.source_revision import SolutionSourceRevisionService
    from src.services.workspace_release_projection import acquire_workspace_release_lock
    from src.services.solution_source_accountability import SourceConsumer, completion_for_source

    selected, resume, checking = asyncio.Event(), asyncio.Event(), asyncio.Event()
    execution_id = uuid4()
    original_retry = retry_policy.workflow_retry_policy_snapshot

    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session
    monkeypatch.setattr("src.core.database.get_db_context", context)

    async def paused_retry(db, workflow_id):
        selected.set()
        await resume.wait()
        return await original_retry(db, workflow_id)
    monkeypatch.setattr(retry_policy, "workflow_retry_policy_snapshot", paused_retry)

    async with async_session_factory() as writer:
        assert await writer.scalar(text("SELECT current_database()")) == "bifrost_test"
        f = await _seed_adopted_revision(writer, platform_admin, monkeypatch)
        request = SolutionSourceRevisionRequest(expected_active_deployment_id=f.base_id,
            expected_active_manifest_hash=f.manifest.content_hash(), source_commit_sha="f" * 40,
            files=[SolutionSourceFile(path=f.path, content_base64=base64.b64encode(f.new_source).decode())])
        reviewed = await SolutionSourceRevisionService(writer).stage(
            f.solution_id, f.revision_id, platform_admin.user_id, request)
        await writer.commit()
        caller = SimpleNamespace(solution_deployment_id=None, event=None,
            org_id=str(PROVIDER_ORG_ID), user_id=str(platform_admin.user_id), name="Admission fixture",
            email="admission@example.invalid", startup=None, form_inputs={}, embed=None, is_platform_admin=True)
        async def dispatch():
            if dispatch_path == "async":
                return await async_executor._persist_execution_pin(caller, str(execution_id),
                    str(f.workflow_id), {}, None, form_id=None, sync=False, api_key_id=None, file_path=None)
            from src.routers.workflows import _insert_scheduled_execution
            async with async_session_factory() as db:
                return await _insert_scheduled_execution(db=db, workflow_id=f.workflow_id,
                    workflow_name="Admission fixture", parameters={}, scheduled_at=datetime.now(UTC),
                    organization_id=PROVIDER_ORG_ID, executed_by=platform_admin.user_id,
                    executed_by_name=caller.name, form_id=None, is_platform_admin=True,
                    execution_id=execution_id)
        admission = asyncio.create_task(dispatch())
        scan = None
        try:
            await asyncio.wait_for(selected.wait(), 10)
            activated = await SolutionSourceRevisionService(writer).activate(f.solution_id, f.revision_id,
                SolutionSourceRevisionCommitRequest(expected_active_deployment_id=f.base_id,
                    expected_active_manifest_hash=f.manifest.content_hash(), expected_evidence_id=reviewed.evidence_id))
            await writer.commit()
            assert activated.state == "active"
            scan_pid = []

            async def accounting_scan():
                async with async_session_factory() as db:
                    scan_pid.append(await db.scalar(text("SELECT pg_backend_pid()")))
                    checking.set()
                    await acquire_workspace_release_lock(db, None)
                    pin = await db.scalar(select(Execution.solution_deployment_id).where(Execution.id == execution_id))
                    await db.commit()
                    return pin
            scan = asyncio.create_task(accounting_scan())
            await asyncio.wait_for(checking.wait(), 10)
            # Observe PostgreSQL's waiting lock, not a timing guess that a task
            # has not finished yet. PgBouncer retains the backend in this xact.
            async def wait_for_fence():
                async with async_session_factory() as observer:
                    while not await observer.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid=:pid AND locktype='advisory' AND NOT granted)"), {"pid": scan_pid[0]}):
                        if scan.done():
                            raise AssertionError("Accounting passed the admission window")
                        await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_for_fence(), 10)
            resume.set()
            await asyncio.wait_for(admission, 10)
            old_pin = await asyncio.wait_for(scan, 10)
            assert old_pin == f.base_id
            expected = "c" * 64
            record = SimpleNamespace(id=uuid4(), disposition="pending", source_commit_sha="f" * 40,
                source_tree_sha="e" * 40, paths={f.path: expected})
            current = SourceConsumer(str(f.revision_id), str(f.solution_id), str(PROVIDER_ORG_ID),
                "sha256:" + "d" * 64, {f.path: {f.path: expected}},
                {"commit_sha": record.source_commit_sha, "tree_sha": record.source_tree_sha,
                    "receipt_id": "receipt", "artifact_digest": "artifact"})
            retained = SourceConsumer(str(old_pin), str(f.solution_id), str(PROVIDER_ORG_ID),
                f.manifest.content_hash(), {f.path: {f.path: "a" * 64}}, admission=False)
            assert completion_for_source(record, [current, retained], loose_hashes={}, uncertain_loose=False,
                verified_at=datetime.now(UTC)) is None
        finally:
            resume.set()
            for task in (admission, scan):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (admission, scan) if task is not None), return_exceptions=True)
            await writer.rollback()
            # Keep immutable fixture history, remove it from future admission.
            await writer.execute(update(Solution).where(Solution.id == f.solution_id).values(status="inactive"))
            await writer.execute(update(Workflow).where(Workflow.id == f.workflow_id).values(is_active=False))
            await writer.execute(update(Execution).where(Execution.id == execution_id).values(status=ExecutionStatus.CANCELLED))
            await writer.commit()
