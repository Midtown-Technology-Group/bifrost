"""Require API event-loop lag samples fully inside an issue-890 load level."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def has_load_window_growth(report: dict, exports: list[dict]) -> bool:
    windows = [
        (datetime.fromisoformat(level["started_at"]), datetime.fromisoformat(level["finished_at"]))
        for level in report["levels"]
    ]
    streams: dict[str, list[tuple[datetime, int]]] = {}
    for export in exports:
        for resource in export.get("resourceMetrics", []):
            for scope in resource.get("scopeMetrics", []):
                for metric in scope.get("metrics", []):
                    if metric.get("name") != "bifrost.event_loop.lag":
                        continue
                    histogram = metric.get("histogram", {})
                    if histogram.get("aggregationTemporality") != 2:
                        continue
                    for point in histogram.get("dataPoints", []):
                        start = point["startTimeUnixNano"]
                        at = datetime.fromtimestamp(int(point["timeUnixNano"]) / 1e9, timezone.utc)
                        streams.setdefault(start, []).append((at, int(point["count"])))

    for points in streams.values():
        points.sort()
        for (before, old_count), (after, new_count) in zip(points, points[1:]):
            if new_count > old_count and any(start <= before < after <= end for start, end in windows):
                return True
    return False


def main() -> int:
    report = json.loads(Path(sys.argv[1]).read_text())
    exports = [json.loads(line) for line in Path(sys.argv[2]).read_text().splitlines() if line]
    if has_load_window_growth(report, exports):
        return 0
    print("No event-loop lag histogram count growth fully inside a load level", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
