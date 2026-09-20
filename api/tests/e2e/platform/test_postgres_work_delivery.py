"""Real PostgreSQL transaction/ownership tests; no external provider calls."""

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from src.jobs.rabbitmq import BaseConsumer, RetryableConsumerError
from src.models.orm.work_deliveries import WorkDelivery
from src.services.work_delivery_store import (
    DeliveryOwnershipLost,
    claim_deliveries,
    current_delivery,
    enqueue_delivery,
    interrupt_delivery,
    interrupt_expired_deliveries,
    recover_interrupted_delivery,
    renew_delivery,
    require_delivery_ownership,
    retire_workflow_delivery_for_retry,
    settle_delivery,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

CRASH_WORKER = """
import asyncio, sys
from src.core.database import get_db_context
from src.services.work_delivery_store import claim_deliveries, current_delivery, require_delivery_ownership
async def main():
    async with get_db_context() as db:
        (lease,) = await claim_deliveries(db, queue_name=sys.argv[1], owner='kill-test')
        if sys.argv[2] == 'started':
            current_delivery.set(lease)
            await require_delivery_ownership(db)
        await db.commit()
    print('CLAIM-COMMITTED', flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""


class RecordingConsumer(BaseConsumer):
    def __init__(self, queue_name, handler):
        super().__init__(queue_name=queue_name)
        self.handler = handler

    async def process_message(self, body):
        await self.handler(body)


@pytest_asyncio.fixture
async def postgres_transport(monkeypatch, async_session_factory):
    from src.core import database
    from src.jobs import postgres_delivery, rabbitmq

    @asynccontextmanager
    async def session():
        async with async_session_factory() as db:
            yield db

    monkeypatch.setattr(database, "get_db_context", session)
    monkeypatch.setattr(postgres_delivery, "get_db_context", session)
    monkeypatch.setattr(postgres_delivery, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(postgres_delivery, "HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(
        rabbitmq,
        "get_settings",
        lambda: SimpleNamespace(work_delivery_backend="postgres"),
    )
    monkeypatch.setattr(
        rabbitmq.rabbitmq,
        "init_pools",
        AsyncMock(side_effect=AssertionError("Rabbit must remain unused")),
    )
    yield rabbitmq


async def wait_delivery_status(factory, queue, status):
    async with asyncio.timeout(10):
        while True:
            async with factory() as db:
                row = (
                    await db.execute(
                        select(WorkDelivery).where(
                            WorkDelivery.queue_name == queue,
                            WorkDelivery.status == status,
                        )
                    )
                ).scalar_one_or_none()
                if row is not None:
                    return row
            await asyncio.sleep(0.02)


@pytest_asyncio.fixture
async def delivery_queue(async_session_factory):
    queue = f"delivery-test-{uuid4()}"
    yield queue
    async with async_session_factory() as db:
        await db.execute(delete(WorkDelivery).where(WorkDelivery.queue_name == queue))
        await db.commit()


async def enqueue(db, queue, message_id="one"):
    return await enqueue_delivery(
        db,
        queue_name=queue,
        message_id=message_id,
        envelope={"body": {"private_input": "never plaintext"}, "headers": {}},
    )


async def test_acceptance_is_in_callers_transaction(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as publisher:
        discarded = await enqueue(publisher, delivery_queue)
        async with async_session_factory() as observer:
            assert await observer.get(WorkDelivery, discarded) is None
        await publisher.rollback()
    async with async_session_factory() as publisher:
        accepted = await enqueue(publisher, delivery_queue)
        await publisher.commit()
    async with async_session_factory() as observer:
        assert await observer.get(WorkDelivery, discarded) is None
        stored = await observer.get(WorkDelivery, accepted)
        assert stored is not None and stored.status == "queued"
        assert "never plaintext" not in stored.encrypted_envelope


async def test_active_admission_deduplicates(async_session_factory, delivery_queue):
    async with async_session_factory() as db:
        first = await enqueue(db, delivery_queue)
        await db.commit()
    async with async_session_factory() as db:
        assert await enqueue(db, delivery_queue) == first
        await db.commit()
        leases = await claim_deliveries(db, queue_name=delivery_queue, owner="worker")
        assert len(leases) == 1
        assert leases[0].envelope["body"]["private_input"] == "never plaintext"
        await db.commit()
    async with async_session_factory() as db:
        assert await enqueue(db, delivery_queue) == first
        await db.commit()


async def test_retention_removes_only_old_completed_transport_receipts(
    async_session_factory, delivery_queue
):
    from src.jobs.schedulers.execution_cleanup import cleanup_completed_deliveries

    async with async_session_factory() as db:
        ids = {}
        for status, days in [("completed", 8), ("completed", 1), ("poison", 8), ("interrupted", 8)]:
            identity = f"{status}-{days}"
            ids[identity] = await enqueue(db, delivery_queue, identity)
            await db.execute(update(WorkDelivery).where(
                WorkDelivery.id == ids[identity]
            ).values(status=status, settled_at=func.clock_timestamp() - timedelta(days=days)))
        await db.commit()
        await cleanup_completed_deliveries(db)
        await db.commit()
        remaining = set((await db.execute(select(WorkDelivery.id).where(
            WorkDelivery.queue_name == delivery_queue
        ))).scalars())
        assert remaining == {value for key, value in ids.items() if key != "completed-8"}


@pytest.mark.parametrize("started", [False, True])
async def test_sigkill_preserves_accepted_work_and_recovers_only_unstarted_delivery(
    async_session_factory, delivery_queue, started
):
    async with async_session_factory() as db:
        delivery_id = await enqueue(db, delivery_queue)
        await db.commit()
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CRASH_WORKER,
        delivery_queue,
        "started" if started else "unstarted",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        assert child.stdout is not None
        async with asyncio.timeout(20):
            while True:
                line = await child.stdout.readline()
                assert line, "Crash-test worker exited before committing its claim"
                if line.strip() == b"CLAIM-COMMITTED":
                    break
    finally:
        if child.returncode is None:
            child.kill()
        await asyncio.wait_for(child.wait(), 5)
    async with async_session_factory() as db:
        # Advance just the lease deadline; do not wait ninety seconds per test.
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id == delivery_id)
            .values(lease_expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )
        await db.commit()
        assert delivery_id in await interrupt_expired_deliveries(
            db, queue_name=delivery_queue
        )
        await db.commit()
        assert await recover_interrupted_delivery(db, delivery_id) is (not started)
        await db.commit()
        leases = await claim_deliveries(
            db, queue_name=delivery_queue, owner="replacement"
        )
        assert len(leases) == (0 if started else 1)
        await db.commit()


async def test_real_worker_executes_postgres_canary_after_pending_cache_loss(
    async_session_factory, platform_admin, tmp_path
):
    """Exercise the deployed worker entry point with an unreachable AMQP URL."""
    from src.core.redis_client import get_redis_client
    from src.models.orm.execution_attempts import ExecutionAttempt
    from src.models.orm.executions import Execution

    queue = f"postgres-{uuid4()}-canary"
    env = {
        **os.environ,
        "BIFROST_WORK_DELIVERY_BACKEND": "postgres",
        "BIFROST_WORKER_CONSUMERS": "workflow",
        "BIFROST_WORKFLOW_QUEUE_NAME": queue,
        "BIFROST_RABBITMQ_URL": "amqp://unused:unused@127.0.0.1:9/",
        "BIFROST_MAX_WORKERS": "1",
        "BIFROST_MAX_CONCURRENCY": "1",
        "BIFROST_DRAIN_DEADLINE_SECONDS": "5",
        "HOSTNAME": f"postgres-test-{uuid4()}",
    }
    processes = []
    execution_id = None
    worker_log = tmp_path / "postgres-worker.log"
    canary_log = tmp_path / "postgres-canary.log"
    try:
        with canary_log.open("wb") as output:
            canary = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "src.jobs.workflow_canary",
                env=env,
                stdout=output,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        processes.append(canary)
        # Acceptance must precede starting a consumer. Drop only this execution's
        # cache and prove the encrypted delivery carries its inline code/context.
        row = await wait_delivery_status(async_session_factory, queue, "queued")
        execution_id = UUID(row.message_id)
        client = get_redis_client()
        await client.delete_pending_execution(str(execution_id))
        assert await client.get_pending_execution(str(execution_id)) is None
        with worker_log.open("wb") as output:
            worker = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "src.worker.main",
                env=env,
                stdout=output,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        processes.append(worker)
        await asyncio.wait_for(canary.wait(), timeout=110)
        assert canary.returncode == 0, (
            canary_log.read_text()[-8000:] + worker_log.read_text()[-8000:]
        )
        assert "isolated workflow canary passed" in canary_log.read_text()
        assert worker.returncode is None
        await wait_delivery_status(async_session_factory, queue, "completed")
        async with async_session_factory() as db:
            execution = await db.get(Execution, execution_id)
            assert execution is not None and execution.status == "Success"
            assert execution.result == {"canary": "ok"}
    finally:
        for process in reversed(processes):
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 20)
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            # Reap any template/worker children left by an early parent failure.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        async with async_session_factory() as db:
            await db.execute(
                delete(WorkDelivery).where(WorkDelivery.queue_name == queue)
            )
            if execution_id is not None:
                await db.execute(
                    delete(ExecutionAttempt).where(
                        ExecutionAttempt.logical_job_id == execution_id
                    )
                )
                await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.commit()


async def test_concurrent_claims_skip_locked_and_are_bounded(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        for number in range(3):
            await enqueue(db, delivery_queue, str(number))
        await db.commit()
    async with async_session_factory() as first, async_session_factory() as second:
        a = await claim_deliveries(
            first, queue_name=delivery_queue, owner="first", limit=1
        )
        # First claim deliberately holds its transaction open. Second must skip
        # its row rather than block or claim the same work.
        b = await claim_deliveries(
            second, queue_name=delivery_queue, owner="second", limit=2
        )
        assert len(a) == 1 and len(b) == 2
        assert {item.id for item in a}.isdisjoint(item.id for item in b)
        await second.commit()
        await first.commit()


async def test_retry_is_atomic_and_old_owner_cannot_settle(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        await enqueue(db, delivery_queue)
        await db.commit()
        (old,) = await claim_deliveries(db, queue_name=delivery_queue, owner="first")
        await db.commit()
        envelope = {**old.envelope, "headers": {"retry_count": 1}}
        assert await settle_delivery(
            db, old, status="queued", envelope=envelope, delay_seconds=30
        )
        await db.commit()
        assert (
            await claim_deliveries(db, queue_name=delivery_queue, owner="second") == []
        )
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id == old.id)
            .values(
                available_at=func.clock_timestamp() - timedelta(seconds=1),
            )
        )
        await db.commit()
        (new,) = await claim_deliveries(db, queue_name=delivery_queue, owner="second")
        await db.commit()
        assert new.id == old.id and new.token != old.token and new.claim_count == 2
        assert new.envelope["headers"]["retry_count"] == 1
        assert not await renew_delivery(db, old)
        assert not await settle_delivery(db, old, status="completed")
        assert await settle_delivery(db, new, status="completed")
        await db.commit()


async def test_expired_claim_requires_recovery_not_blind_retry(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        await enqueue(db, delivery_queue)
        await db.commit()
        (lease,) = await claim_deliveries(db, queue_name=delivery_queue, owner="lost")
        await db.commit()
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id == lease.id)
            .values(
                lease_expires_at=func.clock_timestamp() - timedelta(seconds=1),
            )
        )
        await db.commit()
        assert not await renew_delivery(db, lease)
        assert not await settle_delivery(db, lease, status="completed")
        assert lease.id in await interrupt_expired_deliveries(db)
        await db.commit()
        assert (
            await claim_deliveries(db, queue_name=delivery_queue, owner="replacement")
            == []
        )
        status = (
            await db.execute(
                select(WorkDelivery.status).where(
                    WorkDelivery.id == lease.id,
                )
            )
        ).scalar_one()
        assert status == "interrupted"
        assert await enqueue(db, delivery_queue) == lease.id
        await db.commit()


async def test_poison_retains_work_and_rejects_repeat_settlement(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        await enqueue(db, delivery_queue)
        await db.commit()
        (lease,) = await claim_deliveries(db, queue_name=delivery_queue, owner="worker")
        await db.commit()
        assert await settle_delivery(db, lease, status="poison")
        await db.commit()
        assert not await settle_delivery(db, lease, status="completed")
    async with async_session_factory() as db:
        stored = await db.get(WorkDelivery, lease.id)
        assert stored is not None and stored.status == "poison"
        assert stored.settled_at is not None and stored.lease_token is None


async def test_package_targets_and_outcomes_survive_redis_and_worker_loss(
    async_session_factory, postgres_transport, monkeypatch
):
    from src import config
    from src.models.orm.worker_control_commands import WorkerControlCommand
    from src.services import worker_control_commands as controls
    from src.services.execution import install_progress

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(work_delivery_backend="postgres"),
    )
    monkeypatch.setattr(
        install_progress,
        "_raw_redis",
        AsyncMock(
            side_effect=AssertionError(
                "Durable package progress cannot depend on Redis"
            )
        ),
    )
    operation = uuid4()
    incarnation = uuid4()
    async with async_session_factory() as db:
        for worker in ["survivor", "lost-pod"]:
            await controls.create_worker_control_command(
                db,
                worker_id=worker,
                action="package_install",
                requested_by_user_id=uuid4(),
                reason="test",
                operation_id=operation,
                target_incarnation_id=incarnation,
                payload={"package": "demo", "run_id": str(operation)},
            )
        await db.commit()
        rows = list(
            (
                await db.execute(
                    select(WorkerControlCommand).where(
                        WorkerControlCommand.operation_id == operation
                    )
                )
            ).scalars()
        )
        survivor = next(row for row in rows if row.worker_id == "survivor")
        lost = next(row for row in rows if row.worker_id == "lost-pod")
        try:
            claim = await controls.claim_worker_control_command(
                db,
                command_id=survivor.id,
                worker_id="survivor",
                worker_incarnation_id=incarnation,
            )
            assert claim is not None
            token = claim.claim_token
            await db.commit()
            assert (
                await controls.finish_worker_control_command(
                    db,
                    command_id=survivor.id,
                    worker_id="survivor",
                    worker_incarnation_id=incarnation,
                    claim_token=uuid4(),
                    succeeded=True,
                )
                is None
            )
            assert (
                await controls.finish_worker_control_command(
                    db,
                    command_id=survivor.id,
                    worker_id="survivor",
                    worker_incarnation_id=incarnation,
                    claim_token=token,
                    succeeded=True,
                )
                is not None
            )
            await db.commit()
            progress = await install_progress.get_run_progress(str(operation))
            assert progress["status"] == "running" and progress["total"] == 2
            assert progress["recycled"] == 1
            await db.execute(
                update(WorkerControlCommand)
                .where(WorkerControlCommand.id == lost.id)
                .values(requested_at=func.clock_timestamp() - timedelta(minutes=11))
            )
            await db.commit()
            assert (
                await controls.claim_worker_control_command(
                    db,
                    command_id=lost.id,
                    worker_id="lost-pod",
                    worker_incarnation_id=incarnation,
                )
                is None
            )
            assert operation in await controls.expire_package_commands(db)
            await db.commit()
            progress = await install_progress.get_run_progress(str(operation))
            assert progress["status"] == "failed" and progress["total"] == 2
            assert progress["recycled"] == 1 and progress["failed"] == 1
        finally:
            await db.execute(
                delete(WorkerControlCommand).where(
                    WorkerControlCommand.operation_id == operation
                )
            )
            await db.commit()


async def test_expired_package_command_does_not_block_new_target_command(
    async_session_factory,
):
    from src.models.orm.worker_control_commands import WorkerControlCommand
    from src.services import worker_control_commands as controls

    worker_id = f"package-worker-{uuid4()}"
    incarnation = uuid4()
    old_operation = uuid4()
    new_operation = uuid4()
    async with async_session_factory() as db:
        old = await controls.create_worker_control_command(
            db,
            worker_id=worker_id,
            action="package_install",
            requested_by_user_id=uuid4(),
            reason="expired target",
            operation_id=old_operation,
            target_incarnation_id=incarnation,
            payload={"run_id": str(old_operation)},
        )
        new = await controls.create_worker_control_command(
            db,
            worker_id=worker_id,
            action="package_install",
            requested_by_user_id=uuid4(),
            reason="new target",
            operation_id=new_operation,
            target_incarnation_id=incarnation,
            payload={"run_id": str(new_operation)},
        )
        await db.execute(
            update(WorkerControlCommand)
            .where(WorkerControlCommand.id == old.id)
            .values(requested_at=func.clock_timestamp() - timedelta(minutes=11))
        )
        await db.commit()
        try:
            pending = await controls.get_pending_worker_control_command(
                db,
                worker_id=worker_id,
                worker_incarnation_id=incarnation,
            )
            assert pending is not None
            assert pending.id == new.id
            claim = await controls.claim_worker_control_command(
                db,
                command_id=pending.id,
                worker_id=worker_id,
                worker_incarnation_id=incarnation,
            )
            assert claim is not None and claim.id == new.id
            await db.commit()
        finally:
            await db.execute(
                delete(WorkerControlCommand).where(
                    WorkerControlCommand.operation_id.in_((old_operation, new_operation))
                )
            )
            await db.commit()


async def test_recovery_resumes_unstarted_claim_and_fences_old_handler(
    async_session_factory, delivery_queue
):
    async with async_session_factory() as db:
        await enqueue(db, delivery_queue)
        await db.commit()
        (old,) = await claim_deliveries(db, queue_name=delivery_queue, owner="lost")
        await db.commit()
        assert await interrupt_delivery(db, old)
        await db.commit()
        assert await recover_interrupted_delivery(db, old.id)
        await db.commit()
        (new,) = await claim_deliveries(
            db, queue_name=delivery_queue, owner="replacement"
        )
        await db.commit()
        scope = current_delivery.set(old)
        try:
            with pytest.raises(DeliveryOwnershipLost):
                await require_delivery_ownership(db)
            await db.rollback()
        finally:
            current_delivery.reset(scope)
        scope = current_delivery.set(new)
        try:
            await require_delivery_ownership(db)
            await db.commit()
        finally:
            current_delivery.reset(scope)
        assert await interrupt_delivery(db, new)
        await db.commit()
        # A handler crossed the start fence. Unknown effects cannot be replayed.
        assert not await recover_interrupted_delivery(db, new.id)
        await db.commit()
        assert (
            await claim_deliveries(db, queue_name=delivery_queue, owner="third") == []
        )


async def test_domain_retry_replaces_dispatch_atomically_and_rejects_late_ack(
    async_session_factory,
):
    execution_id = str(uuid4())
    async with async_session_factory() as db:
        old_id = await enqueue(db, "workflow-executions", execution_id)
        await db.commit()
        (old,) = await claim_deliveries(
            db, queue_name="workflow-executions", owner="old"
        )
        await db.commit()
        try:
            await retire_workflow_delivery_for_retry(db, execution_id)
            rolled_back = await enqueue(db, "workflow-executions", execution_id)
            await db.rollback()
            assert await db.get(WorkDelivery, rolled_back) is None
            assert await renew_delivery(db, old)
            await db.commit()
            await retire_workflow_delivery_for_retry(db, execution_id)
            new_id = await enqueue(db, "workflow-executions", execution_id)
            await db.commit()
            assert new_id != old_id
            assert not await settle_delivery(db, old, status="completed")
            await db.commit()
            (new,) = await claim_deliveries(
                db, queue_name="workflow-executions", owner="new"
            )
            assert new.id == new_id
            await db.commit()
        finally:
            await db.execute(
                delete(WorkDelivery).where(
                    WorkDelivery.queue_name == "workflow-executions",
                    WorkDelivery.message_id == execution_id,
                )
            )
            await db.commit()


@pytest.mark.parametrize(
    "domain_status,active_attempt,expected",
    [
        ("Pending", False, "queued"),
        ("Pending", True, "interrupted"),
        ("Running", False, "interrupted"),
        ("Success", False, "completed"),
    ],
)
async def test_workflow_recovery_obeys_durable_domain_outcome(
    async_session_factory, domain_status, active_attempt, expected
):
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution

    execution_id = uuid4()
    async with async_session_factory() as db:
        db.add(
            Execution(
                id=execution_id,
                workflow_name="delivery-recovery-test",
                executed_by_name="test",
                status=ExecutionStatus(domain_status),
            )
        )
        await db.flush()
        await enqueue(db, "workflow-executions", str(execution_id))
        if active_attempt:
            from src.services.execution.attempts import create_claimed_attempt

            execution = await db.get(Execution, execution_id)
            await create_claimed_attempt(
                db, execution, worker_id="still-running", worker_incarnation_id=uuid4()
            )
        await db.commit()
        leases = await claim_deliveries(
            db, queue_name="workflow-executions", owner="test"
        )
        (lease,) = [item for item in leases if item.message_id == str(execution_id)]
        await db.commit()
        scope = current_delivery.set(lease)
        try:
            await require_delivery_ownership(db)
            await db.commit()
        finally:
            current_delivery.reset(scope)
        assert await interrupt_delivery(db, lease)
        await db.commit()
        try:
            await recover_interrupted_delivery(db, lease.id)
            await db.commit()
            status = (
                await db.execute(
                    select(WorkDelivery.status).where(WorkDelivery.id == lease.id)
                )
            ).scalar_one()
            assert status == expected
        finally:
            await db.execute(delete(WorkDelivery).where(WorkDelivery.id == lease.id))
            await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.commit()


async def test_consumer_completes_through_shared_policy_without_rabbit(
    async_session_factory,
    delivery_queue,
    postgres_transport,
):
    handler = AsyncMock()
    consumer = RecordingConsumer(delivery_queue, handler)
    await postgres_transport.publish_message(delivery_queue, {"id": "one", "value": 42})
    try:
        await consumer.start()
        row = await wait_delivery_status(
            async_session_factory, delivery_queue, "completed"
        )
        assert row.claim_count == 1
        handler.assert_awaited_once_with({"id": "one", "value": 42})
        assert consumer._channel is None
    finally:
        await consumer.drain(deadline=1)


async def test_operator_reconcile_preview_rolls_back_and_apply_audits_atomically(
    async_session_factory, postgres_transport
):
    from src.jobs.dlq_cli import postgres_reconcile
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution
    from src.models.orm.poison_message_dispositions import PoisonMessageDisposition

    execution_id = uuid4()
    idempotency_key = f"reconcile-{uuid4()}"
    async with async_session_factory() as db:
        db.add(Execution(
            id=execution_id,
            workflow_name="operator-recovery-test",
            executed_by_name="test",
            status=ExecutionStatus.SUCCESS,
        ))
        delivery_id = await enqueue_delivery(
            db,
            queue_name="workflow-executions",
            message_id=str(execution_id),
            envelope={
                "body": {"private_input": "never plaintext"},
                "headers": {"x-idempotency-key": idempotency_key},
            },
        )
        await db.execute(update(WorkDelivery).where(
            WorkDelivery.id == delivery_id
        ).values(status="interrupted", started_at=func.clock_timestamp()))
        await db.commit()
    try:
        arguments = dict(
            delivery_id=str(delivery_id), actor="test-operator", reason="prove recovery"
        )
        preview = await postgres_reconcile(
            "workflow-executions", dry_run=True, **arguments
        )
        assert preview[0]["after"]["recovery"] == "would_apply"
        async with async_session_factory() as db:
            assert (await db.get(WorkDelivery, delivery_id)).status == "interrupted"
            assert await db.scalar(select(func.count()).select_from(
                PoisonMessageDisposition
            ).where(PoisonMessageDisposition.message_id == str(execution_id))) == 0
        applied = await postgres_reconcile(
            "workflow-executions", dry_run=False, **arguments
        )
        assert applied[0]["after"]["recovery"] == "applied"
        async with async_session_factory() as db:
            assert (await db.get(WorkDelivery, delivery_id)).status == "completed"
            audit = (await db.execute(select(PoisonMessageDisposition).where(
                PoisonMessageDisposition.message_id == str(execution_id)
            ))).scalar_one()
            assert (
                audit.actor == "test-operator"
                and audit.action == "reconcile"
                and audit.idempotency_key == idempotency_key
            )
        assert "private_input" not in str(preview) + str(applied)
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(PoisonMessageDisposition).where(
                PoisonMessageDisposition.message_id == str(execution_id)
            ))
            await db.execute(delete(WorkDelivery).where(WorkDelivery.id == delivery_id))
            await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.commit()


async def test_consumer_retry_is_not_immediately_consumed(
    async_session_factory,
    delivery_queue,
    postgres_transport,
):
    called = asyncio.Event()

    async def handler(body):
        called.set()
        raise RetryableConsumerError("temporary admission pressure")

    consumer = RecordingConsumer(delivery_queue, handler)
    await postgres_transport.publish_message(delivery_queue, {"id": "one"})
    try:
        await consumer.start()
        await asyncio.wait_for(called.wait(), 10)
        row = await wait_delivery_status(
            async_session_factory, delivery_queue, "queued"
        )
        assert row.claim_count == 1
        async with async_session_factory() as db:
            delay = (
                await db.execute(
                    select(
                        WorkDelivery.available_at > func.clock_timestamp(),
                    ).where(WorkDelivery.id == row.id)
                )
            ).scalar_one()
            assert delay
    finally:
        await consumer.drain(deadline=1)


async def test_shutdown_interrupts_running_handler_without_replaying(
    async_session_factory,
    delivery_queue,
    postgres_transport,
):
    started = asyncio.Event()
    effects = []

    async def handler(body):
        effects.append("external effect may have happened")
        started.set()
        await asyncio.Event().wait()

    consumer = RecordingConsumer(delivery_queue, handler)
    await postgres_transport.publish_message(delivery_queue, {"id": "one"})
    try:
        await consumer.start()
        await asyncio.wait_for(started.wait(), 10)
    finally:
        await consumer.drain(deadline=0.05)
    row = await wait_delivery_status(
        async_session_factory, delivery_queue, "interrupted"
    )
    assert row.claim_count == 1 and len(effects) == 1
    async with async_session_factory() as db:
        assert await claim_deliveries(db, queue_name=delivery_queue, owner="new") == []
