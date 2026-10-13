"""Failed password logins must retain their audit event across request denial."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from src.routers import auth


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,status", [("user_not_found", 401), ("no_password", 401),
                      ("wrong_password", 401), ("inactive", 403)]
)
async def test_failed_login_commits_audit_before_denial(monkeypatch, reason, status):
    user = SimpleNamespace(
        id=uuid4(), email="person@example.test",
        hashed_password=None if reason == "no_password" else "password-hash",
        is_active=reason != "inactive",
    )
    repo = SimpleNamespace(
        get_by_email=AsyncMock(return_value=None if reason == "user_not_found" else user)
    )
    monkeypatch.setattr(auth, "UserRepository", lambda *_args, **_kwargs: repo)
    monkeypatch.setattr(auth, "get_client_ip", lambda _request: "127.0.0.1")
    monkeypatch.setattr(auth.auth_limiter, "check", AsyncMock())
    monkeypatch.setattr(auth, "verify_password", lambda *_args: reason != "wrong_password")
    events = []

    async def audit(_db, event, **fields):
        events.append((event, fields))

    async def commit():
        assert len(events) == 1
        events.append("committed")

    monkeypatch.setattr(auth, "emit_audit", audit)
    db = SimpleNamespace(commit=AsyncMock(side_effect=commit))
    with pytest.raises(HTTPException) as denied:
        await auth.login(
            Response(),
            form_data=SimpleNamespace(username=user.email, password="wrong"),
            request=None, db=db,
        )

    assert denied.value.status_code == status
    assert events[0][0] == "auth.login.failed"
    assert events[0][1]["details"] == {"email": user.email, "reason": reason}
    assert events[0][1]["resource_id"] == (None if reason == "user_not_found" else user.id)
    assert events[-1] == "committed"
    db.commit.assert_awaited_once()
