"""Never-clobber global integration shells for declared connections.

Both managed Solution deploys and workspace bundle imports create the same
thing: an EMPTY global ``Integration`` (+ config schema + OAuth skeleton) for
any declared connection whose integration doesn't exist yet. An existing
integration is never touched — it may carry the admin's real credentials and
org mappings, which a package must never clobber. The shell gives the admin a
pre-wired place to enter credentials.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.oauth_provider_identity import (
    assert_oauth_provider_reusable,
    merged_portable_oauth_values,
)


async def upsert_integration_shells(
    db: AsyncSession, connection_schemas: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> int:
    """Create missing global integration shells; return how many were created."""
    from src.models.orm.integrations import Integration, IntegrationConfigSchema
    from src.models.orm.oauth import OAuthProvider

    created = 0
    for decl in connection_schemas:
        name = decl["integration_name"]
        template = decl.get("template") or {}
        exists = (
            await db.execute(
                select(Integration).where(Integration.name == name)
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue  # never clobber a configured integration
        oauth = template.get("oauth")
        provider = None
        oauth_values: dict[str, Any] = {}
        if oauth:
            provider_name = oauth.get("provider_name") or name
            oauth_values = {
                "display_name": oauth.get("display_name"),
                "oauth_flow_type": oauth.get("oauth_flow_type") or "authorization_code",
                "authorization_url": oauth.get("authorization_url"),
                "token_url": oauth.get("token_url"),
                "audience": oauth.get("audience"),
                "token_url_defaults": oauth.get("token_url_defaults") or {},
                "entity_id_source": oauth.get("entity_id_source"),
                "scopes": oauth.get("scopes") or [],
                "redirect_uri": oauth.get("redirect_uri"),
            }
            provider = (
                await db.execute(
                    select(OAuthProvider)
                    .where(
                        OAuthProvider.organization_id.is_(None),
                        OAuthProvider.provider_name == provider_name,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if provider is not None:
                assert_oauth_provider_reusable(
                    provider,
                    oauth_values,
                    provider_name=provider_name,
                    integration_name=name,
                )
        integ = Integration(
            name=name,
            entity_id_name=template.get("entity_id_name"),
            default_entity_id=template.get("default_entity_id"),
        )
        db.add(integ)
        await db.flush()  # need integ.id for the child rows
        for s in template.get("config_schema") or []:
            db.add(
                IntegrationConfigSchema(
                    integration_id=integ.id,
                    key=s["key"],
                    type=s["type"],
                    required=bool(s.get("required")),
                    description=s.get("description"),
                    options=s.get("options"),
                    position=s.get("position", 0),
                )
            )
        if oauth:
            if provider is None:
                db.add(
                    OAuthProvider(
                        integration_id=integ.id,
                        provider_name=oauth.get("provider_name") or name,
                        client_id="",  # empty shell — admin fills credentials
                        encrypted_client_secret=b"",
                        status="not_connected",
                        **oauth_values,
                    )
                )
            else:
                provider.integration_id = integ.id
                for field, value in merged_portable_oauth_values(
                    provider, oauth_values
                ).items():
                    setattr(provider, field, value)
        created += 1
    return created
