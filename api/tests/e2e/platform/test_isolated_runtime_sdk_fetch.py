"""Real SQL fixture fetch/OAuth exclusion, not Rust owner runtime acceptance."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import DBAPIError

from shared import isolated_runtime_sdk as capability
from src.core.constants import PROVIDER_ORG_ID
from src.core.runtime_sdk_credentials import RuntimeSDKDenied
from src.models.orm.integrations import Integration
from src.models.orm.oauth import OAuthProvider
from src.services.isolated_runtime_sdk_tokens import FiniteIntegrationGetIntent

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_synthetic_stable_fetch_rejects_installed_and_concurrent_oauth(
    async_session_factory,
    platform_admin,
    monkeypatch,
):
    integration_id, provider_id = uuid4(), uuid4()
    name = f"isolated-sdk-{integration_id}"
    intent = FiniteIntegrationGetIntent(
        grant_id=uuid4(),
        grant_digest="a" * 64,
        integration_name=name,
        organization_id=PROVIDER_ORG_ID,
        solution_id=uuid4(),
    )
    async with async_session_factory() as session:
        session.add(Integration(id=integration_id, name=name))
        await session.commit()
    try:
        stable_fetch = capability.get_sdk_integration_dict
        attempts = []

        async def fetch_with_competing_provider(session, **kwargs):
            # The check and actual stable fetch share the integration lock.
            async with async_session_factory() as competing:
                await competing.execute(text("SET LOCAL lock_timeout = '100ms'"))
                competing.add(
                    OAuthProvider(
                        id=provider_id,
                        provider_name=name,
                        client_id="synthetic",
                        encrypted_client_secret=b"not-a-secret",
                        integration_id=integration_id,
                    )
                )
                with pytest.raises(DBAPIError) as caught:
                    await competing.flush()
                assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
                await competing.rollback()
                attempts.append("provider attachment rejected by parent lock")
            return await stable_fetch(session, **kwargs)

        monkeypatch.setattr(
            capability, "get_sdk_integration_dict", fetch_with_competing_provider
        )
        async with async_session_factory() as session:
            result = await capability.fetch_synthetic_integration(
                session,
                intent,
                fixture_integration_id=integration_id,
                external=False,
            )
        assert result.integration_id == str(integration_id)
        assert result.oauth is None
        assert len(attempts) == 1
        async with async_session_factory() as session:
            session.add(
                OAuthProvider(
                    id=provider_id,
                    provider_name=name,
                    client_id="synthetic",
                    encrypted_client_secret=b"not-a-secret",
                    integration_id=integration_id,
                )
            )
            await session.commit()
        async with async_session_factory() as session:
            with pytest.raises(RuntimeSDKDenied):
                await capability.fetch_synthetic_integration(
                    session,
                    intent,
                    fixture_integration_id=integration_id,
                    external=False,
                )
        # The rejected provider path never reached the shared OAuth-capable fetch.
        assert len(attempts) == 1
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(OAuthProvider).where(OAuthProvider.id == provider_id)
            )
            await session.execute(
                delete(Integration).where(Integration.id == integration_id)
            )
            await session.commit()
