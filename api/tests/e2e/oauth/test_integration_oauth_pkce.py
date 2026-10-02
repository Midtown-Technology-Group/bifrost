"""E2E: PKCE round trip for third-party integration OAuth over real HTTP.

Exercises the full workflow against the stack (API + Redis + DB) with a
provider opted into ``provider_metadata.use_pkce``:

- both authorize endpoints return ``code_challenge`` /
  ``code_challenge_method=S256`` and never the verifier;
- a callback consumes the stored verifier, and a replayed state is
  rejected with 400 — sequentially and under concurrent callbacks, where
  exactly one request may proceed.

The provider's token endpoint points at a refused loopback port, so the
first exchange deterministically fails *after* consuming the verifier.
That is sufficient here: these tests prove authorize parameters and
single-use consumption ordering at the workflow level. That a consumed
verifier is actually sent to a live token endpoint is covered at unit
level with a mocked transport
(``test_callback_pkce_sends_stored_verifier``).
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from src.models.orm import OAuthProvider

REDIRECT_URI = "http://localhost:3000/callback"
# Refused fast inside the API container: proves consumption ordering without
# depending on any external provider or DNS behavior.
UNREACHABLE_TOKEN_URL = "https://127.0.0.1:9/token"


@pytest.mark.e2e
class TestIntegrationOAuthPKCEWorkflow:
    @pytest_asyncio.fixture
    async def pkce_provider(self, e2e_client, platform_admin, db_session):
        """Integration with a PKCE-enabled authorization_code provider."""
        integration_name = f"e2e_pkce_oauth_{uuid4().hex[:8]}"
        provider_name = f"pkce_provider_{uuid4().hex[:6]}"

        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201, f"Create integration failed: {response.text}"
        integration = response.json()

        provider = OAuthProvider(
            provider_name=provider_name,
            display_name="PKCE Test Provider",
            oauth_flow_type="authorization_code",
            client_id="pkce-client-id",
            encrypted_client_secret=b"encrypted_secret",
            authorization_url="https://login.example.com/authorize",
            token_url=UNREACHABLE_TOKEN_URL,
            scopes=["read"],
            redirect_uri="/api/oauth/callback/pkce",
            provider_metadata={"use_pkce": True},
            integration_id=UUID(integration["id"]),
        )
        db_session.add(provider)
        await db_session.commit()
        await db_session.refresh(provider)

        yield {"integration": integration, "provider": provider}

        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    def _authorize_integration(self, e2e_client, platform_admin, provider_ctx):
        integration_id = provider_ctx["integration"]["id"]
        resp = e2e_client.get(
            f"/api/integrations/{integration_id}/oauth/authorize",
            headers=platform_admin.headers,
            params={"redirect_uri": REDIRECT_URI},
        )
        assert resp.status_code == 200, f"Authorize failed: {resp.text}"
        return resp.json()

    @staticmethod
    def _assert_pkce_authorize_url(authorization_url: str) -> str:
        qs = parse_qs(urlparse(authorization_url).query)
        assert qs.get("code_challenge"), "code_challenge missing from authorize URL"
        assert qs.get("code_challenge_method") == ["S256"]
        assert "code_verifier" not in authorization_url
        assert qs.get("state"), "state missing from authorize URL"
        return qs["state"][0]

    def _callback(self, e2e_client, platform_admin, provider_ctx, *, state, code):
        return e2e_client.post(
            f"/api/oauth/callback/{provider_ctx['provider'].provider_name}",
            headers=platform_admin.headers,
            json={
                "code": code,
                "state": state,
                "redirect_uri": REDIRECT_URI,
                "organization_id": None,
            },
        )

    @pytest.mark.asyncio
    async def test_integration_authorize_pkce_challenge_over_http(
        self, e2e_client, platform_admin, pkce_provider
    ):
        """Integration-level authorize returns challenge params, never the verifier."""
        body = self._authorize_integration(e2e_client, platform_admin, pkce_provider)
        self._assert_pkce_authorize_url(body["authorization_url"])

    @pytest.mark.asyncio
    async def test_mapping_authorize_pkce_challenge_over_http(
        self, e2e_client, platform_admin, pkce_provider, org1
    ):
        """Per-mapping authorize returns challenge params, never the verifier."""
        integration_id = pkce_provider["integration"]["id"]
        mapping_resp = e2e_client.post(
            f"/api/integrations/{integration_id}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "pkce-entity-123",
                "entity_name": "PKCE Entity",
            },
        )
        assert mapping_resp.status_code == 201, f"Create mapping failed: {mapping_resp.text}"
        mapping_id = mapping_resp.json()["id"]

        try:
            resp = e2e_client.post(
                f"/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/authorize",
                headers=platform_admin.headers,
                json={"redirect_uri": REDIRECT_URI},
            )
            assert resp.status_code == 200, f"Authorize failed: {resp.text}"
            self._assert_pkce_authorize_url(resp.json()["authorization_url"])
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration_id}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )

    @pytest.mark.asyncio
    async def test_mapping_callback_replay_rejected_over_http(
        self, e2e_client, platform_admin, pkce_provider, org1, db_session
    ):
        """Per-mapping signed state is single-use: replay gets 400, stores nothing.

        The first callback consumes the mapping nonce and the PKCE verifier
        (its exchange cannot reach a provider, which is incidental). The
        replayed state must be rejected by the early nonce check — before
        any exchange or token storage — leaving zero token rows behind.
        """
        from sqlalchemy import func, select

        from src.models.orm import OAuthToken

        integration_id = pkce_provider["integration"]["id"]
        mapping_resp = e2e_client.post(
            f"/api/integrations/{integration_id}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "pkce-replay-123",
                "entity_name": "PKCE Replay",
            },
        )
        assert mapping_resp.status_code == 201, f"Create mapping failed: {mapping_resp.text}"
        mapping_id = mapping_resp.json()["id"]

        try:
            auth_resp = e2e_client.post(
                f"/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/authorize",
                headers=platform_admin.headers,
                json={"redirect_uri": REDIRECT_URI},
            )
            assert auth_resp.status_code == 200, f"Authorize failed: {auth_resp.text}"
            state = self._assert_pkce_authorize_url(auth_resp.json()["authorization_url"])
            # Signed per-mapping state (not an opaque integration-level state).
            assert "." in state

            first = self._callback(
                e2e_client, platform_admin, pkce_provider, state=state, code="code-first"
            )
            assert first.status_code == 200, f"First callback failed: {first.text}"

            replay = self._callback(
                e2e_client, platform_admin, pkce_provider, state=state, code="code-replay"
            )
            assert replay.status_code == 400, f"Replay was not rejected: {replay.text}"
            # Pins the early nonce guard: any regression to consume-after-store
            # (or a different rejection) changes this detail and fails here.
            assert replay.json()["detail"] == "OAuth state already used (replay rejected)"

            token_count = await db_session.scalar(
                select(func.count())
                .select_from(OAuthToken)
                .where(OAuthToken.provider_id == pkce_provider["provider"].id)
            )
            assert token_count == 0, "Replay or failed exchange stored a token"
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration_id}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )

    @pytest.mark.asyncio
    async def test_callback_replay_rejected_over_http(
        self, e2e_client, platform_admin, pkce_provider
    ):
        """First callback consumes the verifier; replaying the state gets 400."""
        body = self._authorize_integration(e2e_client, platform_admin, pkce_provider)
        state = self._assert_pkce_authorize_url(body["authorization_url"])

        first = self._callback(
            e2e_client, platform_admin, pkce_provider, state=state, code="code-first"
        )
        # The exchange cannot reach a provider, but the verifier was consumed
        # exactly once before that attempt.
        assert first.status_code == 200, f"First callback failed: {first.text}"
        assert first.json()["success"] is False

        replay = self._callback(
            e2e_client, platform_admin, pkce_provider, state=state, code="code-replay"
        )
        assert replay.status_code == 400, f"Replay was not rejected: {replay.text}"

    @pytest.mark.asyncio
    async def test_concurrent_callbacks_single_use_over_http(
        self, e2e_client, platform_admin, pkce_provider
    ):
        """Concurrent callbacks for one state: exactly one proceeds, rest get 400."""
        body = self._authorize_integration(e2e_client, platform_admin, pkce_provider)
        state = self._assert_pkce_authorize_url(body["authorization_url"])

        responses = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._callback,
                    e2e_client,
                    platform_admin,
                    pkce_provider,
                    state=state,
                    code=f"code-racer-{i}",
                )
                for i in range(4)
            )
        )
        statuses = sorted(resp.status_code for resp in responses)
        assert statuses == [200, 400, 400, 400], (
            f"Expected exactly one winner, got statuses {statuses}"
        )
