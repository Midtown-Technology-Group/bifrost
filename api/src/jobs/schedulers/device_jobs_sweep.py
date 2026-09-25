"""
Device-job lost watchdog schedule

Wraps :func:`src.services.device_jobs.sweep_device_jobs` so the server
watchdog that owns terminal ``lost`` transitions actually runs. The service
function defines the frozen M0 semantics (90s activity-silence threshold,
``timeout_seconds + 60s`` backstop); this module only acquires the database
session and reports a bounded summary for scheduler diagnostics.
"""

import logging
from typing import Any

from src.core.database import get_db_context
from src.scheduler.registry import ScheduledTaskOutcome
from src.services.device_jobs import sweep_device_jobs

logger = logging.getLogger(__name__)

# Registration cadence for the watchdog. The M0 contract keeps a running job
# in ``running`` for RUNNING_LOST_SECONDS (90s) of agent-activity silence, so
# a 30s interval bounds detection at that threshold plus at most one interval
# of scheduling delay. Raising this widens the lost-detection window; the
# contract thresholds themselves live in src/models/orm/device_jobs.py.
SWEEP_INTERVAL_SECONDS = 30


async def sweep_lost_device_jobs() -> ScheduledTaskOutcome:
    """Run one device-job watchdog sweep and summarize what it transitioned."""
    async with get_db_context() as db:
        stats: dict[str, Any] = await sweep_device_jobs(db)

    summary = (
        f"lost_silence={stats['lost_silence']} "
        f"lost_backstop={stats['lost_backstop']} "
        f"stale_claimed_reclaimable={stats['stale_claimed_reclaimable']}"
    )
    if stats["lost_silence"] or stats["lost_backstop"]:
        logger.warning(
            "device_jobs_lost_watchdog transitioned running job(s) to lost",
            extra={"task_id": "device_jobs_lost_sweep", **stats},
        )
    return ScheduledTaskOutcome(summary=summary)


__all__ = ["SWEEP_INTERVAL_SECONDS", "sweep_lost_device_jobs"]
