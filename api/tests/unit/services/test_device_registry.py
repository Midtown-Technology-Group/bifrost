"""Service-level tests for the device registry (M1 #831).

Covers the M0 authorization matrix gates, enrollment token lifecycle
(single-use, TTL, disabled device), key rotation, and org-scoped loaders
without a live database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import status

from src.models.contracts.devices import DeviceCreate, DevicePublic
from src.models.orm.devices import (
    DEVICE_STATUS_ACTIVE,
    DEVICE_STATUS_DISABLED,
    DEVICE_STATUS_PENDING_ENROLLED,
    Device,
)
from src.services.device_keys import (
    ENROLLMENT_TOKEN_PREFIX,
    generate_device_key,
    generate_enrollment_token,
)
from src.services.devices import (
    DeviceOperationError,
    create_device,
    enroll_device,
    get_device_scoped,
    list_devices_stmt,
    rotate_device_key_route,
    set_device_enabled,
    user_can_manage_devices,
)
from src.core.principal import UserPrincipal


def make_user(
    *,
    is_superuser: bool = False,
    org_id=None,
    user_id=None,
) -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id or uuid4(),
        email="user@example.com",
        organization_id=org_id,
        name="Test User",
        is_superuser=is_superuser,
    )


def make_device(**overrides) -> Device:
    device = Device(
        id=overrides.pop("id", uuid4()),
        organization_id=overrides.pop("organization_id", uuid4()),
        display_name=overrides.pop("display_name", "unit-device"),
    )
    for key, value in overrides.items():
        setattr(device, key, value)
    return device


def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


class TestPermissionGate:
    async def test_superuser_bypasses(self):
        session = mock_session()
        assert await user_can_manage_devices(session, make_user(is_superuser=True)) is True

    async def test_role_permission_grants(self):
        session = mock_session()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [{"can_manage_config": True}]
        session.execute.return_value = result
        user = make_user()
        assert await user_can_manage_devices(session, user) is True
        # The permission query joins user_roles — no inline org comparisons.
        session.execute.assert_awaited_once()

    async def test_missing_permission_denies(self):
        session = mock_session()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [{}, {"can_manage_config": False}]
        session.execute.return_value = result
        assert await user_can_manage_devices(session, make_user()) is False


class TestCreateDevice:
    async def test_org_user_pinned_to_own_org(self):
        session = mock_session()
        org = uuid4()
        user = make_user(org_id=org)
        device, token, expires = await create_device(
            session, user, DeviceCreate(display_name="alpha")
        )
        assert device.organization_id == org
        assert token.startswith(ENROLLMENT_TOKEN_PREFIX)
        assert device.status == DEVICE_STATUS_PENDING_ENROLLED
        assert device.enrollment_expires_at is not None
        assert expires == device.enrollment_expires_at

    async def test_org_user_cannot_cross_org(self):
        session = mock_session()
        user = make_user(org_id=uuid4())
        with pytest.raises(DeviceOperationError) as exc:
            await create_device(
                session, user, DeviceCreate(display_name="x", organization_id=uuid4())
            )
        assert exc.value.code == "permission_denied"
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    async def test_superuser_requires_target_org(self):
        session = mock_session()
        user = make_user(is_superuser=True, org_id=None)
        with pytest.raises(DeviceOperationError) as exc:
            await create_device(session, user, DeviceCreate(display_name="x"))
        assert exc.value.code == "invalid_parameter"

        target = uuid4()
        device, _token, _exp = await create_device(
            session, user, DeviceCreate(display_name="x", organization_id=target)
        )
        assert device.organization_id == target


class TestEnrollDevice:
    async def test_invalid_format_rejected(self):
        session = mock_session()
        with pytest.raises(DeviceOperationError) as exc:
            await enroll_device(session, "not-a-token")
        assert exc.value.code == "enrollment_token_invalid"
        assert exc.value.status_code == 401

    async def test_unknown_device_rejected(self):
        session = mock_session()
        _result = MagicMock()
        _result.scalar_one_or_none.return_value = None
        session.execute.return_value = _result
        token = f"{ENROLLMENT_TOKEN_PREFIX}{uuid4()}_{'a' * 43}"
        with pytest.raises(DeviceOperationError) as exc:
            await enroll_device(session, token)
        assert exc.value.code == "enrollment_token_invalid"

    async def test_expired_token_rejected(self):
        session = mock_session()
        device_id = uuid4()
        token, token_hash = generate_enrollment_token(device_id)
        device = make_device(
            id=device_id,
            status=DEVICE_STATUS_PENDING_ENROLLED,
            enrollment_token_hash=token_hash,
            enrollment_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        _result = MagicMock()
        _result.scalar_one_or_none.return_value = device
        session.execute.return_value = _result
        with pytest.raises(DeviceOperationError) as exc:
            await enroll_device(session, token)
        assert exc.value.code == "enrollment_token_expired"

    async def test_consumed_token_rejected(self):
        session = mock_session()
        device_id = uuid4()
        token, _hash = generate_enrollment_token(device_id)
        device = make_device(
            id=device_id,
            status=DEVICE_STATUS_ACTIVE,
            enrollment_token_hash=None,
            enrollment_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        _result = MagicMock()
        _result.scalar_one_or_none.return_value = device
        session.execute.return_value = _result
        with pytest.raises(DeviceOperationError) as exc:
            await enroll_device(session, token)
        assert exc.value.code == "enrollment_token_consumed"

    async def test_disabled_device_rejected(self):
        session = mock_session()
        device_id = uuid4()
        token, token_hash = generate_enrollment_token(device_id)
        device = make_device(
            id=device_id,
            status=DEVICE_STATUS_DISABLED,
            enrollment_token_hash=token_hash,
            enrollment_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        _result = MagicMock()
        _result.scalar_one_or_none.return_value = device
        session.execute.return_value = _result
        with pytest.raises(DeviceOperationError) as exc:
            await enroll_device(session, token)
        assert exc.value.code == "device_disabled"
        assert exc.value.status_code == 403

    async def test_wrong_secret_rejected(self):
        session = mock_session()
        device_id = uuid4()
        _other_raw, other_hash = generate_enrollment_token(device_id)
        token = f"{ENROLLMENT_TOKEN_PREFIX}{device_id}_{'b' * 43}"
        device = make_device(
            id=device_id,
            status=DEVICE_STATUS_PENDING_ENROLLED,
            enrollment_token_hash=other_hash,
            enrollment_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        _result = MagicMock()
        _result.scalar_one_or_none.return_value = device
        session.execute.return_value = _result
        with pytest.raises(DeviceOperationError) as exc:
            await enroll_device(session, token)
        assert exc.value.code == "enrollment_token_invalid"

    async def test_success_activates_and_issues_key_once(self):
        session = mock_session()
        device_id = uuid4()
        token, token_hash = generate_enrollment_token(device_id)
        device = make_device(
            id=device_id,
            status=DEVICE_STATUS_PENDING_ENROLLED,
            enrollment_token_hash=token_hash,
            enrollment_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            api_key_hash=None,
            api_key_enabled=False,
        )
        _result = MagicMock()
        _result.scalar_one_or_none.return_value = device
        session.execute.return_value = _result

        result_device, raw_key = await enroll_device(session, token)
        assert result_device.status == DEVICE_STATUS_ACTIVE
        assert result_device.enrollment_token_hash is None
        assert result_device.api_key_hash is not None
        assert result_device.api_key_enabled is True
        assert raw_key.startswith("bfdk_")
        session.commit.assert_awaited()


class TestLifecycle:
    async def test_rotate_requires_active_key(self):
        session = mock_session()
        user = make_user(org_id=uuid4())
        pending = make_device(
            organization_id=user.organization_id,
            status=DEVICE_STATUS_PENDING_ENROLLED,
            api_key_hash=None,
        )

        result = MagicMock()
        result.scalar_one_or_none.return_value = pending
        session.execute.return_value = result

        with pytest.raises(DeviceOperationError) as exc:
            await rotate_device_key_route(session, user, pending.id)
        assert exc.value.code == "device_not_active"
        assert exc.value.status_code == 409

    async def test_rotate_replaces_hash_and_returns_raw_once(self):
        session = mock_session()
        user = make_user(org_id=uuid4())
        old_raw, old_hash = generate_device_key(uuid4())
        active = make_device(
            organization_id=user.organization_id,
            status=DEVICE_STATUS_ACTIVE,
            api_key_hash=old_hash,
            api_key_enabled=True,
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = active
        session.execute.return_value = result

        device, raw_key = await rotate_device_key_route(session, user, active.id)
        assert raw_key != old_raw
        assert device.api_key_hash != old_hash

    async def test_disable_and_enable_transition(self):
        session = mock_session()
        user = make_user(org_id=uuid4())
        active = make_device(
            organization_id=user.organization_id,
            status=DEVICE_STATUS_ACTIVE,
            api_key_hash="hash",
            api_key_enabled=True,
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = active
        session.execute.return_value = result

        disabled = await set_device_enabled(session, user, active.id, False)
        assert disabled.status == DEVICE_STATUS_DISABLED
        assert disabled.api_key_enabled is False

        enabled = await set_device_enabled(session, user, active.id, True)
        assert enabled.status == DEVICE_STATUS_ACTIVE
        # Re-enable restores the key flag cleared by disable.
        assert enabled.api_key_enabled is True

        with pytest.raises(DeviceOperationError) as exc:
            await set_device_enabled(session, user, active.id, True)
        assert exc.value.code == "invalid_parameter"
        assert exc.value.status_code == 422

    async def test_get_scoped_missing_device_404(self):
        session = mock_session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute.return_value = result
        with pytest.raises(DeviceOperationError) as exc:
            await get_device_scoped(session, make_user(org_id=uuid4()), uuid4())
        assert exc.value.code == "unknown_device"
        assert exc.value.status_code == 404


class TestOrgScopedStatements:
    def test_org_user_statement_filters_by_org(self):
        user = make_user(org_id=uuid4())
        stmt = list_devices_stmt(user)
        rendered = str(stmt)
        assert "WHERE devices.organization_id" in rendered

    def test_superuser_unscoped_statement(self):
        user = make_user(is_superuser=True, org_id=None)
        stmt = list_devices_stmt(user)
        rendered = str(stmt)
        assert "WHERE" not in rendered


class TestContracts:
    def test_public_model_has_no_secret_fields(self):
        fields = set(DevicePublic.model_fields)
        assert "api_key_hash" not in fields
        assert "enrollment_token_hash" not in fields
        assert "enrollment_expires_at" not in fields
        assert "device_key" not in fields

    def test_error_envelope_shape(self):
        err = DeviceOperationError(
            status.HTTP_409_CONFLICT, "device_busy", "busy", job_id=str(uuid4())
        )
        response = err.to_response()
        assert response.status_code == 409
        import json

        payload = json.loads(response.body)
        assert payload["error"]["code"] == "device_busy"
        assert payload["error"]["retryable"] is False
        assert payload["error"]["job_id"]
