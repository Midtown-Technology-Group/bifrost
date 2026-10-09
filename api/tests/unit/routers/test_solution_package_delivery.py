"""HTTP boundary contracts for protected complete-package delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from src.core.database import get_db
from src.models.contracts.platform_jobs import PlatformJobPublic
from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
from src.routers.solution_deployments import router
from tests.unit.services.solutions.test_package_recovery import retained_job


def _body():
    return SolutionGitSourceDeliveryRequest(
        source_commit_sha="a" * 40,
        ci_run_id=7,
        ci_run_attempt=1,
        artifact_digest="sha256:" + "b" * 64,
    ).model_dump(mode="json")


def _public_job(*, status="queued", result=None):
    return PlatformJobPublic(
        id=uuid4(),
        job_type="solution.deploy",
        payload_version=1,
        requested_by_user_id="system",
        requested_by_name="Package delivery",
        status=status,
        progress={},
        revision=1,
        attempt=1,
        max_attempts=2,
        can_cancel=False,
        title="Package",
        result=result,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def _app(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth_case,expected", [("disabled", 503), ("missing", 401), ("invalid", 401)]
)
async def test_package_admission_authentication_fails_before_source_or_enqueue(
    monkeypatch,
    auth_case,
    expected,
):
    from src.services.solutions import package_git_source

    sid = uuid4()
    db = SimpleNamespace(rollback=AsyncMock())
    settings = SimpleNamespace(
        solution_package_git_delivery_policy=None
        if auth_case == "disabled"
        else object()
    )
    monkeypatch.setattr(
        "src.routers.solution_deployments.get_settings", lambda: settings
    )
    source = AsyncMock()
    admit = AsyncMock()
    monkeypatch.setattr(package_git_source, "read_package_git_source", source)
    monkeypatch.setattr("src.services.solutions.package_admission.admit_package", admit)
    if auth_case == "invalid":
        from src.services.solutions.github_delivery_source import GitDeliverySourceError

        monkeypatch.setattr(
            package_git_source,
            "authenticate_package_git_delivery",
            AsyncMock(side_effect=GitDeliverySourceError("OIDC claims differ")),
        )

    headers = {"X-GitHub-Job-Token": "test-token"}
    if auth_case != "missing":
        headers["Authorization"] = "Bearer invalid-or-irrelevant"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(db)), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/solutions/{sid}/deployments/github-package",
            headers=headers,
            json=_body(),
        )

    assert response.status_code == expected
    source.assert_not_awaited()
    admit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,status",
    [
        (ValueError("reviewed source differs"), 422),
        (httpx.ReadTimeout("source host unavailable"), 503),
    ],
)
async def test_package_admission_failure_rolls_back_without_enqueue(
    monkeypatch, failure, status
):
    from src.routers import solution_deployments as routes
    from src.services.solutions import package_git_source

    sid = uuid4()
    db = SimpleNamespace(rollback=AsyncMock())
    monkeypatch.setattr(
        routes, "_authenticate_package", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        package_git_source, "read_package_git_source", AsyncMock(side_effect=failure)
    )
    admit = AsyncMock()
    monkeypatch.setattr("src.services.solutions.package_admission.admit_package", admit)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(db)), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/solutions/{sid}/deployments/github-package",
            headers={"X-GitHub-Job-Token": "test-token"},
            json=_body(),
        )

    assert response.status_code == status
    db.rollback.assert_awaited_once()
    admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_package_status_returns_independent_accounting_and_only_verified_rollback(
    monkeypatch,
):
    from src.routers import solution_deployments as routes

    sid = uuid4()
    succeeded = SimpleNamespace(
        status="succeeded", result={"deployment_id": str(uuid4())}
    )
    failed_verified = SimpleNamespace(
        status="failed", result={"schema_version": "rollback"}
    )
    failed_unknown = SimpleNamespace(status="failed", result={"error": "interrupted"})
    released_digest = "sha256:" + "1" * 64
    rolled_back_digest = "sha256:" + "2" * 64
    unknown_digest = "sha256:" + "3" * 64
    jobs = {
        released_digest: succeeded,
        rolled_back_digest: failed_verified,
        unknown_digest: failed_unknown,
    }
    monkeypatch.setattr(
        routes, "_authenticate_package", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        "src.services.solutions.package_admission.inspect_package_job",
        AsyncMock(side_effect=lambda _db, _sid, digest: jobs[digest]),
    )
    accounting = AsyncMock(return_value={"state": "released", "verified": True})
    rollback = AsyncMock(
        side_effect=lambda _db, job: (
            {"verified": True} if job is failed_verified else None
        )
    )
    monkeypatch.setattr(
        "src.services.solutions.package_admission.read_package_accounting", accounting
    )
    monkeypatch.setattr(
        "src.services.solutions.package_admission.read_package_rollback", rollback
    )
    monkeypatch.setattr(
        "src.services.platform_jobs.platform_job_to_public",
        lambda job: _public_job(status=job.status, result=job.result),
    )
    db = SimpleNamespace(rollback=AsyncMock())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(db)), base_url="http://test"
    ) as client:
        released = await client.post(
            f"/api/solutions/{sid}/deployments/github-package/status",
            json={**_body(), "artifact_digest": released_digest},
        )
        rolled = await client.post(
            f"/api/solutions/{sid}/deployments/github-package/status",
            json={**_body(), "artifact_digest": rolled_back_digest},
        )
        unknown = await client.post(
            f"/api/solutions/{sid}/deployments/github-package/status",
            json={**_body(), "artifact_digest": unknown_digest},
        )

    assert released.status_code == rolled.status_code == unknown.status_code == 200
    assert released.json()["result"]["accounting_readback"] == {
        "state": "released",
        "verified": True,
    }
    assert rolled.json()["result"]["rollback_readback"] == {"verified": True}
    assert "rollback_readback" not in unknown.json()["result"]
    accounting.assert_awaited_once_with(db, succeeded)
    assert rollback.await_count == 2


@pytest.mark.asyncio
async def test_recovery_returns_original_digest_when_current_main_is_newer(monkeypatch):
    from src.routers import solution_deployments as routes
    from src.services.solutions.github_delivery_source import ProtectedGitReader

    policy, job, original_payload = retained_job()
    sid = original_payload.install_id
    old_digest = original_payload.options["artifact_digest"]
    current_body = {**_body(), "source_commit_sha": "e" * 40}
    db = SimpleNamespace(rollback=AsyncMock())
    monkeypatch.setattr(routes, "_authenticate_package", AsyncMock(return_value=policy))
    verify = AsyncMock()
    monkeypatch.setattr(ProtectedGitReader, "verify_ci", verify)
    recover = AsyncMock(return_value=job)
    monkeypatch.setattr(
        "src.services.solutions.package_admission.recover_pending_package", recover
    )
    monkeypatch.setattr(
        "src.services.platform_jobs.platform_job_to_public",
        lambda _job: _public_job(status="requires_action", result=job.result),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(db)), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/solutions/{sid}/deployments/github-package/recover",
            headers={"X-GitHub-Job-Token": "test-token"},
            json=current_body,
        )

    assert response.status_code == 200
    assert response.json()["schema_version"] == "bifrost.solution-package-recovery/v1"
    assert response.json()["job"]["result"]["original_artifact_digest"] == old_digest
    assert (
        response.json()["job"]["result"]["original_artifact_digest"]
        != current_body["artifact_digest"]
    )
    verify.assert_awaited_once_with(
        current_body["source_commit_sha"],
        current_body["ci_run_id"],
        current_body["ci_run_attempt"],
    )
    recover.assert_awaited_once_with(db, policy, sid)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,status",
    [
        (httpx.ReadTimeout("GitHub unavailable"), 503),
        (None, 409),
    ],
)
async def test_package_recovery_fails_closed_on_source_transport_or_missing_payload(
    monkeypatch, failure, status
):
    from src.routers import solution_deployments as routes
    from src.services.solutions.github_delivery_source import ProtectedGitReader

    sid = uuid4()
    db = SimpleNamespace(rollback=AsyncMock())
    monkeypatch.setattr(
        routes, "_authenticate_package", AsyncMock(return_value=object())
    )
    if failure is not None:
        monkeypatch.setattr(
            ProtectedGitReader, "verify_ci", AsyncMock(side_effect=failure)
        )
        recover = AsyncMock()
    else:
        monkeypatch.setattr(ProtectedGitReader, "verify_ci", AsyncMock())
        recover = AsyncMock(return_value=SimpleNamespace(encrypted_payload=None))
    monkeypatch.setattr(
        "src.services.solutions.package_admission.recover_pending_package", recover
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(db)), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/solutions/{sid}/deployments/github-package/recover",
            headers={"X-GitHub-Job-Token": "test-token"},
            json=_body(),
        )

    assert response.status_code == status
    assert response.json()["detail"] in {
        "Original package recovery remains unresolved",
        "Original package payload is unavailable",
    }
    db.rollback.assert_awaited_once()
    if failure is not None:
        recover.assert_not_awaited()
