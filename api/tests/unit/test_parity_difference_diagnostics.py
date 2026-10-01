"""Parity failures locate drift without printing observed payload values."""

import pytest

from tests.parity.compare import difference_path


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (
            {"executions": [{"status": "Success"}]},
            {"executions": [{"status": "Failed"}]},
            "database.executions[0].status",
        ),
        ({"executions": []}, {"executions": [{}]}, "database.executions.<length>"),
        ({"executions": []}, {"jobs": []}, "database.<keys>"),
        ({"executions": []}, {"executions": []}, None),
    ],
)
def test_difference_path(left, right, expected):
    assert difference_path(left, right, "database") == expected


def test_difference_diagnostic_omits_values():
    result = difference_path(
        {"claim_token": "synthetic-sensitive-left"},
        {"claim_token": "synthetic-sensitive-right"},
        "database",
    )
    assert result == "database.claim_token"
    assert "synthetic-sensitive" not in result
