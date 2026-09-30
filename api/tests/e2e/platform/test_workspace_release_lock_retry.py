"""HTTP boundary for retrying only a failed immutable release history lock."""

from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.workspace_promotions import WorkspacePromotionRelease
from tests.e2e.platform.test_workspace_release_retirement import (
    _seed_live_release,
)


@pytest.mark.e2e
async def test_retry_history_lock_accepts_exact_failed_job_request(
    e2e_client, platform_admin, db_session
) -> None:
    artifact, release, failed_job = await _seed_live_release(
        db_session,
        source_path=f"modules/history_retry_{uuid4().hex}.py",
        function_name="history_retry",
        user_id=platform_admin.user_id,
    )
    failed_job.job_type = "workspace.release.lock"
    failed_job.resource_type = "workspace_release"
    failed_job.resource_id = str(release.id)
    failed_job.status = "failed"
    release.lock_state = "attention_required"
    await db_session.commit()
    release_row_id = release.id
    failed_job_id = failed_job.id
    digest = artifact.release_id
    new_job_id = None
    try:
        url = f"/api/workspace-promotions/releases/{release_row_id}/retry-history-lock"
        body = {
            "expected_release_id": digest,
            "failed_job_id": str(failed_job_id),
        }
        response = e2e_client.post(url, headers=platform_admin.headers, json=body)

        assert response.status_code == 202, response.text
        new_job_id = response.json()["job_id"]
        assert response.headers["location"] == f"/api/platform-jobs/{new_job_id}"
        assert response.json()["reused"] is False
        await db_session.refresh(release)
        assert str(release.lock_in_job_id) == new_job_id
        assert release.activation_state == "live"

        repeated = e2e_client.post(url, headers=platform_admin.headers, json=body)
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["job_id"] == new_job_id
        assert repeated.json()["reused"] is True
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(WorkspacePromotionRelease).where(
                WorkspacePromotionRelease.id == release_row_id
            )
        )
        if new_job_id is not None:
            await db_session.execute(
                delete(PlatformJob).where(PlatformJob.id == new_job_id)
            )
        await db_session.execute(
            delete(PlatformJob).where(PlatformJob.id == failed_job_id)
        )
        await db_session.commit()


@pytest.mark.e2e
async def test_retry_history_lock_rejects_changed_live_release(
    e2e_client, platform_admin
) -> None:
    response = e2e_client.post(
        f"/api/workspace-promotions/releases/{uuid4()}/retry-history-lock",
        headers=platform_admin.headers,
        json={
            "expected_release_id": "sha256:" + "a" * 64,
            "failed_job_id": str(uuid4()),
        },
    )

    assert response.status_code == 409, response.text
    assert "not the current global Live" in response.json()["detail"]
