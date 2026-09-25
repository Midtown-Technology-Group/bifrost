"""Event-loop lag sampling contract."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from src.core.event_loop_lag import record_event_loop_lag


@pytest.mark.asyncio
async def test_event_loop_lag_records_nonnegative_delay():
    stop = asyncio.Event()
    histogram = MagicMock()
    meter = MagicMock()
    meter.create_histogram.return_value = histogram

    async def one_sample(_interval: float) -> None:
        stop.set()

    with (
        patch("src.core.event_loop_lag.metrics.get_meter", return_value=meter),
        patch("src.core.event_loop_lag.asyncio.sleep", side_effect=one_sample),
    ):
        await record_event_loop_lag(stop)

    assert histogram.record.call_count == 1
    assert histogram.record.call_args.args[0] >= 0
