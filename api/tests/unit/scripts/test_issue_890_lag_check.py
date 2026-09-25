"""The lab must not accept only startup lag samples."""

import pytest

from scripts import issue_890_lag_check as lag_check


def test_requires_count_growth_between_exports_inside_load() -> None:
    report = {"levels": [{"started_at": "2026-09-25T15:43:08+00:00", "finished_at": "2026-09-25T15:43:47+00:00"}]}

    def export(second: int, count: int) -> dict:
        return {"resourceMetrics": [{"scopeMetrics": [{"metrics": [{
            "name": "bifrost.event_loop.lag",
            "histogram": {"aggregationTemporality": 2, "dataPoints": [{
                "startTimeUnixNano": "1790350900000000000",
                "timeUnixNano": str(1790350980 + second) + "000000000",
                "count": str(count),
            }]},
        }]}]}]}

    assert not lag_check.has_load_window_growth(report, [export(0, 100), export(5, 200)])
    assert lag_check.has_load_window_growth(report, [export(0, 100), export(10, 200), export(25, 300)])


def test_lab_paths_reject_outside_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lag_check, "LAB_BASE", tmp_path)
    result_dir = tmp_path / "bifrost-bifrost-test-12345678"
    metrics_dir = result_dir / "issue-890-otel-20260925"
    metrics_dir.mkdir(parents=True)
    report = result_dir / "issue-890-load.json"
    metrics = metrics_dir / "metrics.jsonl"
    report.touch()
    metrics.touch()

    assert lag_check._lab_paths(str(report), str(metrics)) == (report, metrics)
    outside = tmp_path / "issue-890-load.json"
    outside.touch()
    with pytest.raises(ValueError):
        lag_check._lab_paths(str(outside), str(metrics))
