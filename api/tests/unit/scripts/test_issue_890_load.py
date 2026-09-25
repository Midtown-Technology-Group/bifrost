"""Capacity report arithmetic must keep observed percentiles and failures."""

import sys
from datetime import UTC, datetime, timedelta

import pytest
from scripts.issue_890_load import (
    attempt_intervals,
    main,
    summarize,
    summarize_fresh_pools,
)


def test_summarize_uses_nearest_rank_and_successful_work_only():
    result = summarize([0.4, 0.1, 0.2, 0.3], failures=1, elapsed=2.0)

    assert result == {
        "completed": 4,
        "failed": 1,
        "throughput_per_second": 2.0,
        "p50_seconds": 0.2,
        "p95_seconds": 0.4,
        "p99_seconds": 0.4,
        "wall_seconds": 2.0,
    }


def test_attempt_intervals_use_persisted_lifecycle_boundaries():
    assert attempt_intervals(
        {
            "created_at": "2026-09-25T00:00:00+00:00",
            "published_at": "2026-09-25T00:00:00.100000+00:00",
            "claimed_at": "2026-09-25T00:00:00.300000+00:00",
            "started_at": "2026-09-25T00:00:00.350000+00:00",
            "completed_at": "2026-09-25T00:00:00.850000+00:00",
        }
    ) == {
        "dispatch": 0.1,
        "queue": 0.2,
        "claim_to_start": 0.05,
        "running_to_terminal": 0.5,
    }


def test_pool_summary_ignores_stale_heartbeat_and_keeps_admission_wait():
    now = datetime(2026, 9, 25, tzinfo=UTC)
    pools = [
        {
            "worker_id": "secret-worker",
            "last_heartbeat": (now - timedelta(seconds=5)).isoformat(),
            "configured_capacity": 4,
            "busy_count": 3,
            "available_slots": 1,
            "admission": {
                "attempts": 9,
                "successes": 8,
                "wait_seconds_total": 4.5,
                "wait_seconds_max": 2.0,
                "rejections": {"slot_timeout": 1},
            },
        },
        {
            "worker_id": "stale-worker",
            "last_heartbeat": (now - timedelta(minutes=1)).isoformat(),
            "configured_capacity": 4,
            "busy_count": 0,
            "available_slots": 4,
            "admission": {"attempts": 100, "wait_seconds_total": 100.0},
        },
    ]
    sample = summarize_fresh_pools({"pools": pools}, now)
    assert sample == {
        "fresh_pools": 1,
        "stale_pools": 1,
        "capacity": 4,
        "busy": 3,
        "available": 1,
        "admission_attempts": 9,
        "admission_successes": 8,
        "admission_wait_seconds_total": 4.5,
        "admission_wait_seconds_max": 2.0,
        "admission_rejections": {"slot_timeout": 1},
    }
    assert "worker" not in str(sample)


def test_sweep_requires_enough_operations_for_highest_concurrency(monkeypatch):
    monkeypatch.setenv("BIFROST_ENVIRONMENT", "testing")
    monkeypatch.setenv("TEST_API_URL", "http://api:8000")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "issue_890_load.py",
            "--scenario",
            "api-read",
            "--concurrency",
            "128",
            "--operations",
            "50",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
