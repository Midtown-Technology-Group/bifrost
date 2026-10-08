#!/usr/bin/env python3
"""Unit tests for the CI occupancy report.

Run directly: ``python3 scripts/ci/test_occupancy_report.py``.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from occupancy_report import (  # noqa: E402
    collect_report,
    job_intervals,
    occupancy_series,
    peak_concurrency,
    run_disposition,
)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class IntervalTests(unittest.TestCase):
    def test_completed_job_yields_an_interval(self) -> None:
        jobs = [{"started_at": iso(T0), "completed_at": iso(T0 + timedelta(minutes=5))}]
        intervals, incomplete = job_intervals(jobs)
        self.assertEqual(intervals, [(T0, T0 + timedelta(minutes=5))])
        self.assertEqual(incomplete, 0)

    def test_missing_or_backwards_timestamps_are_counted_not_used(self) -> None:
        jobs = [
            {"started_at": iso(T0), "completed_at": None},
            {"started_at": None, "completed_at": iso(T0)},
            {"started_at": iso(T0), "completed_at": iso(T0)},
            {"started_at": iso(T0 + timedelta(minutes=9)), "completed_at": iso(T0)},
        ]
        intervals, incomplete = job_intervals(jobs)
        self.assertEqual(intervals, [])
        self.assertEqual(incomplete, 4)

    def test_null_timestamps_never_raise(self) -> None:
        self.assertEqual(job_intervals([{}]), ([], 1))


class PeakTests(unittest.TestCase):
    def test_peak_counts_true_overlap(self) -> None:
        intervals = [
            (T0, T0 + timedelta(minutes=10)),
            (T0 + timedelta(minutes=5), T0 + timedelta(minutes=15)),
            (T0 + timedelta(minutes=6), T0 + timedelta(minutes=7)),
        ]
        peak, peak_at = peak_concurrency(intervals)
        self.assertEqual(peak, 3)
        self.assertEqual(peak_at, T0 + timedelta(minutes=6))

    def test_empty_intervals_report_zero(self) -> None:
        peak, peak_at = peak_concurrency([])
        self.assertEqual(peak, 0)
        self.assertIsNone(peak_at)

    def test_intervals_ending_before_the_next_starts_do_not_overlap(self) -> None:
        intervals = [
            (T0, T0 + timedelta(minutes=5)),
            (T0 + timedelta(minutes=5), T0 + timedelta(minutes=9)),
        ]
        peak, _ = peak_concurrency(intervals)
        self.assertEqual(peak, 1)


class SeriesTests(unittest.TestCase):
    def test_series_buckets_by_five_minutes(self) -> None:
        window_end = T0 + timedelta(minutes=15)
        intervals = [(T0 + timedelta(minutes=1), T0 + timedelta(minutes=14))]
        series = occupancy_series(intervals, T0, window_end, bucket_seconds=300)
        self.assertEqual(len(series), 3)
        self.assertEqual([row["max_concurrent"] for row in series], [1, 1, 1])

    def test_idle_buckets_are_reported_as_zero(self) -> None:
        window_end = T0 + timedelta(minutes=10)
        series = occupancy_series([], T0, window_end, bucket_seconds=300)
        self.assertEqual([row["max_concurrent"] for row in series], [0, 0])


class DispositionTests(unittest.TestCase):
    def test_terminal_states_map_to_the_taxonomy(self) -> None:
        cases = {
            ("completed", "success"): "completed",
            ("completed", "failure"): "failed",
            ("completed", "cancelled"): "cancelled",
            ("completed", "timed_out"): "timed_out",
            ("queued", None): "no-verdict",
            ("in_progress", None): "running",
            ("completed", "skipped"): "skipped",
        }
        for (status, conclusion), expected in cases.items():
            with self.subTest(status=status, conclusion=conclusion):
                self.assertEqual(
                    run_disposition({"status": status, "conclusion": conclusion}), expected
                )


class StubClient:
    """Serves canned API payloads without touching the network."""

    def __init__(self, runs: list[dict[str, Any]], jobs: dict[int, list[dict[str, Any]]]) -> None:
        self._runs = runs
        self._jobs = jobs
        self.paths: list[str] = []

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        self.paths.append(path)
        if path.endswith("/actions/runs"):
            return {"workflow_runs": self._runs}
        if "/actions/runs/" in path and path.endswith("/jobs"):
            run_id = int(path.split("/actions/runs/")[1].split("/")[0])
            return {"jobs": self._jobs.get(run_id, [])}
        raise AssertionError(f"unexpected path: {path}")


class CollectReportTests(unittest.TestCase):
    def test_report_shape_and_peak(self) -> None:
        runs = [
            {
                "id": 1,
                "run_attempt": 1,
                "name": "CI",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "created_at": iso(T0),
            },
            {
                "id": 2,
                "run_attempt": 1,
                "name": "CI",
                "event": "push",
                "status": "completed",
                "conclusion": "cancelled",
                "created_at": iso(T0),
            },
        ]
        jobs = {
            1: [
                {
                    "started_at": iso(T0),
                    "completed_at": iso(T0 + timedelta(minutes=10)),
                }
            ],
            2: [
                {
                    "started_at": iso(T0 + timedelta(minutes=5)),
                    "completed_at": iso(T0 + timedelta(minutes=8)),
                }
            ],
        }
        report = collect_report(
            StubClient(runs, jobs),  # type: ignore[arg-type]
            "owner/repo",
            T0,
            T0 + timedelta(hours=1),
        )
        self.assertEqual(report["scope"], "repository")
        self.assertEqual(report["runs_considered"], 2)
        self.assertEqual(report["jobs_with_intervals"], 2)
        self.assertEqual(report["incomplete_intervals"], 0)
        self.assertEqual(report["peak_concurrent_jobs"]["value"], 2)
        self.assertEqual(report["runs"][0]["disposition"], "completed")
        self.assertEqual(report["runs"][1]["disposition"], "cancelled")
        self.assertTrue(any("repository-scoped" in note for note in report["notes"]))

    def test_missing_timestamps_are_reported_as_incomplete(self) -> None:
        runs = [
            {
                "id": 7,
                "run_attempt": 1,
                "name": "CI",
                "event": "pull_request",
                "status": "in_progress",
                "conclusion": None,
                "created_at": iso(T0),
            }
        ]
        report = collect_report(
            StubClient(runs, {7: [{"started_at": None, "completed_at": None}]}),  # type: ignore[arg-type]
            "owner/repo",
            T0,
            T0 + timedelta(hours=1),
        )
        self.assertEqual(report["jobs_with_intervals"], 0)
        self.assertEqual(report["incomplete_intervals"], 1)
        self.assertEqual(report["peak_concurrent_jobs"]["value"], 0)
        self.assertEqual(report["runs"][0]["disposition"], "running")


if __name__ == "__main__":
    unittest.main()
