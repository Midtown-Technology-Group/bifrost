"""Registration and wiring proof for the device-job lost watchdog schedule."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from src.jobs.schedulers import device_jobs_sweep


@pytest.mark.asyncio
async def test_sweep_lost_device_jobs_runs_the_service_watchdog(monkeypatch) -> None:
    database = object()

    @asynccontextmanager
    async def db_context():
        yield database

    sweep = AsyncMock(
        return_value={
            "lost_silence": 1,
            "lost_backstop": 0,
            "stale_claimed_reclaimable": 0,
        }
    )
    monkeypatch.setattr(device_jobs_sweep, "get_db_context", db_context)
    monkeypatch.setattr(device_jobs_sweep, "sweep_device_jobs", sweep)

    outcome = await device_jobs_sweep.sweep_lost_device_jobs()

    sweep.assert_awaited_once_with(database)
    assert "lost_silence=1" in outcome.summary
    assert outcome.platform_job_id is None


@pytest.mark.asyncio
async def test_sweep_lost_device_jobs_quiet_when_nothing_transitioned(
    monkeypatch,
) -> None:
    @asynccontextmanager
    async def db_context():
        yield object()

    sweep = AsyncMock(
        return_value={
            "lost_silence": 0,
            "lost_backstop": 0,
            "stale_claimed_reclaimable": 2,
        }
    )
    monkeypatch.setattr(device_jobs_sweep, "get_db_context", db_context)
    monkeypatch.setattr(device_jobs_sweep, "sweep_device_jobs", sweep)

    outcome = await device_jobs_sweep.sweep_lost_device_jobs()

    assert "stale_claimed_reclaimable=2" in outcome.summary
