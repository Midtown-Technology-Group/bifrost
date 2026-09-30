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
    db.scalar.return_value = uuid4()
    solution = SimpleNamespace(id=uuid4(), active_deployment_id=None)

    with pytest.raises(SolutionDeployConflict, match="active immutable deployment"):
        await SolutionDeployer(db).deploy(SolutionBundle(solution=solution))

    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    statement = db.scalar.call_args.args[0]
    assert statement._for_update_arg is not None
