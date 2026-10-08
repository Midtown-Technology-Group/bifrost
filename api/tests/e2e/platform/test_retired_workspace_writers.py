"""The retired loose release ceremony must not remain an HTTP write surface."""

import pytest


@pytest.mark.e2e
@pytest.mark.parametrize(
    "path",
    [
        "/preview",
        "/preview-jobs",
        "/drafts",
        "/artifacts/11111111-1111-1111-1111-111111111111/canary",
        "/artifacts/11111111-1111-1111-1111-111111111111/prepare",
        "/releases/11111111-1111-1111-1111-111111111111/activate",
        "/releases/11111111-1111-1111-1111-111111111111/retry-history-lock",
        "/live/retire",
    ],
)
def test_platform_admin_cannot_call_retired_workspace_writer(
    e2e_client, platform_admin, path,
):
    response = e2e_client.post(
        "/api/workspace-promotions" + path,
        headers=platform_admin.headers,
        json={},
    )
    assert response.status_code == 404, response.text


@pytest.mark.e2e
def test_public_schema_does_not_advertise_retired_workspace_writers(e2e_client):
    response = e2e_client.get("/openapi.json")
    assert response.status_code == 200, response.text
    paths = response.json()["paths"]
    for suffix in (
        "/preview", "/preview-jobs", "/drafts",
        "/artifacts/{artifact_id}/canary",
        "/artifacts/{artifact_id}/prepare",
        "/releases/{release_id}/activate",
        "/releases/{release_id}/retry-history-lock",
        "/live/retire",
    ):
        assert "post" not in paths.get("/api/workspace-promotions" + suffix, {})


@pytest.mark.e2e
def test_retained_source_journal_still_requires_platform_admin(e2e_client, org1_user):
    response = e2e_client.get(
        "/api/workspace-promotions/source-releases",
        headers=org1_user.headers,
    )
    assert response.status_code == 403, response.text
