"""Domain-aware interrupted-delivery recovery against real PostgreSQL."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from src.core.security import decrypt_secret
from src.jobs.postgres_delivery import PostgresConsumerRunner
from src.models.orm.agent_run_flag_conversations import AgentRunFlagConversation
from src.models.orm.agent_runs import AgentRun
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.execution_lifecycle_events import ExecutionLifecycleEvent
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.models.orm.work_deliveries import WorkDelivery
from src.services.work_delivery_store import (
    enqueue_delivery,
    recover_interrupted_delivery,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def recovery_rows(async_session_factory):
    run_ids: list = []
    delivery_ids: list = []
    attempt_ids: list = []
    conversation_ids: list = []
    backfill_ids: list = []

    async def register(**ids):
        run_ids.extend(ids.get("run_ids", []))
        delivery_ids.extend(ids.get("delivery_ids", []))
        attempt_ids.extend(ids.get("attempt_ids", []))
        conversation_ids.extend(ids.get("conversation_ids", []))
        backfill_ids.extend(ids.get("backfill_ids", []))

    yield register
    async with async_session_factory() as db:
        if attempt_ids:
            await db.execute(
                delete(ExecutionLifecycleEvent).where(
                    ExecutionLifecycleEvent.attempt_id.in_(attempt_ids)
                )
            )
            await db.execute(
                delete(ExecutionAttempt).where(ExecutionAttempt.id.in_(attempt_ids))
            )
        if delivery_ids:
            await db.execute(
                delete(WorkDelivery).where(WorkDelivery.id.in_(delivery_ids))
            )
        if conversation_ids:
            await db.execute(
                delete(AgentRunFlagConversation).where(
                    AgentRunFlagConversation.id.in_(conversation_ids)
                )
            )
        if backfill_ids:
            await db.execute(
                delete(SummaryBackfillJob).where(
                    SummaryBackfillJob.id.in_(backfill_ids)
                )
            )
        if run_ids:
            await db.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        await db.commit()


async def _run(db, *, status="completed", summary_status="pending"):
    run = AgentRun(
        id=uuid4(),
        trigger_type="recovery-test",
        status=status,
        summary_status=summary_status,
    )
    db.add(run)
    await db.flush()
    return run


async def _delivery(
    db,
    *,
    queue: str,
    message_id: str,
    body: dict | None = None,
    started: bool = True,
    headers: dict | None = None,
):
    delivery_id = await enqueue_delivery(
        db,
        queue_name=queue,
        message_id=message_id,
        envelope={"body": body or {}, "headers": headers or {}},
    )
    await db.execute(
        update(WorkDelivery)
        .where(WorkDelivery.id == delivery_id)
        .values(
            status="interrupted",
            started_at=func.clock_timestamp() if started else None,
        )
    )
    await db.flush()
    return delivery_id


async def _status(db, delivery_id):
    return await db.scalar(
        select(WorkDelivery.status).where(WorkDelivery.id == delivery_id)
    )


async def test_agent_queued_recovery_requeues_without_replaying_tools(
    async_session_factory, recovery_rows
):
    async with async_session_factory() as db:
        run = await _run(db, status="queued")
        delivery_id = await _delivery(
            db, queue="agent-runs", message_id=str(run.id), started=False
        )
        await db.commit()
    await recovery_rows(run_ids=[run.id], delivery_ids=[delivery_id])

    async with async_session_factory() as db:
        assert await recover_interrupted_delivery(db, delivery_id)
        await db.commit()
        assert await _status(db, delivery_id) == "queued"


async def test_agent_running_recovery_marks_attempt_worker_lost_without_replay(
    async_session_factory, recovery_rows
):
    async with async_session_factory() as db:
        run = await _run(db, status="running")
        attempt = ExecutionAttempt(
            logical_job_type="agent_run",
            logical_job_id=run.id,
            attempt_number=1,
            status="running",
            policy_identifier="agent-runs",
            workload_class="interactive_agent",
            admission_policy="consumer_qos",
            mechanism="postgres",
            queue_name="agent-runs",
            message_id=str(run.id),
        )
        db.add(attempt)
        delivery_id = await _delivery(
            db, queue="agent-runs", message_id=str(run.id), started=True
        )
        await db.flush()
        await db.commit()
        attempt_id = attempt.id
    await recovery_rows(
        run_ids=[run.id], delivery_ids=[delivery_id], attempt_ids=[attempt_id]
    )

    async with async_session_factory() as db:
        assert await recover_interrupted_delivery(db, delivery_id)
        await db.commit()
    async with async_session_factory() as db:
        run = await db.get(AgentRun, run.id)
        attempt = await db.get(ExecutionAttempt, attempt_id)
        assert run.status == "failed"
        assert run.error == "agent delivery lease expired; worker_lost"
        assert run.completed_at is not None
        assert attempt.status == "worker_lost"
        assert attempt.failure_code == "worker_lost"
        assert attempt.completed_at is not None
        # The transport remains interrupted as operator evidence until a later
        # pass sees the terminal domain state and settles it.
        assert await _status(db, delivery_id) == "interrupted"

        assert await recover_interrupted_delivery(db, delivery_id)
        await db.commit()
        assert await _status(db, delivery_id) == "completed"


@pytest.mark.parametrize("summary_status", ["completed", "failed"])
async def test_started_live_summary_with_terminal_domain_is_completed(
    async_session_factory, recovery_rows, summary_status
):
    async with async_session_factory() as db:
        run = await _run(db, summary_status=summary_status)
        delivery_id = await _delivery(
            db,
            queue="agent-summarization",
            message_id=str(uuid4()),
            body={"run_id": str(run.id)},
            started=True,
        )
        run.summary_delivery_id = delivery_id
        await db.commit()
    await recovery_rows(run_ids=[run.id], delivery_ids=[delivery_id])

    async with async_session_factory() as db:
        assert await recover_interrupted_delivery(db, delivery_id)
        await db.commit()
        assert await _status(db, delivery_id) == "completed"


async def test_started_live_summary_generating_uses_bounded_retry(
    async_session_factory, recovery_rows
):
    async with async_session_factory() as db:
        run = await _run(db, summary_status="generating")
        delivery_id = await _delivery(
            db,
            queue="agent-summarization",
            message_id=str(uuid4()),
            body={"run_id": str(run.id)},
            started=True,
            headers={"x-retry-count": 0},
        )
        run.summary_delivery_id = delivery_id
        await db.commit()
    await recovery_rows(run_ids=[run.id], delivery_ids=[delivery_id])

    async with async_session_factory() as db:
        assert await recover_interrupted_delivery(db, delivery_id)
        await db.commit()
        row = await db.get(WorkDelivery, delivery_id)
        assert row.status == "queued"
        envelope = json.loads(decrypt_secret(row.encrypted_envelope))
        assert envelope["headers"]["x-retry-count"] == 1
        assert row.started_at is None


async def test_live_summary_previous_claimed_owner_blocks_recovery(
    async_session_factory, recovery_rows
):
    async with async_session_factory() as db:
        run = await _run(db, summary_status="generating")
        previous_id = await _delivery(
            db,
            queue="agent-summarization",
            message_id=str(uuid4()),
            body={"run_id": str(run.id)},
            started=True,
        )
        previous_token = uuid4()
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id == previous_id)
            .values(
                status="claimed",
                lease_owner="live-summary-worker",
                lease_token=previous_token,
                lease_expires_at=func.clock_timestamp() + timedelta(minutes=2),
            )
        )
        current_id = await _delivery(
            db,
            queue="agent-summarization",
            message_id=str(uuid4()),
            body={"run_id": str(run.id)},
            started=True,
        )
        run.summary_delivery_id = previous_id
        await db.commit()
    await recovery_rows(run_ids=[run.id], delivery_ids=[previous_id, current_id])

    async with async_session_factory() as db:
        assert not await recover_interrupted_delivery(db, current_id)
        await db.commit()
        assert await _status(db, current_id) == "interrupted"


async def test_tuning_completed_delivery_is_settled_without_second_llm(
    async_session_factory, recovery_rows
):
    async with async_session_factory() as db:
        run = await _run(db)
        delivery_id = await _delivery(
            db,
            queue="agent-tuning-chat",
            message_id=str(uuid4()),
            body={"run_id": str(run.id), "content": "why?"},
            started=True,
        )
        now = datetime.now(UTC)
        conversation = AgentRunFlagConversation(
            run_id=run.id,
            messages=[
                {"kind": "user", "delivery_id": str(delivery_id)},
                {"kind": "assistant", "delivery_id": str(delivery_id)},
            ],
            created_at=now,
            last_updated_at=now,
        )
        db.add(conversation)
        await db.flush()
        await db.commit()
        conversation_id = conversation.id
    await recovery_rows(
        run_ids=[run.id], delivery_ids=[delivery_id], conversation_ids=[conversation_id]
    )

    async with async_session_factory() as db:
        assert await recover_interrupted_delivery(db, delivery_id)
        await db.commit()
        assert await _status(db, delivery_id) == "completed"


async def test_summary_backfill_exhaustion_requeues_for_parent_accounting(
    async_session_factory, recovery_rows
):
    async with async_session_factory() as db:
        run = await _run(db, summary_status="generating")
        job = SummaryBackfillJob(
            id=uuid4(), requested_by=uuid4(), status="running", total=1
        )
        db.add(job)
        delivery_id = await _delivery(
            db,
            queue="agent-summarization-backfill",
            message_id=str(uuid4()),
            body={"run_id": str(run.id), "backfill_job_id": str(job.id)},
            started=True,
            headers={"x-retry-count": 99},
        )
        run.summary_delivery_id = delivery_id
        await db.commit()
    await recovery_rows(
        run_ids=[run.id], delivery_ids=[delivery_id], backfill_ids=[job.id]
    )

    async with async_session_factory() as db:
        assert await recover_interrupted_delivery(db, delivery_id)
        await db.commit()
        run = await db.get(AgentRun, run.id)
        row = await db.get(WorkDelivery, delivery_id)
        assert run.summary_status == "failed"
        assert run.summary_delivery_id == delivery_id
        assert row.status == "queued"
        envelope = json.loads(decrypt_secret(row.encrypted_envelope))
        assert envelope["headers"]["x-retry-count"] == 99


async def test_malformed_summary_envelope_remains_interrupted(
    async_session_factory, recovery_rows
):
    async with async_session_factory() as db:
        delivery_id = await enqueue_delivery(
            db,
            queue_name="agent-summarization",
            message_id=str(uuid4()),
            envelope={"body": {"run_id": "not-a-uuid"}, "headers": {}},
        )
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id == delivery_id)
            .values(status="interrupted", started_at=func.clock_timestamp())
        )
        await db.commit()
    await recovery_rows(delivery_ids=[delivery_id])

    async with async_session_factory() as db:
        assert not await recover_interrupted_delivery(db, delivery_id)
        await db.commit()
        assert await _status(db, delivery_id) == "interrupted"


async def test_recovery_backoff_advances_past_oldest_malformed_batch(
    async_session_factory, recovery_rows, monkeypatch
):
    from src.jobs import postgres_delivery

    @asynccontextmanager
    async def session():
        async with async_session_factory() as db:
            yield db

    monkeypatch.setattr(postgres_delivery, "get_db_context", session)
    queue = "agent-summarization"
    malformed_ids = []
    async with async_session_factory() as db:
        for index in range(100):
            delivery_id = await _delivery(
                db,
                queue=queue,
                message_id=f"malformed-{uuid4()}",
                body={"run_id": f"not-a-uuid-{index}"},
                started=False,
            )
            malformed_ids.append(delivery_id)
        await db.execute(
            update(WorkDelivery)
            .where(WorkDelivery.id.in_(malformed_ids))
            .values(available_at=func.clock_timestamp() - timedelta(minutes=1))
        )
        run = await _run(db, summary_status="completed")
        recoverable_id = await _delivery(
            db,
            queue=queue,
            message_id=f"recoverable-{uuid4()}",
            body={"run_id": str(run.id)},
            started=False,
        )
        await db.commit()
    await recovery_rows(
        run_ids=[run.id],
        delivery_ids=[*malformed_ids, recoverable_id],
    )

    class _RecoveryConsumer:
        queue_name = queue

    runner = PostgresConsumerRunner(_RecoveryConsumer())
    await runner._recover()
    async with async_session_factory() as db:
        assert await _status(db, recoverable_id) == "interrupted"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WorkDelivery)
                .where(
                    WorkDelivery.id.in_(malformed_ids),
                    WorkDelivery.status == "interrupted",
                    WorkDelivery.available_at > func.clock_timestamp(),
                )
            )
            == 100
        )

    # The first bounded pass backs off the malformed head; the next pass can
    # reach and settle the valid delivery without making malformed work runnable.
    await runner._recover()
    async with async_session_factory() as db:
        assert await _status(db, recoverable_id) == "completed"
