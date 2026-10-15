"""Security policy tests for profile password changes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from src.models.contracts.profile import PasswordChange
from src.routers import profile


@pytest.mark.asyncio
async def test_external_auth_account_cannot_enroll_password_from_profile(monkeypatch):
    """A stolen profile session must not establish a new login method."""
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        email="external@example.test",
        hashed_password=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = AsyncMock()
    db.execute.return_value = result
    hash_password = MagicMock()
    monkeypatch.setattr(profile, "get_password_hash", hash_password)

    with pytest.raises(HTTPException) as exc:
        await profile.change_password(
            PasswordChange(
                current_password="attacker-controlled",
                new_password="new-persistent-password",
            ),
            SimpleNamespace(user_id=user_id),
            db,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == (
        "Password enrollment is not available from profile settings"
    )
    hash_password.assert_not_called()
    assert db.execute.await_count == 1
    db.commit.assert_not_awaited()
