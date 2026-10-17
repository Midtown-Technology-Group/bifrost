"""Installed source-contract admission; SQL behavior has separate PG gates."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.solution_source_accountability import (
    UnprovenSourceConsumers,
    _common_runtime_pins,
)


def _db(*, owners: bool, collector: bool) -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.mappings.return_value.one.return_value = {
        "owners": owners,
        "collector": collector,
    }
    db.execute.return_value = result
    return db


@pytest.mark.asyncio
async def test_python_only_schema_has_no_common_pins() -> None:
    db = _db(owners=False, collector=False)
    assert await _common_runtime_pins(db) == []
    db.scalars.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owners,collector", [(True, False), (False, True)])
async def test_partial_common_schema_cannot_disappear_from_inventory(
    owners, collector
) -> None:
    db = _db(owners=owners, collector=collector)
    with pytest.raises(UnprovenSourceConsumers, match="partially installed"):
        await _common_runtime_pins(db)
    db.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_installed_collector_returns_historical_pins() -> None:
    db = _db(owners=True, collector=True)
    pins = [uuid4(), uuid4()]
    result = MagicMock()
    result.all.return_value = pins
    db.scalars.return_value = result
    assert await _common_runtime_pins(db) == pins
    db.scalars.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["execute", "scalars"])
async def test_catalog_or_collector_failure_cannot_become_empty_success(
    failure_at,
) -> None:
    db = _db(owners=True, collector=True)
    getattr(db, failure_at).side_effect = RuntimeError("source inventory unavailable")
    with pytest.raises(RuntimeError, match="source inventory unavailable"):
        await _common_runtime_pins(db)
