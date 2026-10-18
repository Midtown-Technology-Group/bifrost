"""Service-level tests for the device registry (M1 #831).

Covers the M0 authorization matrix gates, enrollment token lifecycle
(single-use, TTL, disabled device), key rotation, and org-scoped loaders
without a live database.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
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
    require_device_control_key_issue_permission,
    rotate_device_key_route,
    set_device_enabled,
    user_can_manage_devices,
    require_device_manage_permission,
    require_device_execute_permission,
)
from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller


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


def device_caller(user, *, home=None, base=(), grants=()):
    return Caller(user, AuthorizationContext(
        user_id=user.user_id, home_organization_id=home, base_role_id=uuid4(),
        is_external=False, base_permissions=frozenset(base), role_grants=tuple(grants),
    ))


def device_grant(permissions, org=None, *, kind=BoundaryKind.ORGANIZATION):
    boundaries = () if kind is None else (Boundary(kind, org),)
    return RoleGrant(uuid4(), frozenset(permissions), boundaries)


class TestPermissionGate:
    @pytest.mark.parametrize("manage,execute", [(False, False), (True, False), (False, True), (True, True)])
    async def test_device_permission_matrix_and_same_org_union(self, manage, execute):
        org = uuid4()
        user = make_user(org_id=org)
        grants = []
        if manage:
            grants.append(device_grant({"devices.readwrite"}, org))
        if execute:
            grants.append(device_grant({"devices.execute"}, org))
        caller = device_caller(user, home=org, grants=grants)
        with patch("src.services.devices.load_caller", new=AsyncMock(return_value=caller)) as load:
            assert await user_can_manage_devices(mock_session(), user) is manage
            if manage and execute:
                assert await require_device_control_key_issue_permission(mock_session(), user) == user
            else:
                with pytest.raises(DeviceOperationError) as denied:
                    await require_device_control_key_issue_permission(mock_session(), user)
                assert denied.value.code == "permission_denied"
            assert load.await_count == 2

    @pytest.mark.parametrize("kind", [BoundaryKind.ORGANIZATION, BoundaryKind.PLATFORM, BoundaryKind.MANAGED_ORGANIZATIONS, None])
    async def test_split_org_and_non_org_execution_do_not_issue_keys(self, kind):
        home, foreign = uuid4(), uuid4()
        user = make_user(org_id=home)
        caller = device_caller(user, home=home, grants=[
            device_grant({"devices.readwrite"}, home),
            device_grant({"devices.execute"}, foreign if kind == BoundaryKind.ORGANIZATION else None, kind=kind),
        ])
        with patch("src.services.devices.load_caller", new=AsyncMock(return_value=caller)):
            with pytest.raises(DeviceOperationError) as denied:
                await require_device_control_key_issue_permission(mock_session(), user, home)
            assert "devices.execute" in denied.value.message
            with pytest.raises(DeviceOperationError):
                await require_device_control_key_issue_permission(mock_session(), user, foreign)

    async def test_platform_admin_wildcard_uses_current_rows(self):
        user = make_user(org_id=uuid4())
        caller = device_caller(user, grants=[device_grant({"*"}, kind=BoundaryKind.PLATFORM)])
        with patch("src.services.devices.load_caller", new=AsyncMock(return_value=caller)):
            principal = await require_device_control_key_issue_permission(mock_session(), user, uuid4())
        assert principal.is_superuser
        assert principal.organization_id is None

    @pytest.mark.parametrize("provider,superuser", [(False, True), (True, False), (True, True)])
    async def test_token_admin_or_provider_claim_cannot_replace_current_grants(self, provider, superuser):
        user = make_user(org_id=uuid4(), is_superuser=superuser)
        user.is_provider_org = provider
        with patch("src.services.devices.load_caller", new=AsyncMock(return_value=device_caller(user, home=user.organization_id))):
            assert not await user_can_manage_devices(mock_session(), user)

    @pytest.mark.parametrize("admin", [False, True])
    async def test_inactive_context_cannot_issue_even_with_direct_stale_caller(self, admin):
        org = uuid4()
        user = make_user(org_id=org, is_superuser=True)
        caller = device_caller(user, home=org, grants=[
            device_grant({"*"}, kind=BoundaryKind.PLATFORM) if admin
            else device_grant({"devices.readwrite", "devices.execute"}, org),
        ])
        caller = replace(caller, ctx=replace(caller.ctx, is_active=False))
        with patch("src.services.devices.load_caller", new=AsyncMock(return_value=caller)):
            with pytest.raises(DeviceOperationError):
                await require_device_control_key_issue_permission(mock_session(), user, org)
        assert not caller.ctx.is_platform_admin

    async def test_missing_home_never_infers_platform(self):
        user = make_user()
        caller = device_caller(user, base={"devices.readwrite", "devices.execute"}, grants=[
            device_grant({"devices.readwrite", "devices.execute"}, kind=BoundaryKind.PLATFORM),
        ])
        with patch("src.services.devices.load_caller", new=AsyncMock(return_value=caller)):
            with pytest.raises(DeviceOperationError):
                await require_device_control_key_issue_permission(mock_session(), user)

    async def test_current_home_replaces_stale_token_scope(self):
        current_home = uuid4()
        user = make_user(org_id=uuid4(), is_superuser=True)
        user.is_provider_org = True
        caller = device_caller(user, home=current_home, base={"devices.readwrite"})
        with patch("src.services.devices.load_caller", new=AsyncMock(return_value=caller)):
            principal = await require_device_manage_permission(mock_session(), user)
        assert principal.organization_id == current_home
        assert not principal.is_superuser
        assert not principal.is_provider_org
        assert user.organization_id != current_home

    async def test_revocation_is_not_hidden_by_role_session_cache(self):
        org = uuid4()
        user = make_user(org_id=org)
        user.roles = ["Platform Admin"]
        user.role_ids = [uuid4()]
        before = device_caller(user, home=org, grants=[device_grant({"devices.execute"}, org)])
        after = device_caller(user, home=org)
        with patch("src.services.devices.load_caller", new=AsyncMock(side_effect=[before, after])) as load:
            await require_device_execute_permission(mock_session(), user)
            with pytest.raises(DeviceOperationError):
                await require_device_execute_permission(mock_session(), user)
            assert load.await_count == 2

    @pytest.mark.parametrize("lane", ["embed", "engine_execution_id", "service_id"])
    async def test_non_person_lanes_do_not_borrow_user_grants(self, lane):
        org = uuid4()
        user = make_user(org_id=org)
        setattr(user, lane, True if lane == "embed" else uuid4())
        caller = device_caller(user, home=org, base={"devices.readwrite", "devices.execute"})
        with patch("src.services.devices.load_caller", new=AsyncMock(return_value=caller)):
            with pytest.raises(DeviceOperationError):
                await require_device_control_key_issue_permission(mock_session(), user)

    async def test_existing_explicit_engine_superuser_lane(self):
        user = make_user(is_superuser=True)
        user.is_engine_token = True
        user.engine_execution_id = uuid4()
        with patch("src.services.devices.load_caller", new=AsyncMock(return_value=Caller(user, None))):
            assert await user_can_manage_devices(mock_session(), user)


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

    def test_ordinary_scope_never_cascades_to_global(self):
        org = uuid4()
        user = make_user(org_id=org)
        user.is_provider_org = True
        stmt = list_devices_stmt(user, str(uuid4()))
        assert "IS NULL" not in str(stmt)
        assert stmt.compile().params["organization_id_1"] == org

    def test_missing_home_scope_is_empty_instead_of_global(self):
        stmt = list_devices_stmt(make_user())
        assert "false" in str(stmt).lower()
        assert "IS NULL" not in str(stmt)

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
