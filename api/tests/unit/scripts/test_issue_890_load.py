"""Capacity report arithmetic must keep observed percentiles and failures."""

from scripts.issue_890_load import summarize


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
