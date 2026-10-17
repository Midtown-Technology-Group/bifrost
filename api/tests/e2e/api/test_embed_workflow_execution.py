"""E2E tests for workflow execution via embed token."""

import hashlib
import hmac as hmac_module
import uuid
from urllib.parse import urlparse

import pytest

from tests.e2e.conftest import write_and_register


def _compute_hmac(params: dict[str, str], secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _extract_token_from_redirect(response) -> str:
    """Extract the access token from the redirect URL fragment."""
    location = response.headers.get("location", "")
    parsed = urlparse(location)
    fragment = parsed.fragment  # e.g. "embed_token=eyJ..."
    assert fragment.startswith("embed_token="), f"Expected embed_token in fragment, got: {fragment}"
    return fragment.split("=", 1)[1]


@pytest.mark.e2e
class TestEmbedWorkflowExecution:
    """Test that embed tokens can authenticate workflow execution."""

    @pytest.fixture
    def embed_session(self, e2e_client, platform_admin):
        """Create an app with embed secret and get an embed token."""
        slug = f"embed-wf-test-{uuid.uuid4().hex[:8]}"
        # Create app
        r = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": slug, "slug": slug, "app_model": "inline_v1"},
        )
        assert r.status_code == 201, r.text
        app = r.json()

        # Create embed secret
        r = e2e_client.post(
            f"/api/applications/{app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Test"},
        )
        raw_secret = r.json()["raw_secret"]

        # Get embed token via HMAC-verified entry point
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, raw_secret)
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert r.status_code == 302, r.text
        embed_token = _extract_token_from_redirect(r)

        yield {
            "app": app,
            "embed_token": embed_token,
            "raw_secret": raw_secret,
            "verified_params": params,
        }

        # Cleanup
        e2e_client.delete(f"/api/applications/{app['id']}", headers=platform_admin.headers)

    @pytest.fixture
    def recovery_workflow(self, e2e_client, platform_admin):
        suffix = uuid.uuid4().hex[:8]
        function_name = f"embed_recovery_{suffix}"
        path = f"{function_name}.py"
        workflow = write_and_register(
            e2e_client,
            platform_admin.headers,
            path,
            (
                "from bifrost import workflow\n\n"
                f"@workflow(name='{function_name}')\n"
                f"async def {function_name}(marker: str):\n"
                "    return {'marker': marker}\n"
            ),
            function_name,
        )
        access_update = e2e_client.patch(
            f"/api/workflows/{workflow['id']}",
            headers=platform_admin.headers,
            json={"access_level": "everyone"},
        )
        assert access_update.status_code == 200, access_update.text
        yield workflow
        e2e_client.delete(
            f"/api/files/editor?path={path}",
            headers=platform_admin.headers,
        )

    def test_embed_token_authenticates_workflow_execute(self, e2e_client, embed_session):
        """An embed token should be accepted by the workflow execute endpoint.

        We send a request with a nonexistent workflow — we expect 404 (not found)
        rather than 401/403 (unauthorized), proving the token was accepted.
        """
        r = e2e_client.post(
            "/api/workflows/execute",
            headers={"Authorization": f"Bearer {embed_session['embed_token']}"},
            json={
                "workflow_id": "nonexistent-workflow-for-auth-test",
                "input_data": {},
            },
        )
        # Should get 404 (workflow not found) rather than 401/403 (unauthorized)
        assert r.status_code != 401, f"Embed token rejected as unauthorized: {r.text}"
        assert r.status_code != 403, f"Embed token rejected as forbidden: {r.text}"

    def test_execution_recovery_is_bound_to_exact_embed_session(
        self,
        e2e_client,
        embed_session,
        recovery_workflow,
    ):
        params = embed_session["verified_params"]
        hmac_val = _compute_hmac(params, embed_session["raw_secret"])
        second_bootstrap = e2e_client.get(
            f"/embed/apps/{embed_session['app']['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert second_bootstrap.status_code == 302, second_bootstrap.text
        second_token = _extract_token_from_redirect(second_bootstrap)
        assert second_token != embed_session["embed_token"]

        execution_id = str(uuid.uuid4())
        payload = {
            "workflow_id": recovery_workflow["id"],
            "app_id": embed_session["app"]["id"],
            "input_data": {"marker": "same-input"},
            "sync": True,
        }

        def submit(token: str):
            return e2e_client.post(
                "/api/workflows/execute",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Bifrost-Execution-ID": execution_id,
                },
                json=payload,
            )

        initial = submit(embed_session["embed_token"])
        same_session = submit(embed_session["embed_token"])
        other_same_org_session = submit(second_token)

        assert initial.status_code == 200, initial.text
        assert same_session.status_code == 200, same_session.text
        assert same_session.json()["execution_id"] == execution_id
        assert other_same_org_session.status_code == 403, other_same_org_session.text
        assert "Access denied to this execution" in other_same_org_session.text

    def test_embed_token_cannot_access_admin_endpoints(self, e2e_client, embed_session):
        """Embed tokens should be blocked from admin endpoints by EmbedScopeMiddleware."""
        r = e2e_client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {embed_session['embed_token']}"},
        )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
        assert "Embed tokens cannot access" in r.text
