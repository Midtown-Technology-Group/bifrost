"""Tamper-proof ``state`` parameters for integration OAuth.

Thin wrapper over :mod:`src.services.oauth_state_core` that pins the audience
to integration OAuth and validates the payload shape. Per-mapping flows carry
``provider_id`` and ``mapping_id``. Integration-level flows additionally bind
the callback to the initiating user, connection, organization, and redirect
URI so a provider code cannot be replayed through another administrator's
session.

The per-mapping authorize endpoint encodes; the callback decodes and
verifies the nonce hasn't been seen before. Replay protection is critical
because the same state can otherwise be replayed for the full TTL window.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.services.oauth_state_core import (
    StateDecodeError,
    consume_nonce as _consume_nonce,
    decode_state_jwt,
    encode_state_jwt,
    remember_nonce as _remember_nonce,
)

# Distinct from MCP state tokens AND from JWT access tokens so neither can
# masquerade as the other.
_STATE_AUDIENCE = "bifrost-integration-oauth-state"


# Backwards-compat alias for existing callers / tests that imported the old name.
OAuthStateError = StateDecodeError


def encode_state(
    *,
    provider_id: UUID,
    mapping_id: UUID,
) -> tuple[str, str]:
    """Encode a per-mapping OAuth ``state`` token and return ``(token, nonce)``.

    The caller is expected to record the nonce via :func:`remember_nonce`
    so the callback can reject replays.
    """
    claims: dict[str, Any] = {
        "provider_id": str(provider_id),
        "mapping_id": str(mapping_id),
    }
    return encode_state_jwt(audience=_STATE_AUDIENCE, claims=claims)


def encode_connection_state(
    *,
    provider_id: UUID,
    user_id: UUID,
    organization_id: UUID | None,
    redirect_uri: str,
) -> tuple[str, str]:
    """Encode state for an integration-level authorization.

    Unlike the historical opaque state value, every security-relevant callback
    input is authenticated. ``organization_id`` is intentionally present even
    when it is ``None`` so the callback can distinguish a global authorization
    from a malformed token that omitted its scope.
    """
    claims: dict[str, Any] = {
        "flow": "connection",
        "provider_id": str(provider_id),
        "user_id": str(user_id),
        "organization_id": (
            str(organization_id) if organization_id is not None else None
        ),
        "redirect_uri": redirect_uri,
    }
    return encode_state_jwt(audience=_STATE_AUDIENCE, claims=claims)


def decode_state(token: str) -> dict[str, Any]:
    """Verify the per-mapping ``state`` JWT and return its payload.

    Raises:
        OAuthStateError: any signature/expiry/format issue, or missing
        per-mapping fields.
    """
    payload = decode_state_jwt(token, audience=_STATE_AUDIENCE)

    if "provider_id" not in payload:
        raise OAuthStateError("state missing required field: provider_id")

    if "mapping_id" in payload:
        return payload

    if payload.get("flow") != "connection":
        raise OAuthStateError("state has unsupported integration OAuth flow")

    for required in (
        "user_id",
        "organization_id",
        "redirect_uri",
    ):
        if required not in payload:
            raise OAuthStateError(f"state missing required field: {required}")

    return payload


async def remember_nonce(nonce: str) -> None:
    """Persist a per-mapping state nonce in Redis. Best-effort."""
    await _remember_nonce(nonce, audience=_STATE_AUDIENCE)


async def consume_nonce(nonce: str) -> bool:
    """Atomically check-and-delete a per-mapping state nonce. See core docstring."""
    return await _consume_nonce(nonce, audience=_STATE_AUDIENCE)


__all__ = [
    "OAuthStateError",
    "consume_nonce",
    "decode_state",
    "encode_connection_state",
    "encode_state",
    "remember_nonce",
]
