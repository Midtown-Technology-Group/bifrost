from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from bifrost import _sync


class FakeRedis:
    def __init__(self, pending: dict[str, str] | None = None) -> None:
        self.pending = pending or {}
        self.deleted: list[str] = []
        self.eval_calls: list[tuple[str, int, tuple[str, ...]]] = []

    async def hgetall(self, key: str) -> dict[str, str]:
        self.last_hgetall_key = key
        return self.pending

    async def delete(self, key: str) -> None:
        self.deleted.append(key)

    async def eval(self, script: str, key_count: int, key: str, *args: str) -> int:
        self.eval_calls.append((script, key_count, (key, *args)))
        removed = 0
        for index in range(0, len(args), 2):
            field, expected = args[index : index + 2]
            if self.pending.get(field) == expected:
                self.pending.pop(field)
                removed += 1
        return removed


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeScalarResult:
    def __init__(self, value=None) -> None:
        self.value = value

    def first(self):
        return self.value


class FakeExecuteResult:
    def __init__(self, value=None) -> None:
        self.value = value

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self.value)


class FakeEntitySession(FakeSession):
    def __init__(self, result=None) -> None:
        super().__init__()
        self.result = result
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.executed: list[object] = []

    async def execute(self, statement):
        self.executed.append(statement)
        return FakeExecuteResult(self.result)

    def add(self, item: object) -> None:
        self.added.append(item)

    async def delete(self, item: object) -> None:
        self.deleted.append(item)


def _redis_provider(redis: FakeRedis):
    async def get_redis() -> FakeRedis:
        return redis

    return get_redis


@pytest.mark.asyncio
async def test_flush_pending_changes_returns_zero_when_redis_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(_sync, "get_shared_redis", _redis_provider(redis))
    monkeypatch.setattr(
        _sync, "pending_changes_key", lambda execution_id: f"pending:{execution_id}"
    )

    assert await _sync.flush_pending_changes("exec-1", session=FakeSession()) == 0
    assert redis.deleted == []


@pytest.mark.asyncio
async def test_caller_transaction_defers_ack_and_preserves_concurrent_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis(
        {
            "a": json.dumps({"sequence": 2, "entity_type": "role"}),
            "b": "not-json",
            "c": json.dumps({"sequence": 1, "entity_type": "config"}),
        }
    )
    applied: list[str] = []
    session = FakeSession()
    pending_acknowledgements: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(_sync, "get_shared_redis", _redis_provider(redis))
    monkeypatch.setattr(
        _sync, "pending_changes_key", lambda execution_id: f"pending:{execution_id}"
    )

    async def apply_change(db, change):
        assert db is session
        applied.append(change["entity_type"])

    monkeypatch.setattr(_sync, "_apply_change", apply_change)

    assert (
        await _sync.flush_pending_changes(
            "exec-1",
            session=session,
            pending_acknowledgements=pending_acknowledgements,
        )
        == 2
    )
    assert applied == ["config", "role"]
    assert set(redis.pending) == {"a", "b", "c"}
    assert redis.eval_calls == []
    assert session.commits == 0

    await session.rollback()
    assert session.rollbacks == 1
    assert set(redis.pending) == {"a", "b", "c"}

    # If a later writer replaces a field before a successful retry is acknowledged,
    # compare-and-delete must preserve that newer value.
    newer_value = json.dumps({"sequence": 3, "entity_type": "role"})
    redis.pending["a"] = newer_value
    redis.pending["d"] = json.dumps({"sequence": 4, "entity_type": "config"})
    await _sync.acknowledge_persisted_changes(pending_acknowledgements)

    assert redis.pending == {
        "a": newer_value,
        "d": json.dumps({"sequence": 4, "entity_type": "config"}),
    }
    assert len(redis.eval_calls) == 1


@pytest.mark.asyncio
async def test_flush_pending_changes_retries_and_raises_sync_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis({"a": json.dumps({"sequence": 1, "entity_type": "config"})})
    monkeypatch.setattr(_sync, "get_shared_redis", _redis_provider(redis))
    monkeypatch.setattr(
        _sync, "pending_changes_key", lambda execution_id: f"pending:{execution_id}"
    )

    async def fail_apply(db, change):
        raise RuntimeError("db down")

    monkeypatch.setattr(_sync, "_apply_change", fail_apply)

    with pytest.raises(_sync.SyncError, match="Failed to flush: db down"):
        await _sync.flush_pending_changes("exec-1", session=FakeSession())

    assert redis.deleted == []


@pytest.mark.asyncio
async def test_flush_pending_changes_creates_session_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis({"a": json.dumps({"sequence": 1, "entity_type": "config"})})
    session = FakeSession()
    monkeypatch.setattr(_sync, "get_shared_redis", _redis_provider(redis))
    monkeypatch.setattr(
        _sync, "pending_changes_key", lambda execution_id: f"pending:{execution_id}"
    )

    async def apply_change(db, change):
        assert db is session

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(_sync, "_apply_change", apply_change)
    monkeypatch.setattr(
        "src.core.database.get_session_factory",
        lambda: lambda: SessionContext(),
    )

    assert await _sync.flush_pending_changes("exec-1") == 1
    assert session.commits == 1
    assert redis.pending == {}
    assert len(redis.eval_calls) == 1
    assert redis.deleted == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_type", "target"),
    [
        ("config", "_apply_config_change"),
        ("role", "_apply_role_change"),
        ("organization", "_apply_org_change"),
        ("user_role", "_apply_user_role_change"),
        ("form_role", "_apply_form_role_change"),
    ],
)
async def test_apply_change_dispatches_by_entity_type(
    monkeypatch: pytest.MonkeyPatch,
    entity_type: str,
    target: str,
) -> None:
    calls: list[str] = []

    async def handler(db, change):
        calls.append(change["entity_type"])

    monkeypatch.setattr(_sync, target, handler)

    await _sync._apply_change(FakeSession(), {"entity_type": entity_type})

    assert calls == [entity_type]


@pytest.mark.asyncio
async def test_apply_change_ignores_unknown_entity_type() -> None:
    await _sync._apply_change(FakeSession(), {"entity_type": "unknown"})


@pytest.mark.asyncio
async def test_apply_config_change_creates_global_config() -> None:
    session = FakeEntitySession()

    await _sync._apply_config_change(
        session,
        {
            "entity_key": "feature.enabled",
            "org_id": "GLOBAL",
            "operation": "create",
            "user_id": "user-1",
            "data": {"value": True, "config_type": "bool"},
        },
    )

    assert len(session.added) == 1
    config = session.added[0]
    assert config.organization_id is None
    assert config.key == "feature.enabled"
    assert config.value == {"value": True}
    assert config.updated_by == "user-1"


@pytest.mark.asyncio
async def test_apply_config_change_updates_existing_config() -> None:
    existing = SimpleNamespace(value=None, config_type=None, updated_by=None)
    session = FakeEntitySession(existing)

    await _sync._apply_config_change(
        session,
        {
            "entity_key": "feature.limit",
            "org_id": str(uuid4()),
            "operation": "update",
            "data": {"value": "42", "config_type": "int"},
        },
    )

    assert existing.value == {"value": "42"}
    assert existing.updated_by == "system"
    assert session.added == []


@pytest.mark.asyncio
async def test_apply_config_change_deletes_matching_config() -> None:
    session = FakeEntitySession()

    await _sync._apply_config_change(
        session,
        {"entity_key": "feature.enabled", "org_id": "GLOBAL", "operation": "delete"},
    )

    assert len(session.executed) == 1
    assert session.added == []


@pytest.mark.asyncio
async def test_apply_role_change_create_update_delete() -> None:
    role_id = str(uuid4())
    org_id = str(uuid4())
    session = FakeEntitySession()

    await _sync._apply_role_change(
        session,
        {
            "entity_id": role_id,
            "org_id": org_id,
            "operation": "create",
            "user_id": "admin",
            "data": {"name": "Approver", "description": "Can approve"},
        },
    )

    assert len(session.added) == 1
    role = session.added[0]
    assert str(role.id) == role_id
    assert role.name == "Approver"
    assert role.created_by == "admin"

    existing = SimpleNamespace(name="Old", description="Old desc")
    update_session = FakeEntitySession(existing)
    await _sync._apply_role_change(
        update_session,
        {
            "entity_id": role_id,
            "operation": "update",
            "data": {"name": "New", "description": "New desc", "ignored": True},
        },
    )
    assert existing.name == "New"
    assert existing.description == "New desc"

    delete_session = FakeEntitySession(existing)
    await _sync._apply_role_change(
        delete_session,
        {"entity_id": role_id, "operation": "delete"},
    )
    assert delete_session.deleted == [existing]


@pytest.mark.asyncio
async def test_apply_org_change_create_update_delete() -> None:
    org_id = str(uuid4())
    session = FakeEntitySession()

    await _sync._apply_org_change(
        session,
        {
            "entity_id": org_id,
            "operation": "create",
            "user_id": "owner",
            "data": {"name": "Acme", "domain": "acme.test", "is_active": False},
        },
    )

    org = session.added[0]
    assert str(org.id) == org_id
    assert org.name == "Acme"
    assert org.domain == "acme.test"
    assert org.is_active is False
    assert org.created_by == "owner"

    existing = SimpleNamespace(name="Old", domain=None, is_active=True)
    update_session = FakeEntitySession(existing)
    await _sync._apply_org_change(
        update_session,
        {
            "entity_id": org_id,
            "operation": "update",
            "data": {"name": "New", "domain": "new.test", "is_active": False},
        },
    )
    assert existing.name == "New"
    assert existing.domain == "new.test"
    assert existing.is_active is False

    delete_session = FakeEntitySession(existing)
    await _sync._apply_org_change(
        delete_session, {"entity_id": org_id, "operation": "delete"}
    )
    assert existing.is_active is False


@pytest.mark.asyncio
async def test_apply_user_and_form_role_changes_skip_existing_and_add_missing() -> None:
    role_id = str(uuid4())
    user_id = str(uuid4())
    form_id = str(uuid4())

    existing_user_assignment = object()
    existing_user_session = FakeEntitySession(existing_user_assignment)
    await _sync._apply_user_role_change(
        existing_user_session,
        {"entity_id": role_id, "user_id": "admin", "data": {"user_ids": [user_id]}},
    )
    assert existing_user_session.added == []

    new_user_session = FakeEntitySession()
    await _sync._apply_user_role_change(
        new_user_session,
        {"entity_id": role_id, "user_id": "admin", "data": {"user_ids": [user_id]}},
    )
    assert len(new_user_session.added) == 1
    user_role = new_user_session.added[0]
    assert str(user_role.role_id) == role_id
    assert str(user_role.user_id) == user_id
    assert user_role.assigned_by == "admin"

    existing_form_session = FakeEntitySession(object())
    await _sync._apply_form_role_change(
        existing_form_session,
        {"entity_id": role_id, "user_id": "admin", "data": {"form_ids": [form_id]}},
    )
    assert existing_form_session.added == []

    new_form_session = FakeEntitySession()
    await _sync._apply_form_role_change(
        new_form_session,
        {"entity_id": role_id, "user_id": "admin", "data": {"form_ids": [form_id]}},
    )
    assert len(new_form_session.added) == 1
    form_role = new_form_session.added[0]
    assert str(form_role.role_id) == role_id
    assert str(form_role.form_id) == form_id
    assert form_role.assigned_by == "admin"
