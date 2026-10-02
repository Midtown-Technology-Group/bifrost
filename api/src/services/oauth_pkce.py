"""PKCE support for third-party integration OAuth flows.

This module is the single source of truth for Proof Key for Code Exchange
(RFC 7636) in the integration OAuth lanes:

- ``POST /api/oauth/connections/{name}/authorize`` (integration-level)
- ``GET /api/integrations/{id}/oauth/authorize`` (integration-level)
- ``POST /api/integrations/{id}/mappings/{mid}/oauth/authorize`` (per-mapping)
- ``POST /api/oauth/callback/{name}`` (shared callback for both levels)

Enabling PKCE is an explicit per-provider opt-in via the
``use_pkce`` flag in the provider's ``provider_metadata``::

    {"use_pkce": true}

PKCE is never inferred from a missing client secret: confidential clients
keep sending ``client_secret`` exactly as before, and public clients simply
omit it. A PKCE-enabled confidential client sends both ``client_secret``
and ``code_verifier`` (valid OAuth 2.0).

Verifier storage follows the ``oauth_sso`` lane pattern: the verifier never
leaves the server. The authorize endpoint stores it in Redis keyed by the
``state`` value and the callback consumes it exactly once (get-and-delete),
so a replayed state finds nothing and is rejected.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from typing import Any
from uuid import UUID

from src.core.cache import get_shared_redis
from src.core.cache.keys import integration_oauth_pkce_key

logger = logging.getLogger(__name__)

#: ``provider_metadata`` key that opts an integration OAuth provider into PKCE.
USE_PKCE_METADATA_KEY = "use_pkce"

#: PKCE challenge method. S256 is the only method we emit or accept.
PKCE_CHALLENGE_METHOD = "S256"

#: How long an authorize-issued verifier survives without a callback.
#: Matches the state-JWT TTL in :mod:`src.services.oauth_state_core` so the
#: verifier cannot outlive the state that references it.
PKCE_VERIFIER_TTL_SECONDS = 10 * 60


def generate_code_verifier() -> str:
    """Generate a high-entropy PKCE ``code_verifier`` (RFC 7636 §4.1).

    ``secrets.token_urlsafe(64)`` truncated to 128 chars stays inside the
    43–128 char range and uses only unreserved characters.
    """
    return secrets.token_urlsafe(64)[:128]


def code_challenge_for(verifier: str) -> str:
    """Derive the S256 ``code_challenge`` for a verifier (RFC 7636 §4.2)."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def build_pkce_authorize_params(verifier: str) -> dict[str, str]:
    """Authorize-URL query params carrying the PKCE challenge."""
    return {
        "code_challenge": code_challenge_for(verifier),
        "code_challenge_method": PKCE_CHALLENGE_METHOD,
    }


def provider_uses_pkce(provider: Any) -> bool:
    """Return True when an OAuth provider is explicitly configured for PKCE.

    Accepts an ``OAuthProvider`` ORM row (or test double exposing
    ``provider_metadata``) or a bare metadata dict. Anything else —
    missing, non-dict, or falsy ``use_pkce`` — means confidential-client
    behavior is preserved unchanged.
    """
    if isinstance(provider, dict):
        metadata = provider
    else:
        metadata = getattr(provider, "provider_metadata", None) or {}
    if not isinstance(metadata, dict):
        return False
    return metadata.get(USE_PKCE_METADATA_KEY) is True


async def store_pkce_verifier(
    *,
    state: str,
    code_verifier: str,
    redirect_uri: str,
    provider_id: UUID | str | None = None,
    mapping_id: UUID | str | None = None,
) -> None:
    """Persist a PKCE verifier server-side, bound to an authorize ``state``.

    The payload binds the verifier to the exact ``state`` issued alongside
    it plus the provider/integration, redirect URI, and optional per-mapping
    context, so the callback can verify the round trip instead of trusting
    client-supplied values.
    """
    payload = {
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "provider_id": str(provider_id) if provider_id is not None else None,
        "mapping_id": str(mapping_id) if mapping_id is not None else None,
    }
    redis_client = await get_shared_redis()
    await redis_client.setex(
        integration_oauth_pkce_key(state),
        PKCE_VERIFIER_TTL_SECONDS,
        json.dumps(payload),
    )


def validate_pkce_binding(
    payload: dict[str, Any],
    *,
    provider_id: UUID | str | None = None,
    redirect_uri: str | None = None,
) -> None:
    """Verify a consumed PKCE payload belongs to this callback round trip.

    Raises:
        ValueError: the verifier was issued for a different provider or a
            different redirect URI (state swapped across flows).
    """
    if provider_id is not None and payload.get("provider_id") not in (None, str(provider_id)):
        raise ValueError("PKCE verifier was not issued for this provider")
    if redirect_uri is not None and payload.get("redirect_uri") not in (None, redirect_uri):
        raise ValueError("PKCE redirect URI does not match the authorize request")


async def consume_pkce_verifier(state: str) -> dict[str, Any] | None:
    """Fetch and delete the PKCE payload stored for ``state`` (single-use).

    Returns the stored payload dict, or ``None`` when no verifier was
    stored, it expired, or it was already consumed (replay). Callers must
    treat ``None`` on a PKCE-enabled provider as a rejection, and must
    ignore any payload on a provider that does not use PKCE.
    """
    redis_client = await get_shared_redis()
    key = integration_oauth_pkce_key(state)
    raw = await redis_client.get(key)
    if raw is None:
        return None
    # Single-use: delete immediately so a replayed state finds nothing.
    await redis_client.delete(key)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Stored PKCE payload for OAuth state is not valid JSON")
        return None
    if not isinstance(payload, dict) or not payload.get("code_verifier"):
        return None
    return payload


__all__ = [
    "PKCE_CHALLENGE_METHOD",
    "PKCE_VERIFIER_TTL_SECONDS",
    "USE_PKCE_METADATA_KEY",
    "build_pkce_authorize_params",
    "code_challenge_for",
    "consume_pkce_verifier",
    "generate_code_verifier",
    "provider_uses_pkce",
    "store_pkce_verifier",
    "validate_pkce_binding",
]
