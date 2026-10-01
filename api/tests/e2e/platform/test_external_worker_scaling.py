"""Real PostgreSQL proof of durable demand and singleton control transactions."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.models.enums import ExecutionStatus
from src.models.orm.config import SystemConfig
from src.models.orm.executions import Execution
from src.models.orm.work_deliveries import WorkDelivery
from src.services.external_worker_scaling import CATEGORY, demand_counts, lock, row

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_completed_delivery_does_not_hide_running_domain_work(async_session_factory):
    execution_id, delivery_id = uuid4(), uuid4()
    async with async_session_factory() as db:
        execution = Execution(id=execution_id, workflow_name="external-scaling-test",
                              executed_by_name="external-scaling-test", status=ExecutionStatus.RUNNING)
        delivery = WorkDelivery(id=delivery_id, queue_name="workflow-executions",
                                message_id=str(execution_id), encrypted_envelope="opaque-test-envelope",
                                status="completed", settled_at=datetime.now(UTC))
        db.add_all([execution, delivery])
        await db.flush()
        pending, active = await demand_counts(db)
        assert pending == 0
        assert active >= 1
        execution.status = ExecutionStatus.SUCCESS
        await db.flush()
        assert (await demand_counts(db))[1] == active - 1
        await db.rollback()


async def test_lock_serializes_publishers_and_state_survives_restart(async_session_factory):
    key = "test-" + uuid4().hex
    try:
        async with async_session_factory() as first, async_session_factory() as second:
            assert await lock(first)
            assert await lock(second) is False
            state = await row(first, key)
            state.value_json = {"desired": 2, "active": 1}
            await first.commit()
            assert await lock(second)
            persisted = await row(second, key)
            assert persisted.value_json == {"desired": 2, "active": 1}
            await second.rollback()
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(SystemConfig).where(SystemConfig.category == CATEGORY, SystemConfig.key == key))
            await db.commit()
