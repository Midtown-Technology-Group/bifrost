#!/usr/bin/env python3
"""Record a CI occupancy report for the concurrency budget.

The organization caps concurrent GitHub-hosted runners at 20, so every shard or
lane decision has to be argued against measured demand rather than a single
run's arithmetic. This script samples run/job intervals over a window and
reports peak concurrency plus a bucketed occupancy series.

It is intentionally read-only and dependency-free (stdlib + GitHub REST). It is
invoked as a step inside the existing ``affected-test-plan`` job so the report
adds no runner to the test wave.

Scope: repository only. Cross-repository sampling is **not implemented** - the
report states that in its notes rather than implying org-wide coverage, because
the 20-runner cap is an org-wide constraint and repository scope cannot speak
to it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1
API_ROOT = "https://api.github.com"
DEFAULT_WINDOW_HOURS = 6
DEFAULT_MAX_RUNS = 50
BUCKET_SECONDS = 300
MAX_JOB_PAGES = 10  # bound on jobs pagination (100 jobs per page)


class GitHubClient:
    def __init__(self, token: str | None, api_root: str = API_ROOT) -> None:
        self._token = token
        self._api_root = api_root.rstrip("/")

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{self._api_root}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def job_intervals(jobs: list[dict[str, Any]]) -> tuple[list[tuple[datetime, datetime]], int]:
    """Return completed [start, end] intervals and the count of unusable ones."""
    intervals: list[tuple[datetime, datetime]] = []
    incomplete = 0
    for job in jobs:
        start = parse_iso(job.get("started_at"))
        end = parse_iso(job.get("completed_at"))
        if start is None or end is None or end <= start:
            incomplete += 1
            continue
        intervals.append((start, end))
    return intervals, incomplete


def peak_concurrency(intervals: list[tuple[datetime, datetime]]) -> tuple[int, datetime | None]:
    events: list[tuple[datetime, int]] = []
    for start, end in intervals:
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda item: (item[0], item[1]))
    current = 0
    peak = 0
    peak_at: datetime | None = None
    for moment, delta in events:
        current += delta
        if current > peak:
            peak = current
            peak_at = moment
    return peak, peak_at


def occupancy_series(
    intervals: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int = BUCKET_SECONDS,
) -> list[dict[str, Any]]:
    bucket = timedelta(seconds=bucket_seconds)
    series: list[dict[str, Any]] = []
    cursor = window_start.replace(second=0, microsecond=0)
    while cursor < window_end:
        bucket_end = cursor + bucket
        active = sum(1 for start, end in intervals if start < bucket_end and end > cursor)
        series.append({"bucket_start": cursor.isoformat(), "max_concurrent": active})
        cursor = bucket_end
    return series


def run_disposition(run: dict[str, Any]) -> str:
    """Terminal disposition for an admitted candidate, per the latency taxonomy."""
    status = run.get("status")
    conclusion = run.get("conclusion")
    if status in {"queued", "waiting", "requested", "pending"}:
        return "no-verdict"
    if status == "in_progress":
        return "running"
    if conclusion in {"success"}:
        return "completed"
    if conclusion in {"failure", "action_required", "startup_failure"}:
        return "failed"
    if conclusion in {"cancelled"}:
        return "cancelled"
    if conclusion in {"timed_out", "neutral", "skipped", "stale"}:
        return conclusion
    return "no-verdict" if conclusion is None else str(conclusion)


def fetch_run_jobs(
    client: GitHubClient, repository: str, run_id: int
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch every jobs page for a run, bounded by MAX_JOB_PAGES.

    Returns ``(jobs, truncated)``. A run with more jobs than the page bound
    would otherwise silently lose intervals and understate peak concurrency.
    """
    jobs: list[dict[str, Any]] = []
    for page in range(1, MAX_JOB_PAGES + 1):
        payload = client.get(
            f"/repos/{repository}/actions/runs/{run_id}/jobs",
            {"per_page": "100", "page": str(page)},
        )
        batch = payload.get("jobs", [])
        jobs.extend(batch)
        # total_count drives the stop condition, so its absence (or a zero)
        # must mean "unknown", never "everything is fetched" - otherwise a
        # full first page stops pagination and hides the overflow silently.
        raw_total = payload.get("total_count")
        total = int(raw_total) if raw_total is not None else None
        if total is not None and total <= 0:
            total = None
        if len(batch) < 100 or (total is not None and len(jobs) >= total):
            return jobs, total is not None and len(jobs) < total
    return jobs, True


def collect_report(
    client: GitHubClient,
    repository: str,
    window_start: datetime,
    window_end: datetime,
    max_runs: int = DEFAULT_MAX_RUNS,
) -> dict[str, Any]:
    params = {
        "created": f"{window_start.strftime('%Y-%m-%dT%H:%M:%SZ')}..{window_end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "per_page": "100",
    }
    payload = client.get(f"/repos/{repository}/actions/runs", params)
    runs_total = int(payload.get("total_count") or 0)
    runs = payload.get("workflow_runs", [])[:max_runs]
    runs_truncated = runs_total > len(runs)

    all_intervals: list[tuple[datetime, datetime]] = []
    incomplete = 0
    jobs_truncated = False
    run_rows: list[dict[str, Any]] = []
    for run in runs:
        jobs, run_truncated = fetch_run_jobs(client, repository, run["id"])
        jobs_truncated = jobs_truncated or run_truncated
        intervals, run_incomplete = job_intervals(jobs)
        all_intervals.extend(intervals)
        incomplete += run_incomplete
        starts = [start for start, _ in intervals]
        ends = [end for _, end in intervals]
        run_rows.append(
            {
                "id": run["id"],
                "attempt": run.get("run_attempt"),
                "name": run.get("name"),
                "event": run.get("event"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "disposition": run_disposition(run),
                "created_at": run.get("created_at"),
                "first_job_start": min(starts).isoformat() if starts else None,
                "last_job_end": max(ends).isoformat() if ends else None,
                "jobs": len(jobs),
            }
        )

    peak, peak_at = peak_concurrency(all_intervals)
    notes = [
        "repository-scoped only: cross-repository sampling is not implemented.",
        "interval timestamps come from the workflow-jobs API; true runner wait is "
        + "not exposed and is not reported here.",
    ]
    if runs_truncated:
        notes.append(
            f"run list truncated: {runs_total} runs matched the window but only "
            f"{len(runs)} were fetched (max-runs={max_runs}); occupancy may be understated."
        )
    if jobs_truncated:
        notes.append(
            "job pagination reached its page bound for at least one run; "
            "occupancy may be understated for that run."
        )
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "repository",
        "repository": repository,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "runs_considered": len(runs),
        "runs_total": runs_total,
        "runs_truncated": runs_truncated,
        "jobs_truncated": jobs_truncated,
        "jobs_with_intervals": len(all_intervals),
        "incomplete_intervals": incomplete,
        "peak_concurrent_jobs": {
            "value": peak,
            "at": peak_at.isoformat() if peak_at else None,
        },
        "occupancy_series": occupancy_series(all_intervals, window_start, window_end),
        "runs": run_rows,
        "notes": notes,
    }


def _valid_repository(value: str) -> bool:
    """``owner/name`` with GitHub-safe characters only.

    The value is interpolated into the API path, so a stray ``/`` or ``..``
    would address a different endpoint than the operator intended (S8707).
    """
    parts = value.split("/")
    return len(parts) == 2 and all(
        part and all(char.isalnum() or char in "._-" for char in part) for part in parts
    )


def _output_roots() -> tuple[Path, ...]:
    """Directories the report may be written into.

    The workflow writes to ``$RUNNER_TEMP``; local and test invocations use
    the working directory or the system temp directory.
    """
    roots = [Path.cwd(), Path(tempfile.gettempdir())]
    runner_temp = os.environ.get("RUNNER_TEMP")
    if runner_temp:
        roots.append(Path(runner_temp))
    return tuple(roots)


def _safe_output(raw: Path, roots: tuple[Path, ...]) -> Path:
    """Contain a caller-supplied output path inside one of ``roots`` (S8707).

    Only the working directory, the system temp directory and the workflow's
    own temp directory are writable destinations: traversal segments and
    absolute paths outside them are input errors, never an arbitrary write.
    """
    if ".." in raw.parts:
        raise ValueError(f"--out contains a traversal segment: {raw}")
    resolved = (raw if raw.is_absolute() else Path.cwd() / raw).resolve()
    allowed = tuple(root.resolve() for root in roots if root)
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise ValueError(f"--out escapes every allowed directory: {raw}")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS)
    parser.add_argument("--max-runs", type=int, default=DEFAULT_MAX_RUNS)
    parser.add_argument("--out", required=True, help="path of the JSON report to write")
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="owner/name (defaults to GITHUB_REPOSITORY)",
    )
    args = parser.parse_args(argv)

    if not args.repository:
        print("occupancy_report: no repository given", file=sys.stderr)
        return 2
    if not _valid_repository(args.repository):
        print(
            f"occupancy_report: --repository must be 'owner/name': {args.repository}",
            file=sys.stderr,
        )
        return 2
    out_path = Path(args.out)
    try:
        out_path = _safe_output(out_path, _output_roots())
    except ValueError as error:
        print(f"occupancy_report: {error}", file=sys.stderr)
        return 2

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=args.window_hours)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    report: dict[str, Any]
    try:
        report = collect_report(
            GitHubClient(token), args.repository, window_start, window_end, args.max_runs
        )
    except urllib.error.HTTPError as error:
        report = {
            "schema": SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": "repository",
            "repository": args.repository,
            "status": "unavailable",
            "error": f"HTTP {error.code} from the GitHub API",
            "notes": ["measurement unavailable; occupancy evidence is missing for this window"],
        }
        print(f"occupancy_report: API error {error.code}", file=sys.stderr)
    except Exception as error:  # pragma: no cover - defensive: never break planning
        report = {
            "schema": SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": "repository",
            "repository": args.repository,
            "status": "unavailable",
            "error": str(error),
            "notes": ["measurement unavailable; occupancy evidence is missing for this window"],
        }
        print(f"occupancy_report: {error}", file=sys.stderr)

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")

    peak = report.get("peak_concurrent_jobs", {}).get("value")
    print(
        f"occupancy_report: scope={report.get('scope')} runs={report.get('runs_considered', 0)} "
        f"peak={peak} out={args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
