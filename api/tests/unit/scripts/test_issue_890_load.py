"""Capacity report arithmetic must keep observed percentiles and failures."""

import sys

import pytest
from scripts.issue_890_load import main, summarize


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
