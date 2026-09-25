"""The lab must not accept only startup lag samples."""

from scripts.issue_890_lag_check import has_load_window_growth


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

    assert not has_load_window_growth(report, [export(0, 100), export(5, 200)])
    assert has_load_window_growth(report, [export(0, 100), export(10, 200), export(25, 300)])
