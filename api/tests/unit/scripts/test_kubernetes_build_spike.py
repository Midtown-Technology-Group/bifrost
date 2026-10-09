"""The disposable build lab must fail closed and restore its owned override."""

import hashlib
import json
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from scripts import kubernetes_build_spike as spike


def job(*, status="running", backend="kubernetes"):
    fields = dict.fromkeys(spike.JobSnapshot.__dataclass_fields__)
    fields.update(id=uuid4(), job_type="application.deploy", status=status,
                  execution_backend=backend, kubernetes_pod_uid="fixture-pod",
                  memory_limit_bytes=64 * 1024 * 1024)
    return SimpleNamespace(**fields)


@pytest.fixture
def database(monkeypatch):
    db = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.get = AsyncMock()
    db.execute = AsyncMock()
    @asynccontextmanager
    async def context():
        yield db
    monkeypatch.setattr(spike, "get_db_context", context)
    return db


@pytest.mark.parametrize("environment,backend,enabled", [
    ("production", "kubernetes", "1"), ("testing", "local", "1"),
    ("testing", "kubernetes", "0"), ("testing", "kubernetes", "1"),
])
def test_environment_guard_requires_all_three_local_lab_conditions(monkeypatch, environment, backend, enabled):
    monkeypatch.setenv("BIFROST_KUBERNETES_SPIKE", enabled)
    monkeypatch.setattr(spike, "get_settings", lambda: SimpleNamespace(environment=environment, platform_build_backend=backend))
    if environment == "testing" and backend == "kubernetes" and enabled == "1":
        spike._guard_environment()
    else:
        with pytest.raises(RuntimeError):
            spike._guard_environment()


def test_archive_contains_a_real_app_and_explicitly_labeled_synthetic_delay(tmp_path):
    path = tmp_path / "source.zip"
    digest = spike._write_vite_source_zip(path, marker="fixture", hold_seconds=2)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"package.json", "index.html", "src/main.ts", "vite.config.ts"}
        assert json.loads(archive.read("package.json"))["scripts"] == {"build": "vite build"}
        assert b"2000" in archive.read("vite.config.ts")
    stamp = datetime(2026, 10, 7, tzinfo=UTC)
    assert spike._json_default(stamp) == stamp.isoformat()
    assert spike._json_default(uuid4())
    assert spike._json_default(4) == "4"


@pytest.mark.asyncio
async def test_database_read_preserves_requested_order_and_missing_job_is_not_success(database):
    first, second = job(), job()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [second, first]
    database.execute.return_value = result
    assert await spike._load_jobs([first.id, second.id]) == [first, second]
    result.scalars.return_value.all.return_value = []
    with pytest.raises(RuntimeError, match="disappeared"):
        await spike._load_job(first.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "failed", "canceled", "timeout"])
async def test_running_proof_requires_every_job_and_actual_pod_identity(monkeypatch, case):
    item = job(status=case if case in {"failed", "canceled"} else "running")
    monkeypatch.setattr(spike, "_load_jobs", AsyncMock(return_value=[item]))
    if case == "timeout":
        item.kubernetes_pod_uid = None
        monkeypatch.setattr(spike, "BUILD_TIMEOUT_SECONDS", -1)
    if case == "success":
        assert await spike._wait_for_builds_running([item.id]) == [item]
    else:
        with pytest.raises(TimeoutError if case == "timeout" else RuntimeError):
            await spike._wait_for_builds_running([item.id])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "failed", "canceled", "running"])
async def test_terminal_and_success_checks_do_not_confuse_failure_or_timeout(monkeypatch, status):
    item = job(status=status)
    monkeypatch.setattr(spike, "_load_job", AsyncMock(return_value=item))
    if status == "running":
        with pytest.raises(TimeoutError):
            await spike._wait_for_job_terminal(item.id, timeout_seconds=-1)
    else:
        assert await spike._wait_for_job_terminal(item.id, timeout_seconds=1) is item
        if status == "succeeded":
            assert await spike._wait_for_job_succeeded(item.id, timeout_seconds=1) is item
        else:
            with pytest.raises(RuntimeError, match="did not succeed"):
                await spike._wait_for_job_succeeded(item.id, timeout_seconds=1)


@pytest.mark.asyncio
async def test_seed_creates_standalone_app_without_solution_or_org_retarget(database):
    app = await spike._seed_application("fixture", 2)
    assert app.app_model == "standalone_v2"
    assert app.organization_id is None
    assert app.solution_id is None
    database.add.assert_called_once_with(app)
    database.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["deploy", "maintenance", "sdk"])
@pytest.mark.parametrize("reused", [False, True])
async def test_enqueue_binds_exact_resource_and_does_not_publish_unexpected_reuse(database, monkeypatch, tmp_path, kind, reused):
    app = SimpleNamespace(id=uuid4(), slug="fixture", active_deployment_id=uuid4(), sdk_package_version="fixture", sdk_fingerprint="a" * 64, sdk_contract_version=13, sdk_built_at=None)
    database.get.return_value = app
    queued = job(status="queued")
    enqueue = AsyncMock(return_value=(queued, reused))
    publish = AsyncMock()
    storage = MagicMock()
    storage.write_path = AsyncMock(return_value=("b" * 64, 42))
    monkeypatch.setattr(spike, "enqueue_platform_job", enqueue)
    monkeypatch.setattr(spike, "publish_platform_job_update", publish)
    monkeypatch.setattr(spike, "ApplicationDeployStorage", lambda _id: storage)
    operation = (spike._enqueue_deploy_job(app, tmp_path / "fixture.zip") if kind == "deploy" else
                 spike._enqueue_sdk_update_job(app) if kind == "sdk" else spike._enqueue_maintenance_job("fixture"))
    if reused:
        with pytest.raises(RuntimeError, match="reused"):
            await operation
        publish.assert_not_awaited()
        database.commit.assert_not_awaited()
    else:
        assert await operation is queued
        publish.assert_awaited_once_with(queued)
        args = enqueue.await_args
        assert args.kwargs["organization_id"] is None
        if kind != "maintenance":
            assert args.kwargs["resource_lock_key"] == f"application:{app.id}"
            assert args.args[2].application_id == app.id
        if kind == "sdk":
            assert args.args[2].expected_active_deployment_id == app.active_deployment_id


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "null", "unreadable", "success"])
async def test_deployment_readback_requires_exact_active_bytes(database, monkeypatch, case):
    app = SimpleNamespace(active_deployment_id=None if case == "null" else uuid4(), sdk_package_version="fixture", sdk_fingerprint="sealed", sdk_contract_version=13, sdk_built_at=None)
    database.get.return_value = None if case == "missing" else app
    builder = SimpleNamespace(read_dist=AsyncMock(return_value=b"not html" if case == "unreadable" else b"<!doctype html>"))
    monkeypatch.setattr(spike, "SolutionAppBuilder", lambda: builder)
    app_id = uuid4()
    if case == "success":
        result = await spike._verify_application_deployment(app_id)
        assert result["active_deployment_id"] == str(app.active_deployment_id)
        builder.read_dist.assert_awaited_once_with(app_id, "index.html", deployment_id=app.active_deployment_id)
    else:
        with pytest.raises(RuntimeError):
            await spike._verify_application_deployment(app_id)
        if case in {"missing", "null"}:
            builder.read_dist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "backend", "memory", "maintenance", "no-overlap", "stale-sdk"])
async def test_lab_requires_real_overlap_and_restores_concurrency_on_every_exit(database, monkeypatch, case):
    from src.services.kubernetes_execution import KubernetesExecutionService
    override = AsyncMock()
    monkeypatch.setattr(KubernetesExecutionService, "set_job_type_concurrency", override)
    monkeypatch.setattr(spike, "_guard_environment", lambda: None)
    monkeypatch.setattr(spike, "configure_mappers", lambda: None)
    monkeypatch.setattr(spike, "init_db", AsyncMock())
    close = AsyncMock()
    monkeypatch.setattr(spike, "close_db", close)
    apps = [SimpleNamespace(id=uuid4(), slug=f"fixture-{i}") for i in range(2)]
    monkeypatch.setattr(spike, "_seed_application", AsyncMock(side_effect=apps))
    builds = [job(), job()]
    if case == "backend":
        builds[0].execution_backend = "local"
    if case == "memory":
        builds[0].memory_limit_bytes = 1
    maintenance = job(status="succeeded", backend="kubernetes" if case == "maintenance" else "local")
    sdk = job(status="succeeded")
    monkeypatch.setattr(spike, "get_settings", lambda: SimpleNamespace(kubernetes_build_memory_limit_mib=64))
    monkeypatch.setattr(spike, "_enqueue_deploy_job", AsyncMock(side_effect=builds))
    monkeypatch.setattr(spike, "_wait_for_builds_running", AsyncMock(return_value=builds))
    monkeypatch.setattr(spike, "_enqueue_maintenance_job", AsyncMock(return_value=maintenance))
    monkeypatch.setattr(spike, "_enqueue_sdk_update_job", AsyncMock(return_value=sdk))
    terminal = {j.id: j for j in [*builds, maintenance, sdk]}
    monkeypatch.setattr(spike, "_wait_for_job_succeeded", AsyncMock(side_effect=lambda jid, **_kw: terminal[jid]))
    observed = [job(status="succeeded"), job(status="succeeded")] if case == "no-overlap" else builds
    monkeypatch.setattr(spike, "_load_jobs", AsyncMock(return_value=observed))
    checks = [{"active_deployment_id": "before"}, {"active_deployment_id": "other"}, {"active_deployment_id": "before" if case == "stale-sdk" else "after"}]
    monkeypatch.setattr(spike, "_verify_application_deployment", AsyncMock(side_effect=checks))
    if case == "success":
        result = await spike.run_build_mode(SimpleNamespace(mode="build", hold_seconds=1))
        assert result["sdk_update_deployment_check"]["active_deployment_id"] == "after"
        assert result["submitted_job_ids"]["deploy"] == [str(j.id) for j in builds]
    else:
        arguments = SimpleNamespace(mode="build", hold_seconds=1)
        with pytest.raises(RuntimeError):
            await spike.run_build_mode(arguments)
    assert override.await_args_list[0].args == ("application.deploy", 2)
    assert override.await_args_list[-1].args == ("application.deploy", None)
    close.assert_awaited_once()
