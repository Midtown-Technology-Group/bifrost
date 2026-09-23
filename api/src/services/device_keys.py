"""Key material services for the device control plane (epic #818, M1.2 #830).

Implements the M0 freeze (docs/architecture/device-control-plane.md):

- Key formats embed their row UUID so verification is an O(1) lookup:
  ``bfdk_<device_uuid>_<secret>`` (device key), ``bfck_<key_uuid>_<secret>``
  (control key), ``bfen_<device_uuid>_<secret>`` (enrollment token); secrets
  are ``token_urlsafe(32)``.
- Only bcrypt hashes of the **secret component** are stored (same discipline
  as ``workflow_keys`` hashing its 43-char token): the UUID routes to the
  row, then bcrypt verifies the secret. The full prefixed raw key is 85
  bytes, over bcrypt's 72-byte limit, so the full raw value is never hashed
  directly. Raw values are returned exactly once to the caller that minted
  them.
- A control key always carries a non-empty device allow-list; there is no
  org-wide implicit scope.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.core.security import get_password_hash, verify_password

DEVICE_KEY_PREFIX = "bfdk_"
CONTROL_KEY_PREFIX = "bfck_"
ENROLLMENT_TOKEN_PREFIX = "bfen_"

_KEY_PATTERN = re.compile(
    r"^(?P<prefix>bfdk_|bfck_|bfen_)"
    r"(?P<key_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})_"
    r"(?P<secret>[A-Za-z0-9_-]{43})$"
)


class ParsedKey:
    """Structured form of a device-control-plane raw key."""

    __slots__ = ("prefix", "key_id", "secret")

    def __init__(self, prefix: str, key_id: UUID, secret: str) -> None:
        self.prefix = prefix
        self.key_id = key_id
        self.secret = secret


def parse_device_key(raw: str) -> ParsedKey | None:
    """Parse a raw key; returns None when the format does not match."""
    match = _KEY_PATTERN.match(raw)
    if match is None:
        return None
    return ParsedKey(match["prefix"], UUID(match["key_id"]), match["secret"])


def _mint(prefix: str, key_id: UUID) -> tuple[str, str]:
    """Build raw key + bcrypt hash of its secret component."""
    raw = f"{prefix}{key_id}_{secrets.token_urlsafe(32)}"
    parsed = parse_device_key(raw)
    assert parsed is not None  # minted keys always match our own pattern
    return raw, get_password_hash(parsed.secret)


def generate_device_key(device_id: UUID) -> tuple[str, str]:
    """Mint a device key for an existing device row.

    Returns (raw, bcrypt_hash-of-secret). Raw is shown once; only the hash
    of the secret component is stored.
    """
    return _mint(DEVICE_KEY_PREFIX, device_id)


def generate_enrollment_token(device_id: UUID) -> tuple[str, str]:
    """Mint a single-use enrollment token for a pending device.

    Returns (raw, bcrypt_hash-of-secret). Single-use and TTL enforcement
    happen at the enroll route; the raw token is returned exactly once.
    """
    return _mint(ENROLLMENT_TOKEN_PREFIX, device_id)


def generate_control_key(key_id: UUID | None = None) -> tuple[UUID, str, str]:
    """Mint control-key material.

    Returns (key_id, raw, bcrypt_hash-of-secret). The caller persists a
    ``device_control_keys`` row with exactly this ``key_id`` (the UUID
    embedded in the raw key) and the hash.
    """
    resolved_id = key_id if key_id is not None else uuid4()
    raw, hashed = _mint(CONTROL_KEY_PREFIX, resolved_id)
    return resolved_id, raw, hashed


def verify_key_hash(raw: str, hashed: str | None) -> bool:
    """Constant-verify a raw control-plane key against its stored hash.

    Parses the raw key, then bcrypt-verifies its secret component. Unparsable
    input or a missing hash fails closed.
    """
    if not hashed:
        return False
    parsed = parse_device_key(raw)
    if parsed is None:
        return False
    return verify_password(parsed.secret, hashed)


def control_key_usable(
    row: object, now: datetime | None = None
) -> bool:
    """True when a control key row is enabled and unexpired.

    ``row`` is anything with ``enabled: bool`` and ``expires_at`` (a
    ``DeviceControlKey`` or a test double).
    """
    if not getattr(row, "enabled", False):
        return False
    expires_at: datetime | None = getattr(row, "expires_at", None)
    if expires_at is None:
        return True
    current = now if now is not None else datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > current


def key_can_target(row: object, device: object) -> bool:
    """Scope check: the control key's org matches and its allow-list contains
    the device. Device status/health is checked separately on the create path.
    """
    if getattr(device, "organization_id") != getattr(row, "organization_id"):
        return False
    device_ids = set(getattr(row, "device_ids") or [])
    return getattr(device, "id") in device_ids


def validate_control_key_scope(device_ids: list[UUID]) -> list[UUID]:
    """Reject an empty allow-list — no org-wide implicit control key (M0)."""
    if not device_ids:
        raise ValueError(
            "control key requires a non-empty device allow-list (device_ids)"
        )
    deduped = list(dict.fromkeys(device_ids))
    if not deduped:
        raise ValueError(
            "control key requires a non-empty device allow-list (device_ids)"
        )
    return deduped


def rotate_device_key(device: object) -> str:
    """Replace a device's key hash in place (same device id) and return the
    raw key exactly once. Old raw keys stop verifying immediately.
    """
    raw, hashed = generate_device_key(device.id)  # type: ignore[attr-defined]
    device.api_key_hash = hashed  # type: ignore[attr-defined]
    device.api_key_enabled = True  # type: ignore[attr-defined]
    return raw


def rotate_control_key(row: object) -> str:
    """Replace a control key's secret/hash on the same row id (audit
    continuity). Does not change ``enabled`` or ``expires_at`` — rotation is
    not a re-authorization; revoke/expiry stay authoritative.
    """
    secret = secrets.token_urlsafe(32)
    raw = f"{CONTROL_KEY_PREFIX}{row.id}_{secret}"  # type: ignore[attr-defined]
    row.key_hash = get_password_hash(secret)  # type: ignore[attr-defined]
    return raw


def revoke_control_key(row: object) -> None:
    """Disable a control key without deleting the audit row."""
    row.enabled = False  # type: ignore[attr-defined]


def touch_control_key_usage(row: object, now: datetime | None = None) -> None:
    """Record ``last_used_at`` on a successful authorized use."""
    row.last_used_at = now if now is not None else datetime.now(timezone.utc)  # type: ignore[attr-defined]
