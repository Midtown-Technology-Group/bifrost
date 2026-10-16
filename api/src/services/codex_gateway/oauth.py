"""OAuth onboarding helpers for Codex Gateway upstream accounts."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


MAX_CODEX_AUTH_CACHE_IMPORT_BYTES = 256 * 1024
MAX_CODEX_TOKEN_CHARS = 64 * 1024
MAX_CODEX_SUBJECT_CHARS = 512
MAX_CODEX_EMAIL_CHARS = 320
MAX_CODEX_WORKSPACE_ID_CHARS = 512
MAX_CODEX_SCOPE_COUNT = 128
MAX_CODEX_SCOPE_CHARS = 256
MAX_CODEX_EXPIRY_CHARS = 128


class CodexAuthCacheError(ValueError):
    """Raised when a Codex auth cache payload cannot be safely imported."""


@dataclass(frozen=True)
class ParsedCodexAuthCache:
    """Token material and safe metadata extracted from a Codex auth cache."""

    access_token: str | None
    refresh_token: str | None
    upstream_subject: str
    upstream_email: str | None
    upstream_workspace_id: str | None
    access_token_expires_at: datetime | None
    scopes: list[str]


def parse_codex_auth_cache(auth_cache: dict[str, Any]) -> ParsedCodexAuthCache:
    """Parse a user's own Codex auth cache without leaking token values."""
    if not isinstance(auth_cache, dict):
        raise CodexAuthCacheError("Codex auth cache must be a JSON object.")

    token_sources = _candidate_mappings(
        auth_cache.get("tokens"),
        auth_cache.get("openai"),
        auth_cache.get("chatgpt"),
        auth_cache,
    )
    account_sources = _candidate_mappings(
        auth_cache.get("account"),
        auth_cache.get("user"),
        auth_cache.get("profile"),
        auth_cache.get("tokens"),
        auth_cache.get("openai"),
        auth_cache.get("chatgpt"),
        auth_cache,
    )

    access_token = _find_string(
        token_sources,
        "access_token",
        "OPENAI_ACCESS_TOKEN",
        "codex_access_token",
    )
    refresh_token = _find_string(
        token_sources,
        "refresh_token",
        "OPENAI_REFRESH_TOKEN",
        "codex_refresh_token",
    )
    access_token = _bounded_string(
        access_token,
        field="access token",
        max_chars=MAX_CODEX_TOKEN_CHARS,
    )
    refresh_token = _bounded_string(
        refresh_token,
        field="refresh token",
        max_chars=MAX_CODEX_TOKEN_CHARS,
    )
    if not access_token and not refresh_token:
        raise CodexAuthCacheError(
            "Codex auth cache does not contain usable token material."
        )

    subject = _find_string(
        account_sources,
        "sub",
        "subject",
        "user_id",
        "account_id",
    )
    email = _find_string(
        account_sources,
        "email",
        "upstream_email",
    )
    workspace_id = _find_string(
        account_sources,
        "workspace_id",
        "workspace",
        "organization_id",
    )

    subject = _bounded_string(
        subject,
        field="upstream subject",
        max_chars=MAX_CODEX_SUBJECT_CHARS,
    )
    email = _bounded_string(
        email,
        field="upstream email",
        max_chars=MAX_CODEX_EMAIL_CHARS,
    )
    workspace_id = _bounded_string(
        workspace_id,
        field="upstream workspace id",
        max_chars=MAX_CODEX_WORKSPACE_ID_CHARS,
    )

    if subject is None:
        subject = _unknown_subject(access_token or refresh_token)

    return ParsedCodexAuthCache(
        access_token=access_token,
        refresh_token=refresh_token,
        upstream_subject=subject,
        upstream_email=email,
        upstream_workspace_id=workspace_id,
        access_token_expires_at=_parse_expiry(token_sources),
        scopes=_parse_scopes(_find_value(token_sources, "scope", "scopes")),
    )


def _candidate_mappings(*values: Any) -> list[dict[str, Any]]:
    return [value for value in values if isinstance(value, dict)]


def _find_string(sources: list[dict[str, Any]], *keys: str) -> str | None:
    return _first_string(_find_value(sources, *keys))


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _find_value(sources: list[dict[str, Any]], *keys: str) -> Any:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None:
                return value
    return None


def _bounded_string(
    value: str | None,
    *,
    field: str,
    max_chars: int,
) -> str | None:
    if value is not None and len(value) > max_chars:
        raise CodexAuthCacheError(f"Codex auth cache {field} exceeds its limit.")
    return value


def _parse_expiry(token_sources: list[dict[str, Any]]) -> datetime | None:
    raw = _find_value(token_sources, "expires_at", "expiry", "expiration")
    if isinstance(raw, (int, float)):
        return _datetime_from_epoch(raw)
    if isinstance(raw, str) and raw:
        if len(raw) > MAX_CODEX_EXPIRY_CHARS:
            raise CodexAuthCacheError(
                "Codex auth cache token expiry exceeds its limit."
            )
        if raw.isdigit():
            return _datetime_from_epoch(int(raw))
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    exp = _find_value(token_sources, "expires_at_epoch", "expires_at_unix")
    if isinstance(exp, (int, float)):
        return _datetime_from_epoch(exp)
    return None


def _datetime_from_epoch(value: int | float) -> datetime | None:
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_scopes(value: Any) -> list[str]:
    if isinstance(value, str):
        scopes = [scope for scope in value.split() if scope]
    elif isinstance(value, list):
        scopes = [scope for scope in value if isinstance(scope, str) and scope]
    else:
        scopes = []

    if len(scopes) > MAX_CODEX_SCOPE_COUNT:
        raise CodexAuthCacheError("Codex auth cache scope count exceeds its limit.")
    if any(len(scope) > MAX_CODEX_SCOPE_CHARS for scope in scopes):
        raise CodexAuthCacheError("Codex auth cache scope value exceeds its limit.")
    return scopes


def _unknown_subject(token: str | None) -> str:
    digest = hashlib.sha256((token or "missing-token").encode()).hexdigest()[:16]
    return f"unknown:{digest}"
