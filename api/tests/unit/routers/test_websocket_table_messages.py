from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.auth import UserPrincipal
from src.routers import websocket as ws_mod
from src.models.contracts.policies import Expr, TablePolicies


def _websocket_with_subscriptions(*table_ids: str) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            table_subscriptions={table_id: {"filter": None} for table_id in table_ids},
        ),
        send_json=AsyncMock(),
    )


def _user() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid.uuid4(),
        email="user@example.com",
        organization_id=uuid.uuid4(),
        is_superuser=False,
    )


async def test_table_invalidated_for_active_subscription_forwards_canonical_payload(monkeypatch):
    table_id = str(uuid.uuid4())
    websocket = _websocket_with_subscriptions(table_id)
    load_policies = AsyncMock(return_value=TablePolicies.model_validate({"policies": [{"name": "read-all", "actions": ["read"]}]}))
    monkeypatch.setattr(ws_mod, "_load_policies_for_table", load_policies)

    await ws_mod._handle_table_message(
        websocket,
        _user(),
        f"table:{table_id}",
        {"type": "table_invalidated", "table_id": "ignored", "extra": "ignored",
         "mutations": [{"old_row": None, "new_row": {"id": "private-row", "secret": "never-forward"}}]},
    )

    websocket.send_json.assert_awaited_once_with({
        "type": "table_invalidated",
        "table_id": table_id,
    })
    load_policies.assert_awaited_once()


@pytest.mark.parametrize("old_owner,new_owner,filter_matches,should_send", [
    ("other", "other", True, False),
    ("other", "user@example.com", True, True),
    ("user@example.com", "other", True, True),
    ("user@example.com", "user@example.com", True, True),
    (None, "user@example.com", False, False),
    ("user@example.com", None, True, True),
    (None, None, True, False),
])
async def test_invalidations_apply_row_policy_and_subscription_filter(
    monkeypatch, old_owner, new_owner, filter_matches, should_send
):
    table_id = str(uuid.uuid4())
    websocket = _websocket_with_subscriptions(table_id)
    websocket.state.table_subscriptions[table_id]["filter"] = Expr.model_validate({"eq": [{"row": "selected"}, True]})
    policies = TablePolicies.model_validate({"policies": [{
        "name": "owner-read", "actions": ["read"], "when": {"eq": [{"row": "owner"}, {"user": "email"}]},
    }]})
    monkeypatch.setattr(ws_mod, "_load_policies_for_table", AsyncMock(return_value=policies))

    def row(owner):
        return None if owner is None else {"id": "r1", "owner": owner, "selected": filter_matches}

    await ws_mod._handle_table_message(websocket, _user(), f"table:{table_id}", {
        "type": "table_invalidated", "mutations": [{"old_row": row(old_owner), "new_row": row(new_owner)}],
    })
    if should_send:
        websocket.send_json.assert_awaited_once_with({"type": "table_invalidated", "table_id": table_id})
    else:
        websocket.send_json.assert_not_awaited()


async def test_table_invalidated_without_active_subscription_does_not_send(monkeypatch):
    table_id = str(uuid.uuid4())
    websocket = _websocket_with_subscriptions()
    load_policies = AsyncMock()
    monkeypatch.setattr(ws_mod, "_load_policies_for_table", load_policies)

    await ws_mod._handle_table_message(
        websocket,
        _user(),
        f"table:{table_id}",
        {"type": "table_invalidated", "table_id": table_id},
    )

    websocket.send_json.assert_not_awaited()
    load_policies.assert_not_awaited()
