"""Persist criteria without turning absent operator values into explicit null."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models.contracts.events import EventCriteria, EventSubscriptionCreate
from src.routers import events


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", [
    {"kind": "condition", "field": "event.body.id", "operator": "exists"},
    {"kind": "condition", "field": "event.body.id", "operator": "not_exists"},
    {"kind": "condition", "field": "event.body.id", "operator": "equals", "value": None},
])
async def test_rest_subscription_criteria_remain_valid_after_persistence(monkeypatch, condition):
    request = EventSubscriptionCreate(
        workflow_id=uuid4(), criteria={"version": 1, "root": condition},
    )
    db = MagicMock()
    added = []
    db.add.side_effect = lambda entity: added.append(entity)
    db.flush = AsyncMock()
    result = MagicMock()
    result.unique.return_value.scalar_one.side_effect = lambda: added[0]
    db.execute = AsyncMock(return_value=result)
    repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(organization_id=None)))
    monkeypatch.setattr(events, "EventSourceRepository", lambda _db: repo)
    monkeypatch.setattr(events, "_validate_subscription_target", AsyncMock())
    monkeypatch.setattr(events, "emit_audit", AsyncMock())
    monkeypatch.setattr(events, "_build_event_subscription_response", AsyncMock(side_effect=lambda row, _db: row))
    response = await events.create_subscription(
        uuid4(), request, SimpleNamespace(user=SimpleNamespace(email="test@example.com")),
        SimpleNamespace(), db,
    )
    persisted = response.criteria
    assert persisted["root"] == condition
    assert EventCriteria.model_validate(persisted).root.operator.value == condition["operator"]
