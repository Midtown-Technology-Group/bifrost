"""Measure event-loop scheduling delay for runtime capacity investigations."""

import asyncio

from opentelemetry import metrics


async def record_event_loop_lag(stop: asyncio.Event, *, interval: float = 0.1) -> None:
    histogram = metrics.get_meter(__name__).create_histogram(
        "bifrost.event_loop.lag",
        unit="s",
        description="Delay beyond the scheduled event-loop wakeup time.",
    )
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        expected = loop.time() + interval
        await asyncio.sleep(interval)
        histogram.record(max(0.0, loop.time() - expected))
