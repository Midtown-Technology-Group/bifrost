"""Durability and fencing contracts for worker controls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.worker_control_commands import (
    claim_worker_control_command,
    create_worker_control_command,
    finish_worker_control_command,
)


@pytest.mark.asyncio
async def test_create_worker_command_records_requester_and_bounded_reason() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    requester = uuid4()
    command = await create_worker_control_command(
        db,
        worker_id="worker-a",
        action="recycle_all",
        requested_by_user_id=requester,
        reason="x" * 3000,
    )
    assert command.requested_by_user_id == requester
    assert len(command.reason) == 2000
    assert command.status is None or command.status == "pending"
    db.add.assert_called_once_with(command)


@pytest.mark.asyncio
async def test_package_command_keeps_operation_and_incarnation_fence() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    operation_id = uuid4()
    incarnation_id = uuid4()
    command = await create_worker_control_command(
        db,
        worker_id="worker-a",
        action="package_install",
        requested_by_user_id=uuid4(),
        reason="package fanout",
        operation_id=operation_id,
        target_incarnation_id=incarnation_id,
        payload={"run_id": str(operation_id), "action": "install"},
    )
    assert command.operation_id == operation_id
    assert command.target_incarnation_id == incarnation_id
    assert command.payload["action"] == "install"


@pytest.mark.asyncio
async def test_claim_and_finish_are_status_fenced() -> None:
    command = SimpleNamespace(
        status="pending",
        claimed_at=None,
        completed_at=None,
        failure_message=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = command
    db = AsyncMock()
    db.execute.return_value = result

    claimed = await claim_worker_control_command(
        db,
        command_id=uuid4(),
        worker_id="worker-a",
    )
    assert claimed is command
    assert command.status == "running"
    assert command.claimed_at is not None

    finished = await finish_worker_control_command(
        db,
        command_id=uuid4(),
        worker_id="worker-a",
        succeeded=False,
        failure_message="boom",
    )
    assert finished is command
    assert command.status == "failed"
    assert command.failure_message == "boom"
    assert command.completed_at is not None


@pytest.mark.asyncio
async def test_unknown_worker_command_is_rejected_before_persistence() -> None:
    db = AsyncMock()
    with pytest.raises(ValueError, match="unsupported"):
        await create_worker_control_command(
            db,
            worker_id="worker-a",
            action="shell",
            requested_by_user_id=uuid4(),
            reason="nope",
        )
    db.add.assert_not_called()
