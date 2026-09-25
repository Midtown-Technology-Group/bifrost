"""The event/execution link must be committed before a fast worker completes."""

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.enums import EventDeliveryStatus, EventSourceType, ExecutionStatus
from src.models.orm import (
    Event,
    EventDelivery,
    EventSource,
    EventSubscription,
    Execution,
    Workflow,
)
from src.services.events import processor as events
from src.services.execution import async_executor
from src.sdk.context import EventContext


@pytest_asyncio.fixture
async def linked_event(async_session_factory):
    workflow_id, source_id, subscription_id, event_id, delivery_id = [
        uuid4() for _ in range(5)
    ]
    name = f"event_link_{uuid4().hex}"
    async with async_session_factory() as db:
        db.add(
            Workflow(id=workflow_id, name=name, function_name=name, path=f"{name}.py")
        )
        db.add(
            EventSource(
                id=source_id,
                name=name,
                source_type=EventSourceType.TOPIC,
                event_type=name,
                created_by="event-link-test",
            )
        )
        await db.flush()
        db.add(
            EventSubscription(
                id=subscription_id,
                event_source_id=source_id,
                workflow_id=workflow_id,
                created_by="event-link-test",
            )
        )
        db.add(Event(id=event_id, event_source_id=source_id, event_type=name, data={}))
        await db.flush()
        db.add(
            EventDelivery(
                id=delivery_id,
                event_id=event_id,
                event_subscription_id=subscription_id,
                workflow_id=workflow_id,
            )
        )
        await db.commit()
    try:
        yield event_id, delivery_id, workflow_id
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(EventSource).where(EventSource.id == source_id))
            await db.commit()
        async with async_session_factory() as db:
            await db.execute(
                delete(Execution).where(Execution.workflow_id == workflow_id)
            )
            await db.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await db.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dispatch_releases_outer_connection_before_nested_publication(
    async_engine, async_session_factory, linked_event, monkeypatch
):
    """A bounded pool must not deadlock on the dispatcher's own transaction."""
    event_id, delivery_id, workflow_id = linked_event
    async with async_session_factory() as setup:
        first = await setup.get(EventDelivery, delivery_id)
        event = await setup.get(Event, event_id)
        assert first is not None
        assert event is not None
        subscription = EventSubscription(
            id=uuid4(), event_source_id=event.event_source_id,
            workflow_id=workflow_id, created_by="event-link-test",
        )
        setup.add(subscription)
        await setup.flush()
        setup.add(EventDelivery(
            id=uuid4(), event_id=event_id,
            event_subscription_id=subscription.id, workflow_id=first.workflow_id,
        ))
        await setup.commit()

    engine = create_async_engine(
        async_engine.url, pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def db_context():
        async with sessions() as db:
            yield db

    publish = AsyncMock()
    monkeypatch.setattr("src.core.database.get_db_context", db_context)
    monkeypatch.setattr(async_executor, "_publish_pending", publish)
    monkeypatch.setattr(events.EventProcessor, "_broadcast_event_update", AsyncMock())
    try:
        async with sessions() as publisher:
            assert await events.EventProcessor(publisher).queue_event_deliveries(event_id) == 2
            await publisher.commit()
        assert publish.await_count == 2
        async with sessions() as observer:
            deliveries = (await observer.scalars(
                select(EventDelivery).where(EventDelivery.event_id == event_id)
            )).all()
            assert len(deliveries) == 2
            assert all(d.status == EventDeliveryStatus.QUEUED for d in deliveries)
            assert all(d.execution_id is not None for d in deliveries)
    finally:
        await engine.dispose()


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ExecutionStatus.SUCCESS, ExecutionStatus.FAILED])
@pytest.mark.parametrize("lose_publication_response", [False, True])
async def test_fast_completion_keeps_terminal_delivery(
    async_session_factory, linked_event, monkeypatch, status, lose_publication_response
):
    event_id, delivery_id, _ = linked_event
    exhausted = AsyncMock()
    monkeypatch.setattr(events, "_emit_delivery_retry_exhausted", exhausted)
    monkeypatch.setattr(events, "_broadcast_event_status_update", AsyncMock())
    monkeypatch.setattr(events.EventProcessor, "_broadcast_event_update", AsyncMock())
    published = []

    async def complete_before_publish_returns(*, execution_id, publish_kwargs):
        # Deliberately interleave a second real DB transaction at publication;
        # no sleep or scheduler timing is needed to expose the old race.
        async with async_session_factory() as completion:
            delivery = await completion.get(EventDelivery, delivery_id)
            assert str(delivery.execution_id) == execution_id
            assert delivery.status == EventDeliveryStatus.QUEUED
            execution = await completion.get(Execution, delivery.execution_id)
            assert execution is not None
            execution.status = status
            await events.update_delivery_from_execution(
                execution_id, status.value, session=completion
            )
            await completion.commit()
        published.append(execution_id)
        if lose_publication_response:
            raise ConnectionError("publication response lost after completion")
        return True

    monkeypatch.setattr(
        async_executor, "_publish_scheduled_once", complete_before_publish_returns
    )
    async with async_session_factory() as publisher:
        await events.EventProcessor(publisher).queue_event_deliveries(event_id)
        await publisher.commit()
    async with async_session_factory() as observer:
        delivery = await observer.get(EventDelivery, delivery_id)
        expected = (
            EventDeliveryStatus.SUCCESS
            if status == ExecutionStatus.SUCCESS
            else EventDeliveryStatus.FAILED
        )
        assert delivery.status == expected
        assert delivery.attempt_count == 1
        assert delivery.completed_at is not None
        assert published == [str(delivery.execution_id)]
        assert exhausted.await_count == (1 if status == ExecutionStatus.FAILED else 0)
        # Queueing the same event again cannot replay a completed delivery.
        assert (
            await events.EventProcessor(observer).queue_event_deliveries(event_id) == 0
        )
        assert len(published) == 1


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_concurrent_event_dispatchers_share_execution_and_publish_once(
    async_session_factory, linked_event, monkeypatch
):
    event_id, delivery_id, workflow_id = linked_event
    publish = AsyncMock()
    monkeypatch.setattr(async_executor, "_publish_pending", publish)
    kwargs = dict(
        workflow_id=str(workflow_id),
        parameters={},
        source="Event System",
        org_id="00000000-0000-0000-0000-000000000002",
        event=EventContext(
            id=str(event_id),
            type="test.link",
            data={},
            organization_id=None,
            received_at="",
        ),
        event_delivery_id=str(delivery_id),
    )
    first, second = await asyncio.gather(
        async_executor.enqueue_system_workflow_execution(**kwargs),
        async_executor.enqueue_system_workflow_execution(**kwargs),
    )
    assert first == second
    publish.assert_awaited_once()
    async with async_session_factory() as observer:
        delivery = await observer.get(EventDelivery, delivery_id)
        assert delivery.execution_id == UUID(first)
        assert delivery.status == EventDeliveryStatus.QUEUED
        executions = (
            (
                await observer.execute(
                    select(Execution).where(Execution.workflow_id == workflow_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(executions) == 1
        assert executions[0].status == ExecutionStatus.PENDING


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["delivery", "workflow", "event"])
async def test_event_link_mismatch_cannot_create_or_publish_execution(
    async_session_factory, linked_event, monkeypatch, mismatch
):
    event_id, delivery_id, workflow_id = linked_event
    publish = AsyncMock()
    monkeypatch.setattr(async_executor, "_publish_pending", publish)
    with pytest.raises(ValueError, match="event delivery does not belong"):
        await async_executor.enqueue_system_workflow_execution(
            workflow_id=str(uuid4() if mismatch == "workflow" else workflow_id),
            parameters={},
            source="Event System",
            event=EventContext(
                id=str(uuid4() if mismatch == "event" else event_id),
                type="test.link",
                data={},
                organization_id=None,
                received_at="",
            ),
            event_delivery_id=str(uuid4() if mismatch == "delivery" else delivery_id),
        )
    publish.assert_not_awaited()
    async with async_session_factory() as observer:
        delivery = await observer.get(EventDelivery, delivery_id)
        assert delivery.execution_id is None
        assert delivery.status == EventDeliveryStatus.PENDING
        executions = (
            (
                await observer.execute(
                    select(Execution).where(Execution.workflow_id == workflow_id)
                )
            )
            .scalars()
            .all()
        )
        assert executions == []


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_fast_agent_completion_is_not_overwritten_by_dispatcher(
    async_session_factory, linked_event, monkeypatch
):
    from src.jobs.consumers.agent_run import AgentRunConsumer
    from src.models.orm.agent_runs import AgentRun

    event_id, delivery_id, _ = linked_event
    run_id = uuid4()
    async with async_session_factory() as db:
        delivery = await db.get(EventDelivery, delivery_id)
        subscription = await db.get(EventSubscription, delivery.event_subscription_id)
        subscription.target_type = "agent"
        db.add(
            AgentRun(
                id=run_id,
                status="completed",
                trigger_type="event",
                event_delivery_id=delivery_id,
                input={},
            )
        )
        await db.commit()

    async def complete_before_enqueue_returns(self, delivery, event):
        async with async_session_factory() as completion:
            await AgentRunConsumer._update_event_delivery(
                completion, str(delivery_id), str(run_id), "completed"
            )
        delivery.agent_run_id = run_id

    monkeypatch.setattr(
        events.EventProcessor, "_queue_agent_run", complete_before_enqueue_returns
    )
    monkeypatch.setattr(events.EventProcessor, "_broadcast_event_update", AsyncMock())
    try:
        async with async_session_factory() as publisher:
            assert (
                await events.EventProcessor(publisher).queue_event_deliveries(event_id)
                == 1
            )
            await publisher.commit()
        async with async_session_factory() as observer:
            delivery = await observer.get(EventDelivery, delivery_id)
            assert delivery.status == EventDeliveryStatus.SUCCESS
            assert delivery.attempt_count == 1
            assert delivery.agent_run_id == run_id
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await db.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["rabbitmq", "postgres"])
async def test_promoter_recovers_unpublished_event_without_losing_context(
    async_session_factory, linked_event, monkeypatch, backend
):
    """A failed first publication retains one accepted execution and its event."""
    from src.jobs.schedulers import deferred_execution_promoter as promoter

    event_id, delivery_id, workflow_id = linked_event

    @asynccontextmanager
    async def db_context():
        async with async_session_factory() as db:
            yield db

    monkeypatch.setattr("src.core.database.get_db_context", db_context)
    monkeypatch.setattr(promoter, "get_db_context", db_context)
    monkeypatch.setattr(promoter, "_capacity_aware_batch_limit", AsyncMock(return_value=500))
    monkeypatch.setattr(events.EventProcessor, "_broadcast_event_update", AsyncMock())
    from types import SimpleNamespace
    monkeypatch.setattr(async_executor, "get_settings", lambda: SimpleNamespace(work_delivery_backend=backend))
    publish = AsyncMock(side_effect=ConnectionError("publisher unavailable"))
    monkeypatch.setattr(async_executor, "_publish_pending", publish)
    async with async_session_factory() as db:
        assert await events.EventProcessor(db).queue_event_deliveries(event_id) == 0
        await db.commit()
    original_dispatch = publish.await_args.kwargs.copy()
    original_dispatch.pop("delivery_db", None)
    async with async_session_factory() as db:
        delivery = await db.get(EventDelivery, delivery_id)
        assert delivery.status == EventDeliveryStatus.QUEUED
        execution_id = delivery.execution_id
        execution = await db.get(Execution, execution_id)
        assert execution.status == ExecutionStatus.SCHEDULED
        assert execution.scheduled_at is None
    publish.reset_mock(side_effect=True)
    # Concurrent recovery ticks must use the same serialized publication fence.
    await asyncio.gather(promoter.promote_due_executions(), promoter.promote_due_executions())
    calls = [c.kwargs for c in publish.await_args_list if c.kwargs["execution_id"] == str(execution_id)]
    assert len(calls) == 1
    recovered = calls[0].copy()
    recovered.pop("delivery_db", None)
    assert recovered == original_dispatch
    assert recovered["event"]["id"] == str(event_id)
    assert recovered["dispatch_metadata"]["event_delivery_id"] == str(delivery_id)
    async with async_session_factory() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == ExecutionStatus.PENDING
        delivery = await db.get(EventDelivery, delivery_id)
        assert delivery.execution_id == execution_id
        assert delivery.status == EventDeliveryStatus.QUEUED


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["terminal", "future", "corrupt", "unlinked", "failed_delivery", "superseded"])
async def test_recovery_never_publishes_unsafe_or_terminal_event(
    async_session_factory, linked_event, monkeypatch, unsafe
):
    from datetime import datetime, timedelta, timezone
    from src.jobs.schedulers import deferred_execution_promoter as promoter
    from src.services.solutions.deployment_manifest import canonical_json, sha256_digest

    event_id, delivery_id, workflow_id = linked_event

    @asynccontextmanager
    async def db_context():
        async with async_session_factory() as db:
            yield db

    monkeypatch.setattr("src.core.database.get_db_context", db_context)
    monkeypatch.setattr(promoter, "get_db_context", db_context)
    monkeypatch.setattr(promoter, "_capacity_aware_batch_limit", AsyncMock(return_value=500))
    publish = AsyncMock(side_effect=ConnectionError("publisher unavailable"))
    monkeypatch.setattr(async_executor, "_publish_pending", publish)
    dispatch = dict(
        workflow_id=str(workflow_id), parameters={}, source="Event System",
        event=EventContext(id=str(event_id), type="test.link", data={}, organization_id=None, received_at=""),
        event_delivery_id=str(delivery_id),
    )
    with pytest.raises(ConnectionError):
        await async_executor.enqueue_system_workflow_execution(**dispatch)
    execution_id = UUID(publish.await_args.kwargs["execution_id"])
    async with async_session_factory() as db:
        execution = await db.get(Execution, execution_id)
        delivery = await db.get(EventDelivery, delivery_id)
        if unsafe == "terminal":
            execution.status = ExecutionStatus.FAILED
        elif unsafe == "future":
            execution.scheduled_at = datetime.now(timezone.utc) + timedelta(hours=1)
        elif unsafe == "corrupt":
            execution.dispatch_evidence_hash = "corrupted"
        elif unsafe == "unlinked":
            import copy
            envelope = copy.deepcopy(execution.dispatch_evidence)
            envelope["publish"].pop("dispatch_metadata")
            envelope["publish_hash"] = sha256_digest(canonical_json(envelope["publish"]))
            execution.dispatch_evidence = envelope
            execution.dispatch_evidence_hash = sha256_digest(canonical_json(envelope))
        elif unsafe == "failed_delivery":
            delivery.status = EventDeliveryStatus.FAILED
        elif unsafe == "superseded":
            delivery.execution_id = None
        await db.commit()
    publish.reset_mock(side_effect=True)
    await promoter.promote_due_executions()
    assert not any(c.kwargs["execution_id"] == str(execution_id) for c in publish.await_args_list)
    async with async_session_factory() as db:
        execution = await db.get(Execution, execution_id)
        assert execution.status == (ExecutionStatus.FAILED if unsafe == "terminal" else ExecutionStatus.SCHEDULED)
