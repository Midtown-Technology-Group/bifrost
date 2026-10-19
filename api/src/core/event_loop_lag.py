"""Measure event-loop scheduling delay for runtime capacity investigations."""

import asyncio
import faulthandler
import gc
import json
import time

from opentelemetry import metrics


async def record_event_loop_lag(stop: asyncio.Event, *, interval: float = 0.1) -> None:
    histogram = metrics.get_meter(__name__).create_histogram(
        "bifrost.event_loop.lag",
        unit="s",
        description="Delay beyond the scheduled event-loop wakeup time.",
    )
    loop = asyncio.get_running_loop()
    gc_started = {}

    def record_gc(phase, info):
        generation = info["generation"]
        if phase == "start":
            gc_started[generation] = time.time()
        else:
            started = gc_started.pop(generation, None)
            if started is not None and time.time() - started >= 0.05:
                print("SEARCH_DIAGNOSTIC_GC " + json.dumps({
                    "generation": generation, "started_unix": started,
                    "ended_unix": time.time(), "collected": info["collected"],
                }), flush=True)

    # Temporary diagnostic only: native watchdog captures the blocked Python stack.
    gc.callbacks.append(record_gc)
    print("SEARCH_DIAGNOSTIC_ENABLED", flush=True)
    while not stop.is_set():
        faulthandler.dump_traceback_later(0.35)
        expected = loop.time() + interval
        await asyncio.sleep(interval)
        lag = max(0.0, loop.time() - expected)
        histogram.record(lag)
        if lag >= 0.05:
            print("SEARCH_DIAGNOSTIC_LOOP " + json.dumps({
                "at_unix": time.time(), "lag_seconds": lag,
            }), flush=True)
    faulthandler.cancel_dump_traceback_later()
    gc.callbacks.remove(record_gc)
