"""Synthetic codec inputs only: no app, authority, consumers or live proof."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from scripts import agent_reference_contract as contract

LANE = "1" * 32
CASE = "2" * 32
NONCE = "3" * 32
FINISH = "4" * 32
UUID = "11111111-2222-3333-4444-555555555555"
FAMILY_CAPS = {
    "receipt": 8192,
    "ack": 1024,
    "status": 2048,
    "arm": 4096,
    "bind": 4096,
    "close": 4096,
    "control_ack": 4096,
    "readback": 65536,
    "finish_request": 4096,
    "host_status": 4096,
    "error": 1024,
}


def _counters(receipts=3, requests=1):
    return {
        "receipts_seen": receipts,
        "queued": receipts,
        "enqueue_rejected": 0,
        "acknowledged": receipts,
        "send_failed": 0,
        "queue_pending": 0,
        "sending": 0,
        "requests_started": requests,
        "requests_finished": requests,
        "requests_in_flight": 0,
        "seq_allocated": min(receipts, 128),
        "last_ack_seq": min(receipts, 128),
        "capacity_rejected": 0,
    }


def _receipt(kind="sdk_request"):
    payload = {
        "started_ns": "99",
        "method": "POST",
        "path": "/api/sdk/integrations/get",
        "query_present": False,
        "request": {
            "complete": True,
            "bytes": 128,
            "reason": None,
            "value": {
                "name": "Cove Data Protection",
                "scope": "global",
                "solution": UUID,
            },
        },
        "verification": {
            "outcome": "verified",
            "reason": None,
            "claims": {
                "sub": UUID,
                "engine_execution_id": UUID,
                "engine_solution_id": UUID,
                "org_id": UUID,
                "delegated_user_id": UUID,
                "engine": True,
                "is_superuser": True,
                "engine_global_repo_access": True,
                "delegated_is_superuser": False,
                "delegated_is_provider_org": False,
                "delegated_is_external": False,
            },
        },
        "response": {
            "status": 200,
            "bytes": 123,
            "complete": True,
            "disconnect_seen": False,
            "app_exception": None,
        },
        "failures": [],
    }
    seq = 2
    if kind == "ready":
        payload, seq = {"startup_forwarded": True}, 1
    elif kind == "unexpected_sdk_path":
        payload.pop("path")
        payload.pop("request")
        payload["failures"] = ["unexpected_sdk_path"]
    elif kind == "failure":
        payload = {"phase": "transport", "code": "transport_failure"}
    elif kind == "closed":
        payload, seq = (
            {
                "shutdown_forwarded": True,
                "prior": _counters(2),
                "first_failure": None,
            },
            3,
        )
    return {
        "schema": "bifrost.agent-reference.sdk-observation/v1",
        "lane_id": LANE,
        "role": "api",
        "nonce": NONCE,
        "seq": seq,
        "mono_ns": "100",
        "kind": kind,
        "payload": payload,
    }


def _case_state():
    return {
        "case_id": CASE,
        "state": "bound",
        "mode": "nominal-capacity",
        "solution_id": UUID,
        "deployment_id": UUID,
        "agent_id": UUID,
        "run_id": UUID,
        "finish_id": None,
        "request_count": 3,
        "sdk_receipt_count": 1,
        "first_failure": None,
    }


def _entry(index=7):
    return {
        "index": index,
        "received_ns": "0",
        "case_id": CASE,
        "association": "active-arm-pending-db",
        "receipt": _receipt(),
    }


def _samples():
    return {
        "receipt": _receipt(),
        "ack": {
            "schema": "bifrost.agent-reference.observer-ack/v1",
            "lane_id": LANE,
            "role": "api",
            "nonce": NONCE,
            "seq": 2,
            "accepted": True,
            "case_id": CASE,
        },
        "status": {
            "schema": "bifrost.agent-reference.observer-status/v1",
            "lane_id": LANE,
            "role": "api",
            "nonce": NONCE,
            "generation": 5,
            "mono_ns": "100",
            "phase": "closed",
            "counters": _counters(),
            "upstream": {"startup_forwarded": True, "shutdown_forwarded": True},
            "ready_acked": True,
            "closed_acked": True,
            "first_failure": None,
        },
        "arm": {
            "schema": "bifrost.agent-reference.case-arm/v1",
            "lane_id": LANE,
            "case_id": CASE,
            "mode": "nominal-capacity",
            "solution_id": UUID,
            "deployment_id": UUID,
            "agent_id": UUID,
            "declaration_sha256": "b79b5064f375098dabc119083cf2ff16778b683722b5df394f6cb8a06ca22914",
        },
        "bind": {
            "schema": "bifrost.agent-reference.case-bind/v1",
            "lane_id": LANE,
            "case_id": CASE,
            "run_id": UUID,
        },
        "close": {
            "schema": "bifrost.agent-reference.case-close/v1",
            "lane_id": LANE,
            "case_id": CASE,
            "run_id": UUID,
            "finish_id": FINISH,
            "disposition": "finish",
        },
        "control_ack": {
            "schema": "bifrost.agent-reference.case-control-ack/v1",
            "lane_id": LANE,
            "case_id": CASE,
            "operation": "bind",
            "state": "bound",
            "run_id": UUID,
            "finish_id": None,
            "first_failure": None,
        },
        "readback": {
            "schema": "bifrost.agent-reference.observer-readback/v1",
            "lane_id": LANE,
            "scope": "case",
            "case_id": CASE,
            "offset": 0,
            "next_cursor": None,
            "total": 1,
            "records": [_entry()],
            "case": _case_state(),
            "lane": {
                "case_count": 1,
                "active_case_id": CASE,
                "roles_ready": ["api", "api-replica"],
                "roles_closed": [],
                "first_failure": None,
            },
        },
        "finish_request": {
            "schema": "bifrost.agent-reference.finish-request/v1",
            "lane_id": LANE,
            "case_id": CASE,
            "run_id": UUID,
            "finish_id": FINISH,
            "disposition": "finish",
            "state": "closing",
            "roles": [
                {"role": role, "nonce": NONCE} for role in ("api", "api-replica")
            ],
        },
        "host_status": {
            "schema": "bifrost.agent-reference.host-status/v1",
            "lane_id": LANE,
            "runner_container_id": "9" * 64,
            "case_id": CASE,
            "run_id": UUID,
            "finish_id": FINISH,
            "generation": 4,
            "mono_ns": "100",
            "phase": "exited",
            "roles": [
                {
                    "role": role,
                    "container_id": digit * 64,
                    "image_id": "sha256:" + "c" * 64,
                    "observer_nonce": NONCE,
                    "stop_requested": True,
                    "exit_observed": True,
                    "exit_code": 143,
                }
                for role, digit in (("api", "a"), ("api-replica", "b"))
            ],
            "first_failure": None,
            "failure_stage": None,
        },
        "error": {
            "schema": "bifrost.agent-reference.private-error/v1",
            "error": "invalid_json",
        },
    }


def _wire(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _invalid(call, code="invalid_schema"):
    with pytest.raises(contract.ContractError) as caught:
        call()
    error = caught.value
    assert error.code == code
    assert error.args == (code,)
    assert str(error) == code
    assert error.__cause__ is None
    assert error.__context__ is None


def _reject_value(family, value):
    _invalid(lambda: contract.validate_private(family, value))
    _invalid(lambda: contract.encode_private(family, value))
    _invalid(lambda: contract.decode_private(family, _wire(value)))


def _set_path(value, path, replacement):
    for name in path[:-1]:
        value = value[name]
    value[path[-1]] = replacement


def test_frozen_family_caps_and_literal_canonical_bytes():
    assert dict(contract.FAMILY_BYTE_CAPS) == FAMILY_CAPS
    expected = (
        b'{"error":"invalid_json","schema":"bifrost.agent-reference.private-error/v1"}'
    )
    assert contract.encode_private("error", _samples()["error"]) == expected
    assert contract.decode_private("error", expected) == _samples()["error"]


@pytest.mark.parametrize("family", FAMILY_CAPS)
def test_all_eleven_families_roundtrip_without_mutating_input(family):
    value = _samples()[family]
    original = deepcopy(value)
    expected = _wire(value)
    assert contract.validate_private(family, value) is None
    assert contract.encode_private(family, value) == expected
    assert contract.decode_private(family, expected) == value
    assert value == original
    assert len(expected) <= FAMILY_CAPS[family]


@pytest.mark.parametrize(
    "kind", ["ready", "sdk_request", "unexpected_sdk_path", "failure", "closed"]
)
def test_all_closed_receipt_variants(kind):
    value = _receipt(kind)
    assert (
        contract.decode_private("receipt", contract.encode_private("receipt", value))
        == value
    )


@pytest.mark.parametrize("family", FAMILY_CAPS)
@pytest.mark.parametrize("mutation", ["missing", "extra", "discriminator", "root_type"])
def test_each_family_rejects_key_discriminator_and_root_type_corruption(
    family, mutation
):
    value = _samples()[family]
    if mutation == "missing":
        value.pop("schema")
    elif mutation == "extra":
        value["raw_authorization"] = "DO-NOT-EXPORT-private-value"
    elif mutation == "discriminator":
        value["schema"] += "/unknown"
    else:
        value = [value]
    _reject_value(family, value)


@pytest.mark.parametrize("family", FAMILY_CAPS)
def test_byte_cap_precedes_json_parsing_for_every_family(family):
    _invalid(
        lambda: contract.decode_private(family, b"x" * (FAMILY_CAPS[family] + 1)),
        "capacity_exhausted",
    )
    _invalid(
        lambda: contract.decode_private(family, b"x" * FAMILY_CAPS[family]),
        "invalid_json",
    )


@pytest.mark.parametrize("raw", [None, "{}", bytearray(b"{}"), memoryview(b"{}"), 1])
def test_decoder_requires_exact_bytes(raw):
    _invalid(lambda: contract.decode_private("error", raw), "invalid_json")


@pytest.mark.parametrize("family", ["unknown", "Receipt", "récéipt", None, [], 1])
def test_unknown_family_has_static_schema_error(family):
    _invalid(lambda: contract.encode_private(family, {}))
    _invalid(lambda: contract.validate_private(family, {}))
    _invalid(lambda: contract.decode_private(family, b"{}"))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b'"DO-NOT-EXPORT-private-value',
        b"\xff",
        b'[{"x":1},]',
        b'{"x":01}',
        b'{"x":"\x00"}',
        b'[{"schema":"x"}] garbage',
    ],
)
def test_malformed_wire_errors_have_no_parser_chain_or_payload(raw):
    _invalid(lambda: contract.decode_private("error", raw), "invalid_json")


def test_deep_json_rejection_is_sanitized_and_bounded_by_family_cap():
    # Missing the outer close is invalid regardless of a process recursion limit.
    raw = b"[" * 1800 + b"0" + b"]" * 1799
    assert len(raw) < FAMILY_CAPS["readback"]
    _invalid(lambda: contract.decode_private("readback", raw), "invalid_json")


def test_duplicate_keys_at_root_and_nested_depth_are_invalid_json():
    raw = b'{"error":"invalid_json","error":"DO-NOT-EXPORT","schema":"x"}'
    _invalid(lambda: contract.decode_private("error", raw), "invalid_json")
    nested = _wire(_receipt()).replace(
        b'"scope":"global"',
        b'"scope":"global","scope":"global"',
    )
    _invalid(lambda: contract.decode_private("receipt", nested), "invalid_json")


@pytest.mark.parametrize(
    "mutation",
    ["newline", "space", "bom", "reverse_keys", "escaped_key", "negative_zero"],
)
def test_noncanonical_wire_is_not_coerced(mutation):
    family, raw = "error", _wire(_samples()["error"])
    if mutation == "newline":
        raw += b"\n"
    elif mutation == "space":
        raw = raw.replace(b":", b": ")
    elif mutation == "bom":
        raw = b"\xef\xbb\xbf" + raw
    elif mutation == "reverse_keys":
        raw = b'{"schema":"bifrost.agent-reference.private-error/v1","error":"invalid_json"}'
    elif mutation == "escaped_key":
        raw = raw.replace(b'"error"', b'"\\u0065rror"')
    else:
        family = "status"
        raw = _wire(_samples()[family]).replace(b'"generation":5', b'"generation":-0')
    _invalid(lambda: contract.decode_private(family, raw), "invalid_json")


@pytest.mark.parametrize(
    "literal",
    ["1.0", "1e0", "1E+0", "-0.0", "2.5", "1e999", "NaN", "Infinity", "-Infinity"],
)
def test_all_float_exponent_and_nonfinite_literals_are_invalid_json(literal):
    raw = _wire(_receipt()).replace(b'"seq":2', b'"seq":' + literal.encode())
    _invalid(lambda: contract.decode_private("receipt", raw), "invalid_json")


@pytest.mark.parametrize(
    "unsupported",
    [1.0, -0.0, float("nan"), float("inf"), float("-inf"), b"x", (1,), {1}, object()],
)
def test_encoder_unsupported_python_values_are_static_schema_errors(unsupported):
    value = _receipt()
    value["seq"] = unsupported
    _invalid(lambda: contract.encode_private("receipt", value))
    _invalid(lambda: contract.validate_private("receipt", value))


@pytest.mark.parametrize(
    "family,path,replacement",
    [
        ("ack", ("accepted",), 1),
        ("receipt", ("seq",), True),
        ("receipt", ("seq",), 0),
        ("receipt", ("seq",), 129),
        ("receipt", ("lane_id",), "A" * 32),
        ("receipt", ("mono_ns",), "01"),
        ("receipt", ("mono_ns",), "9223372036854775808"),
        ("receipt", ("payload", "started_ns"), "101"),
        ("receipt", ("payload", "query_present"), 0),
        ("receipt", ("payload", "verification", "claims", "engine"), 1),
        (
            "receipt",
            ("payload", "verification", "claims", "sub"),
            "ABCDEFAB-2222-3333-4444-555555555555",
        ),
        ("receipt", ("payload", "request", "bytes"), 4098),
        ("receipt", ("payload", "response", "status"), 99),
        ("receipt", ("payload", "response", "bytes"), 65538),
        ("receipt", ("payload", "request", "value", "scope"), "org"),
        ("arm", ("declaration_sha256",), "0" * 64),
        ("arm", ("mode",), "negative"),
        ("bind", ("run_id",), None),
        ("close", ("run_id",), None),
        ("close", ("finish_id",), "0" * 31),
        ("host_status", ("roles", 0, "image_id"), "c" * 64),
        ("host_status", ("roles", 0, "container_id"), "sha256:" + "a" * 64),
        ("host_status", ("roles", 0, "exit_code"), True),
    ],
)
def test_strict_types_bounds_and_fixed_values(family, path, replacement):
    value = _samples()[family]
    _set_path(value, path, replacement)
    _reject_value(family, value)


@pytest.mark.parametrize(
    "mutation",
    [
        "reason_on_verified",
        "no_claims",
        "unverified_with_claims",
        "body_missing",
        "body_overflow",
        "failed_body_retained",
    ],
)
def test_verification_and_body_conditionals(mutation):
    value = _receipt()
    payload = value["payload"]
    if mutation == "reason_on_verified":
        payload["verification"]["reason"] = "jwt_rejected"
    elif mutation == "no_claims":
        payload["verification"]["claims"] = None
    elif mutation == "unverified_with_claims":
        payload["verification"].update(outcome="unverified", reason="jwt_rejected")
    elif mutation == "body_missing":
        payload["request"]["value"] = None
    elif mutation == "body_overflow":
        payload["request"]["bytes"] = 4097
    else:
        payload["request"]["reason"] = "body_invalid"
    _reject_value("receipt", value)


def test_safe_failed_sdk_witness_is_valid_diagnostic_not_authority():
    value = _receipt()
    payload = value["payload"]
    payload["verification"] = {
        "outcome": "unverified",
        "reason": "jwt_rejected",
        "claims": None,
    }
    payload["request"] = {
        "complete": False,
        "bytes": 1,
        "value": None,
        "reason": "body_incomplete",
    }
    payload["response"].update(
        complete=False, disconnect_seen=True, app_exception="cancelled"
    )
    payload["failures"] = [
        "jwt_rejected",
        "body_incomplete",
        "app_exception",
        "disconnect_seen",
        "response_incomplete",
    ]
    assert (
        contract.decode_private("receipt", contract.encode_private("receipt", value))
        == value
    )


@pytest.mark.parametrize(
    "failures",
    [
        ["jwt_rejected", "jwt_rejected"],
        ["query_present", "method_unexpected"],
        ["unknown"],
    ],
)
def test_failure_codes_are_unique_closed_and_in_declaration_order(failures):
    value = _receipt()
    if failures[0] == "jwt_rejected":
        value["payload"]["verification"] = {
            "outcome": "unverified",
            "reason": "jwt_rejected",
            "claims": None,
        }
    elif failures[0] == "query_present":
        value["payload"].update(method="OTHER", query_present=True)
    value["payload"]["failures"] = failures
    _reject_value("receipt", value)


@pytest.mark.parametrize(
    "code",
    [
        "auth_missing",
        "auth_duplicate",
        "auth_oversized",
        "auth_malformed",
        "jwt_rejected",
        "claims_schema",
        "body_incomplete",
        "body_oversized",
        "body_invalid",
        "method_unexpected",
        "query_present",
        "app_exception",
        "disconnect_seen",
        "response_incomplete",
        "response_oversized",
        "unexpected_sdk_path",
    ],
)
def test_source_determinable_failure_cannot_contradict_retained_fields(code):
    value = _receipt()
    value["payload"]["failures"] = [code]
    _reject_value("receipt", value)


def test_selected_single_reason_cannot_invent_another_auth_or_body_cause():
    value = _receipt()
    value["payload"]["verification"] = {
        "outcome": "unverified",
        "reason": "jwt_rejected",
        "claims": None,
    }
    value["payload"]["request"].update(reason="body_invalid", value=None)
    value["payload"]["failures"] = ["jwt_rejected", "body_invalid"]
    assert (
        contract.decode_private("receipt", contract.encode_private("receipt", value))
        == value
    )
    value["payload"]["failures"] = ["auth_missing", "jwt_rejected", "body_invalid"]
    _reject_value("receipt", value)
    value["payload"]["failures"] = ["jwt_rejected", "body_oversized", "body_invalid"]
    _reject_value("receipt", value)


def test_unknown_path_does_not_invent_exclusion_for_absent_body_witness():
    value = _receipt("unexpected_sdk_path")
    value["payload"]["failures"] = [
        "body_invalid",
        "unexpected_sdk_path",
        "observer_exception",
    ]
    assert (
        contract.decode_private("receipt", contract.encode_private("receipt", value))
        == value
    )


def test_detected_method_query_and_unknown_path_cannot_omit_failures():
    for mutation in ("method", "query", "unknown_path"):
        value = _receipt(
            "unexpected_sdk_path" if mutation == "unknown_path" else "sdk_request"
        )
        if mutation == "method":
            value["payload"]["method"] = "OTHER"
        elif mutation == "query":
            value["payload"]["query_present"] = True
        else:
            value["payload"]["failures"] = []
        _reject_value("receipt", value)


@pytest.mark.parametrize(
    "counter,replacement",
    [
        ("receipts_seen", 4),
        ("queued", 2),
        ("requests_started", 2),
        ("seq_allocated", 2),
        ("capacity_rejected", 1),
        ("last_ack_seq", 4),
    ],
)
@pytest.mark.parametrize("failed", [False, True])
def test_unsaturated_counter_corruption_rejected_even_in_failed_reports(
    counter, replacement, failed
):
    value = _samples()["status"]
    if failed:
        value["first_failure"] = "transport_failure"
    value["counters"][counter] = replacement
    _reject_value("status", value)


def test_saturation_preserves_earliest_failure_and_only_exempts_relevant_equation():
    value = _samples()["status"]
    value["first_failure"] = "transport_failure"
    value["counters"]["requests_started"] = 2147483647
    assert (
        contract.decode_private("status", contract.encode_private("status", value))
        == value
    )
    value["first_failure"] = None
    _reject_value("status", value)


def test_starting_pending_send_and_failed_enqueue_snapshots_keep_counter_equations():
    value = _samples()["status"]
    value.update(phase="starting", generation=0, ready_acked=False, closed_acked=False)
    value["upstream"] = {"startup_forwarded": False, "shutdown_forwarded": False}
    value["counters"] = _counters(0, 0)
    assert (
        contract.decode_private("status", contract.encode_private("status", value))
        == value
    )
    value.update(phase="active", ready_acked=True)
    value["upstream"]["startup_forwarded"] = True
    value["counters"] = _counters(2, 1)
    value["counters"].update(acknowledged=1, sending=1, last_ack_seq=1)
    assert (
        contract.decode_private("status", contract.encode_private("status", value))
        == value
    )
    value["first_failure"] = "queue_exhausted"
    value["counters"].update(
        receipts_seen=3,
        enqueue_rejected=1,
        capacity_rejected=1,
        seq_allocated=3,
        requests_started=2,
        requests_finished=2,
    )
    assert (
        contract.decode_private("status", contract.encode_private("status", value))
        == value
    )


@pytest.mark.parametrize(
    "counter,replacement",
    [
        ("receipts_seen", 0),
        ("queued", 1),
        ("capacity_rejected", 1),
        ("last_ack_seq", 128),
        ("seq_allocated", 0),
    ],
)
def test_request_saturation_never_waives_receipt_or_sequence_invariants(
    counter, replacement
):
    value = _samples()["status"]
    value["first_failure"] = "observer_exception"
    value["counters"]["requests_started"] = 2147483647
    value["counters"][counter] = replacement
    _reject_value("status", value)


def test_receipt_saturation_still_enforces_sequence_min_and_request_equation():
    value = _samples()["status"]
    value["first_failure"] = "transport_failure"
    value["counters"]["receipts_seen"] = 2147483647
    value["counters"]["seq_allocated"] = 128
    assert (
        contract.decode_private("status", contract.encode_private("status", value))
        == value
    )
    value["counters"]["requests_started"] = 2
    _reject_value("status", value)


@pytest.mark.parametrize("family", ["status", "host_status"])
def test_generation_saturation_cannot_claim_success(family):
    value = _samples()[family]
    value["generation"] = 2147483647
    _reject_value(family, value)
    value["first_failure"] = "transport_failure"
    if family == "host_status":
        value.update(phase="failed", failure_stage="api-exit")
    assert (
        contract.decode_private(family, contract.encode_private(family, value)) == value
    )


def test_failed_closed_diagnostics_need_equations_but_not_success_flags():
    status = _samples()["status"]
    status.update(first_failure="shutdown_incomplete", closed_acked=False)
    status["upstream"]["shutdown_forwarded"] = False
    status["counters"].update(acknowledged=2, queue_pending=1)
    assert (
        contract.decode_private("status", contract.encode_private("status", status))
        == status
    )
    receipt = _receipt("closed")
    receipt["payload"].update(
        first_failure="shutdown_incomplete", shutdown_forwarded=False
    )
    receipt["payload"]["prior"].update(acknowledged=1, queue_pending=1)
    assert (
        contract.decode_private("receipt", contract.encode_private("receipt", receipt))
        == receipt
    )
    receipt["payload"]["prior"]["queued"] = 3
    _reject_value("receipt", receipt)


@pytest.mark.parametrize(
    "family,mutation",
    [
        ("status", "missing_ack"),
        ("status", "missing_upstream"),
        ("status", "sdk_count_parity"),
        ("receipt", "bad_prior_sequence"),
        ("receipt", "pending_prior"),
        ("receipt", "false_shutdown"),
    ],
)
def test_successful_closed_claims_require_local_drain_and_count_rules(family, mutation):
    value = _samples()[family] if family == "status" else _receipt("closed")
    if mutation == "missing_ack":
        value["closed_acked"] = False
    elif mutation == "missing_upstream":
        value["upstream"]["startup_forwarded"] = False
    elif mutation == "sdk_count_parity":
        value["counters"].update(requests_started=0, requests_finished=0)
    elif mutation == "bad_prior_sequence":
        value["seq"] = 4
    elif mutation == "pending_prior":
        value["payload"]["prior"].update(acknowledged=1, queue_pending=1)
    else:
        value["payload"]["shutdown_forwarded"] = False
    _reject_value(family, value)


def test_filtered_case_indices_preserve_global_holes_and_current_tail():
    value = _samples()["readback"]
    value.update(total=2, records=[_entry(7), _entry(12)])
    assert (
        contract.decode_private("readback", contract.encode_private("readback", value))
        == value
    )
    value.update(offset=2, records=[])
    assert (
        contract.decode_private("readback", contract.encode_private("readback", value))
        == value
    )


def test_lane_indices_equal_unfiltered_page_offset_and_provisional_cursor():
    value = _samples()["readback"]
    value.update(scope="lane", case_id=None, case=None, total=5, next_cursor=4)
    value["records"] = [_entry(i) for i in range(4)]
    assert (
        contract.decode_private("readback", contract.encode_private("readback", value))
        == value
    )
    value.update(offset=4, total=5, next_cursor=None, records=[_entry(4)])
    assert (
        contract.decode_private("readback", contract.encode_private("readback", value))
        == value
    )
    value.update(offset=0, total=1, records=[_entry(255)])
    _reject_value("readback", value)


@pytest.mark.parametrize(
    "mutation",
    [
        "offset",
        "cursor",
        "length",
        "scope",
        "case_match",
        "lane_match",
        "duplicate_index",
        "roles_order",
    ],
)
def test_readback_corruption_rejected_without_normalizing_indices(mutation):
    value = _samples()["readback"]
    if mutation == "offset":
        value["offset"] = 2
    elif mutation == "cursor":
        value["next_cursor"] = 1
    elif mutation == "length":
        value["records"] = []
    elif mutation == "scope":
        value["scope"] = "lane"
    elif mutation == "case_match":
        value["case"]["case_id"] = "8" * 32
    elif mutation == "lane_match":
        value["records"][0]["receipt"]["lane_id"] = "8" * 32
    elif mutation == "duplicate_index":
        value.update(total=2, records=[_entry(7), _entry(7)])
    else:
        value["lane"]["roles_ready"].reverse()
    _reject_value("readback", value)


@pytest.mark.parametrize("diagnostic", [False, True])
def test_case_sdk_records_cannot_exceed_total_request_count(diagnostic):
    value = _samples()["readback"]
    value["case"].update(request_count=0, sdk_receipt_count=1)
    if diagnostic:
        value["case"].update(
            request_count=32, sdk_receipt_count=33, first_failure="collector_capacity"
        )
    _reject_value("readback", value)


def test_failed_case_capacity_markers_are_valid_only_with_truthful_local_bounds():
    value = _samples()["readback"]
    value["case"].update(
        request_count=65,
        sdk_receipt_count=33,
        first_failure="collector_capacity",
    )
    assert (
        contract.decode_private("readback", contract.encode_private("readback", value))
        == value
    )
    value["case"]["first_failure"] = None
    _reject_value("readback", value)


@pytest.mark.parametrize("mutation", ["active_without_case", "closed_before_ready"])
def test_lane_state_cannot_claim_locally_impossible_registration_snapshot(mutation):
    value = _samples()["readback"]
    if mutation == "active_without_case":
        value["lane"]["case_count"] = 0
    else:
        value["lane"].update(roles_ready=["api"], roles_closed=["api-replica"])
    _reject_value("readback", value)


@pytest.mark.parametrize(
    "kind", ["ready", "sdk_request", "unexpected_sdk_path", "failure", "closed"]
)
def test_every_receipt_payload_rejects_unknown_or_secret_fields(kind):
    value = _receipt(kind)
    value["payload"]["raw_token"] = "DO-NOT-EXPORT-private-value"
    _reject_value("receipt", value)


@pytest.mark.parametrize(
    "family,path",
    [
        ("receipt", ("payload", "request")),
        ("receipt", ("payload", "request", "value")),
        ("receipt", ("payload", "verification")),
        ("receipt", ("payload", "verification", "claims")),
        ("receipt", ("payload", "response")),
        ("status", ("counters",)),
        ("status", ("upstream",)),
        ("readback", ("records", 0)),
        ("readback", ("case",)),
        ("readback", ("lane",)),
        ("finish_request", ("roles", 0)),
        ("host_status", ("roles", 0)),
    ],
)
def test_nested_objects_reject_omitted_and_extra_keys(family, path):
    value = _samples()[family]
    target = value
    for name in path:
        target = target[name]
    removed = next(iter(target))
    original = target.pop(removed)
    _reject_value(family, value)
    target[removed] = original
    target["raw_secret"] = "DO-NOT-EXPORT-private-value"
    _reject_value(family, value)


def test_contract_error_constructor_never_keeps_an_unknown_value():
    error = contract.ContractError("DO-NOT-EXPORT-private-value")
    assert error.code == "invalid_schema"
    assert error.args == ("invalid_schema",)
    assert str(error) == "invalid_schema"


@pytest.mark.parametrize("operation", ["arm", "bind", "close"])
def test_control_ack_operation_has_closed_state_fields(operation):
    value = _samples()["control_ack"]
    if operation == "arm":
        value.update(operation="arm", state="armed", run_id=None)
    elif operation == "close":
        value.update(operation="close", state="closing", finish_id=FINISH)
    assert (
        contract.decode_private(
            "control_ack", contract.encode_private("control_ack", value)
        )
        == value
    )
    value["state"] = "closed"
    _reject_value("control_ack", value)


def test_abort_without_bound_run_is_diagnostic_and_not_finish():
    value = _samples()["close"]
    value.update(disposition="abort", run_id=None)
    assert (
        contract.decode_private("close", contract.encode_private("close", value))
        == value
    )
    value["disposition"] = "finish"
    _reject_value("close", value)


@pytest.mark.parametrize("state", [None, "armed", "bound", "closing", "closed"])
def test_finish_request_stateless_lifecycle_shapes(state):
    value = _samples()["finish_request"]
    value["state"] = state
    if state is None:
        value.update(case_id=None, run_id=None, finish_id=None, disposition=None)
    elif state in ("armed", "bound"):
        value.update(finish_id=None, disposition=None)
        if state == "armed":
            value["run_id"] = None
    assert (
        contract.decode_private(
            "finish_request", contract.encode_private("finish_request", value)
        )
        == value
    )
    if state in ("closing", "closed"):
        value["roles"].pop()
    else:
        value["finish_id"] = FINISH
    _reject_value("finish_request", value)


@pytest.mark.parametrize(
    "mutation",
    [
        "false_exit",
        "false_stop",
        "missing_code",
        "kill_code",
        "role_duplicate",
        "no_finish",
        "false_failure",
    ],
)
def test_host_exit_claims_require_exact_flags_roles_and_failure_fields(mutation):
    value = _samples()["host_status"]
    if mutation == "false_exit":
        value["roles"][0]["exit_observed"] = False
    elif mutation == "false_stop":
        value["roles"][0]["stop_requested"] = False
    elif mutation == "missing_code":
        value["roles"][0]["exit_code"] = None
    elif mutation == "kill_code":
        value["roles"][0]["exit_code"] = 137
    elif mutation == "role_duplicate":
        value["roles"][1]["role"] = "api"
    elif mutation == "no_finish":
        value["finish_id"] = None
    else:
        value["first_failure"] = "shutdown_incomplete"
    _reject_value("host_status", value)


def test_host_failed_exit_is_safe_diagnostic_not_success():
    value = _samples()["host_status"]
    value.update(
        phase="failed", first_failure="shutdown_incomplete", failure_stage="api-exit"
    )
    value["roles"][0]["exit_code"] = 137
    assert (
        contract.decode_private(
            "host_status", contract.encode_private("host_status", value)
        )
        == value
    )
    value["failure_stage"] = None
    _reject_value("host_status", value)


def test_host_watching_and_requested_stop_are_distinct_from_observed_exit():
    value = _samples()["host_status"]
    value.update(
        phase="watching", generation=0, case_id=None, run_id=None, finish_id=None
    )
    for role in value["roles"]:
        role.update(
            observer_nonce=None,
            stop_requested=False,
            exit_observed=False,
            exit_code=None,
        )
    assert (
        contract.decode_private(
            "host_status", contract.encode_private("host_status", value)
        )
        == value
    )
    value.update(phase="stop_requested", case_id=CASE, run_id=UUID, finish_id=FINISH)
    for role in value["roles"]:
        role.update(observer_nonce=NONCE, stop_requested=True)
    assert (
        contract.decode_private(
            "host_status", contract.encode_private("host_status", value)
        )
        == value
    )
    value["phase"] = "exited"
    _reject_value("host_status", value)
