"""A real mixed package commit with lost ACK recovers by readback only."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from bifrost.workspace_release import canonical_digest
from src.config import get_settings
from src.core.constants import PROVIDER_ORG_ID, SYSTEM_USER_UUID, SYSTEM_USER_EMAIL
from src.core.security import encrypt_secret
from src.core.solution_package_delivery_policy import SolutionPackageGitDeliveryPolicy
from src.jobs.platform.base import PlatformJobContext, PlatformJobFailure, PlatformJobRequiresAction
from src.jobs.platform.solution_deploy import SolutionDeployPayload, run_solution_deploy
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.services.solutions.deploy_job_storage import SolutionDeployJobStorage
from src.services.solutions.package_controls import capture_package_controls
from tests.unit.services.solutions.test_package_runtime import compile_app as compile_app
from tests.unit.test_solution_app_deploy import _reviewed_package_source


@pytest.mark.e2e
@pytest.mark.parametrize("include_app", [False, True])
@pytest.mark.parametrize("interruption", [
    "lost_ack", "stage_failure", "superseded_before_retry",
    "main_changed_during_stage", "ci_attempt_changed_during_stage",
])
async def test_package_recovery_distinguishes_committed_and_rolled_back_transactions(
    db_session, async_session_factory, seed_user, compile_app, monkeypatch, interruption, include_app,
):
    from src.jobs.platform import solution_package_delivery as worker
    from src.services import platform_jobs

    assert await db_session.scalar(text("SELECT current_database()")) == "bifrost_test"
    solution = Solution(id=uuid4(), slug=f"package-{uuid4().hex[:8]}", name="Package", organization_id=None,
        version="2.0.0", execution_runtime_mode="repo-v1")
    db_session.add(solution)
    await db_session.flush()
    solution_id = solution.id
    source = _reviewed_package_source(solution, runtime=True, source_commit_sha=uuid4().hex + uuid4().hex[:8],
        source_version="1.0.0", include_app=include_app)
    policy = SolutionPackageGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace",
        repository_id=1197464564, repository_owner_id=87775189, organization_id=PROVIDER_ORG_ID,
        workflow_path=".github/workflows/deliver-solutions.yml", ci_workflow_path=".github/workflows/ci.yml",
        ci_workflow_id=257449914, packages={solution_id: {"organization_id": None,
            "repo_subpath": source.authored.repo_subpath,
            "recipe_path": "config/solution-package-delivery/fixture.json"}})
    envelope = {**source.evidence(), "repository": policy.repository, "repository_id": policy.repository_id,
        "repository_owner_id": policy.repository_owner_id, "recipe_path": policy.packages[solution_id].recipe_path,
        "source_subtree_sha": source.authored.subtree_sha, "ci_run_id": 1, "ci_run_attempt": 1}
    source = replace(source, evidence_json=json.dumps(envelope).encode(), artifact_digest=canonical_digest(envelope))
    settings = get_settings().model_copy(update={"solution_package_git_delivery_policy": policy})
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr("src.services.solutions.github_delivery_source.ProtectedGitReader.verify_ci", AsyncMock())
    from src.services.solutions.package_admission import package_publication_id
    job_id, lease_token = package_publication_id(solution_id, source.artifact_digest), uuid4()
    payload = SolutionDeployPayload(deploy_job_id=job_id, kind="deliver_package", install_id=solution_id,
        input_sha256=hashlib.sha256(source.source_archive).hexdigest(), options={
            "package_source": source.evidence(), "artifact_digest": source.artifact_digest,
            "delivery_git_token": "test-only-token",
            "expected_active_deployment_id": None,
            "expected_controls_digest": canonical_digest(await capture_package_controls(db_session, solution_id))})
    db_session.add(SolutionDeployJob(id=job_id, install_id=solution_id, status="running"))
    db_session.add(PlatformJob(id=job_id, job_type="solution.deploy", payload_version=1,
        payload={}, encrypted_payload=encrypt_secret(payload.model_dump_json()), status="running",
        lease_token=lease_token, lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        requested_by_user_id=str(SYSTEM_USER_UUID), requested_by_email=SYSTEM_USER_EMAIL,
        requested_by_name="Package test", title="Package test", organization_id=None,
        dedupe_key=str(job_id), resource_type="solution_deploy", resource_id=str(job_id),
        resource_lock_key=f"solution:{solution_id}", priority=500))
    await db_session.commit()
    await SolutionDeployJobStorage(job_id).write_bytes(source.source_archive)

    @asynccontextmanager
    async def ordinary_context():
        async with async_session_factory() as session:
            yield session
            await session.commit()

    lost = False

    @asynccontextmanager
    async def commit_then_lose_ack():
        nonlocal lost
        async with async_session_factory() as session:
            yield session
            await session.commit()
            if not lost:
                lost = True
                raise TimeoutError("Acknowledgment lost after actual package commit")

    monkeypatch.setattr(platform_jobs, "get_db_context", ordinary_context)
    monkeypatch.setattr(worker, "get_db_context", commit_then_lose_ack if interruption == "lost_ack" else ordinary_context)
    calls = {"compile": 0, "stage": 0}
    original_compile, original_stage = worker.compile_package_runtime, worker.stage_package_runtime

    async def count_compile(*args, **kwargs):
        calls["compile"] += 1
        return await original_compile(*args, **kwargs)

    async def count_stage(*args, **kwargs):
        calls["stage"] += 1
        result = await original_stage(*args, **kwargs)
        if interruption in {"main_changed_during_stage", "ci_attempt_changed_during_stage"}:
            from src.services.solutions.github_delivery_source import GitDeliverySourceError
            reason = "Current Main advanced" if interruption == "main_changed_during_stage" else "CI attempt changed"
            monkeypatch.setattr("src.services.solutions.github_delivery_source.ProtectedGitReader.verify_ci",
                AsyncMock(side_effect=GitDeliverySourceError(reason)))
        elif interruption != "lost_ack" and calls["stage"] == 1:
            raise TimeoutError("Storage failure before actual package commit")
        return result

    monkeypatch.setattr(worker, "compile_package_runtime", count_compile)
    monkeypatch.setattr(worker, "stage_package_runtime", count_stage)
    context = PlatformJobContext(job_id, lease_token, None, str(SYSTEM_USER_UUID), SYSTEM_USER_EMAIL, "Package test")
    with pytest.raises(PlatformJobRequiresAction) as unknown:
        await run_solution_deploy(context, payload)
    assert lost is (interruption == "lost_ack")
    async with async_session_factory() as independent:
        active = await independent.get(Solution, solution_id)
        assert active is not None
        projection = await independent.get(SolutionDeployJob, job_id)
        assert projection is not None
        if interruption == "lost_ack":
            assert active.active_deployment_id is not None and active.execution_runtime_mode == "deployment-v1"
            assert projection.status == "succeeded"
            assert active.version == "1.0.0" and active.upgraded_from_version == "2.0.0"
        else:
            from uuid import UUID
            from src.models.orm.solution_deployments import SolutionDeployment
            assert active.active_deployment_id is None and active.execution_runtime_mode == "repo-v1"
            assert active.version == "2.0.0"
            assert await independent.get(SolutionDeployment, UUID(unknown.value.result["deployment_id"])) is None
            from sqlalchemy import select
            from src.models.orm.applications import Application
            assert list((await independent.scalars(select(Application).where(
                Application.solution_id == solution_id))).all()) == []
    monkeypatch.setattr(worker, "get_db_context", ordinary_context)
    if interruption in {"superseded_before_retry", "main_changed_during_stage", "ci_attempt_changed_during_stage"}:
        from src.services.solutions.github_delivery_source import GitDeliverySourceError
        from src.services.solutions.package_admission import recover_pending_package, read_package_rollback
        ci = AsyncMock(side_effect=GitDeliverySourceError("Current Main advanced"))
        monkeypatch.setattr("src.services.solutions.github_delivery_source.ProtectedGitReader.verify_ci", ci)
        with pytest.raises(PlatformJobFailure) as refused:
            await run_solution_deploy(replace(context, checkpoint=unknown.value.result), payload)
        assert refused.value.code == "package_prepublication_refused"
        assert refused.value.result is not None
        assert await platform_jobs.finish_platform_job(job_id, lease_token, status="failed", result=refused.value.result)
        await db_session.rollback()
        # The actual SQL selector must surface the terminal rollback receipt,
        # not pretend this older publication never existed or requeue it.
        retained = await recover_pending_package(db_session, policy, solution_id)
        assert retained is not None and retained.id == job_id and retained.status == "failed"
        rollback_proof = await read_package_rollback(db_session, retained)
        assert rollback_proof is not None and rollback_proof["verified"] is True
        assert calls == {"compile": 1, "stage": 1}
        return
    recovered = await run_solution_deploy(replace(context, checkpoint=unknown.value.result), payload)
    assert recovered["source_verified"]
    assert recovered.get("recovered_from_intent", False) is (interruption == "lost_ack")
    if interruption == "stage_failure":
        assert recovered["recovered_from_rollback"] is True
        assert recovered["deployment_id"] != unknown.value.result["deployment_id"]
    assert recovered["runtime_verified"] and recovered["registrations_verified"]
    assert len(recovered["workflow_runtime_pins"]) == 1
    assert len(recovered["app_runtime_pins"]) == int(include_app)
    expected_calls = {"compile": 1, "stage": 1} if interruption == "lost_ack" else {"compile": 2, "stage": 2}
    assert calls == expected_calls
    assert await platform_jobs.finish_platform_job(job_id, lease_token, status="succeeded", result=recovered)

    # Declaration arrives AFTER publication/recovery. The ordinary declaration
    # hook must settle it from this original job without another deployment.
    from src import config
    from src.models.contracts.workspace_promotions import (
        SolutionDeployObligationDeclare, WorkspaceSourceReleaseDeclareRequest,
    )
    from src.services.workspace_source_releases import WorkspaceSourceReleaseService
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    authored = worker.staged_package_source(source.source_archive, envelope, source.artifact_digest).authored
    child = SolutionDeployObligationDeclare(solution_slug=authored.solution_slug,
        repo_subpath=authored.repo_subpath, source_subtree_sha=authored.subtree_sha,
        source_content_id=authored.source_content_id, source_files=authored.file_manifest(),
        changed_paths={item.path: item.sha256 for item in authored.source_files},
        disposition="solution_deploy_required")
    declaration = WorkspaceSourceReleaseDeclareRequest(source_commit_sha=authored.commit_sha,
        source_tree_sha=authored.tree_sha, paths={}, disposition="non_production",
        reason="Only complete Solution source changed", solution_deploy_obligations=[child])
    response = await WorkspaceSourceReleaseService(db_session, PROVIDER_ORG_ID).declare(
        declaration, created_by=seed_user.id)
    # Parent response retains the immutable declaration; child status lives in
    # the canonical obligation row, rather than the declaration DTO.
    from sqlalchemy import select
    from src.models.orm.workspace_promotions import SolutionDeployObligation
    receipt = await db_session.scalar(select(SolutionDeployObligation).where(
        SolutionDeployObligation.source_release_id == response.id))
    assert receipt is not None and receipt.disposition == "released"
    assert calls == expected_calls
    from src.services.solutions.package_admission import inspect_package_job, read_package_accounting
    inspected = await inspect_package_job(db_session, solution_id, source.artifact_digest)
    # Its old completion was not tracked before the declaration. Inspection
    # uses the current ledger without mutating the old job result or publishing.
    old_result = dict(inspected.result)
    assert (await read_package_accounting(db_session, inspected))["verified"] is True
    assert inspected.result == old_result
    assert calls == expected_calls


@pytest.mark.asyncio
async def test_busy_solution_does_not_abort_other_package_accounting(monkeypatch):
    from types import SimpleNamespace
    from src.jobs.platform import solution_package_delivery as worker
    from src.jobs.platform.solution_deploy import SolutionDeployPayload
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.package_runtime import PACKAGE_RUNTIME_SCHEMA
    from src.services.solutions.write_lock import SolutionWriteLockHeld

    busy, available, child = uuid4(), uuid4(), uuid4()
    policy = SolutionPackageGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace",
        repository_id=1197464564, repository_owner_id=87775189, organization_id=PROVIDER_ORG_ID,
        workflow_path=".github/workflows/deliver-solutions.yml", ci_workflow_path=".github/workflows/ci.yml",
        ci_workflow_id=257449914, packages={sid: {"organization_id": None,
            "repo_subpath": f"solutions/package-{sid.hex[:8]}",
            "recipe_path": f"config/solution-package-delivery/{sid}.json"} for sid in [busy, available]})
    settings = get_settings().model_copy(update={"solution_package_git_delivery_policy": policy})
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    rows, released = {}, []
    for sid in [busy, available]:
        did, jid = uuid4(), uuid4()
        envelope = {"package": {"solution_id": str(sid)}}
        payload = SolutionDeployPayload(deploy_job_id=jid, kind="deliver_package", install_id=sid,
            input_sha256="c" * 64, options={"package_source": envelope, "artifact_digest": "sha256:" + "f" * 64})
        rows[Solution, sid] = SimpleNamespace(status="active", active_deployment_id=did,
            organization_id=None, slug=f"package-{sid.hex[:8]}")
        rows[SolutionDeployment, did] = SimpleNamespace(id=did, compiled_manifest={"package_evidence": {
            "schema_version": PACKAGE_RUNTIME_SCHEMA, "publication_job_id": str(jid), "source": envelope}})
        rows[PlatformJob, jid] = SimpleNamespace(id=jid, status="succeeded", organization_id=None,
            requested_by_user_id=str(SYSTEM_USER_UUID), encrypted_payload=encrypt_secret(payload.model_dump_json()),
            result={"deployment_id": str(did)})

    async def lookup(model, identity, **_kwargs):
        return rows.get((model, identity))

    @asynccontextmanager
    async def lock(sid):
        if sid == busy:
            raise SolutionWriteLockHeld(str(sid))
        try:
            yield
        finally:
            released.append(sid)

    db = SimpleNamespace(get=AsyncMock(side_effect=lookup), scalar=AsyncMock(return_value=None), commit=AsyncMock())
    monkeypatch.setattr(worker, "solution_write_lock", lock)
    monkeypatch.setattr("src.services.solutions.deployment_storage.SolutionDeploymentStorage.read_source_artifact",
        AsyncMock(return_value=b"retained package"))
    monkeypatch.setattr(worker, "staged_package_source", lambda *_: SimpleNamespace(authored=SimpleNamespace(
        solution_slug="available", source_content_id="sha256:" + "c" * 64)))
    settle = AsyncMock(return_value={"obligation_ids": [str(child)]})
    monkeypatch.setattr("src.services.solution_deploy_obligations.reconcile_solution_deploy_obligation", settle)
    assert await worker.reconcile_reviewed_package_obligations(db) == [child]
    assert settle.await_args.kwargs["solution_id"] == available
    assert released == [available]
    db.commit.assert_awaited_once()
