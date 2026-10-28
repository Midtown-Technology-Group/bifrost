"""Wire contracts for the production remote execution transport."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.worker.remote.protocol import (
    MAX_FRAME_BYTES,
    AuthenticationError,
    ControlEnvelope,
    FragmentAssembler,
    Hello,
    ProtocolError,
    SignedHello,
    fragment,
    sign_hello,
    verify_hello,
)


def _hello(**changes: object) -> Hello:
    values: dict[str, object] = {
        "launcher_id": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "incarnation_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        "job_uid": "job-uid",
        "pod_uid": "pod-uid",
        "hostname": "runner-0",
        "form": "job",
        "issued_at": 1_700_000_000,
        "nonce": "n" * 32,
        "hello_sequence": 1,
    }
    values.update(changes)
    return Hello(**values)


def test_hello_signature_binds_every_identity_field() -> None:
    hello = _hello()
    signed = sign_hello("platform-secret", hello)

    assert verify_hello("platform-secret", signed, now=hello.issued_at + 1) == hello

    tampered = replace(signed, pod_uid="other-pod")
    with pytest.raises(AuthenticationError):
        verify_hello("platform-secret", tampered, now=hello.issued_at + 1)


def test_hello_rejects_stale_proof_before_database_registration() -> None:
    hello = _hello()
    signed = sign_hello("platform-secret", hello)

    with pytest.raises(AuthenticationError, match="expired"):
        verify_hello("platform-secret", signed, now=hello.issued_at + 61)


def test_hello_signature_binds_monotonic_replay_sequence() -> None:
    hello = _hello(hello_sequence=2)
    signed = sign_hello("platform-secret", hello)

    with pytest.raises(AuthenticationError):
        verify_hello("platform-secret", replace(signed, hello_sequence=3), now=hello.issued_at)


def test_envelope_rejects_oversize_frames_before_decoding_json() -> None:
    with pytest.raises(ProtocolError, match="frame"):
        ControlEnvelope.from_json("x" * (MAX_FRAME_BYTES + 1))


def test_envelope_round_trips_only_json_object_payloads() -> None:
    envelope = ControlEnvelope(
        session_generation=7,
        sequence=3,
        kind="result",
        payload={"route_id": "route-1", "result": {"success": True}},
    )

    decoded = ControlEnvelope.from_json(envelope.to_json())

    assert decoded == envelope
    with pytest.raises(ProtocolError, match="object"):
        ControlEnvelope.from_json(json.dumps(["not", "an", "object"]))


def test_envelope_uses_the_execution_result_json_contract_for_non_json_values() -> None:
    created_at = datetime(2026, 10, 25, 12, 34, 56, tzinfo=timezone.utc)
    identifier = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    envelope = ControlEnvelope(
        session_generation=7,
        sequence=3,
        kind="result",
        payload={
            "result": {
                "metadata": {"created_at": created_at, "day": date(2026, 10, 25)},
                "identifier": identifier,
                "amount": Decimal("12.34"),
            },
        },
    )

    decoded = ControlEnvelope.from_json(envelope.to_json())

    assert decoded.payload == {
        "result": {
            "metadata": {"created_at": str(created_at), "day": "2026-10-25"},
            "identifier": str(identifier),
            "amount": "12.34",
        },
    }


def test_large_logical_message_round_trips_in_bounded_ordered_frames() -> None:
    created_at = datetime(2026, 10, 25, 12, 34, 56, tzinfo=timezone.utc)
    envelope = ControlEnvelope(
        session_generation=1,
        sequence=1,
        kind="command",
        payload={
            "context": "x" * (MAX_FRAME_BYTES * 2),
            "result": {"metadata": {"created_at": created_at}},
        },
    )

    frames = list(fragment(envelope))
    assembler = FragmentAssembler()

    assert len(frames) > 1
    assert all(len(frame.to_json().encode()) <= MAX_FRAME_BYTES for frame in frames)
    received = [assembler.feed(frame) for frame in frames]
    assert received[:-1] == [None] * (len(frames) - 1)
    assert received[-1] == ControlEnvelope(
        session_generation=1,
        sequence=1,
        kind="command",
        payload={
            "context": "x" * (MAX_FRAME_BYTES * 2),
            "result": {"metadata": {"created_at": str(created_at)}},
        },
    )


def test_unset_lifecycle_fields_keep_the_historical_signed_bytes() -> None:
    """A launcher that does not send a build still proves the same payload."""
    hello = _hello()
    payload = {
        "form": "job",
        "hello_sequence": 1,
        "hostname": "runner-0",
        "incarnation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "issued_at": 1_700_000_000,
        "job_uid": "job-uid",
        "launcher_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "nonce": "n" * 32,
        "pod_uid": "pod-uid",
    }

    assert hello.canonical_bytes() == json.dumps(
        payload, separators=(",", ":"), sort_keys=True
    ).encode()
    signed = sign_hello("platform-secret", hello)
    wire = signed.to_dict()
    for key in ("version", "drain_when_outdated", "exit_when_idle_seconds", "max_runs"):
        wire.pop(key, None)
    restored = SignedHello.from_dict(wire)

    assert verify_hello("platform-secret", restored, now=hello.issued_at) == hello


def test_reported_build_and_static_limits_are_signed() -> None:
    hello = _hello(
        form="static",
        job_uid=None,
        pod_uid=None,
        version="launcher-9",
        drain_when_outdated=True,
        exit_when_idle_seconds=0,
        max_runs=4,
    )
    signed = sign_hello("platform-secret", hello)

    assert verify_hello("platform-secret", signed, now=hello.issued_at) == hello
    assert sign_hello("platform-secret", _hello(form="static", job_uid=None, pod_uid=None)).proof != (
        signed.proof
    )
    with pytest.raises(AuthenticationError):
        verify_hello(
            "platform-secret", replace(signed, version="other-build"), now=hello.issued_at
        )
    with pytest.raises(AuthenticationError):
        verify_hello(
            "platform-secret", replace(signed, max_runs=1), now=hello.issued_at
        )


def test_hello_rejects_an_unusable_reported_build_or_limit() -> None:
    with pytest.raises(ProtocolError, match="version"):
        _hello(version="")
    with pytest.raises(ProtocolError, match="version"):
        _hello(version="x" * 101)
    with pytest.raises(ProtocolError, match="limit"):
        _hello(form="static", job_uid=None, pod_uid=None, max_runs=0)
    with pytest.raises(ProtocolError, match="limit"):
        _hello(form="static", job_uid=None, pod_uid=None, exit_when_idle_seconds=-1)

    signed = sign_hello("platform-secret", _hello()).to_dict()
    signed["max_runs"] = True
    with pytest.raises(AuthenticationError):
        SignedHello.from_dict(signed)
