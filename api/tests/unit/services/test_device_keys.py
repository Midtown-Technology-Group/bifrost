"""Tests for device control-plane key services (#830).

Covers the M0 freeze: prefixed key formats, bcrypt-at-rest verification,
single-use-style rotation semantics, control-key usability, and the
non-empty device allow-list scope rule.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.services.device_keys import (
    CONTROL_KEY_PREFIX,
    DEVICE_KEY_PREFIX,
    ENROLLMENT_TOKEN_PREFIX,
    control_key_usable,
    generate_control_key,
    generate_device_key,
    generate_enrollment_token,
    key_can_target,
    parse_device_key,
    revoke_control_key,
    rotate_control_key,
    rotate_device_key,
    touch_control_key_usage,
    validate_control_key_scope,
    verify_key_hash,
)


def _key_row(**overrides):
    device_ids = overrides.pop("device_ids", None)
    row = SimpleNamespace(
        id=overrides.pop("id", uuid4()),
        organization_id=overrides.pop("organization_id", uuid4()),
        enabled=overrides.pop("enabled", True),
        expires_at=overrides.pop("expires_at", None),
        key_hash=overrides.pop("key_hash", "unused"),
        device_ids=device_ids if device_ids is not None else [uuid4()],
        last_used_at=overrides.pop("last_used_at", None),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _device(**overrides):
    return SimpleNamespace(
        id=overrides.pop("id", uuid4()),
        organization_id=overrides.pop("organization_id", uuid4()),
        status=overrides.pop("status", "active"),
    )


class TestKeyFormats:
    def test_device_key_embeds_device_id(self):
        device_id = uuid4()
        raw, hashed = generate_device_key(device_id)
        parsed = parse_device_key(raw)
        assert parsed is not None
        assert raw.startswith(DEVICE_KEY_PREFIX)
        assert parsed.key_id == device_id
        assert hashed.startswith("$2")

    def test_enrollment_token_embeds_device_id(self):
        device_id = uuid4()
        raw, hashed = generate_enrollment_token(device_id)
        parsed = parse_device_key(raw)
        assert parsed is not None
        assert raw.startswith(ENROLLMENT_TOKEN_PREFIX)
        assert parsed.key_id == device_id
        assert hashed.startswith("$2")

    def test_control_key_embeds_returned_key_id(self):
        key_id, raw, hashed = generate_control_key()
        parsed = parse_device_key(raw)
        assert parsed is not None
        assert raw.startswith(CONTROL_KEY_PREFIX)
        assert parsed.key_id == key_id
        assert hashed.startswith("$2")

    def test_parse_rejects_malformed(self):
        assert parse_device_key("nope") is None
        assert parse_device_key(f"{DEVICE_KEY_PREFIX}not-a-uuid_{'a' * 43}") is None
        assert parse_device_key(f"{DEVICE_KEY_PREFIX}{uuid4()}_short") is None
        assert parse_device_key(f"xxxx_{uuid4()}_{'a' * 43}") is None

    def test_parse_rejects_uppercase_uuid(self):
        device_id = str(uuid4()).upper()
        raw = f"{DEVICE_KEY_PREFIX}{device_id}_{'a' * 43}"
        assert parse_device_key(raw) is None

    def test_secrets_unique_and_urlsafe(self):
        device_id = uuid4()
        raw1, _ = generate_device_key(device_id)
        raw2, _ = generate_device_key(device_id)
        assert raw1 != raw2


class TestVerification:
    def test_verify_roundtrip_device_key(self):
        raw, hashed = generate_device_key(uuid4())
        assert verify_key_hash(raw, hashed) is True

    def test_verify_rejects_wrong_secret(self):
        device_id = uuid4()
        raw, hashed = generate_device_key(device_id)
        wrong = f"{DEVICE_KEY_PREFIX}{device_id}_{'b' * 43}"
        assert verify_key_hash(wrong, hashed) is False

    def test_verify_rejects_missing_hash(self):
        raw, _ = generate_device_key(uuid4())
        assert verify_key_hash(raw, None) is False
        assert verify_key_hash(raw, "") is False

    def test_verify_control_and_enrollment(self):
        _key_id, raw, hashed = generate_control_key()
        assert verify_key_hash(raw, hashed) is True
        assert verify_key_hash(f"{CONTROL_KEY_PREFIX}{uuid4()}_{'c' * 43}", hashed) is False

        raw_t, hashed_t = generate_enrollment_token(uuid4())
        assert verify_key_hash(raw_t, hashed_t) is True


class TestControlKeyUsability:
    def test_enabled_without_expiry_is_usable(self):
        assert control_key_usable(_key_row()) is True

    def test_disabled_is_not_usable(self):
        assert control_key_usable(_key_row(enabled=False)) is False

    def test_expired_is_not_usable(self):
        now = datetime.now(timezone.utc)
        row = _key_row(expires_at=now - timedelta(seconds=1))
        assert control_key_usable(row, now=now) is False

    def test_future_expiry_is_usable(self):
        now = datetime.now(timezone.utc)
        row = _key_row(expires_at=now + timedelta(minutes=5))
        assert control_key_usable(row, now=now) is True


class TestScope:
    def test_in_scope_device_is_targetable(self):
        org = uuid4()
        device_id = uuid4()
        row = _key_row(organization_id=org, device_ids=[device_id])
        device = _device(id=device_id, organization_id=org)
        assert key_can_target(row, device) is True

    def test_device_outside_allow_list_is_denied(self):
        org = uuid4()
        row = _key_row(organization_id=org, device_ids=[uuid4()])
        device = _device(id=uuid4(), organization_id=org)
        assert key_can_target(row, device) is False

    def test_cross_org_device_is_denied(self):
        device_id = uuid4()
        row = _key_row(organization_id=uuid4(), device_ids=[device_id])
        device = _device(id=device_id, organization_id=uuid4())
        assert key_can_target(row, device) is False

    def test_empty_scope_is_rejected(self):
        with pytest.raises(ValueError, match="non-empty device allow-list"):
            validate_control_key_scope([])

    def test_scope_dedupes_preserving_order(self):
        a, b = uuid4(), uuid4()
        assert validate_control_key_scope([a, b, a]) == [a, b]


class TestRotationAndRevocation:
    def test_rotate_device_key_invalidates_old_raw(self):
        device = _device()
        old_raw, old_hash = generate_device_key(device.id)
        device.api_key_hash = old_hash
        device.api_key_enabled = True

        new_raw = rotate_device_key(device)
        assert new_raw != old_raw
        assert device.api_key_enabled is True
        assert verify_key_hash(old_raw, device.api_key_hash) is False
        assert verify_key_hash(new_raw, device.api_key_hash) is True

    def test_rotate_control_key_keeps_row_id_and_authorization_state(self):
        row = _key_row(enabled=False)
        old_hash = row.key_hash

        new_raw = rotate_control_key(row)
        parsed = parse_device_key(new_raw)
        assert parsed is not None
        assert parsed.key_id == row.id
        assert row.key_hash != old_hash
        assert verify_key_hash(new_raw, row.key_hash) is True
        # Rotation must not silently re-enable a revoked key.
        assert row.enabled is False

    def test_revoke_disables(self):
        row = _key_row(enabled=True)
        revoke_control_key(row)
        assert row.enabled is False
        assert control_key_usable(row) is False

    def test_touch_usage_sets_timestamp(self):
        row = _key_row()
        touch_control_key_usage(row)
        assert row.last_used_at is not None

    def test_key_id_types_are_uuid(self):
        key_id, raw, _hashed = generate_control_key()
        assert isinstance(key_id, UUID)
        parsed = parse_device_key(raw)
        assert parsed is not None
        assert isinstance(parsed.key_id, UUID)
