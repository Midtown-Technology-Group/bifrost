"""Service-level tests for control-key registry services (M1.4 #832).

Covers the M0 scope contract (non-empty allow-list, same-org devices only),
expiry validation, rotate/revoke semantics, and org-scoped loaders without a
live database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import status

from src.core.principal import UserPrincipal
from src.models.contracts.device_control_keys import ControlKeyCreate
from src.models.orm.device_control_keys import DeviceControlKey
from src.services.device_control_keys import (
    create_control_key,
    get_control_key_scoped,
    list_control_keys_stmt,
    resolve_control_key,
    revoke_control_key_route,
    rotate_control_key_route,
)
from src.services.device_keys import generate_control_key, verify_key_hash
from src.services.devices import DeviceOperationError


def make_user(*, is_superuser: bool = False, org_id=None) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="user@example.com",
        organization_id=org_id,
        name="Test User",
        is_superuser=is_superuser,
    )


def mock_session(*, visible_ids=None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    if visible_ids is not None:
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(visible_ids)
        session.execute.return_value = result
    return session


class TestCreateControlKey:
    async def test_empty_scope_rejected(self):
        session = mock_session()
        user = make_user(org_id=uuid4())
        # Bypass pydantic min_length with a direct service call.
        body = ControlKeyCreate.model_construct(
            name="k", device_ids=[], organization_id=None, expires_at=None
        )
        with pytest.raises(DeviceOperationError) as exc:
            await create_control_key(session, user, body)
        assert exc.value.code == "invalid_parameter"
        assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    async def test_invisible_device_rejected(self):
        device_a, device_b = uuid4(), uuid4()
        session = mock_session(visible_ids=[device_a])  # b not in org scope
        user = make_user(org_id=uuid4())
        body = ControlKeyCreate(name="k", device_ids=[device_a, device_b])
        with pytest.raises(DeviceOperationError) as exc:
            await create_control_key(session, user, body)
        assert exc.value.code == "invalid_parameter"

    async def test_cross_org_create_rejected(self):
        session = mock_session(visible_ids=[uuid4()])
        user = make_user(org_id=uuid4())
        body = ControlKeyCreate(
            name="k",
            device_ids=[uuid4()],
            organization_id=uuid4(),
        )
        with pytest.raises(DeviceOperationError) as exc:
            await create_control_key(session, user, body)
        assert exc.value.code == "permission_denied"

    async def test_past_expiry_rejected(self):
        device_id = uuid4()
        session = mock_session(visible_ids=[device_id])
        user = make_user(org_id=uuid4())
        body = ControlKeyCreate(
            name="k",
            device_ids=[device_id],
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        with pytest.raises(DeviceOperationError) as exc:
            await create_control_key(session, user, body)
        assert exc.value.code == "invalid_parameter"

    async def test_success_embeds_key_id_and_stores_secret_hash(self):
        device_ids = [uuid4(), uuid4()]
        session = mock_session(visible_ids=device_ids)
        user = make_user(org_id=uuid4())
        body = ControlKeyCreate(
            name="workspace",
            device_ids=[device_ids[1], device_ids[0], device_ids[0]],  # dupes
        )
        row, raw = await create_control_key(session, user, body)

        assert isinstance(row, DeviceControlKey)
        assert row.organization_id == user.organization_id
        assert row.enabled is True
        assert row.created_by == str(user.user_id)
        # Scope deduped and complete.
        assert sorted(map(str, row.device_ids)) == sorted(map(str, device_ids))
        # Raw embeds this row's id and starts with the frozen prefix.
        assert raw.startswith("bfck_")
        assert str(row.id) in raw
        # The stored hash verifies the raw but is not the raw.
        assert row.key_hash != raw
        assert verify_key_hash(raw, row.key_hash) is True
        session.add.assert_called_once()


class TestScopedAccess:
    async def test_missing_key_404(self):
        session = mock_session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute.return_value = result
        with pytest.raises(DeviceOperationError) as exc:
            await get_control_key_scoped(session, make_user(org_id=uuid4()), uuid4())
        assert exc.value.code == "unknown_control_key"
        assert exc.value.status_code == 404

    async def test_rotate_keeps_id_invalidates_old_raw(self):
        device_id = uuid4()
        org = uuid4()
        existing = DeviceControlKey(
            id=uuid4(),
            organization_id=org,
            name="k",
            key_hash="$2b$old",
            enabled=True,
            device_ids=[device_id],
            created_by=str(uuid4()),
        )
        session = mock_session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = existing
        session.execute.return_value = result
        user = make_user(org_id=org)

        row, raw = await rotate_control_key_route(session, user, existing.id)
        assert row.id == existing.id
        assert raw.startswith("bfck_")
        assert str(row.id) in raw
        assert row.key_hash != "$2b$old"
        assert verify_key_hash(raw, row.key_hash) is True

    async def test_revoke_disables_and_is_idempotent(self):
        org = uuid4()
        existing = DeviceControlKey(
            id=uuid4(),
            organization_id=org,
            name="k",
            key_hash="$2b$old",
            enabled=True,
            device_ids=[uuid4()],
            created_by=str(uuid4()),
        )
        session = mock_session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = existing
        session.execute.return_value = result
        user = make_user(org_id=org)

        first = await revoke_control_key_route(session, user, existing.id)
        assert first.enabled is False
        second = await revoke_control_key_route(session, user, existing.id)
        assert second.enabled is False

    async def test_rotate_rejects_revoked_key(self):
        org = uuid4()
        existing = DeviceControlKey(
            id=uuid4(),
            organization_id=org,
            name="k",
            key_hash="$2b$old",
            enabled=False,
            device_ids=[uuid4()],
            created_by=str(uuid4()),
        )
        session = mock_session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = existing
        session.execute.return_value = result
        user = make_user(org_id=org)

        with pytest.raises(DeviceOperationError) as exc:
            await rotate_control_key_route(session, user, existing.id)
        assert exc.value.code == "control_key_inactive"
        assert exc.value.status_code == 409
        # The old secret hash is untouched — rotation did not happen.
        assert existing.key_hash == "$2b$old"

    async def test_rotate_rejects_expired_key(self):
        org = uuid4()
        existing = DeviceControlKey(
            id=uuid4(),
            organization_id=org,
            name="k",
            key_hash="$2b$old",
            enabled=True,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            device_ids=[uuid4()],
            created_by=str(uuid4()),
        )
        session = mock_session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = existing
        session.execute.return_value = result
        user = make_user(org_id=org)

        with pytest.raises(DeviceOperationError) as exc:
            await rotate_control_key_route(session, user, existing.id)
        assert exc.value.code == "control_key_inactive"
        assert existing.key_hash == "$2b$old"


class TestListStatement:
    def test_org_user_statement_filters_by_org(self):
        user = make_user(org_id=uuid4())
        rendered = str(list_control_keys_stmt(user))
        assert "WHERE device_control_keys.organization_id" in rendered

    def test_superuser_unscoped_statement(self):
        user = make_user(is_superuser=True, org_id=None)
        rendered = str(list_control_keys_stmt(user))
        assert "WHERE" not in rendered


class TestResolveControlKey:
    """Header-auth resolution for X-Bifrost-Control-Key (M2.4 #836)."""

    @staticmethod
    def _row(key_id, *, enabled=True, expires_at=None, key_hash=None):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=key_id, organization_id=uuid4(), enabled=enabled,
            expires_at=expires_at, key_hash=key_hash, device_ids=[uuid4()],
        )

    async def test_valid_key_resolves(self):
        key_id, raw, hashed = generate_control_key()
        session = AsyncMock()
        session.get = AsyncMock(return_value=self._row(key_id, key_hash=hashed))
        row = await resolve_control_key(session, raw)
        assert row.id == key_id

    async def test_malformed_key_401(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        with pytest.raises(DeviceOperationError) as exc:
            await resolve_control_key(session, "not-a-key")
        assert exc.value.code == "invalid_key"
        assert exc.value.status_code == 401

        with pytest.raises(DeviceOperationError) as exc:
            await resolve_control_key(
                session, f"bfck_{uuid4()}_{ 'a' * 43 }"
            )
        assert exc.value.code == "invalid_key"
        session.get.assert_awaited()  # well-formed: row lookup attempted

    async def test_unknown_key_401_no_oracle(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        _key_id, raw, _hashed = generate_control_key()
        with pytest.raises(DeviceOperationError) as exc:
            await resolve_control_key(session, raw)
        assert exc.value.code == "invalid_key"

    async def test_revoked_key_401(self):
        key_id, raw, hashed = generate_control_key()
        session = AsyncMock()
        session.get = AsyncMock(
            return_value=self._row(key_id, enabled=False, key_hash=hashed)
        )
        with pytest.raises(DeviceOperationError) as exc:
            await resolve_control_key(session, raw)
        assert exc.value.code == "invalid_key"

    async def test_expired_key_401(self):
        from datetime import datetime, timedelta, timezone

        key_id, raw, hashed = generate_control_key()
        session = AsyncMock()
        session.get = AsyncMock(
            return_value=self._row(
                key_id,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                key_hash=hashed,
            )
        )
        with pytest.raises(DeviceOperationError) as exc:
            await resolve_control_key(session, raw)
        assert exc.value.code == "invalid_key"

    async def test_wrong_secret_401(self):
        key_id, _raw, hashed = generate_control_key()
        other_id, other_raw, _other_hash = generate_control_key()
        session = AsyncMock()
        session.get = AsyncMock(return_value=self._row(key_id, key_hash=hashed))
        # Well-formed key for a DIFFERENT row id would 404-fetch → unknown;
        # same-row wrong secret: rebuild raw with this row id + other secret.
        forged = f"bfck_{key_id}_{other_raw.rsplit('_', 1)[1]}"
        with pytest.raises(DeviceOperationError) as exc:
            await resolve_control_key(session, forged)
        assert exc.value.code == "invalid_key"
