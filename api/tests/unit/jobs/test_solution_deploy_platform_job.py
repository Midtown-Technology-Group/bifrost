import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.jobs.platform.solution_deploy import (
    SolutionDeployPayload,
    _SolutionDeployReporter,
    run_solution_deploy,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution


@pytest.mark.asyncio
async def test_deploy_reporter_bounds_progress_and_tolerates_timing_failure():
    context = AsyncMock()
    reporter = _SolutionDeployReporter(context, last_percent=2)
    for percent in (50, 50, 20, 150):
        await reporter.progress("Publishing", percent)
    assert [call.kwargs["percent"] for call in context.report.await_args_list] == [50, 99]
    context.log.side_effect = RuntimeError("diagnostic transport unavailable")
    await reporter.timing("redis_module_cache", 12)
    context.log.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", [PlatformJobCancelled, asyncio.CancelledError])
async def test_deploy_progress_preserves_cancellation_and_lease_rejection(signal):
    context = AsyncMock()
    context.report.side_effect = signal
    reporter = _SolutionDeployReporter(context)
    with pytest.raises(signal):
        await reporter.progress("Publishing", 70)


@pytest.mark.asyncio
async def test_failed_repo_install_cleanup_commits_before_job_failure(monkeypatch):
    deploy_job_id = uuid4()
    install_id = uuid4()
    projection = SolutionDeployJob(
        id=deploy_job_id,
        install_id=install_id,
        status="running",
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
    assert projection.status == "failed"
    assert projection.install_id is None
    db.flush.assert_awaited_once()
    db.delete.assert_awaited_once_with(orphan)


@pytest.mark.asyncio
async def test_reviewed_package_uses_its_own_publication_and_recovery_handler(monkeypatch):
    job_id, install_id = uuid4(), uuid4()
    payload = SolutionDeployPayload(
        deploy_job_id=job_id, install_id=install_id, kind="deliver_package",
        input_sha256="a" * 64, options={},
    )
    context = AsyncMock()
    context.job_id = job_id
    result = {"original_job_id": str(job_id), "deployment_id": str(uuid4())}
    package_worker = AsyncMock(return_value=result)
    manual_deploy, manual_install, cleanup = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(
        "src.jobs.platform.solution_package_delivery.run_solution_package_delivery", package_worker,
    )
    monkeypatch.setattr("src.routers.solutions._run_deploy_job", manual_deploy)
    monkeypatch.setattr("src.routers.solutions._run_install_job", manual_install)
    monkeypatch.setattr("src.jobs.platform.solution_deploy.SolutionDeployJobStorage.delete", cleanup)
    assert await run_solution_deploy(context, payload) == result
    package_worker.assert_awaited_once_with(context, payload)
    manual_deploy.assert_not_awaited()
    manual_install.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", [PlatformJobCancelled, asyncio.CancelledError, RuntimeError])
async def test_activation_progress_failure_preserves_control_signal_through_retry(monkeypatch, signal):
    from src.services.solutions import deploy

    context = AsyncMock()
    # A retry could appear healthy; the first rejected lease must remain final.
    context.report.side_effect = [signal(), None]
    reporter = _SolutionDeployReporter(context, last_percent=90)
    with deploy.solution_deploy_instrumentation(
        deploy.SolutionDeployInstrumentation(progress_callback=reporter.progress)
    ):
        deployer = deploy.SolutionDeployer(AsyncMock())
    activate, upload, sleep = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(deployer, "_activate_compiled_dists", activate)
    monkeypatch.setattr("src.services.solutions.app_build.SolutionAppBuilder.upload_deployment", upload)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    item = deploy.CompiledSolutionAppDeployment(
        app_id=uuid4(), solution_id=uuid4(), deployment_id=uuid4(),
        expected_old_deployment_id=None, superseded_deployment_id=None,
        dist={"index.html": b"app"}, runtime_pin={},
    )
    async def publish():
        await deployer._upload_compiled_dists([item])

    if signal is RuntimeError:
        await deploy._retry_idempotent("upload apps", item.solution_id, publish)
        activate.assert_awaited_once_with([item])
    else:
        with pytest.raises(signal):
            await deploy._retry_idempotent("upload apps", item.solution_id, publish)
        activate.assert_not_awaited()
    upload.assert_awaited_once()
    context.report.assert_awaited_once_with("Activating Solution applications", percent=95)
    sleep.assert_not_awaited()
