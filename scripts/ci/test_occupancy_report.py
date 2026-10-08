#!/usr/bin/env python3
"""Unit tests for the CI occupancy report.

Run directly: ``python3 scripts/ci/test_occupancy_report.py``.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from occupancy_report import (  # noqa: E402
    GitHubClient,
    collect_report,
    job_intervals,
    main,
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

    def test_action_required_is_classified_as_failed(self) -> None:
        self.assertEqual(
            run_disposition({"status": "completed", "conclusion": "action_required"}),
            "failed",
        )

    def test_unrecognised_conclusion_is_passed_through(self) -> None:
        self.assertEqual(
            run_disposition({"status": "completed", "conclusion": "surprise_state"}),
            "surprise_state",
        )


class StubClient:
    """Serves canned API payloads without touching the network."""

    def __init__(
        self,
        runs: list[dict[str, Any]],
        jobs: dict[int, list[dict[str, Any]]],
        runs_total: int | None = None,
    ) -> None:
        self._runs = runs
        self._jobs = jobs
        self._runs_total = runs_total
        self.paths: list[str] = []

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        self.paths.append(path)
        params = params or {}
        if path.endswith("/actions/runs"):
            return {
                "workflow_runs": self._runs,
                "total_count": self._runs_total
                if self._runs_total is not None
                else len(self._runs),
            }
        if "/actions/runs/" in path and path.endswith("/jobs"):
            run_id = int(path.split("/actions/runs/")[1].split("/")[0])
            all_jobs = self._jobs.get(run_id, [])
            page = int(params.get("page", "1"))
            start = (page - 1) * 100
            return {"jobs": all_jobs[start : start + 100], "total_count": len(all_jobs)}
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

    def test_jobs_are_paginated_so_intervals_are_not_lost(self) -> None:
        """A run with >100 jobs must not silently lose the overflow page."""
        runs = [
            {
                "id": 1,
                "run_attempt": 1,
                "name": "CI",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "created_at": iso(T0),
            }
        ]
        jobs = [
            {
                "started_at": iso(T0 + timedelta(seconds=index)),
                "completed_at": iso(T0 + timedelta(seconds=index) + timedelta(minutes=1)),
            }
            for index in range(150)
        ]
        report = collect_report(
            StubClient(runs, {1: jobs}),  # type: ignore[arg-type]
            "owner/repo",
            T0,
            T0 + timedelta(hours=1),
        )
        self.assertEqual(report["jobs_with_intervals"], 150)
        self.assertFalse(report["jobs_truncated"])
        self.assertGreater(report["peak_concurrent_jobs"]["value"], 0)

    def test_run_list_truncation_is_disclosed_not_silent(self) -> None:
        runs = [
            {
                "id": index,
                "run_attempt": 1,
                "name": "CI",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "created_at": iso(T0),
            }
            for index in range(3)
        ]
        report = collect_report(
            StubClient(runs, {}, runs_total=40),  # type: ignore[arg-type]
            "owner/repo",
            T0,
            T0 + timedelta(hours=1),
            max_runs=1,
        )
        self.assertTrue(report["runs_truncated"])
        self.assertEqual(report["runs_total"], 40)
        self.assertTrue(any("truncated" in note for note in report["notes"]))

    def test_page_bound_discloses_job_truncation(self) -> None:
        runs = [
            {
                "id": 1,
                "run_attempt": 1,
                "name": "CI",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "created_at": iso(T0),
            }
        ]
        jobs = [
            {
                "started_at": iso(T0),
                "completed_at": iso(T0 + timedelta(minutes=1)),
            }
            for _ in range(1001)
        ]
        report = collect_report(
            StubClient(runs, {1: jobs}),  # type: ignore[arg-type]
            "owner/repo",
            T0,
            T0 + timedelta(hours=1),
        )
        self.assertTrue(report["jobs_truncated"])
        self.assertEqual(report["jobs_with_intervals"], 1000)
        self.assertTrue(any("page bound" in note for note in report["notes"]))

    def test_notes_do_not_promise_unimplemented_options(self) -> None:
        report = collect_report(
            StubClient([], {}),  # type: ignore[arg-type]
            "owner/repo",
            T0,
            T0 + timedelta(hours=1),
        )
        joined = " ".join(report["notes"])
        self.assertNotIn("--org", joined)
        self.assertNotIn("GH_ORG_TOKEN", joined)
        self.assertIn("not implemented", joined)


class _JsonHandler(http.server.BaseHTTPRequestHandler):
    """Serves a fixed payload and records the request for assertions."""

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        server: StubHttpServer = self.server  # type: ignore[assignment]
        server.last_path = self.path
        server.last_headers = dict(self.headers)
        body = json.dumps(server.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # silence test output
        return


class StubHttpServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    payload: dict[str, Any] = {}
    last_path: str = ""
    last_headers: dict[str, str] = {}


class HttpClientTests(unittest.TestCase):
    """Exercise GitHubClient.get for real against a local HTTP server."""

    def test_get_builds_the_request_and_parses_the_body(self) -> None:
        server = StubHttpServer(("127.0.0.1", 0), _JsonHandler)
        server.payload = {"workflow_runs": [], "total_count": 3}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        root = f"http://127.0.0.1:{server.server_address[1]}"
        client = GitHubClient("token-abc", api_root=root)
        data = client.get("/repos/o/r/actions/runs", {"per_page": "100"})

        self.assertEqual(data["total_count"], 3)
        self.assertIn("per_page=100", server.last_path)
        self.assertEqual(server.last_headers.get("Authorization"), "Bearer token-abc")
        self.assertEqual(server.last_headers.get("Accept"), "application/vnd.github+json")


class CliTests(unittest.TestCase):
    """Drive main() end-to-end: input validation, success and API failure."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "ci-occupancy.json"

    def _run(self, *extra: str) -> int:
        env = {"GITHUB_REPOSITORY": "owner/repo"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            return main(["--out", str(self.out), *extra])

    def test_missing_repository_is_an_input_error(self) -> None:
        with unittest.mock.patch.dict(
            os.environ, {"GITHUB_REPOSITORY": ""}, clear=True
        ):
            self.assertEqual(main(["--out", str(self.out)]), 2)

    def test_malformed_repository_is_rejected(self) -> None:
        self.assertEqual(self._run("--repository", "no-slash"), 2)
        self.assertEqual(self._run("--repository", "../evil/x"), 2)
        self.assertEqual(self._run("--repository", "owner/repo/extra"), 2)

    def test_traversal_in_out_is_rejected(self) -> None:
        with unittest.mock.patch.dict(
            os.environ, {"GITHUB_REPOSITORY": "owner/repo"}, clear=True
        ):
            code = main(
                ["--out", str(Path(self._tmp.name) / "sub" / ".." / ".." / "x.json")]
            )
        self.assertEqual(code, 2)

    def test_success_writes_a_report(self) -> None:
        payload = {"workflow_runs": [], "total_count": 0}
        with unittest.mock.patch.object(GitHubClient, "get", return_value=payload):
            self.assertEqual(self._run(), 0)

        report = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(report["scope"], "repository")
        self.assertEqual(report["runs_considered"], 0)
        self.assertFalse(report["runs_truncated"])

    def test_api_failure_still_writes_an_unavailable_report(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.github.com/x", 503, "unavailable", None, None
        )
        with unittest.mock.patch.object(GitHubClient, "get", side_effect=error):
            self.assertEqual(self._run(), 0)

        report = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "unavailable")
        self.assertIn("HTTP 503", report["error"])
        self.assertTrue(any("unavailable" in note for note in report["notes"]))


if __name__ == "__main__":
    unittest.main()
