from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost.workspace_release import canonical_digest
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure, PlatformJobRequiresAction
from src.jobs.platform.solution_deploy import (
    SOLUTION_DEPLOY_INTENT_SCHEMA,
    SolutionDeployPayload,
    run_solution_deploy,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution


@pytest.mark.asyncio
async def test_failed_repo_install_cleanup_commits_before_job_failure(monkeypatch):
    deploy_job_id = uuid4()
    install_id = uuid4()
    projection = SolutionDeployJob(
        id=deploy_job_id,
        install_id=install_id,
        status="failed",
        error="manifest invalid",
    )
    orphan = Solution(id=install_id, slug="failed-install", name="Failed install")

    class FakeDB:
        def __init__(self) -> None:
            self.flush = AsyncMock()
            self.delete = AsyncMock()

        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            if model is SolutionDeployJob and row_id == deploy_job_id:
                return projection
            if model is Solution and row_id == install_id:
                return orphan
            return None

    db = FakeDB()
    transaction_committed = False

    @asynccontextmanager
    async def fake_db_context():
        nonlocal transaction_committed
        yield db
        transaction_committed = True

    context = AsyncMock()
    context.job_id = deploy_job_id
    context.checkpoint = None
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.SolutionDeployJobStorage.copy_to_path",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.SolutionDeployJobStorage.delete",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.get_db_context", fake_db_context
    )
    monkeypatch.setattr("src.routers.solutions._run_deploy_job", AsyncMock())

    with pytest.raises(PlatformJobFailure, match="manifest invalid"):
        await run_solution_deploy(
            context,
            SolutionDeployPayload(
                deploy_job_id=deploy_job_id,
                kind="install_from_repo",
                install_id=install_id,
                input_sha256="a" * 64,
                options={},
            ),
        )

    assert transaction_committed is True
    assert projection.install_id is None
    db.flush.assert_awaited_once()
    db.delete.assert_awaited_once_with(orphan)


@pytest.mark.asyncio
async def test_uncertain_commit_retains_input_and_install_without_replaying(monkeypatch):
    job_id, install_id = uuid4(), uuid4()
    payload = SolutionDeployPayload(deploy_job_id=job_id, install_id=install_id,
        kind="install_from_repo", input_sha256="a" * 64, options={})
    context = SimpleNamespace(job_id=job_id, organization_id=None, checkpoint=None,
        report=AsyncMock(), log=AsyncMock(), save_checkpoint=AsyncMock())
    projection = SolutionDeployJob(id=job_id, install_id=install_id, status="failed", error="lost commit acknowledgement")
    db = SimpleNamespace(get=AsyncMock(return_value=projection), scalar=AsyncMock(return_value=None),
        delete=AsyncMock(), flush=AsyncMock())

    @asynccontextmanager
    async def session():
        yield db

    async def original_attempt(*_args, before_commit, **_kwargs):
        await before_commit(install_id)
        # The router captured the uncertain outcome on its projection.

    deploy = AsyncMock(side_effect=original_attempt)
    staging = AsyncMock()
    monkeypatch.setattr("src.jobs.platform.solution_deploy.get_db_context", session)
    monkeypatch.setattr("src.routers.solutions._run_deploy_job", deploy)
    monkeypatch.setattr("src.jobs.platform.solution_deploy.SolutionDeployJobStorage.copy_to_path", AsyncMock())
    monkeypatch.setattr("src.jobs.platform.solution_deploy.SolutionDeployJobStorage.delete", staging)
    with pytest.raises(PlatformJobRequiresAction) as stopped:
        await run_solution_deploy(context, payload)
    intent = stopped.value.result
    assert intent["schema_version"] == SOLUTION_DEPLOY_INTENT_SCHEMA
    assert intent["original_job_id"] == str(job_id)
    assert intent["solution_id"] == str(install_id)
    context.save_checkpoint.assert_awaited_once()
    staging.assert_not_awaited()
    db.delete.assert_not_awaited()
    assert projection.install_id == install_id

    context.checkpoint = intent
    with pytest.raises(PlatformJobRequiresAction):
        await run_solution_deploy(context, payload)
    # Even a delayed retry never calls either effectful operation again.
    deploy.assert_awaited_once()
    staging.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [None, "source", "runtime", "scope", "newer", "payload", "target", "not_completed"])
async def test_completed_deploy_recovery_checks_original_bytes_and_runtime(monkeypatch, drift):
    job_id, install_id = uuid4(), uuid4()
    source = b"original retained source"
    sha = hashlib.sha256(source).hexdigest()
    payload = SolutionDeployPayload(deploy_job_id=job_id, install_id=install_id,
        kind="deploy", input_sha256=sha, options={"candidate_id": f"sha256:{sha}"})
    result = {"solution_id": str(install_id), "candidate_id": f"sha256:{sha}"}
    intent = {"schema_version": SOLUTION_DEPLOY_INTENT_SCHEMA, "original_job_id": str(job_id),
        "solution_id": str(install_id), "organization_id": None,
        "payload_digest": canonical_digest(payload.model_dump(mode="json"))}
    if drift == "payload":
        payload.options["force"] = True
    if drift == "target":
        intent["solution_id"] = str(uuid4())
    projection = SolutionDeployJob(id=job_id, install_id=install_id,
        status="running" if drift == "not_completed" else "succeeded", result=result,
        created_at=datetime.now(timezone.utc))
    solution = Solution(id=install_id, slug="retained", name="Retained",
        organization_id=uuid4() if drift == "scope" else None)
    db = SimpleNamespace(get=AsyncMock(side_effect=[projection, solution]),
        scalar=AsyncMock(return_value=uuid4() if drift == "newer" else None))

    @asynccontextmanager
    async def session(*_args):
        yield db

    effect = AsyncMock()
    runtime = AsyncMock(return_value=(drift != "runtime", "runtime differs", {"verified": True}))
    monkeypatch.setattr("src.jobs.platform.solution_deploy.get_db_context", session)
    monkeypatch.setattr("src.services.solutions.write_lock.solution_write_lock", session)
    monkeypatch.setattr("src.services.solutions.source_artifact.SolutionSourceArtifactStorage.read",
        AsyncMock(return_value=b"different" if drift == "source" else source))
    monkeypatch.setattr("src.services.solution_deploy_obligations._runtime_and_registration_readback", runtime)
    monkeypatch.setattr("src.routers.solutions._run_deploy_job", effect)
    monkeypatch.setattr("src.routers.solutions._run_install_job", effect)
    monkeypatch.setattr("src.jobs.platform.solution_deploy.SolutionDeployJobStorage.copy_to_path", effect)
    context = SimpleNamespace(job_id=job_id, organization_id=None, checkpoint=intent, report=AsyncMock())
    if drift is None:
        recovered = await run_solution_deploy(context, payload)
        assert recovered["recovered_from_intent"] is True
        assert recovered["original_job_id"] == str(job_id)
        runtime.assert_awaited_once_with(db, solution_id=install_id, artifact=source)
    else:
        with pytest.raises(PlatformJobRequiresAction) as stopped:
            await run_solution_deploy(context, payload)
        assert stopped.value.result == intent
    effect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", [PlatformJobCancelled, asyncio.CancelledError])
async def test_interrupted_runner_keeps_original_input_for_next_lease(monkeypatch, interruption):
    job_id, install_id = uuid4(), uuid4()
    context = SimpleNamespace(job_id=job_id, organization_id=None, checkpoint=None,
        report=AsyncMock(), save_checkpoint=AsyncMock(side_effect=interruption))
    payload = SolutionDeployPayload(deploy_job_id=job_id, install_id=install_id,
        kind="deploy", input_sha256="a" * 64, options={})

    @asynccontextmanager
    async def session():
        yield SimpleNamespace(scalar=AsyncMock(return_value=None))

    async def attempt(*_args, before_commit, **_kwargs):
        await before_commit(install_id)
        pytest.fail("lost lease must stop before commit")

    cleanup = AsyncMock()
    monkeypatch.setattr("src.jobs.platform.solution_deploy.get_db_context", session)
    monkeypatch.setattr("src.routers.solutions._run_deploy_job", AsyncMock(side_effect=attempt))
    monkeypatch.setattr("src.jobs.platform.solution_deploy.SolutionDeployJobStorage.copy_to_path", AsyncMock())
    monkeypatch.setattr("src.jobs.platform.solution_deploy.SolutionDeployJobStorage.delete", cleanup)
    with pytest.raises(interruption):
        await run_solution_deploy(context, payload)
    cleanup.assert_not_awaited()
