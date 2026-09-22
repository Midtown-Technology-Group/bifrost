import asyncio

import pytest
from src.jobs.postgres_delivery import PostgresConsumerRunner


class Consumer:
    queue_name = "workflow-executions"


@pytest.mark.asyncio
async def test_pause_cooperatively_waits_for_poller_without_cancelling_it() -> None:
    runner = PostgresConsumerRunner(Consumer())  # type: ignore[arg-type]
    exited = asyncio.Event()

    async def poller() -> None:
        await runner._pause_requested.wait()
        exited.set()

    task = asyncio.create_task(poller())
    runner._poller = task

    await runner.pause()

    assert exited.is_set()
    assert task.cancelled() is False
    assert runner._poller is None


@pytest.mark.asyncio
async def test_start_is_idempotent_while_poller_is_running(monkeypatch) -> None:
    runner = PostgresConsumerRunner(Consumer())  # type: ignore[arg-type]
    task = asyncio.create_task(asyncio.sleep(10))
    runner._poller = task

    await runner.start()

    assert runner._poller is task
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_pause_cancels_a_stuck_poller_at_its_deadline() -> None:
    runner = PostgresConsumerRunner(Consumer())  # type: ignore[arg-type]
    cancelled = asyncio.Event()

    async def poller() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runner._poller = asyncio.create_task(poller())

    with pytest.raises(TimeoutError, match="did not pause"):
        await runner.pause(timeout=0.01)

    await asyncio.wait_for(cancelled.wait(), timeout=1)
    await asyncio.sleep(0)
    assert runner._poller is None
