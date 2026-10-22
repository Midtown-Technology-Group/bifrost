"""Unit tests for shared worker heartbeat normalization (MIDT-184)."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from shared.worker_heartbeat import normalize_heartbeat


def test_modern_payload_passes_through():
    hb = {
        "active_process_count": 2,
        "pool_size": 2,
        "configured_capacity": 4,
        "max_workers": 4,
        "idle_count": 1,
        "busy_count": 1,
        "available_slots": 3,
        "saturation_ratio": 0.25,
        "requirements_installed": 7,
        "requirements_total": 8,
        "admission": {"rejections": {"memory_pressure": 1}},
    }

    norm = normalize_heartbeat(hb)

    assert norm.active_process_count == 2
    assert norm.pool_size == 2
    assert norm.configured_capacity == 4
    assert norm.max_workers == 4
    assert norm.idle_count == 1
    assert norm.busy_count == 1
    assert norm.available_slots == 3
    assert norm.saturation_ratio == 0.25
    assert norm.requirements_installed == 7
    assert norm.requirements_total == 8
    assert norm.admission_rejections == {"memory_pressure": 1}


def test_max_workers_falls_back_to_configured_capacity():
    norm = normalize_heartbeat({"active_process_count": 1, "max_workers": 3})

    assert norm.active_process_count == 1
    assert norm.pool_size == 1
    assert norm.configured_capacity == 3
    assert norm.max_workers == 3


def test_configured_capacity_falls_back_to_max_workers():
    norm = normalize_heartbeat({"active_process_count": 1, "configured_capacity": 5})

    assert norm.configured_capacity == 5
    assert norm.max_workers == 5


def test_legacy_pool_size_only_payload():
    norm = normalize_heartbeat({"pool_size": 3})

    assert norm.active_process_count == 3
    assert norm.pool_size == 3
    assert norm.configured_capacity is None
    assert norm.max_workers is None


def test_missing_and_empty_heartbeat_uses_defaults():
    assert normalize_heartbeat({}).active_process_count == 0
    assert normalize_heartbeat({}).configured_capacity is None
    assert normalize_heartbeat({}).admission_rejections == {}
    assert normalize_heartbeat(None).active_process_count == 0
    assert normalize_heartbeat(None).pool_size == 0


def test_string_and_invalid_capacity_values():
    norm = normalize_heartbeat(
        {
            "active_process_count": "2",
            "configured_capacity": "oops",
            "max_workers": "4",
            "idle_count": "1",
            "available_slots": "n/a",
            "saturation_ratio": "0.5",
            "admission": {"rejections": {"timeout": "3", "bad": "n/a"}},
        }
    )

    assert norm.active_process_count == 2
    assert norm.configured_capacity == 4
    assert norm.max_workers == 4
    assert norm.idle_count == 1
    assert norm.available_slots is None
    assert norm.saturation_ratio == 0.5
    assert norm.admission_rejections == {"timeout": 3}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (" 2 ", 2), ("2.0", 2), (2.0, 2), ("2.5", 0), ("", 0), ("nan", 0), (True, 0)],
)
def test_legacy_process_counts_accept_only_integral_numbers(value, expected):
    norm = normalize_heartbeat({"pool_size": value})

    assert norm.active_process_count == expected
    assert norm.pool_size == expected


@pytest.mark.parametrize(
    "ratio",
    ["NaN", "Infinity", "-Infinity", float("nan"), float("inf"), float("-inf")],
)
def test_nonfinite_saturation_is_unknown_and_json_serializable(ratio):
    norm = normalize_heartbeat({"saturation_ratio": ratio})

    assert norm.saturation_ratio is None
    assert json.loads(json.dumps(asdict(norm), allow_nan=False))["saturation_ratio"] is None


@pytest.mark.parametrize("value", [-1, -1.0, "-1", " -1.0 "])
def test_negative_counts_and_capacities_are_unknown_or_defaulted(value):
    fields = (
        "active_process_count", "pool_size", "configured_capacity", "max_workers",
        "idle_count", "busy_count", "available_slots", "requirements_installed",
        "requirements_total",
    )
    payload = {field: value for field in fields}
    payload["admission"] = {"rejections": {"invalid": value, "valid": 0}}
    norm = normalize_heartbeat(payload)

    assert norm.active_process_count == norm.pool_size == 0
    assert norm.idle_count == norm.busy_count == 0
    assert norm.configured_capacity is None
    assert norm.max_workers is None
    assert norm.available_slots is None
    assert norm.requirements_installed is None
    assert norm.requirements_total is None
    assert norm.admission_rejections == {"valid": 0}


def test_negative_modern_fields_use_valid_legacy_fallbacks():
    norm = normalize_heartbeat({
        "active_process_count": -1, "pool_size": 2,
        "configured_capacity": "-3", "max_workers": 0,
    })

    assert norm.active_process_count == norm.pool_size == 2
    assert norm.configured_capacity == norm.max_workers == 0
