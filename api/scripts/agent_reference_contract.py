"""Pure, test-only C1-R private wire codec; validation is never live proof.

The architect-owned observation wire and additive model-oracle contract freeze
these families. This module performs no authentication, I/O, clock/random calls,
sequence-history checks, or lifecycle/association certification.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from types import MappingProxyType
from typing import Any

MAX_CASES = 32
MAX_REQUESTS_PER_CASE = 64
MAX_FIXTURE_INPUT_BYTES = 65536
MAX_SDK_BODY_BYTES = 4096
MAX_AUTH_HEADER_BYTES = 8192
MAX_SELECTED_RESPONSE_BYTES = 65536
MAX_SDK_RECEIPT_BYTES = 8192
MAX_SDK_RECEIPTS_PER_CASE = 32
MAX_EVENT_RECEIPTS_PER_CASE = 64
MAX_PREBIND_EVENTS = 64
MAX_EVENT_BYTES = 65536
MAX_ROLE_QUEUE = 32
MAX_ROLE_RECEIPTS_PER_LANE = 128
MAX_ROLE_ACTIVE_CAPTURES = 32
MAX_ACK_BYTES = 1024
MAX_STATUS_BYTES = 2048
MAX_CONTROL_BYTES = 4096
MAX_READBACK_BYTES = 65536
MAX_HOST_STATUS_BYTES = 4096
MAX_MODEL_INPUT_BYTES = 2048
MAX_MODEL_READBACK_BYTES = 16384
MAX_TOOL_CONTENT_BYTES = 2048
READBACK_PAGE_SIZE = 4
OBSERVER_CLOSE_SECONDS = 2
MAX_PRIVATE_TRANSPORT_STAGE_SECONDS = 1
HOST_FINISH_WAIT_SECONDS = 240
MAX_HOST_POLLS = 960
HOST_POLL_INTERVAL_MS = 250
MAX_HOST_EXEC_SECONDS = 3
MAX_PRIVATE_HOST_HTTP_SECONDS = 1
HOST_STOP_PAIR_SECONDS = 15
DOCKER_API_STOP_GRACE_SECONDS = 10
RUNNER_CLOSURE_WAIT_SECONDS = 20
MAX_RUNNER_CLOSURE_POLLS = 80
MAX_COUNTER = 2147483647
MAX_MONOTONIC_NS = 9223372036854775807

DECLARATION_SHA256 = "b79b5064f375098dabc119083cf2ff16778b683722b5df394f6cb8a06ca22914"
SDK_PATH = "/api/sdk/integrations/get"
ROLES = ("api", "api-replica")
METHODS = (
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
    "CONNECT",
    "OTHER",
)
RECEIPT_KINDS = (
    "ready",
    "sdk_request",
    "unexpected_sdk_path",
    "failure",
    "closed",
)
FAILURE_CODES = (
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
    "observer_exception",
    "queue_exhausted",
    "receipt_exhausted",
    "transport_failure",
    "ack_invalid",
    "status_io_failure",
    "shutdown_incomplete",
    "counter_exhausted",
    "collector_capacity",
    "unbound_sdk",
    "cross_case_sdk",
    "extra_run",
    "case_aborted",
    "control_invalid",
    "event_invalid",
    "event_missing",
    "event_overflow",
    "correlation_failed",
    "product_not_settled",
    "late_witness",
)
PRIVATE_ERROR_CODES = (
    "unauthorized",
    "invalid_json",
    "invalid_schema",
    "invalid_state",
    "unknown_case",
    "capacity_exhausted",
    "identity_mismatch",
    "sequence_mismatch",
    "unbound_observation",
    "internal_failure",
)
FAMILY_BYTE_CAPS = MappingProxyType(
    {
        "receipt": MAX_SDK_RECEIPT_BYTES,
        "ack": MAX_ACK_BYTES,
        "status": MAX_STATUS_BYTES,
        "arm": MAX_CONTROL_BYTES,
        "bind": MAX_CONTROL_BYTES,
        "close": MAX_CONTROL_BYTES,
        "control_ack": MAX_CONTROL_BYTES,
        "readback": MAX_READBACK_BYTES,
        "finish_request": MAX_HOST_STATUS_BYTES,
        "host_status": MAX_HOST_STATUS_BYTES,
        "error": MAX_ACK_BYTES,
        "model_input": MAX_MODEL_INPUT_BYTES,
        "model_readback": MAX_MODEL_READBACK_BYTES,
    }
)


class ContractError(ValueError):
    """Only a frozen, value-free private error code is retained."""

    def __init__(self, code: str):
        self.code = (
            code
            if type(code) is str and code in PRIVATE_ERROR_CODES
            else "invalid_schema"
        )
        super().__init__(self.code)


Validator = Callable[[Any], None]


def _require(condition: bool) -> None:
    if not condition:
        raise ContractError("invalid_schema")


def _integer(maximum: int, minimum: int = 0) -> Validator:
    def check(value: Any) -> None:
        _require(type(value) is int and minimum <= value <= maximum)

    return check


def _choice(*values: str | None) -> Validator:
    def check(value: Any) -> None:
        _require((type(value) is str or value is None) and value in values)

    return check


def _pattern(pattern: str) -> Validator:
    def check(value: Any) -> None:
        _require(type(value) is str and re.fullmatch(pattern, value) is not None)

    return check


def _nullable(check: Validator) -> Validator:
    def optional(value: Any) -> None:
        if value is not None:
            check(value)

    return optional


def _boolean(value: Any) -> None:
    _require(type(value) is bool)


def _true(value: Any) -> None:
    _require(value is True)


def _object(value: Any, fields: dict[str, Validator]) -> dict[str, Any]:
    _require(type(value) is dict)
    _require(all(type(key) is str for key in value))
    _require(value.keys() == fields.keys())
    for name, check in fields.items():
        check(value[name])
    return value


def _array(value: Any, check: Validator, maximum: int) -> None:
    _require(type(value) is list and len(value) <= maximum)
    for item in value:
        check(item)


_hex32 = _pattern(r"[0-9a-f]{32}")
_hex64 = _pattern(r"[0-9a-f]{64}")
_image_id = _pattern(r"sha256:[0-9a-f]{64}")
_uuid = _pattern(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_count = _integer(MAX_COUNTER)
_seq = _integer(MAX_ROLE_RECEIPTS_PER_LANE, 1)
_role = _choice(*ROLES)
_code = _choice(*FAILURE_CODES)
_auth_reason = _choice(*FAILURE_CODES[:6])
_body_reason = _choice("body_incomplete", "body_oversized", "body_invalid")


def _mono(value: Any) -> None:
    _pattern(r"0|[1-9][0-9]{0,18}")(value)
    _require(int(value) <= MAX_MONOTONIC_NS)


def _schema(family: str) -> Validator:
    return _choice(
        "bifrost.agent-reference."
        + {
            "receipt": "sdk-observation",
            "ack": "observer-ack",
            "status": "observer-status",
            "arm": "case-arm",
            "bind": "case-bind",
            "close": "case-close",
            "control_ack": "case-control-ack",
            "readback": "observer-readback",
            "finish_request": "finish-request",
            "host_status": "host-status",
            "error": "private-error",
            "model_input": "model-oracle-input",
            "model_readback": "model-oracle-readback",
        }[family]
        + "/v1"
    )


def _roles(value: Any) -> None:
    _array(value, _role, len(ROLES))
    _require(value == [role for role in ROLES if role in value])


def _failures(value: Any) -> None:
    _array(value, _code, len(FAILURE_CODES))
    _require(value == [code for code in FAILURE_CODES if code in value])


def _claims(value: Any) -> None:
    _object(
        value,
        {
            "sub": _uuid,
            "engine_execution_id": _nullable(_uuid),
            "engine_solution_id": _nullable(_uuid),
            "org_id": _nullable(_uuid),
            "delegated_user_id": _nullable(_uuid),
            "engine": _nullable(_boolean),
            "is_superuser": _nullable(_boolean),
            "engine_global_repo_access": _nullable(_boolean),
            "delegated_is_superuser": _nullable(_boolean),
            "delegated_is_provider_org": _nullable(_boolean),
            "delegated_is_external": _nullable(_boolean),
        },
    )


def _verification(value: Any) -> None:
    v = _object(
        value,
        {
            "outcome": _choice("verified", "unverified"),
            "reason": _nullable(_auth_reason),
            "claims": _nullable(_claims),
        },
    )
    if v["outcome"] == "verified":
        _require(v["reason"] is None and v["claims"] is not None)
    else:
        _require(v["reason"] is not None and v["claims"] is None)


def _sdk_body(value: Any) -> None:
    _object(
        value,
        {
            "name": _choice("Cove Data Protection"),
            "scope": _choice("global"),
            "solution": _uuid,
        },
    )


def _request(value: Any) -> None:
    v = _object(
        value,
        {
            "complete": _boolean,
            "bytes": _integer(MAX_SDK_BODY_BYTES + 1),
            "value": _nullable(_sdk_body),
            "reason": _nullable(_body_reason),
        },
    )
    if v["reason"] is None:
        _require(v["complete"] and v["bytes"] <= MAX_SDK_BODY_BYTES)
        _require(v["value"] is not None)
    else:
        _require(v["value"] is None)


def _response(value: Any) -> None:
    _object(
        value,
        {
            "status": _nullable(_integer(599, 100)),
            "bytes": _integer(MAX_SELECTED_RESPONSE_BYTES + 1),
            "complete": _boolean,
            "disconnect_seen": _boolean,
            "app_exception": _choice(None, "exception", "cancelled"),
        },
    )


def _sdk_payload(value: Any, unexpected: bool) -> None:
    fields = {
        "started_ns": _mono,
        "method": _choice(*METHODS),
        "query_present": _boolean,
        "verification": _verification,
        "response": _response,
        "failures": _failures,
    }
    if not unexpected:
        fields.update(path=_choice(SDK_PATH), request=_request)
    v = _object(value, fields)
    failures = set(v["failures"])
    auth_reason = v["verification"]["reason"]
    _require(
        failures.intersection(FAILURE_CODES[:6])
        == ({auth_reason} if auth_reason is not None else set())
    )
    if not unexpected:
        body_reason = v["request"]["reason"]
        _require(
            failures.intersection(FAILURE_CODES[6:9])
            == ({body_reason} if body_reason is not None else set())
        )
    response = v["response"]
    expected = {
        "method_unexpected": v["method"] != "POST",
        "query_present": v["query_present"],
        "app_exception": response["app_exception"] is not None,
        "disconnect_seen": response["disconnect_seen"],
        "response_incomplete": not response["complete"],
        "response_oversized": response["bytes"] > MAX_SELECTED_RESPONSE_BYTES,
        "unexpected_sdk_path": unexpected,
    }
    _require(all((code in failures) == present for code, present in expected.items()))


def _counters(value: Any, first_failure: str | None) -> None:
    v = _object(
        value,
        {
            "receipts_seen": _count,
            "queued": _count,
            "enqueue_rejected": _count,
            "acknowledged": _count,
            "send_failed": _count,
            "queue_pending": _integer(MAX_ROLE_QUEUE),
            "sending": _integer(1),
            "requests_started": _count,
            "requests_finished": _count,
            "requests_in_flight": _count,
            "seq_allocated": _integer(MAX_ROLE_RECEIPTS_PER_LANE),
            "last_ack_seq": _integer(MAX_ROLE_RECEIPTS_PER_LANE),
            "capacity_rejected": _count,
        },
    )
    if MAX_COUNTER in v.values():
        # A diagnostic retains the EARLIEST failure, never overwrites it.
        _require(first_failure is not None)
    if MAX_COUNTER not in (v["receipts_seen"], v["queued"], v["enqueue_rejected"]):
        _require(v["receipts_seen"] == v["queued"] + v["enqueue_rejected"])
    if MAX_COUNTER not in (v["queued"], v["acknowledged"], v["send_failed"]):
        _require(
            v["queued"]
            == (
                v["acknowledged"] + v["send_failed"] + v["queue_pending"] + v["sending"]
            )
        )
    if MAX_COUNTER not in (
        v["requests_started"],
        v["requests_finished"],
        v["requests_in_flight"],
    ):
        _require(
            v["requests_started"] == (v["requests_finished"] + v["requests_in_flight"])
        )
    _require(v["seq_allocated"] == min(v["receipts_seen"], MAX_ROLE_RECEIPTS_PER_LANE))
    _require(v["capacity_rejected"] <= v["enqueue_rejected"])
    _require(v["last_ack_seq"] <= v["seq_allocated"])


def _drained(v: dict[str, Any]) -> None:
    _require(
        all(
            v[name] == 0
            for name in (
                "enqueue_rejected",
                "send_failed",
                "queue_pending",
                "sending",
                "capacity_rejected",
                "requests_in_flight",
            )
        )
    )
    _require(
        all(
            v[name] == v["receipts_seen"]
            for name in (
                "queued",
                "acknowledged",
                "seq_allocated",
                "last_ack_seq",
            )
        )
    )


def _receipt(value: Any) -> None:
    v = _object(
        value,
        {
            "schema": _schema("receipt"),
            "lane_id": _hex32,
            "role": _role,
            "nonce": _hex32,
            "seq": _seq,
            "mono_ns": _mono,
            "kind": _choice(*RECEIPT_KINDS),
            "payload": lambda p: _require(type(p) is dict),
        },
    )
    kind, payload = v["kind"], v["payload"]
    if kind == "ready":
        _require(v["seq"] == 1)
        _object(payload, {"startup_forwarded": _true})
    elif kind in ("sdk_request", "unexpected_sdk_path"):
        _sdk_payload(payload, kind == "unexpected_sdk_path")
        _require(int(payload["started_ns"]) <= int(v["mono_ns"]))
    elif kind == "failure":
        _object(
            payload,
            {
                "phase": _choice(
                    "startup", "request", "transport", "status", "shutdown"
                ),
                "code": _code,
            },
        )
    else:
        _object(
            payload,
            {
                "shutdown_forwarded": _boolean,
                "prior": lambda p: _counters(p, payload["first_failure"]),
                "first_failure": _nullable(_code),
            },
        )
        if payload["first_failure"] is None:
            _require(payload["shutdown_forwarded"])
            _drained(payload["prior"])
            _require(payload["prior"]["receipts_seen"] == v["seq"] - 1)


def _ack(value: Any) -> None:
    _object(
        value,
        {
            "schema": _schema("ack"),
            "lane_id": _hex32,
            "role": _role,
            "nonce": _hex32,
            "seq": _seq,
            "accepted": _true,
            "case_id": _nullable(_hex32),
        },
    )


def _upstream(value: Any) -> None:
    _object(value, {"startup_forwarded": _boolean, "shutdown_forwarded": _boolean})


def _status(value: Any) -> None:
    v = _object(
        value,
        {
            "schema": _schema("status"),
            "lane_id": _hex32,
            "role": _role,
            "nonce": _hex32,
            "generation": _count,
            "mono_ns": _mono,
            "phase": _choice("starting", "active", "stopping", "closed"),
            "first_failure": _nullable(_code),
            "counters": lambda c: _counters(c, value["first_failure"]),
            "upstream": _upstream,
            "ready_acked": _boolean,
            "closed_acked": _boolean,
        },
    )
    if v["generation"] == MAX_COUNTER:
        _require(v["first_failure"] is not None)
    _require(not v["ready_acked"] or v["upstream"]["startup_forwarded"])
    _require(not v["closed_acked"] or v["upstream"]["shutdown_forwarded"])
    if v["phase"] == "closed" and v["first_failure"] is None:
        _require(v["ready_acked"] and v["closed_acked"])
        _require(all(v["upstream"].values()))
        _drained(v["counters"])
        _require(
            v["counters"]["receipts_seen"] == v["counters"]["requests_started"] + 2
        )


def _arm(value: Any) -> None:
    _object(
        value,
        {
            "schema": _schema("arm"),
            "lane_id": _hex32,
            "case_id": _hex32,
            "mode": _choice("nominal-capacity"),
            "solution_id": _uuid,
            "deployment_id": _uuid,
            "agent_id": _uuid,
            "declaration_sha256": _choice(DECLARATION_SHA256),
        },
    )


def _bind(value: Any) -> None:
    _object(
        value,
        {
            "schema": _schema("bind"),
            "lane_id": _hex32,
            "case_id": _hex32,
            "run_id": _uuid,
        },
    )


def _close(value: Any) -> None:
    v = _object(
        value,
        {
            "schema": _schema("close"),
            "lane_id": _hex32,
            "case_id": _hex32,
            "run_id": _nullable(_uuid),
            "finish_id": _hex32,
            "disposition": _choice("finish", "abort"),
        },
    )
    _require(v["disposition"] != "finish" or v["run_id"] is not None)


def _control_ack(value: Any) -> None:
    v = _object(
        value,
        {
            "schema": _schema("control_ack"),
            "lane_id": _hex32,
            "case_id": _hex32,
            "operation": _choice("arm", "bind", "close"),
            "state": _choice("armed", "bound", "closing", "closed"),
            "run_id": _nullable(_uuid),
            "finish_id": _nullable(_hex32),
            "first_failure": _nullable(_code),
        },
    )
    if v["operation"] == "arm":
        _require(v["state"] == "armed" and v["run_id"] is None)
        _require(v["finish_id"] is None)
    elif v["operation"] == "bind":
        _require(v["state"] == "bound" and v["run_id"] is not None)
        _require(v["finish_id"] is None)
    else:
        _require(v["state"] == "closing" and v["finish_id"] is not None)
        _require(v["run_id"] is not None or v["first_failure"] is not None)


def _case_state(value: Any) -> None:
    v = _object(
        value,
        {
            "case_id": _hex32,
            "state": _choice("armed", "bound", "closing", "closed"),
            "mode": _choice("nominal-capacity"),
            "solution_id": _uuid,
            "deployment_id": _uuid,
            "agent_id": _uuid,
            "run_id": _nullable(_uuid),
            "finish_id": _nullable(_hex32),
            "request_count": _integer(MAX_REQUESTS_PER_CASE + 1),
            "sdk_receipt_count": _integer(MAX_SDK_RECEIPTS_PER_CASE + 1),
            "first_failure": _nullable(_code),
        },
    )
    _require(v["sdk_receipt_count"] <= v["request_count"])
    if v["state"] == "armed":
        _require(v["run_id"] is None and v["finish_id"] is None)
    elif v["state"] == "bound":
        _require(v["run_id"] is not None and v["finish_id"] is None)
    else:
        _require(v["finish_id"] is not None)
        _require(v["run_id"] is not None or v["first_failure"] is not None)
    if (
        v["request_count"] > MAX_REQUESTS_PER_CASE
        or v["sdk_receipt_count"] > MAX_SDK_RECEIPTS_PER_CASE
    ):
        _require(v["first_failure"] is not None)


def _lane_state(value: Any) -> None:
    v = _object(
        value,
        {
            "case_count": _integer(MAX_CASES),
            "active_case_id": _nullable(_hex32),
            "roles_ready": _roles,
            "roles_closed": _roles,
            "first_failure": _nullable(_code),
        },
    )
    _require(v["active_case_id"] is None or v["case_count"] >= 1)
    _require(set(v["roles_closed"]).issubset(v["roles_ready"]))


def _entry(value: Any) -> None:
    v = _object(
        value,
        {
            "index": _integer(2 * MAX_ROLE_RECEIPTS_PER_LANE - 1),
            "received_ns": _mono,
            "case_id": _nullable(_hex32),
            "association": _choice(
                "lifecycle", "active-arm-pending-db", "unbound", "late"
            ),
            "receipt": _receipt,
        },
    )
    lifecycle = v["receipt"]["kind"] in ("ready", "failure", "closed")
    if v["association"] == "lifecycle":
        _require(lifecycle and v["case_id"] is None)
    else:
        _require(not lifecycle)
        _require((v["case_id"] is None) == (v["association"] == "unbound"))


def _readback(value: Any) -> None:
    v = _object(
        value,
        {
            "schema": _schema("readback"),
            "lane_id": _hex32,
            "scope": _choice("lane", "case"),
            "case_id": _nullable(_hex32),
            "offset": _integer(2 * MAX_ROLE_RECEIPTS_PER_LANE),
            "next_cursor": _nullable(_integer(2 * MAX_ROLE_RECEIPTS_PER_LANE, 1)),
            "total": _integer(2 * MAX_ROLE_RECEIPTS_PER_LANE),
            "records": lambda r: _array(r, _entry, READBACK_PAGE_SIZE),
            "case": _nullable(_case_state),
            "lane": _lane_state,
        },
    )
    _require(v["offset"] <= v["total"])
    _require(len(v["records"]) == min(READBACK_PAGE_SIZE, v["total"] - v["offset"]))
    end = v["offset"] + len(v["records"])
    _require(v["next_cursor"] == (end if end < v["total"] else None))
    if v["scope"] == "lane":
        _require(v["case_id"] is None and v["case"] is None)
        _require(
            all(
                record["index"] == v["offset"] + position
                for position, record in enumerate(v["records"])
            )
        )
    else:
        _require(v["case_id"] is not None and v["case"] is not None)
        _require(v["case"]["case_id"] == v["case_id"])
        _require(v["total"] <= MAX_SDK_RECEIPTS_PER_CASE)
        _require(all(r["case_id"] == v["case_id"] for r in v["records"]))
    _require(all(r["receipt"]["lane_id"] == v["lane_id"] for r in v["records"]))
    indices = [r["index"] for r in v["records"]]
    _require(indices == sorted(set(indices)))


def _role_identity(value: Any) -> None:
    _object(value, {"role": _role, "nonce": _hex32})


def _finish_request(value: Any) -> None:
    v = _object(
        value,
        {
            "schema": _schema("finish_request"),
            "lane_id": _hex32,
            "case_id": _nullable(_hex32),
            "run_id": _nullable(_uuid),
            "finish_id": _nullable(_hex32),
            "disposition": _choice(None, "finish", "abort"),
            "state": _choice(None, "armed", "bound", "closing", "closed"),
            "roles": lambda r: _array(r, _role_identity, len(ROLES)),
        },
    )
    roles = [r["role"] for r in v["roles"]]
    _roles(roles)
    if v["state"] is None:
        _require(
            all(
                v[name] is None
                for name in (
                    "case_id",
                    "run_id",
                    "finish_id",
                    "disposition",
                )
            )
        )
    else:
        _require(v["case_id"] is not None)
        if v["state"] in ("armed", "bound"):
            _require(v["finish_id"] is None and v["disposition"] is None)
            _require((v["run_id"] is None) == (v["state"] == "armed"))
        else:
            _require(v["finish_id"] is not None and v["disposition"] is not None)
            if v["disposition"] == "finish":
                _require(v["run_id"] is not None and roles == list(ROLES))


def _host_role(value: Any) -> None:
    v = _object(
        value,
        {
            "role": _role,
            "container_id": _hex64,
            "image_id": _image_id,
            "observer_nonce": _nullable(_hex32),
            "stop_requested": _boolean,
            "exit_observed": _boolean,
            "exit_code": _nullable(_integer(255)),
        },
    )
    _require((v["exit_code"] is not None) == v["exit_observed"])


def _host_status(value: Any) -> None:
    v = _object(
        value,
        {
            "schema": _schema("host_status"),
            "lane_id": _hex32,
            "runner_container_id": _hex64,
            "case_id": _nullable(_hex32),
            "run_id": _nullable(_uuid),
            "finish_id": _nullable(_hex32),
            "generation": _count,
            "mono_ns": _mono,
            "phase": _choice("watching", "stop_requested", "exited", "failed"),
            "roles": lambda r: _array(r, _host_role, len(ROLES)),
            "first_failure": _nullable(_code),
            "failure_stage": _choice(
                None, "poll", "identity", "runner", "api-stop", "api-exit", "status"
            ),
        },
    )
    _require([r["role"] for r in v["roles"]] == list(ROLES))
    failed = v["phase"] == "failed"
    _require((v["first_failure"] is not None) == failed)
    _require((v["failure_stage"] is not None) == failed)
    if v["generation"] == MAX_COUNTER:
        _require(failed)
    if v["phase"] == "watching":
        _require(all(v[name] is None for name in ("case_id", "run_id", "finish_id")))
    elif v["phase"] in ("stop_requested", "exited"):
        _require(
            all(v[name] is not None for name in ("case_id", "run_id", "finish_id"))
        )
        _require(all(r["observer_nonce"] is not None for r in v["roles"]))
    if v["phase"] == "exited":
        _require(all(r["stop_requested"] and r["exit_observed"] for r in v["roles"]))
        _require(all(r["exit_code"] in (0, 143) for r in v["roles"]))


def _error(value: Any) -> None:
    _object(value, {"schema": _schema("error"), "error": _choice(*PRIVATE_ERROR_CODES)})


def _model_input(value: Any) -> None:
    v = _object(
        value,
        {
            "schema": _schema("model_input"),
            "lane_id": _hex32,
            "case_id": _hex32,
            "model_key": _hex64,
            "password": _hex64,
            "visa": _hex64,
            "partner_name": _choice("C1R Synthetic Partner"),
            "username": _choice("c1r-reference@example.invalid"),
        },
    )
    _require(len({v["model_key"], v["password"], v["visa"]}) == 3)


def _tool_content(value: Any) -> None:
    _require(type(value) is str)
    _require(value.isascii() and len(value) <= MAX_TOOL_CONTENT_BYTES)
    parsed = False
    result = None
    try:
        result = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        parsed = True
    except (ValueError, RecursionError):
        pass
    _require(parsed)

    def empty_object(item: Any) -> None:
        _require(type(item) is dict and not item)

    def empty_array(item: Any) -> None:
        _require(type(item) is list and not item)

    def timestamp(item: Any) -> None:
        _pattern(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?\+00:00"
        )(item)
        valid = False
        try:
            datetime.fromisoformat(item)
            valid = True
        except ValueError:
            pass
        _require(valid)

    def capacity(item: Any) -> None:
        _object(
            item,
            {
                "success": _true,
                "read_only": _true,
                "agent_count": _integer(0),
                "restore_row_count": _integer(0),
                "active_restore_count": _integer(0),
                "active_restore_count_by_agent": empty_object,
                "agents": empty_array,
                "active_restores": empty_array,
                "recent_restores": empty_array,
            },
        )

    _object(result, {"success": _true, "observed_at": timestamp, "capacity": capacity})


def _model_record(value: Any) -> None:
    v = _object(
        value,
        {
            "index": _integer(MAX_REQUESTS_PER_CASE - 1),
            "kind": _choice(
                "responses_probe",
                "chat_probe",
                "agent_first",
                "cove_login",
                "cove_agents",
                "cove_dashboard",
                "agent_final",
                "summary",
                "unexpected",
            ),
            "settled": _boolean,
            "matched": _nullable(_boolean),
            "status": _nullable(_integer(599, 100)),
            "write_complete": _boolean,
            "tool_content": _nullable(_tool_content),
        },
    )
    if not v["settled"]:
        _require(v["matched"] is None and v["status"] is None)
        _require(not v["write_complete"] and v["tool_content"] is None)
    else:
        _require(v["matched"] is not None)
        _require((v["status"] is not None) == v["write_complete"])
        _require(
            (v["tool_content"] is not None)
            == (v["kind"] == "agent_final" and v["matched"])
        )
        _require(v["kind"] != "unexpected" or not v["matched"])


def _model_readback(value: Any) -> None:
    v = _object(
        value,
        {
            "schema": _schema("model_readback"),
            "lane_id": _hex32,
            "case_id": _hex32,
            "run_id": _nullable(_uuid),
            "finish_id": _nullable(_hex32),
            "state": _choice("armed", "bound", "closing", "closed"),
            "first_failure": _nullable(_code),
            "attempt_count": _integer(MAX_REQUESTS_PER_CASE + 1),
            "offset": _integer(MAX_REQUESTS_PER_CASE),
            "total": _integer(MAX_REQUESTS_PER_CASE),
            "next_offset": _nullable(_integer(MAX_REQUESTS_PER_CASE)),
            "records": lambda r: _array(r, _model_record, READBACK_PAGE_SIZE),
        },
    )
    _require(v["total"] <= v["attempt_count"])
    if v["first_failure"] is None:
        _require(v["attempt_count"] - v["total"] <= MAX_SDK_RECEIPTS_PER_CASE)
    _require(v["offset"] <= v["total"])
    _require(len(v["records"]) == min(READBACK_PAGE_SIZE, v["total"] - v["offset"]))
    end = v["offset"] + len(v["records"])
    _require(v["next_offset"] == (end if end < v["total"] else None))
    _require(all(r["index"] == v["offset"] + n for n, r in enumerate(v["records"])))
    if v["state"] == "armed":
        _require(v["run_id"] is None and v["finish_id"] is None)
    elif v["state"] == "bound":
        _require(v["run_id"] is not None and v["finish_id"] is None)
    else:
        _require(v["finish_id"] is not None)
        _require(v["run_id"] is not None or v["first_failure"] is not None)
    if v["attempt_count"] > MAX_REQUESTS_PER_CASE:
        _require(v["first_failure"] is not None)
    if any(
        r["settled"] and (not r["matched"] or not r["write_complete"])
        for r in v["records"]
    ):
        _require(v["first_failure"] is not None)
    if v["state"] == "closed" and v["first_failure"] is None:
        _require(all(r["settled"] for r in v["records"]))


_VALIDATORS = MappingProxyType(
    {
        "receipt": _receipt,
        "ack": _ack,
        "status": _status,
        "arm": _arm,
        "bind": _bind,
        "close": _close,
        "control_ack": _control_ack,
        "readback": _readback,
        "finish_request": _finish_request,
        "host_status": _host_status,
        "error": _error,
        "model_input": _model_input,
        "model_readback": _model_readback,
    }
)


def _family_cap(family: Any) -> int:
    _require(type(family) is str and family in FAMILY_BYTE_CAPS)
    return FAMILY_BYTE_CAPS[family]


def _canonical(value: Any, code: str) -> bytes:
    encoded = None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        pass
    # Raising outside except leaves no chained parser exception holding data.
    if encoded is None:
        raise ContractError(code)
    return encoded


def encode_private(family: str, value: Any) -> bytes:
    """Validate a closed family and return its canonical, capped UTF-8 bytes."""
    cap = _family_cap(family)
    _VALIDATORS[family](value)
    encoded = _canonical(value, "invalid_schema")
    if len(encoded) > cap:
        raise ContractError("capacity_exhausted")
    return encoded


def validate_private(family: str, value: Any) -> None:
    """Check shape, stateless semantics and encoded cap without any live proof."""
    encode_private(family, value)


class _JSONRejected(ValueError):
    pass


def _reject_number(_: str) -> Any:
    raise _JSONRejected()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _JSONRejected()
        result[key] = value
    return result


def decode_private(family: str, raw_bytes: bytes) -> dict[str, Any]:
    """Reject malformed/noncanonical wire before validating its selected family."""
    cap = _family_cap(family)
    if type(raw_bytes) is not bytes:
        raise ContractError("invalid_json")
    if len(raw_bytes) > cap:
        raise ContractError("capacity_exhausted")
    parsed = False
    value = None
    try:
        value = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        parsed = True
    except (ValueError, UnicodeError, RecursionError):
        pass
    if not parsed:
        raise ContractError("invalid_json")
    if _canonical(value, "invalid_json") != raw_bytes:
        raise ContractError("invalid_json")
    _VALIDATORS[family](value)
    return value
