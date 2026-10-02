"""Additive model evidence validation; these synthetic inputs prove no custody."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from scripts import agent_reference_contract as wire

LANE = "1" * 32
CASE = "2" * 32
RUN = "11111111-2222-3333-4444-555555555555"


def _input():
    return {
        "schema": "bifrost.agent-reference.model-oracle-input/v1",
        "lane_id": LANE,
        "case_id": CASE,
        "model_key": "3" * 64,
        "password": "4" * 64,
        "visa": "5" * 64,
        "partner_name": "C1R Synthetic Partner",
        "username": "c1r-reference@example.invalid",
    }


def _content():
    return {
        "success": True,
        "observed_at": "2026-10-02T01:02:03.456789+00:00",
        "capacity": {
            "success": True,
            "read_only": True,
            "agent_count": 0,
            "restore_row_count": 0,
            "active_restore_count": 0,
            "active_restore_count_by_agent": {},
            "agents": [],
            "active_restores": [],
            "recent_restores": [],
        },
    }


def _record(index=0):
    return {
        "index": index,
        "kind": "agent_final",
        "settled": True,
        "matched": True,
        "status": 200,
        "write_complete": True,
        "tool_content": json.dumps(_content()),
    }


def _readback():
    return {
        "schema": "bifrost.agent-reference.model-oracle-readback/v1",
        "lane_id": LANE,
        "case_id": CASE,
        "run_id": RUN,
        "finish_id": None,
        "state": "bound",
        "first_failure": None,
        "attempt_count": 2,
        "offset": 0,
        "total": 1,
        "next_offset": None,
        "records": [_record()],
    }


def _reject(family, value):
    original = deepcopy(value)
    raw = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()
    for call in (
        lambda: wire.validate_private(family, value),
        lambda: wire.encode_private(family, value),
        lambda: wire.decode_private(family, raw),
    ):
        with pytest.raises(wire.ContractError) as caught:
            call()
        assert caught.value.args == ("invalid_schema",)
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None
    assert value == original


@pytest.mark.parametrize(
    "family,sample,cap",
    [
        ("model_input", _input, 2048),
        ("model_readback", _readback, 16384),
    ],
)
def test_new_families_are_canonical_closed_and_capped(family, sample, cap):
    value = sample()
    original = deepcopy(value)
    encoded = wire.encode_private(family, value)
    assert wire.FAMILY_BYTE_CAPS[family] == cap
    assert (
        encoded
        == json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    assert wire.decode_private(family, encoded) == value == original
    for malformed in (encoded + b"\n", b"{}", b'{"lane_id":1,"lane_id":2}'):
        with pytest.raises(wire.ContractError):
            wire.decode_private(family, malformed)
    with pytest.raises(wire.ContractError, match="capacity_exhausted"):
        wire.decode_private(family, b"x" * (cap + 1))
    value["unexpected"] = "do not retain this secret"
    _reject(family, value)


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("schema", "bifrost.agent-reference.model-oracle-input/v2"),
        ("lane_id", "A" * 32),
        ("case_id", True),
        ("model_key", "3" * 63),
        ("password", "3" * 64),
        ("visa", "4" * 64),
        ("partner_name", "other"),
        ("username", "other@example.invalid"),
    ],
)
def test_input_rejects_authority_bags_aliases_and_reused_synthetic_secrets(
    field, replacement
):
    value = _input()
    value[field] = replacement
    _reject("model_input", value)


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("attempt_count", True),
        ("attempt_count", 0),
        ("attempt_count", 66),
        ("offset", 2),
        ("next_offset", 1),
        ("total", 2),
        ("run_id", None),
        ("finish_id", "6" * 32),
    ],
)
def test_readback_rejects_counter_pagination_and_state_drift(field, replacement):
    value = _readback()
    value[field] = replacement
    _reject("model_readback", value)


@pytest.mark.parametrize(
    "replacement",
    [
        {"index": 1},
        {"index": True},
        {"status": True},
        {"status": 600},
        {"matched": None},
        {"matched": False},
        {"write_complete": False},
        {"tool_content": None},
        {"kind": "summary"},
        {"settled": False},
        {"kind": "unexpected"},
        {"kind": "unknown"},
    ],
)
def test_record_rejects_premature_success_unmatched_content_and_emission_drift(
    replacement,
):
    value = _readback()
    value["records"][0].update(replacement)
    _reject("model_readback", value)


def test_pending_slot_is_visible_without_fabricated_evidence():
    value = _readback()
    value["records"][0].update(
        settled=False,
        matched=None,
        status=None,
        write_complete=False,
        tool_content=None,
    )
    wire.validate_private("model_readback", value)
    value.update(state="closed", finish_id="6" * 32)
    _reject("model_readback", value)
    value["first_failure"] = "transport_failure"
    wire.validate_private("model_readback", value)


def test_partial_write_preserves_matching_tool_evidence_but_latches_failure():
    value = _readback()
    value["records"][0].update(status=None, write_complete=False)
    _reject("model_readback", value)
    value["first_failure"] = "transport_failure"
    wire.validate_private("model_readback", value)


def test_rejected_request_is_retained_as_settled_failed_slot():
    value = _readback()
    value["records"][0].update(
        kind="unexpected", matched=False, tool_content=None, status=400
    )
    _reject("model_readback", value)
    value["first_failure"] = "correlation_failed"
    wire.validate_private("model_readback", value)


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("success", False),
        ("observed_at", "2026-02-30T01:02:03+00:00"),
        ("observed_at", "2026-10-02T01:02:03Z"),
        ("observed_at", "2026-10-02T01:02:03.1+00:00"),
        ("observed_at", "2026-10-02T01:02:03+01:00"),
        ("observed_at", "2026-10-02T25:02:03+00:00"),
        ("observed_at", "0000-10-02T01:02:03+00:00"),
    ],
)
def test_selected_tool_content_rejects_false_result_and_timestamp_drift(
    field, replacement
):
    content = _content()
    content[field] = replacement
    value = _readback()
    value["records"][0]["tool_content"] = json.dumps(content)
    _reject("model_readback", value)


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("success", False),
        ("read_only", 1),
        ("agent_count", False),
        ("restore_row_count", 1),
        ("active_restore_count", 0.0),
        ("active_restore_count_by_agent", {"7": 1}),
        ("agents", [{}]),
        ("active_restores", {}),
        ("recent_restores", ["secret"]),
    ],
)
def test_tool_content_cannot_smuggle_arbitrary_results(field, replacement):
    content = _content()
    content["capacity"][field] = replacement
    value = _readback()
    value["records"][0]["tool_content"] = json.dumps(content)
    _reject("model_readback", value)


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '{"success":true,"success":false}',
        '{"success": NaN}',
        "[" * 1000 + "]" * 1000,
        "x" * 2049,
        "\u00e9",
        '"Error: unexpected credential"',
    ],
)
def test_bad_embedded_json_is_value_free_and_not_retained_in_errors(content):
    value = _readback()
    value["records"][0]["tool_content"] = content
    _reject("model_readback", value)


def test_otherwise_valid_duplicate_key_cannot_hide_behind_structural_rejection():
    valid = _content()
    duplicated = json.dumps(valid).replace(
        '"agent_count": 0', '"agent_count": 0, "agent_count": 0'
    )
    # A permissive decoder produces the valid shape, so duplicate rejection is decisive.
    assert json.loads(duplicated) == valid
    value = _readback()
    value["records"][0]["tool_content"] = duplicated
    _reject("model_readback", value)


def test_embedded_decoder_recursion_is_static_without_parser_exception_context(
    monkeypatch,
):
    value = _readback()
    content = value["records"][0]["tool_content"]
    original = json.loads

    def decoder(text, *args, **kwargs):
        if text == content:
            raise RecursionError("must not retain this payload")
        return original(text, *args, **kwargs)

    monkeypatch.setattr(wire.json, "loads", decoder)
    _reject("model_readback", value)


def test_residual_sdk_attempts_cannot_exceed_original_capacity_without_failure():
    value = _readback()
    value.update(total=0, records=[], attempt_count=32)
    wire.validate_private("model_readback", value)
    value["attempt_count"] = 33
    _reject("model_readback", value)
    value["first_failure"] = "collector_capacity"
    wire.validate_private("model_readback", value)


def test_paginated_final_tail_and_diagnostic_overflow():
    value = _readback()
    value.update(
        total=64, offset=60, attempt_count=64, state="closed", finish_id="6" * 32
    )
    value["records"] = [_record(i) for i in range(60, 64)]
    wire.validate_private("model_readback", value)
    value["attempt_count"] = 65
    _reject("model_readback", value)
    value["first_failure"] = "collector_capacity"
    wire.validate_private("model_readback", value)
    value["records"][-1]["index"] = 64
    _reject("model_readback", value)


def test_empty_current_tail_and_armed_state_do_not_claim_closure():
    value = _readback()
    value.update(
        state="armed", run_id=None, total=0, offset=0, records=[], attempt_count=0
    )
    wire.validate_private("model_readback", value)
    value["finish_id"] = "6" * 32
    _reject("model_readback", value)
