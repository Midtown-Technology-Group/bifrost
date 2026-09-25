"""Engine tokens must outlive the execution they are minted for."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException, Request, Response

from src.config import get_settings
from src.core.auth import renew_engine_access_token
from src.core.security import NO_TIMEOUT_TOKEN_SECONDS, decode_token, mint_engine_token


def _expiry(timeout_seconds: int) -> datetime:
    token, expires_at = mint_engine_token(
        execution_id="00000000-0000-0000-0000-000000000123",
        solution_id=None,
        global_repo_access=False,
        timeout_seconds=timeout_seconds,
    )
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    assert payload["engine_execution_id"] == "00000000-0000-0000-0000-000000000123"
    return datetime.fromisoformat(expires_at)


def test_token_covers_timeout_plus_flush_margin():
    remaining = _expiry(600) - datetime.now(timezone.utc)
    assert timedelta(seconds=890) < remaining <= timedelta(seconds=900)


def test_no_timeout_workflow_gets_short_renewable_token():
    remaining = _expiry(0) - datetime.now(timezone.utc)
    assert timedelta(seconds=NO_TIMEOUT_TOKEN_SECONDS + 290) < remaining
    assert remaining <= timedelta(seconds=NO_TIMEOUT_TOKEN_SECONDS + 300)


@pytest.mark.asyncio
async def test_expired_no_timeout_engine_token_renews_only_for_active_attempt():
    execution_id, attempt_token = uuid4(), uuid4()
    token, _ = mint_engine_token(
        execution_id=str(execution_id),
        attempt_token=str(attempt_token),
        timeout_seconds=0,
    )
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    payload["exp"] = int(datetime.now(timezone.utc).timestamp()) - 1
    settings = get_settings()
    expired = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    assert decode_token(expired, expected_type="access") is None

    db = AsyncMock()
    db.scalar.return_value = uuid4()
    renewed = await renew_engine_access_token(db, expired)
    assert renewed is not None
    renewed_payload = decode_token(renewed, expected_type="access")
    assert renewed_payload is not None
    assert renewed_payload["engine_execution_id"] == str(execution_id)
    assert renewed_payload["engine_attempt_token"] == str(attempt_token)
    assert renewed_payload["exp"] - int(datetime.now(timezone.utc).timestamp()) <= 600

    db.scalar.return_value = None
    assert await renew_engine_access_token(db, expired) is None


@pytest.mark.asyncio
async def test_finite_engine_token_cannot_renew():
    token, _ = mint_engine_token(
        execution_id=str(uuid4()), attempt_token=str(uuid4()), timeout_seconds=600
    )
    db = AsyncMock()
    assert await renew_engine_access_token(db, token) is None
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_engine_refresh_has_attempt_limit_and_invalid_token_keeps_ip_limit(monkeypatch):
    from src.routers import auth

    execution_id, attempt_token = uuid4(), uuid4()
    token, _ = mint_engine_token(
        execution_id=str(execution_id), attempt_token=str(attempt_token), timeout_seconds=0
    )
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 12345)})
    rate_check = AsyncMock()
    renew = AsyncMock(return_value="renewed")
    monkeypatch.setattr(auth.auth_limiter, "check", rate_check)
    monkeypatch.setattr(auth, "renew_engine_access_token", renew)

    response = await auth.refresh_token(
        request, Response(), auth.TokenRefresh(refresh_token=token), AsyncMock()
    )
    assert response.access_token == "renewed"
    rate_check.assert_awaited_once_with(
        "engine_refresh", f"{execution_id}:{attempt_token}"
    )

    rate_check.reset_mock()
    renew.return_value = None
    with pytest.raises(HTTPException) as exc:
        await auth.refresh_token(
            request, Response(), auth.TokenRefresh(refresh_token="invalid"), AsyncMock()
        )
    assert exc.value.status_code == 401
    rate_check.assert_awaited_once_with("refresh", "127.0.0.1")
