"""Tenant-bound persistence tests for the shared SDK OAuth refresh path."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.first.return_value = value
    return result


@pytest.mark.asyncio
async def test_org_client_credentials_preserve_global_token() -> None:
    """The shared SDK path binds writeback to the resolved org before HTTP."""
    from shared.sdk_integrations import refresh_sdk_oauth_token

    org_id = uuid4()
    provider = MagicMock(
        id=uuid4(),
        provider_name="Pax8",
        oauth_flow_type="client_credentials",
        integration_id=None,
        organization_id=None,
        scopes=["read"],
        audience=None,
    )
    global_token = MagicMock(
        organization_id=None,
        encrypted_access_token=b"global-access",
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _result(provider),
            _result(None),
            _result(global_token),
        ]
    )
    session.add = MagicMock()
    session.commit = AsyncMock()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    with patch(
        "src.services.oauth_provider.refresh_oauth_token_http",
        new=AsyncMock(
            return_value={
                "success": True,
                "access_token": "org-access",
                "encrypted_access_token": b"encrypted-org-access",
                "expires_at": expires_at,
            }
        ),
    ):
        result = await refresh_sdk_oauth_token(
            session,
            connection_name="Pax8",
            scope=None,
            caller_org_id=org_id,
            is_platform_admin=False,
        )

    assert result == {
        "access_token": "org-access",
        "expires_at": expires_at.isoformat(),
    }
    assert session.execute.await_count == 2
    assert global_token.encrypted_access_token == b"global-access"
    session.add.assert_called_once()
    created = session.add.call_args.args[0]
    assert created.organization_id == org_id
    assert created.provider_id == provider.id
    assert created.encrypted_access_token == b"encrypted-org-access"
