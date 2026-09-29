"""Legacy full-replace deploy cannot drift an immutable Solution runtime."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployConflict,
    SolutionDeployer,
)


@pytest.mark.asyncio
async def test_legacy_solution_deploy_rejects_active_immutable_pointer():
    db = AsyncMock()
    solution = SimpleNamespace(id=uuid4(), active_deployment_id=uuid4())

    with pytest.raises(SolutionDeployConflict, match="active immutable deployment"):
        await SolutionDeployer(db).deploy(SolutionBundle(solution=solution))

    db.execute.assert_not_awaited()
    db.add.assert_not_called()
