"""Restricted synthetic capability fetch, after live Rust owner admission.

This is an isolated implementation seam, not a production route or lifecycle
writer. The fixture identity is supplied by the trusted parent, never HTTP.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.sdk_integrations import get_sdk_integration_dict
from src.core.runtime_sdk_credentials import RuntimeSDKDenied
from src.models.contracts.cli import SDKIntegrationsGetResponse
from src.models.orm.integrations import Integration
from src.models.orm.oauth import OAuthProvider
from src.services.isolated_runtime_sdk_tokens import FiniteIntegrationGetIntent


async def fetch_synthetic_integration(
    session: AsyncSession,
    intent: FiniteIntegrationGetIntent,
    *,
    fixture_integration_id: UUID,
    external: bool,
) -> SDKIntegrationsGetResponse:
    """Use the stable SDK implementation without enabling vendor refresh.

    Hold the integration parent row through the fetch. An OAuth provider's FK
    key-share lock conflicts with this FOR UPDATE lock, so a provider cannot be
    installed between the absence check and the stable fetch. This transaction
    takes no lifecycle locks and must start after the Rust bridge has replied.
    """
    async with session.begin():
        integration_id = await session.scalar(
            select(Integration.id)
            .where(
                Integration.id == fixture_integration_id,
                Integration.name == intent.integration_name,
                Integration.is_deleted.is_(False),
            )
            .with_for_update(nowait=True)
        )
        if integration_id is None:
            raise RuntimeSDKDenied("isolated runtime SDK fetch denied")
        provider_id = await session.scalar(
            select(OAuthProvider.id)
            .where(OAuthProvider.integration_id == integration_id)
            .limit(1)
        )
        if provider_id is not None:
            raise RuntimeSDKDenied("isolated runtime SDK fetch denied")
        result = await get_sdk_integration_dict(
            session,
            name=intent.integration_name,
            org_id=intent.organization_id,
            solution_id=intent.solution_id,
            oauth_scope=None,
            external=external,
        )
        if (
            result is None
            or result.get("integration_id") != str(fixture_integration_id)
            or result.get("oauth") is not None
        ):
            raise RuntimeSDKDenied("isolated runtime SDK fetch denied")
        return SDKIntegrationsGetResponse.model_validate(result)
