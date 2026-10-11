from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers import devices


def _engine_principal() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="engine@example.test",
        organization_id=None,
        name="Engine",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        roles=[],
        is_engine_token=True,
        engine_execution_id=uuid4(),
        engine_attempt_token=uuid4(),
    )


@pytest.mark.asyncio
async def test_get_device_rejects_revoked_engine_attempt_before_authorization() -> None:
    class RevokedAttemptDb:
        async def scalar(self, statement):
            return None

    with pytest.raises(HTTPException) as exc:
        await devices.get_device_route(
            device_id=uuid4(),
            db=RevokedAttemptDb(),
            user=_engine_principal(),
            x_bifrost_control_key=None,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Execution attempt lease is missing or revoked"


@pytest.mark.asyncio
async def test_get_device_control_key_path_does_not_require_engine_lease(
    monkeypatch,
) -> None:
    device_id = uuid4()
    organization_id = uuid4()
    device = SimpleNamespace(
        id=device_id,
        status="active",
        last_seen_at=None,
    )

    async def resolve_key(db, raw_key):
        return SimpleNamespace(organization_id=organization_id)

    async def get_for_org(db, requested_device_id, requested_org_id):
        assert requested_device_id == device_id
        assert requested_org_id == organization_id
        return device

    monkeypatch.setattr(devices, "resolve_control_key", resolve_key)
    monkeypatch.setattr(devices, "get_device_for_org", get_for_org)
    monkeypatch.setattr(devices, "key_can_target", lambda key, candidate: True)

    response = await devices.get_device_route(
        device_id=device_id,
        db=object(),
        user=_engine_principal(),
        x_bifrost_control_key="bfck_test",
    )

    assert response.id == device_id
    assert response.status == "active"
