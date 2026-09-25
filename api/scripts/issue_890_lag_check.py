"""Require API event-loop lag samples fully inside an issue-890 load level."""

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LAB_BASE = Path(tempfile.gettempdir())


def _metrics(exports: list[dict]):
    for export in exports:
        for resource in export.get("resourceMetrics", []):
            for scope in resource.get("scopeMetrics", []):
                yield from scope.get("metrics", [])


def _lag_points(exports: list[dict]):
    for metric in _metrics(exports):
        histogram = metric.get("histogram", {})
        if metric.get("name") == "bifrost.event_loop.lag" and histogram.get("aggregationTemporality") == 2:
            yield from histogram.get("dataPoints", [])


def has_load_window_growth(report: dict, exports: list[dict]) -> bool:
    windows = [
        (datetime.fromisoformat(level["started_at"]), datetime.fromisoformat(level["finished_at"]))
        for level in report["levels"]
    ]
    streams: dict[str, list[tuple[datetime, int]]] = {}
    for point in _lag_points(exports):
        start = point["startTimeUnixNano"]
        at = datetime.fromtimestamp(int(point["timeUnixNano"]) / 1e9, timezone.utc)
        streams.setdefault(start, []).append((at, int(point["count"])))

    for points in streams.values():
        points.sort()
        for (before, old_count), (after, new_count) in zip(points, points[1:]):
            if new_count > old_count and any(start <= before < after <= end for start, end in windows):
                return True
    return False


def _lab_paths(report_arg: str, metrics_arg: str) -> tuple[Path, Path]:
    report = Path(report_arg).resolve(strict=True)
    metrics = Path(metrics_arg).resolve(strict=True)
    if report.name != "issue-890-load.json" or report.parent.parent != LAB_BASE or not report.parent.name.startswith("bifrost-bifrost-test-"):
        raise ValueError("Report must be in this lab's /tmp result directory")
    if metrics.name != "metrics.jsonl" or metrics.parent.parent != report.parent or not metrics.parent.name.startswith("issue-890-otel-"):
        raise ValueError("Metrics must be in the report's lab result directory")
    return report, metrics


def main() -> int:
    report_path, metrics_path = _lab_paths(sys.argv[1], sys.argv[2])
    report = json.loads(report_path.read_text())
    exports = [json.loads(line) for line in metrics_path.read_text().splitlines() if line]
    if has_load_window_growth(report, exports):
        return 0
    print("No event-loop lag histogram count growth fully inside a load level", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
