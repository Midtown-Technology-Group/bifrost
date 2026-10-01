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


async def test_provider_outage_does_not_roll_back_demand_or_prevent_zero(async_session_factory, caplog):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    import httpx
    from sqlalchemy import select

    from src.config import Settings
    from src.services.external_worker_scaling import controller_loop

    configuration = Settings(
        external_worker_app_resource_id="/subscriptions/" + str(uuid4()) +
            "/resourceGroups/test/providers/Microsoft.App/containerApps/test-worker",
        external_worker_queue_account="scalingtestaccount",
        external_worker_queue_name="bifrost-worker-demand-test",
        external_worker_tenant_id=str(uuid4()), external_worker_client_id=str(uuid4()),
        external_worker_principal_id=str(uuid4()), external_worker_enrollment_audience="api://" + str(uuid4()),
        external_worker_defined_network_id="network-TEST", external_worker_defined_role_id="role-TEST",
        work_delivery_backend="postgres")
    azure = SimpleNamespace(workflow_queue="workflow-executions-test-canary", replicas=AsyncMock(return_value=set()),
                            publish=AsyncMock(), close=AsyncMock())
    stop = asyncio.Event()
    clock = SimpleNamespace(now=1000.0)
    attempted_cleanup = []
    cleanup_key = "test-cleanup-" + uuid4().hex

    @asynccontextmanager
    async def context():
        async with async_session_factory() as db:
            yield db

    async def cleanup_failure(db, _azure, _live):
        # Demand is already durable, even when this separate transaction fails.
        async with async_session_factory() as reader:
            demand = await reader.scalar(select(SystemConfig).where(
                SystemConfig.category == CATEGORY, SystemConfig.key == "demand"))
            assert demand.value_json["observed_at"] == clock.now
            attempted_cleanup.append(demand.value_json["desired"])
        value = await row(db, cleanup_key)
        value.value_json = {"must_rollback": True}
        raise httpx.ReadTimeout("provider credential must not appear in logs")

    async def tick():
        clock.now += 10
        if clock.now >= 1150:
            stop.set()

    try:
        with patch("src.services.external_worker_scaling.get_settings", return_value=configuration), patch(
            "src.services.external_worker_scaling.Azure", return_value=azure
        ), patch("src.services.external_worker_scaling.get_db_context", side_effect=context), patch(
            "src.services.external_worker_scaling.demand_counts", new=AsyncMock(return_value=(0, 0))
        ), patch("src.services.external_worker_scaling.reconcile_hosts", side_effect=cleanup_failure), patch(
            "src.services.external_worker_scaling.datetime"
        ) as mock_datetime, patch.object(stop, "wait", side_effect=tick):
            mock_datetime.now.side_effect = lambda _: datetime.fromtimestamp(clock.now, UTC)
            await controller_loop(stop)
        assert attempted_cleanup[:12] == [2] * 12
        assert attempted_cleanup[12:] == [0] * 3
        assert azure.publish.await_args_list[-1].args == (0,)
        azure.close.assert_awaited_once()
        async with async_session_factory() as db:
            demand = await row(db, "demand")
            assert demand.value_json["idle_since"] == 1000
            assert demand.value_json["observed_at"] == 1140
            assert demand.value_json["desired"] == 0
            assert await db.scalar(select(SystemConfig.id).where(
                SystemConfig.category == CATEGORY, SystemConfig.key == cleanup_key)) is None
        assert "provider credential must not appear in logs" not in caplog.text
        assert "External host reconciliation failed: ReadTimeout" in caplog.text
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(SystemConfig).where(SystemConfig.category == CATEGORY,
                                                       SystemConfig.key.in_(("demand", cleanup_key))))
            await db.commit()
