"""RabbitMQ connection/start/publish helper tests with fake broker objects."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import aio_pika
import pytest

from src.jobs import rabbitmq as rabbitmq_module
from src.jobs.rabbitmq import (
    BaseConsumer,
    BroadcastConsumer,
    publish_broadcast,
    publish_to_exchange,
    suspend_consumer_admission,
)


class _StartedBaseConsumer(BaseConsumer):
    async def process_message(self, body: dict[str, Any]) -> None:
        return None


class _StartedBroadcastConsumer(BroadcastConsumer):
    async def process_message(self, body: dict[str, Any]) -> None:
        return None


class _FakeQueue:
    def __init__(self, name: str = "queue") -> None:
        self.name = name
        self.bind = AsyncMock()
        self.consume = AsyncMock(return_value=f"{name}-consumer-tag")


class _FakeExchange:
    def __init__(self) -> None:
        self.publish = AsyncMock()


class _FakeChannel:
    def __init__(self) -> None:
        self.default_exchange = _FakeExchange()
        self.exchanges: dict[str, _FakeExchange] = {}
        self.declare_exchange = AsyncMock(side_effect=self._declare_exchange)
        self.declare_queue = AsyncMock(side_effect=self._declare_queue)
        self.set_qos = AsyncMock()
        self.close = AsyncMock()
        self.queues: dict[str, _FakeQueue] = {}

    async def _declare_exchange(self, name: str, *args: Any, **kwargs: Any) -> _FakeExchange:
        exchange = _FakeExchange()
        self.exchanges[name] = exchange
        return exchange

    async def _declare_queue(self, name: str, **kwargs: Any) -> _FakeQueue:
        queue = _FakeQueue(name or "generated")
        self.queues[name] = queue
        return queue


class _FakeConnection:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    async def channel(self) -> _FakeChannel:
        return self._channel


class _FakeConnectionContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _FakeConnection:
        self.entered = True
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


class _FakeProcessedMessage:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = json.dumps(body).encode()
        self.message_id = "broadcast-msg"
        self.entered = False
        self.exited = False

    def process(self, **kwargs: Any) -> "_FakeProcessedMessage":
        self.process_kwargs = kwargs
        return self

    async def __aenter__(self) -> "_FakeProcessedMessage":
        self.entered = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


@pytest.mark.asyncio
async def test_consumers_share_process_admission_semaphore() -> None:
    semaphore = asyncio.Semaphore(1)
    first = _StartedBaseConsumer("first")
    second = _StartedBaseConsumer("second")
    first.set_admission_semaphore(semaphore)
    second.set_admission_semaphore(semaphore)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def hold_first(_body: dict[str, Any]) -> None:
        first_started.set()
        await release_first.wait()

    async def run_second(_body: dict[str, Any]) -> None:
        second_started.set()

    first.process_message = hold_first  # type: ignore[method-assign]
    second.process_message = run_second  # type: ignore[method-assign]

    first_task = asyncio.create_task(first._process_admitted_message({}))
    await first_started.wait()
    second_task = asyncio.create_task(second._process_admitted_message({}))
    await asyncio.sleep(0)

    assert not second_started.is_set()
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_started.is_set()


@pytest.mark.asyncio
async def test_all_parent_slots_transfer_admission_to_child_workflows() -> None:
    """Dependent workflow dispatch cannot starve behind all admitted agents."""
    capacity = 3
    semaphore = asyncio.Semaphore(capacity)
    parents = [_StartedBaseConsumer(f"agent-{index}") for index in range(capacity)]
    children = [_StartedBaseConsumer(f"workflow-{index}") for index in range(capacity)]
    for consumer in [*parents, *children]:
        consumer.set_admission_semaphore(semaphore)

    all_parents_started = asyncio.Event()
    started_count = 0
    child_completions: list[int] = []
    parent_completions: list[int] = []

    async def run_child(index: int, _body: dict[str, Any]) -> None:
        child_completions.append(index)

    def run_parent(index: int):
        async def process(_body: dict[str, Any]) -> None:
            nonlocal started_count
            started_count += 1
            if started_count == capacity:
                all_parents_started.set()
            await all_parents_started.wait()
            async with suspend_consumer_admission():
                await children[index]._process_admitted_message({})
            parent_completions.append(index)

        return process

    for index, child in enumerate(children):
        child.process_message = (  # type: ignore[method-assign]
            lambda body, index=index: run_child(index, body)
        )
    for index, parent in enumerate(parents):
        parent.process_message = run_parent(index)  # type: ignore[method-assign]

    await asyncio.wait_for(
        asyncio.gather(
            *(parent._process_admitted_message({}) for parent in parents)
        ),
        timeout=1,
    )

    assert sorted(child_completions) == list(range(capacity))
    assert sorted(parent_completions) == list(range(capacity))
    assert semaphore._value == capacity


@pytest.mark.asyncio
async def test_base_consumer_start_declares_topology_and_stores_consumer_tag(monkeypatch):
    channel = _FakeChannel()
    context = _FakeConnectionContext(_FakeConnection(channel))
    init_pools = AsyncMock()
    monkeypatch.setattr(rabbitmq_module.rabbitmq, "init_pools", init_pools)
    monkeypatch.setattr(rabbitmq_module.rabbitmq, "get_connection", lambda: context)

    consumer = _StartedBaseConsumer("work", prefetch_count=3, retry_delays_seconds=[5, 9])

    await consumer.start()

    init_pools.assert_awaited_once()
    assert context.entered is True
    channel.set_qos.assert_awaited_once_with(prefetch_count=3)
    exchange_call = channel.declare_exchange.await_args_list[0]
    assert exchange_call.args == ("work-dlx", aio_pika.ExchangeType.DIRECT)
    assert exchange_call.kwargs == {"durable": True}
    declared = [(call.args[0], call.kwargs) for call in channel.declare_queue.await_args_list]
    assert declared[0] == ("work-poison", {"durable": True})
    assert declared[1][0] == "work-retry-1"
    assert declared[1][1]["arguments"]["x-message-ttl"] == 5000
    assert declared[2][0] == "work-retry-2"
    assert declared[2][1]["arguments"]["x-message-ttl"] == 9000
    assert declared[3][0] == "work"
    channel.queues["work-poison"].bind.assert_awaited_once()
    channel.queues["work"].consume.assert_awaited_once_with(consumer._on_message)
    assert consumer._channel is channel
    assert consumer._queue is channel.queues["work"]
    assert consumer._consumer_tag == "work-consumer-tag"


@pytest.mark.asyncio
async def test_broadcast_consumer_start_binds_generated_queue(monkeypatch):
    channel = _FakeChannel()
    context = _FakeConnectionContext(_FakeConnection(channel))
    monkeypatch.setattr(rabbitmq_module.rabbitmq, "init_pools", AsyncMock())
    monkeypatch.setattr(rabbitmq_module.rabbitmq, "get_connection", lambda: context)

    consumer = _StartedBroadcastConsumer("fanout-events")

    await consumer.start()

    channel.set_qos.assert_awaited_once_with(prefetch_count=1)
    channel.declare_exchange.assert_awaited_once_with(
        "fanout-events",
        aio_pika.ExchangeType.FANOUT,
        durable=True,
    )
    channel.declare_queue.assert_awaited_once_with("", exclusive=True, auto_delete=True)
    channel.queues[""].bind.assert_awaited_once()
    channel.queues[""].consume.assert_awaited_once_with(consumer._on_message)
    assert consumer._consumer_tag == "generated-consumer-tag"


@pytest.mark.asyncio
async def test_publish_broadcast_publishes_persistent_message_and_closes_channel(monkeypatch):
    channel = _FakeChannel()
    monkeypatch.setattr(rabbitmq_module.rabbitmq, "init_pools", AsyncMock())
    monkeypatch.setattr(
        rabbitmq_module.rabbitmq,
        "get_connection",
        lambda: _FakeConnectionContext(_FakeConnection(channel)),
    )

    await publish_broadcast("events", {"type": "refresh"})

    channel.declare_exchange.assert_awaited_once_with(
        "events",
        aio_pika.ExchangeType.FANOUT,
        durable=True,
    )
    exchange = channel.exchanges["events"]
    exchange.publish.assert_awaited_once()
    published = exchange.publish.await_args.args[0]
    assert json.loads(published.body.decode()) == {"type": "refresh"}
    assert published.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert exchange.publish.await_args.kwargs == {"routing_key": ""}
    channel.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_to_exchange_uses_transient_exchange_and_closes_channel(monkeypatch):
    channel = _FakeChannel()
    monkeypatch.setattr(rabbitmq_module.rabbitmq, "init_pools", AsyncMock())
    monkeypatch.setattr(
        rabbitmq_module.rabbitmq,
        "get_connection",
        lambda: _FakeConnectionContext(_FakeConnection(channel)),
    )

    await publish_to_exchange("stream", {"delta": 1}, routing_key="run-1")

    channel.declare_exchange.assert_awaited_once_with(
        "stream",
        aio_pika.ExchangeType.FANOUT,
        durable=False,
        auto_delete=True,
    )
    exchange = channel.exchanges["stream"]
    exchange.publish.assert_awaited_once()
    published = exchange.publish.await_args.args[0]
    assert json.loads(published.body.decode()) == {"delta": 1}
    assert published.delivery_mode == aio_pika.DeliveryMode.NOT_PERSISTENT
    assert exchange.publish.await_args.kwargs == {"routing_key": "run-1"}
    channel.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_broadcast_closes_channel_when_publish_fails(monkeypatch):
    channel = _FakeChannel()
    exchange = _FakeExchange()
    exchange.publish = AsyncMock(side_effect=RuntimeError("publish failed"))
    channel.declare_exchange = AsyncMock(return_value=exchange)
    monkeypatch.setattr(rabbitmq_module.rabbitmq, "init_pools", AsyncMock())
    monkeypatch.setattr(
        rabbitmq_module.rabbitmq,
        "get_connection",
        lambda: _FakeConnectionContext(_FakeConnection(channel)),
    )

    with pytest.raises(RuntimeError, match="publish failed"):
        await publish_broadcast("events", {"type": "refresh"})

    channel.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_process_message_logs_and_reraises_handler_error():
    class _FailingBroadcast(BroadcastConsumer):
        async def process_message(self, body: dict[str, Any]) -> None:
            raise RuntimeError(f"bad body {body['id']}")

    consumer = _FailingBroadcast("fanout-events")
    message = _FakeProcessedMessage({"id": "msg-1"})

    with pytest.raises(RuntimeError, match="bad body msg-1"):
        await consumer._process_message_with_ack(message)  # type: ignore[arg-type]

    assert message.entered is True
    assert message.exited is True
    assert message.process_kwargs == {"requeue": False}
