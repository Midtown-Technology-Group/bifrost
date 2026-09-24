"""Only an active provider user can receive a customer-scoped actor grant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from shared.models import ExternalIdentityCreateRequest

from src.models.orm.users import User
from src.routers.users import create_external_identity


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [False, True])
async def test_customer_grant_requires_provider_home_org(provider):
    user_id, home_id, customer_id = uuid4(), uuid4(), uuid4()
    target = SimpleNamespace(
        id=user_id, organization_id=home_id, is_active=True, is_system=False,
    )
    home = SimpleNamespace(id=home_id, is_active=True, is_provider=provider)
    customer = SimpleNamespace(id=customer_id, is_active=True)
    db = AsyncMock()
    db.add = MagicMock()
    async def flush():
        identity = db.add.call_args.args[0]
        identity.id = uuid4()
        identity.is_active = True
    db.flush.side_effect = flush
    db.get.side_effect = lambda model, key: (
        target if model is User else home if key == home_id else customer
    )
    request = ExternalIdentityCreateRequest(
        provider="microsoft_teams", external_scope_id="tenant-A",
        external_user_id="jane-object-id", authorized_organization_id=customer_id,
    )
    with patch("src.routers.users.emit_audit", new=AsyncMock()) as audit:
        if not provider:
            with pytest.raises(HTTPException) as exc:
                await create_external_identity(user_id, request, user=SimpleNamespace(), db=db)
            assert exc.value.status_code == 400
            db.add.assert_not_called()
            return

        response = await create_external_identity(
            user_id, request, user=SimpleNamespace(), db=db,
        )

    assert response.user_id == user_id
    assert response.authorized_organization_id == customer_id
    assert audit.await_args.kwargs["strict"] is True
    db.commit.assert_awaited_once()
