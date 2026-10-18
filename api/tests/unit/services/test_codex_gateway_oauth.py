"""Tests for Codex Gateway OAuth onboarding helpers."""

from datetime import datetime, timezone
from typing import Any

import pytest

from src.services.codex_gateway.oauth import (
    MAX_CODEX_EMAIL_CHARS,
    MAX_CODEX_EXPIRY_CHARS,
    MAX_CODEX_SCOPE_CHARS,
    MAX_CODEX_SCOPE_COUNT,
    MAX_CODEX_SUBJECT_CHARS,
    MAX_CODEX_TOKEN_CHARS,
    MAX_CODEX_WORKSPACE_ID_CHARS,
    CodexAuthCacheError,
    parse_codex_auth_cache,
)


def test_parse_codex_auth_cache_extracts_nested_tokens_and_identity():
    parsed = parse_codex_auth_cache(
        {
            "tokens": {
                "access_token": "access-token-secret",
                "refresh_token": "refresh-token-secret",
                "id_token": "id-token-secret",
                "expires_at": "2026-05-25T12:00:00Z",
                "scope": "openid profile offline_access",
            },
            "account": {
                "sub": "chatgpt-user-123",
                "email": "dev@example.test",
                "workspace_id": "workspace-midtown",
            },
        }
    )

    assert parsed.access_token == "access-token-secret"
    assert parsed.refresh_token == "refresh-token-secret"
    assert parsed.upstream_subject == "chatgpt-user-123"
    assert parsed.upstream_email == "dev@example.test"
    assert parsed.upstream_workspace_id == "workspace-midtown"
    assert parsed.scopes == ["openid", "profile", "offline_access"]
    assert parsed.access_token_expires_at is not None


def test_parse_codex_auth_cache_accepts_flat_codex_auth_json_shape():
    parsed = parse_codex_auth_cache(
        {
            "OPENAI_ACCESS_TOKEN": "access-token-secret",
            "OPENAI_REFRESH_TOKEN": "refresh-token-secret",
            "user_id": "chatgpt-user-123",
            "email": "dev@example.test",
        }
    )

    assert parsed.access_token == "access-token-secret"
    assert parsed.refresh_token == "refresh-token-secret"
    assert parsed.upstream_subject == "chatgpt-user-123"
    assert parsed.upstream_email == "dev@example.test"


def test_parse_codex_auth_cache_searches_later_token_sources():
    parsed = parse_codex_auth_cache(
        {
            "tokens": {"note": "not the token source"},
            "openai": {
                "access_token": "access-token-secret",
                "refresh_token": "refresh-token-secret",
                "expires_at_epoch": 1770000000,
            },
            "account": {"note": "not the account source"},
            "profile": {
                "sub": "chatgpt-user-123",
                "email": "dev@example.test",
            },
        }
    )

    assert parsed.access_token == "access-token-secret"
    assert parsed.refresh_token == "refresh-token-secret"
    assert parsed.upstream_subject == "chatgpt-user-123"
    assert parsed.upstream_email == "dev@example.test"
    assert parsed.access_token_expires_at is not None


def test_parse_codex_auth_cache_accepts_codex_auth_tokens_metadata_shape():
    parsed = parse_codex_auth_cache(
        {
            "tokens": {
                "access_token": "access-token-secret",
                "refresh_token": "refresh-token-secret",
                "account_id": "chatgpt-account-from-token-block",
                "email": "coworker@example.test",
                "workspace_id": "workspace-midtown",
                "expires_at": 1770000000,
                "scope": ["openid", "profile", "offline_access"],
            }
        }
    )

    assert parsed.upstream_subject == "chatgpt-account-from-token-block"
    assert parsed.upstream_email == "coworker@example.test"
    assert parsed.upstream_workspace_id == "workspace-midtown"
    assert parsed.access_token_expires_at == datetime.fromtimestamp(
        1770000000,
        tz=timezone.utc,
    )
    assert parsed.scopes == ["openid", "profile", "offline_access"]


def test_parse_codex_auth_cache_accepts_numeric_expiry_string():
    parsed = parse_codex_auth_cache(
        {
            "tokens": {
                "access_token": "access-token-secret",
                "expires_at": "1770000000",
            }
        }
    )

    assert parsed.access_token_expires_at == datetime.fromtimestamp(
        1770000000,
        tz=timezone.utc,
    )


def test_parse_codex_auth_cache_ignores_oversized_numeric_expiry_string():
    parsed = parse_codex_auth_cache(
        {
            "tokens": {
                "access_token": "access-token-secret",
                "expires_at": "9" * 5000,
            }
        }
    )

    assert parsed.access_token_expires_at is None


def test_parse_codex_auth_cache_ignores_invalid_epoch_expiry():
    parsed = parse_codex_auth_cache(
        {
            "tokens": {
                "access_token": "access-token-secret",
                "expires_at_epoch": 10**100,
            },
            "account": {"sub": "chatgpt-user-123"},
        }
    )

    assert parsed.access_token_expires_at is None


def test_parse_codex_auth_cache_rejects_payload_without_access_or_refresh_token():
    with pytest.raises(CodexAuthCacheError) as excinfo:
        parse_codex_auth_cache({"account": {"email": "dev@example.test"}})

    assert str(excinfo.value) == "Codex auth cache does not contain usable token material."


def test_parse_codex_auth_cache_error_text_does_not_include_token_values():
    auth_cache: Any = "access-token-secret"

    with pytest.raises(CodexAuthCacheError) as excinfo:
        parse_codex_auth_cache(auth_cache)

    assert "access-token-secret" not in str(excinfo.value)
    assert str(excinfo.value) == "Codex auth cache must be a JSON object."


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            {"tokens": {"access_token": "a" * (MAX_CODEX_TOKEN_CHARS + 1)}},
            "Codex auth cache access token exceeds its limit.",
        ),
        (
            {"tokens": {"refresh_token": "r" * (MAX_CODEX_TOKEN_CHARS + 1)}},
            "Codex auth cache refresh token exceeds its limit.",
        ),
        (
            {
                "tokens": {"access_token": "token"},
                "account": {"sub": "s" * (MAX_CODEX_SUBJECT_CHARS + 1)},
            },
            "Codex auth cache upstream subject exceeds its limit.",
        ),
        (
            {
                "tokens": {"access_token": "token"},
                "account": {"email": "e" * (MAX_CODEX_EMAIL_CHARS + 1)},
            },
            "Codex auth cache upstream email exceeds its limit.",
        ),
        (
            {
                "tokens": {"access_token": "token"},
                "account": {
                    "workspace_id": "w" * (MAX_CODEX_WORKSPACE_ID_CHARS + 1)
                },
            },
            "Codex auth cache upstream workspace id exceeds its limit.",
        ),
        (
            {
                "tokens": {
                    "access_token": "token",
                    "expires_at": "1" * (MAX_CODEX_EXPIRY_CHARS + 1),
                }
            },
            "Codex auth cache token expiry exceeds its limit.",
        ),
    ],
)
def test_parse_codex_auth_cache_rejects_oversized_token_and_metadata_fields(
    payload,
    expected_error,
):
    with pytest.raises(CodexAuthCacheError) as excinfo:
        parse_codex_auth_cache(payload)

    assert str(excinfo.value) == expected_error


def test_parse_codex_auth_cache_rejects_excessive_scope_count():
    with pytest.raises(CodexAuthCacheError) as excinfo:
        parse_codex_auth_cache(
            {
                "tokens": {
                    "access_token": "token",
                    "scopes": ["scope"] * (MAX_CODEX_SCOPE_COUNT + 1),
                }
            }
        )

    assert str(excinfo.value) == "Codex auth cache scope count exceeds its limit."


def test_parse_codex_auth_cache_rejects_oversized_scope_value():
    with pytest.raises(CodexAuthCacheError) as excinfo:
        parse_codex_auth_cache(
            {
                "tokens": {
                    "access_token": "token",
                    "scope": "s" * (MAX_CODEX_SCOPE_CHARS + 1),
                }
            }
        )

    assert str(excinfo.value) == "Codex auth cache scope value exceeds its limit."
