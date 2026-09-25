"""Install-owned webhook bindings survive a Solution redeploy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.solutions.deploy import SolutionDeployer


@pytest.mark.asyncio
async def test_redeploy_keeps_existing_webhook_integration_binding():
    source_id = uuid4()
    integration_id = uuid4()
    statements = []
    db = AsyncMock()

    async def execute(statement):
        statements.append(statement)
        result = MagicMock()
        result.scalar_one_or_none.return_value = integration_id
        return result

    db.execute.side_effect = execute
    deployer = SolutionDeployer(db)
    deployer._guard_owner = AsyncMock()
    await deployer._upsert_events(
        SimpleNamespace(id=uuid4(), organization_id=None),
        [{
            "id": str(source_id),
            "name": "Teams webhook",
            "source_type": "webhook",
            "adapter_name": "microsoft_bot_framework",
            "webhook_config": {"app_id": str(uuid4())},
            "subscriptions": [],
        }],
    )

    webhook_insert = next(
        statement for statement in statements
        if getattr(statement, "table", None) is not None
        and statement.table.name == "webhook_sources"
        and statement.is_insert
    )
    assert webhook_insert.compile().params["integration_id"] == integration_id
