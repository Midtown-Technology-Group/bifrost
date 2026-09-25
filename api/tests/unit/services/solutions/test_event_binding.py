"""Install-owned webhook bindings survive a Solution redeploy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.sql.dml import Delete, Insert

from src.services.solutions.deploy import SolutionDeployer, SolutionDeployConflict


@pytest.mark.asyncio
@pytest.mark.parametrize("integration_name", ["Microsoft Teams Bot", "NinjaOne"])
async def test_redeploy_keeps_only_valid_teams_webhook_binding(integration_name):
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
    db.scalar.return_value = integration_name
    deployer = SolutionDeployer(db)
    deployer._guard_owner = AsyncMock()
    deploy = deployer._upsert_events(
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
    if integration_name != "Microsoft Teams Bot":
        with pytest.raises(SolutionDeployConflict, match="Microsoft Teams Bot integration"):
            await deploy
        assert not any(isinstance(statement, Delete) for statement in statements)
        return
    await deploy

    webhook_insert = next(
        statement for statement in statements
        if getattr(statement, "table", None) is not None
        and statement.table.name == "webhook_sources"
        and isinstance(statement, Insert)
    )
    assert webhook_insert.compile().params["integration_id"] == integration_id
