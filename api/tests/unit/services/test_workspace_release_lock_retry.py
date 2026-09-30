"""Exact Live and failed-job fences for history-only retries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.contracts.workspace_promotions import WorkspaceReleaseLockRetryRequest
from src.services.workspace_release_activation import (
    WorkspaceReleaseActivationError,
    WorkspaceReleaseActivationService,
)


@pytest.mark.asyncio
async def test_retry_queues_new_job_for_exact_failed_live_release(monkeypatch) -> None:
    release_id = "sha256:" + "a" * 64
    release_row_id = uuid4()
    failed_job_id = uuid4()
    organization_id = uuid4()
    release = SimpleNamespace(
        id=release_row_id,
        lock_state="attention_required",
        lock_in_job_id=failed_job_id,
    )
    old_job = SimpleNamespace(
        status="failed",
        job_type="workspace.release.lock",
        resource_id=str(release_row_id),
        organization_id=organization_id,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=old_job),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = WorkspaceReleaseActivationService(db, organization_id)
    service._current_live_any_organization = AsyncMock(
        return_value=(release, SimpleNamespace())
    )
    lock = AsyncMock()
    enqueue = AsyncMock(return_value=(SimpleNamespace(id=uuid4()), False))
    monkeypatch.setattr(
        "src.services.workspace_release_activation.acquire_workspace_release_lock",
        lock,
    )
    monkeypatch.setattr(
        "src.services.workspace_release_activation.WorkspaceReleaseDescriptor.from_rows",
        lambda _release, _artifact: SimpleNamespace(release_id=release_id),
    )
    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_lock.enqueue_workspace_release_lock",
        enqueue,
    )
    request = WorkspaceReleaseLockRetryRequest(
        expected_release_id=release_id, failed_job_id=failed_job_id
    )

    job, reused = await service.retry_projection(
        release_row_id,
        request,
        requested_by_user_id=uuid4(),
        requested_by_email="operator@example.com",
        requested_by_name="Operator",
    )

    assert job is enqueue.return_value[0]
    assert reused is False
    assert enqueue.await_args.kwargs["release"] is release
    assert enqueue.await_args.kwargs["artifact"] is service._current_live_any_organization.return_value[1]
    lock.assert_awaited_once_with(db, organization_id)
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_retry_returns_current_job_without_enqueuing(monkeypatch) -> None:
    release_row_id = uuid4()
    organization_id = uuid4()
    failed_job_id = uuid4()
    current_job_id = uuid4()
    digest = "sha256:" + "a" * 64
    release = SimpleNamespace(
        id=release_row_id,
        lock_state="queued",
        lock_in_job_id=current_job_id,
    )
    failed = SimpleNamespace(
        status="failed",
        job_type="workspace.release.lock",
        resource_id=str(release_row_id),
        organization_id=organization_id,
    )
    current = SimpleNamespace(
        status="queued",
        job_type="workspace.release.lock",
        resource_id=str(release_row_id),
        organization_id=organization_id,
    )
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda _model, job_id: (
            failed if job_id == failed_job_id else current
        )),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = WorkspaceReleaseActivationService(db, organization_id)
    service._current_live_any_organization = AsyncMock(
        return_value=(release, SimpleNamespace())
    )
    monkeypatch.setattr(
        "src.services.workspace_release_activation.acquire_workspace_release_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.workspace_release_activation.WorkspaceReleaseDescriptor.from_rows",
        lambda _release, _artifact: SimpleNamespace(release_id=digest),
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_lock.enqueue_workspace_release_lock",
        enqueue,
    )

    job, reused = await service.retry_projection(
        release_row_id,
        WorkspaceReleaseLockRetryRequest(
            expected_release_id=digest, failed_job_id=failed_job_id
        ),
        requested_by_user_id=uuid4(),
        requested_by_email="operator@example.com",
        requested_by_name="Operator",
    )

    assert job is current
    assert reused is True
    enqueue.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mismatch", "message"),
    [
        ("live", "not the current global Live"),
        ("digest", "digest differs"),
        ("job_id", "no longer references"),
        ("job_status", "not a failed lock job"),
    ],
)
async def test_retry_rejects_stale_or_active_request(
    monkeypatch, mismatch: str, message: str
) -> None:
    release_id = "sha256:" + "a" * 64
    release_row_id = uuid4()
    failed_job_id = uuid4()
    release = SimpleNamespace(
        id=uuid4() if mismatch == "live" else release_row_id,
        lock_state="attention_required",
        lock_in_job_id=uuid4() if mismatch == "job_id" else failed_job_id,
    )
    old_job = SimpleNamespace(
        status="running" if mismatch == "job_status" else "failed",
        job_type="workspace.release.lock",
        resource_id=str(release_row_id),
        organization_id=None,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=old_job),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = WorkspaceReleaseActivationService(db, uuid4())
    old_job.organization_id = service.organization_id
    service._current_live_any_organization = AsyncMock(
        return_value=(release, SimpleNamespace())
    )
    monkeypatch.setattr(
        "src.services.workspace_release_activation.acquire_workspace_release_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.workspace_release_activation.WorkspaceReleaseDescriptor.from_rows",
        lambda _release, _artifact: SimpleNamespace(
            release_id="sha256:" + "b" * 64 if mismatch == "digest" else release_id
        ),
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_lock.enqueue_workspace_release_lock",
        enqueue,
    )

    with pytest.raises(WorkspaceReleaseActivationError, match=message):
        await service.retry_projection(
            release_row_id,
            WorkspaceReleaseLockRetryRequest(
                expected_release_id=release_id, failed_job_id=failed_job_id
            ),
            requested_by_user_id=uuid4(),
            requested_by_email="operator@example.com",
            requested_by_name="Operator",
        )

    enqueue.assert_not_awaited()
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()
