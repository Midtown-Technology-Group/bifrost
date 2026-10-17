from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from shared.role_cache import get_user_roles, invalidate_user
from src.models.contracts.users import BulkUserOperation
from src.routers import users


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        del ex
        self.values[key] = value

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


class _RoleRows:
    def __init__(self, roles: list[tuple[UUID, str]]) -> None:
        self.roles = roles

    def all(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=role_id, name=name) for role_id, name in self.roles]


class _RoleDb:
    def __init__(self, roles: list[tuple[UUID, str]]) -> None:
        self.roles = roles

    async def execute(self, _statement) -> _RoleRows:
        return _RoleRows(self.roles)


class _Users:
    def __init__(self, user_rows: list[SimpleNamespace]) -> None:
        self.user_rows = user_rows

    def all(self) -> list[SimpleNamespace]:
        return self.user_rows


class _UserQueryResult:
    def __init__(self, user_rows: list[SimpleNamespace]) -> None:
        self.user_rows = user_rows

    def scalars(self) -> _Users:
        return _Users(self.user_rows)


class _BulkDb:
    def __init__(self, target: SimpleNamespace, events: list[str]) -> None:
        self.target = target
        self.events = events
        self.execute_count = 0
        self.added: list[object] = []

    async def execute(self, _statement) -> object:
        self.execute_count += 1
        if self.execute_count == 1:
            return _UserQueryResult([self.target])
        self.events.append("replace")
        return SimpleNamespace()

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.events.append("flush")

    async def commit(self) -> None:
        self.events.append("commit")


@pytest.mark.asyncio
async def test_bulk_role_replacement_commits_then_evicts_cached_membership() -> None:
    user_id = uuid4()
    actor_id = uuid4()
    removed_role_id = uuid4()
    replacement_role_id = uuid4()
    redis = _Redis()

    async def get_redis() -> _Redis:
        return redis

    # Prime the same role cache consumed by HTTP table-policy has_role checks.
    with patch("shared.role_cache.get_shared_redis", get_redis):
        role_ids, _ = await get_user_roles(
            user_id,
            _RoleDb([(removed_role_id, "removed")]),
        )
    assert role_ids == [removed_role_id]
    assert redis.values

    events: list[str] = []
    db = _BulkDb(
        SimpleNamespace(
            id=user_id,
            is_system=False,
            is_superuser=False,
            updated_at=None,
        ),
        events,
    )

    async def invalidate_after_commit(affected_user_id: UUID) -> None:
        events.append("invalidate")
        await invalidate_user(affected_user_id)

    async def audit(*_args, **_kwargs) -> None:
        events.append("audit")

    with (
        patch("shared.role_cache.get_shared_redis", get_redis),
        patch.object(users, "invalidate_user_role_cache", invalidate_after_commit),
        patch.object(users, "emit_audit", audit),
    ):
        response = await users.bulk_update_users(
            BulkUserOperation(
                user_ids=[user_id],
                operation="replace_roles",
                role_ids=[replacement_role_id],
            ),
            SimpleNamespace(user_id=actor_id),
            db,
        )

    assert response.succeeded == [user_id]
    assert response.failed == []
    assert events == ["replace", "flush", "audit", "commit", "invalidate"]
    assert not redis.values

    # An immediate post-response authorization read misses Redis and observes
    # the committed replacement rather than the removed role.
    with patch("shared.role_cache.get_shared_redis", get_redis):
        role_ids, role_names = await get_user_roles(
            user_id,
            _RoleDb([(replacement_role_id, "replacement")]),
        )
    assert role_ids == [replacement_role_id]
    assert role_names == ["replacement"]
    assert removed_role_id not in role_ids


@pytest.mark.asyncio
async def test_bulk_role_replacement_does_not_invalidate_failed_targets() -> None:
    actor_id = uuid4()
    db = _BulkDb(
        SimpleNamespace(
            id=actor_id,
            is_system=False,
            is_superuser=True,
            updated_at=None,
        ),
        [],
    )

    with (
        patch.object(users, "invalidate_user_role_cache", AsyncMock()) as invalidate,
        patch.object(users, "emit_audit", AsyncMock()),
    ):
        response = await users.bulk_update_users(
            BulkUserOperation(
                user_ids=[actor_id],
                operation="replace_roles",
                role_ids=[],
            ),
            SimpleNamespace(user_id=actor_id),
            db,
        )

    assert response.succeeded == []
    assert response.failed[0].user_id == actor_id
    invalidate.assert_not_awaited()
