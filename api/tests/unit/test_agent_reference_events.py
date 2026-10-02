"""Injected mappings test retention; they do not prove actual Redis behavior."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from itertools import permutations
from types import SimpleNamespace

import pytest

from scripts import agent_reference_contract as contract
from tests.e2e.platform import agent_reference_events as events

AGENT = "11111111-2222-3333-4444-555555555555"
ORG = "00000000-0000-0000-0000-000000000002"
RUN = "22222222-2222-3333-4444-555555555555"
OTHER = "33333333-2222-3333-4444-555555555555"
ORG_CHANNEL = f"bifrost:agent-runs:org:{ORG}".encode()
DETAIL = f"bifrost:agent-run:{RUN}".encode()
ACKS = (
    ("psubscribe", events.PATTERN),
    ("subscribe", events.ALL_CHANNEL),
    ("subscribe", ORG_CHANNEL),
)


def _ack(kind, channel, count):
    return {"type": kind, "pattern": None, "channel": channel, "data": count}


def _ready(collector):
    for count, (kind, channel) in enumerate(ACKS, 1):
        collector.accept(_ack(kind, channel, count))


def _collector(*, ready=True, bound=False):
    collector = events.BoundedEventCollector(AGENT, ORG)
    if ready:
        _ready(collector)
    if bound:
        collector.bind(RUN)
    return collector


def _payload(**changes):
    return {
        "type": "agent_run_update",
        "run_id": RUN,
        "agent_id": AGENT,
        "org_id": ORG,
        **changes,
    }


def _message(
    payload=None, *, raw=None, channel=DETAIL, pattern=events.PATTERN, kind="pmessage"
):
    return {
        "type": kind,
        "pattern": pattern,
        "channel": channel,
        "data": json.dumps(_payload() if payload is None else payload).encode()
        if raw is None
        else raw,
    }


def _denied(collector, mutation, code):
    with pytest.raises(events.EventEvidenceError) as caught:
        mutation()
    assert caught.value.code == code
    assert str(caught.value) == code
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert collector.first_failure == code
    return caught.value


def test_shared_frozen_limits():
    assert (
        events.MAX_EVENT_RECEIPTS_PER_CASE == contract.MAX_EVENT_RECEIPTS_PER_CASE == 64
    )
    assert events.MAX_PREBIND_EVENTS == contract.MAX_PREBIND_EVENTS == 64
    assert events.MAX_EVENT_BYTES == contract.MAX_EVENT_BYTES == 65536
    assert events.MAX_TOTAL_EVENT_BYTES == 64 * 65536 == 4194304


@pytest.mark.parametrize("order", list(permutations(ACKS)))
def test_readiness_requires_three_actual_identities_in_arrival_order(order):
    collector = _collector(ready=False)
    for count, (kind, channel) in enumerate(order, 1):
        collector.accept(_ack(kind, channel, count))
        assert collector.ready is (count == 3)
    collector.bind(RUN)
    assert collector.snapshot() == ()


def test_publications_interleave_with_subscription_acknowledgements():
    collector = _collector(ready=False)
    for count, (kind, channel) in enumerate(ACKS, 1):
        collector.accept(_message())
        collector.accept(_ack(kind, channel, count))
    collector.bind(RUN)
    assert collector.publication_count == len(collector.snapshot()) == 3


@pytest.mark.parametrize(
    "change",
    [
        {"data": True},
        {"data": "1"},
        {"data": 1.0},
        {"data": 0},
        {"data": 2},
        {"channel": events.PATTERN.decode()},
        {"channel": b"unknown"},
        {"channel": b"x" * 129},
        {"type": "subscribe"},
        {"pattern": events.PATTERN},
    ],
)
def test_invalid_ack_latches_failure(change):
    collector = _collector(ready=False)
    message = _ack(*ACKS[0], 1)
    message.update(change)
    _denied(collector, lambda: collector.accept(message), "acknowledgement_mismatch")
    assert not collector.ready


@pytest.mark.parametrize("count", [1, 2])
def test_duplicate_ack_cannot_replace_missing_subscription(count):
    collector = _collector(ready=False)
    collector.accept(_ack(*ACKS[0], 1))
    _denied(
        collector,
        lambda: collector.accept(_ack(*ACKS[0], count)),
        "acknowledgement_mismatch",
    )


def test_fourth_ack_is_not_resubscription():
    collector = _collector()
    _denied(
        collector,
        lambda: collector.accept(_ack(*ACKS[0], 4)),
        "acknowledgement_mismatch",
    )


def test_fanout_duplicates_and_actual_local_order_are_retained(monkeypatch):
    ticks = iter((101, 109, 111))
    monkeypatch.setattr(
        events, "time", SimpleNamespace(monotonic_ns=lambda: next(ticks))
    )
    collector = _collector()
    for message in (
        _message(),
        _message(channel=events.ALL_CHANNEL, pattern=None, kind="message"),
        _message(channel=ORG_CHANNEL, pattern=None, kind="message"),
    ):
        collector.accept(message)
    collector.bind(RUN)
    records = collector.snapshot()
    assert [record.index for record in records] == [1, 2, 3]
    assert [record.observed_ns for record in records] == ["101", "109", "111"]
    assert [record.channel for record in records] == [
        DETAIL,
        events.ALL_CHANNEL,
        ORG_CHANNEL,
    ]
    assert [record.pattern for record in records] == [events.PATTERN, None, None]
    assert [record.payload for record in records] == [_payload()] * 3


@pytest.mark.parametrize("prebind", [0, 1, 32, 64])
def test_64_publications_share_pre_and_post_bind_budget(prebind):
    collector = _collector()
    for _ in range(prebind):
        collector.accept(_message())
    collector.bind(RUN)
    for _ in range(64 - prebind):
        collector.accept(_message())
    assert len(collector.snapshot()) == collector.publication_count == 64
    retained = collector.retained_bytes
    _denied(collector, lambda: collector.accept(_message()), "capacity_exhausted")
    assert collector.publication_count == 64
    assert collector.retained_bytes == retained


def test_65th_prebind_is_not_filtered_or_reset():
    collector = _collector()
    for _ in range(64):
        collector.accept(_message())
    _denied(collector, lambda: collector.accept(_message()), "capacity_exhausted")
    _denied(collector, lambda: collector.bind(RUN), "capacity_exhausted")
    assert collector.publication_count == 64


def test_exact_per_message_and_aggregate_byte_boundary():
    collector = _collector(bound=True)
    base = json.dumps(_payload()).encode()
    raw = base + b" " * (65536 - len(base))
    for _ in range(64):
        collector.accept(_message(raw=raw))
    assert collector.retained_bytes == 4194304
    assert len(collector.snapshot()) == 64
    assert collector.snapshot()[0].payload == _payload()
    _denied(
        collector, lambda: collector.accept(_message(raw=raw)), "capacity_exhausted"
    )
    assert collector.retained_bytes == 4194304


def test_original_noncanonical_wire_is_retained_without_reserialization():
    raw = (
        b' \n{ "org_id" : "' + ORG.encode() + b'", '
        b'"agent_id" : "' + AGENT.encode() + b'", '
        b'"run_id" : "' + RUN.encode() + b'", '
        b'"type" : "agent_run_update", "future" : [1, 2] }\t '
    )
    expected = {
        "org_id": ORG,
        "agent_id": AGENT,
        "run_id": RUN,
        "type": "agent_run_update",
        "future": [1, 2],
    }
    collector = _collector(bound=True)
    collector.accept(_message(raw=raw))
    record = collector.snapshot()[0]
    assert record._raw == raw
    assert record._raw != json.dumps(expected).encode()
    assert record.payload == expected
    assert collector.retained_bytes == len(raw)


def test_oversized_bytes_fail_before_json_parse(monkeypatch):
    def unexpected(_raw):
        pytest.fail("oversized bytes reached the JSON parser")

    monkeypatch.setattr(events, "_decode", unexpected)
    collector = _collector(bound=True)
    _denied(
        collector,
        lambda: collector.accept(_message(raw=b"x" * 65537)),
        "capacity_exhausted",
    )
    assert collector.publication_count == collector.retained_bytes == 0


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"{",
        b'{"secret":"synthetic-secret-marker",',
        b"[]",
        b"null",
        b"1",
        b"[" * 2000 + b"0" + b"]" * 2000,
        b'"text"',
        b'{"type":"agent_run_update","type":"agent_run_step"}',
        json.dumps(_payload(extra={"same": 1}))
        .encode()
        .replace(b'"same": 1', b'"same": 1, "same": 2'),
        *[
            json.dumps(_payload(extra=0))
            .encode()
            .replace(b'"extra": 0', b'"extra": ' + value)
            for value in (b"NaN", b"Infinity", b"-Infinity", b"1e999")
        ],
        "synthetic-secret-marker",
        bytearray(b"{}"),
        None,
    ],
)
def test_malformed_payload_is_never_retained(raw):
    collector = _collector(bound=True)
    message = _message()
    message["data"] = raw
    error = _denied(collector, lambda: collector.accept(message), "invalid_payload")
    assert "synthetic-secret-marker" not in str(error) + repr(error)
    assert collector.publication_count == collector.retained_bytes == 0


def _depth_payload(depth):
    extra = 0
    for _ in range(depth - 2):
        extra = [extra]
    return _payload(extra=extra)


def test_depth_64_vs_65():
    collector = _collector(bound=True)
    collector.accept(_message(_depth_payload(64)))
    _denied(
        collector,
        lambda: collector.accept(_message(_depth_payload(65))),
        "invalid_payload",
    )
    assert collector.publication_count == 1


def test_value_node_budget_8192_vs_8193():
    collector = _collector(bound=True)
    collector.accept(_message(_payload(extra=[0] * (8192 - 6))))
    _denied(
        collector,
        lambda: collector.accept(_message(_payload(extra=[0] * (8193 - 6)))),
        "invalid_payload",
    )
    assert collector.publication_count == 1


def test_parser_recursion_failure_is_static(monkeypatch):
    def recursion(_raw):
        raise RecursionError("synthetic-secret-marker")

    monkeypatch.setattr(events, "_decode", recursion)
    collector = _collector(bound=True)
    error = _denied(collector, lambda: collector.accept(_message()), "invalid_payload")
    assert error.__cause__ is None and error.__suppress_context__
    assert "synthetic-secret-marker" not in str(error) + repr(error)


@pytest.mark.parametrize(
    "change",
    [
        {"type": "pong"},
        {"type": b"pmessage"},
        {"pattern": b"other"},
        {"pattern": events.PATTERN.decode()},
        {"channel": DETAIL.decode()},
        {"channel": b"unknown"},
        {"channel": b"bifrost:agent-run:bad"},
        {"channel": b"bifrost:agent-run:\xff"},
        {"channel": events.DETAIL_PREFIX + b"\xff" + b"a" * 35},
        {"extra": 1},
        {"channel": events.ALL_CHANNEL},
        {"type": "message", "pattern": None},
    ],
)
def test_wrong_transport_identity_or_shape_fails(change):
    collector = _collector(bound=True)
    message = _message()
    message.update(change)
    _denied(collector, lambda: collector.accept(message), "invalid_message")


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", OTHER),
        ("agent_id", OTHER),
        ("org_id", OTHER),
        ("org_id", None),
        ("agent_id", None),
    ],
)
def test_foreign_identity_is_not_quietly_filtered(field, value):
    collector = _collector(bound=True)
    _denied(
        collector,
        lambda: collector.accept(_message(_payload(**{field: value}))),
        "identity_mismatch",
    )
    assert collector.publication_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        _payload(run_id="not-a-uuid"),
        _payload(run_id=True),
        _payload(run_id=None),
        _payload(type="other"),
        _payload(type="agent_run_step", step=None),
    ],
)
def test_selected_event_schema_has_no_aliases(payload):
    collector = _collector(bound=True)
    _denied(collector, lambda: collector.accept(_message(payload)), "invalid_payload")


def test_second_prebind_run_fails():
    collector = _collector()
    collector.accept(_message())
    _denied(
        collector,
        lambda: collector.accept(
            _message(
                _payload(run_id=OTHER), channel=f"bifrost:agent-run:{OTHER}".encode()
            )
        ),
        "identity_mismatch",
    )


def test_matching_detail_channel_cannot_admit_foreign_run_after_binding():
    collector = _collector(bound=True)
    _denied(
        collector,
        lambda: collector.accept(
            _message(
                _payload(run_id=OTHER), channel=f"bifrost:agent-run:{OTHER}".encode()
            )
        ),
        "identity_mismatch",
    )
    assert collector.publication_count == 0


def test_bind_checks_prebind_identity_and_occurs_once():
    collector = _collector()
    collector.accept(_message())
    _denied(collector, lambda: collector.bind(OTHER), "identity_mismatch")
    second = _collector(bound=True)
    _denied(second, lambda: second.bind(RUN), "invalid_state")


def test_step_requires_detail_without_invented_agent_org_fields():
    payload = {"type": "agent_run_step", "run_id": RUN, "step": {"content": {}}}
    collector = _collector(bound=True)
    collector.accept(_message(payload))
    assert collector.snapshot()[0].payload == payload
    _denied(
        collector,
        lambda: collector.accept(
            _message(payload, channel=events.ALL_CHANNEL, pattern=None, kind="message")
        ),
        "invalid_payload",
    )


def test_unknown_fields_finite_float_and_snapshot_isolation():
    payload = _payload(
        confidence=0.75,
        future={"items": [1, {"x": 2}], "private": "synthetic-secret-marker"},
    )
    collector = _collector(bound=True)
    collector.accept(_message(payload))
    first = collector.snapshot()
    copied = first[0].payload
    copied["future"]["items"][1]["x"] = 99
    copied["run_id"] = OTHER
    assert collector.snapshot()[0].payload == payload
    collector.accept(_message())
    assert len(first) == 1 and len(collector.snapshot()) == 2
    with pytest.raises(FrozenInstanceError):
        setattr(first[0], "index", 99)
    assert "future" not in repr(first[0])
    assert "synthetic-secret-marker" not in repr(first[0])


@pytest.mark.parametrize("method", ["accept", "bind", "close", "snapshot"])
def test_first_failure_permanently_rejects_later_operations(method):
    collector = _collector(bound=True)
    _denied(collector, lambda: collector.accept(_message(raw=b"{")), "invalid_payload")
    operations = {
        "accept": lambda: collector.accept(_message()),
        "bind": lambda: collector.bind(RUN),
        "close": collector.close,
        "snapshot": collector.snapshot,
    }
    _denied(collector, operations[method], "invalid_payload")


@pytest.mark.parametrize("ready", [False, True])
def test_premature_snapshot_is_not_accepted_evidence(ready):
    collector = _collector(ready=ready)
    _denied(collector, collector.snapshot, "invalid_state")


def test_bind_before_ack_readiness_is_invalid():
    collector = _collector(ready=False)
    _denied(collector, lambda: collector.bind(RUN), "invalid_state")


@pytest.mark.parametrize(
    "identity", [None, True, "invalid", "AAAAAAAA-2222-3333-4444-555555555555"]
)
def test_constructor_rejects_noncanonical_ids(identity):
    with pytest.raises(events.EventEvidenceError, match="^identity_mismatch$"):
        events.BoundedEventCollector(identity, ORG)
    with pytest.raises(events.EventEvidenceError, match="^identity_mismatch$"):
        events.BoundedEventCollector(AGENT, identity)


def test_close_preserves_snapshot_but_is_not_producer_closure():
    collector = _collector(bound=True)
    collector.accept(_message())
    snapshot = collector.snapshot()
    collector.close()
    assert collector.snapshot() == snapshot
    _denied(collector, lambda: collector.accept(_message()), "invalid_state")
    assert collector.publication_count == 1
