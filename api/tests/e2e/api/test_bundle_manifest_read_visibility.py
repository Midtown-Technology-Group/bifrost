"""Draft app bundles are platform-admin-only control-plane artifacts."""

import uuid

import pytest


@pytest.mark.e2e
class TestDraftBundleAuthorization:
    @pytest.fixture
    def inline_app(self, e2e_client, platform_admin, org1):
        """Create an org-scoped app whose draft bundle exists."""
        slug = f"bundle-manifest-rv-{uuid.uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={
                "name": slug,
                "slug": slug,
                "app_model": "inline_v1",
                "organization_id": org1["id"],
            },
        )
        assert resp.status_code == 201, resp.text
        app = resp.json()
        yield app
        e2e_client.delete(
            f"/api/applications/{app['id']}", headers=platform_admin.headers
        )

    @pytest.mark.parametrize(
        "path",
        [
            "bundle-manifest?mode=draft",
            "bundle-asset/manifest.json?mode=draft",
        ],
    )
    def test_org_user_cannot_read_draft_bundle(
        self, e2e_client, org1_user, inline_app, path
    ):
        """App read access does not grant access to executable draft artifacts."""
        resp = e2e_client.get(
            f"/api/applications/{inline_app['id']}/{path}",
            headers=org1_user.headers,
        )

        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "Platform admin privileges required"

    def test_platform_admin_can_read_draft_manifest(
        self, e2e_client, platform_admin, inline_app
    ):
        """The App Builder preview path remains available to platform admins."""
        resp = e2e_client.get(
            f"/api/applications/{inline_app['id']}/bundle-manifest",
            headers=platform_admin.headers,
            params={"mode": "draft"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["mode"] == "preview"
