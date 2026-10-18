"""Legacy RBAC buffers have no trusted actor and cannot write assignments."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost._sync import SyncError, _apply_user_role_change
from src.core.constants import SYSTEM_USER_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", [SYSTEM_USER_ID, str(uuid4()), "not-a-uuid", None])
@pytest.mark.parametrize("operation", ["assign", "remove", "unexpected"])
async def test_legacy_membership_buffer_refused_before_any_database_access(
    holder, operation
):
    session = AsyncMock()
    with pytest.raises(SyncError, match="canonical role assignment API"):
        await _apply_user_role_change(
            session,
            {
                "entity_type": "user_role",
                "operation": operation,
                "entity_id": str(uuid4()),
                "user_id": "untrusted-admin-string",
                "data": {"user_ids": [holder]},
            },
        )
    assert session.mock_calls == []
