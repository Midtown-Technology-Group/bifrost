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
from src.jobs.platform.base import PlatformJobContext, PlatformJobRequiresAction
from src.jobs.platform.solution_deploy import SolutionDeployPayload, run_solution_deploy
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.services.solutions.deploy_job_storage import SolutionDeployJobStorage
from src.services.solutions.package_controls import capture_package_controls
from tests.unit.services.solutions.test_package_runtime import compile_app as compile_app
from tests.unit.test_solution_app_deploy import _reviewed_package_source


@pytest.mark.e2e
async def test_committed_package_lost_ack_recovers_without_reprepare_or_republication(
    db_session, async_session_factory, seed_user, compile_app, monkeypatch,
):
    from src.jobs.platform import solution_package_delivery as worker
    from src.services import platform_jobs

    assert await db_session.scalar(text("SELECT current_database()")) == "bifrost_test"
    solution = Solution(id=uuid4(), slug=f"package-{uuid4().hex[:8]}", name="Package", organization_id=None)
    db_session.add(solution)
    await db_session.flush()
    source = _reviewed_package_source(solution, runtime=True, source_commit_sha=uuid4().hex + uuid4().hex[:8])
    policy = SolutionPackageGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace",
        repository_id=1197464564, repository_owner_id=87775189, organization_id=PROVIDER_ORG_ID,
        workflow_path=".github/workflows/deliver-solutions.yml", ci_workflow_path=".github/workflows/ci.yml",
        ci_workflow_id=257449914, packages={solution.id: {"organization_id": None,
            "repo_subpath": source.authored.repo_subpath,
            "recipe_path": "config/solution-package-delivery/fixture.json"}})
    envelope = {**source.evidence(), "repository": policy.repository, "repository_id": policy.repository_id,
        "repository_owner_id": policy.repository_owner_id, "recipe_path": policy.packages[solution.id].recipe_path,
        "source_subtree_sha": source.authored.subtree_sha, "ci_run_id": 1, "ci_run_attempt": 1}
    source = replace(source, evidence_json=json.dumps(envelope).encode(), artifact_digest=canonical_digest(envelope))
    settings = get_settings().model_copy(update={"solution_package_git_delivery_policy": policy})
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr("src.services.solutions.github_delivery_source.ProtectedGitReader.verify_ci", AsyncMock())
    from src.services.solutions.package_admission import package_publication_id
    job_id, lease_token = package_publication_id(solution.id, source.artifact_digest), uuid4()
    payload = SolutionDeployPayload(deploy_job_id=job_id, kind="deliver_package", install_id=solution.id,
        input_sha256=hashlib.sha256(source.source_archive).hexdigest(), options={
            "package_source": source.evidence(), "artifact_digest": source.artifact_digest,
            "delivery_git_token": "test-only-token",
            "expected_active_deployment_id": None,
            "expected_controls_digest": canonical_digest(await capture_package_controls(db_session, solution.id))})
    db_session.add(SolutionDeployJob(id=job_id, install_id=solution.id, status="running"))
    db_session.add(PlatformJob(id=job_id, job_type="solution.deploy", payload_version=1,
        payload={}, encrypted_payload=encrypt_secret(payload.model_dump_json()), status="running",
        lease_token=lease_token, lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        requested_by_user_id=str(SYSTEM_USER_UUID), requested_by_email=SYSTEM_USER_EMAIL,
        requested_by_name="Package test", title="Package test", organization_id=None))
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
    monkeypatch.setattr(worker, "get_db_context", commit_then_lose_ack)
    calls = {"compile": 0, "stage": 0}
    original_compile, original_stage = worker.compile_package_runtime, worker.stage_package_runtime

    async def count_compile(*args, **kwargs):
        calls["compile"] += 1
        return await original_compile(*args, **kwargs)

    async def count_stage(*args, **kwargs):
        calls["stage"] += 1
        return await original_stage(*args, **kwargs)

    monkeypatch.setattr(worker, "compile_package_runtime", count_compile)
    monkeypatch.setattr(worker, "stage_package_runtime", count_stage)
    context = PlatformJobContext(job_id, lease_token, None, str(SYSTEM_USER_UUID), SYSTEM_USER_EMAIL, "Package test")
    with pytest.raises(PlatformJobRequiresAction) as unknown:
        await run_solution_deploy(context, payload)
    assert lost
    async with async_session_factory() as independent:
        active = await independent.get(Solution, solution.id)
        assert active is not None and active.active_deployment_id is not None
        assert active.execution_runtime_mode == "deployment-v1"
        projection = await independent.get(SolutionDeployJob, job_id)
        assert projection is not None and projection.status == "succeeded"
    monkeypatch.setattr(worker, "get_db_context", ordinary_context)
    recovered = await run_solution_deploy(replace(context, checkpoint=unknown.value.result), payload)
    assert recovered["recovered_from_intent"] and recovered["source_verified"]
    assert recovered["runtime_verified"] and recovered["registrations_verified"]
    assert len(recovered["workflow_runtime_pins"]) == len(recovered["app_runtime_pins"]) == 1
    assert calls == {"compile": 1, "stage": 1}
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
    assert calls == {"compile": 1, "stage": 1}
    from src.services.solutions.package_admission import inspect_package_job, read_package_accounting
    inspected = await inspect_package_job(db_session, solution.id, source.artifact_digest)
    # Its old completion was not tracked before the declaration. Inspection
    # uses the current ledger without mutating the old job result or publishing.
    old_result = dict(inspected.result)
    assert (await read_package_accounting(db_session, inspected))["verified"] is True
    assert inspected.result == old_result
    assert calls == {"compile": 1, "stage": 1}
