"""Synthetic fixture instruments only; these tests prove no nominal execution."""

from __future__ import annotations

import importlib
import json
import os
import ssl
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from types import SimpleNamespace

import pytest

from scripts import agent_reference_contract as wire
from scripts import agent_reference_fixture as fixture

LANE = "1" * 32
CASE = "2" * 32
NONCES = {"api": "3" * 32, "api-replica": "4" * 32}
FINISH = "5" * 32
SOLUTION = "abcdefab-1234-1234-1234-123456789abc"
RUN = "11111111-2222-3333-4444-555555555555"
INGEST = b"a" * 64
CONTROL = b"b" * 64


def receipt(kind="ready", role="api", seq=1, payload=None):
    return {
        "schema": "bifrost.agent-reference.sdk-observation/v1",
        "lane_id": LANE,
        "role": role,
        "nonce": NONCES[role],
        "seq": seq,
        "mono_ns": "100",
        "kind": kind,
        "payload": {"startup_forwarded": True} if payload is None else payload,
    }


def sdk_payload():
    return {
        "started_ns": "99",
        "method": "POST",
        "path": wire.SDK_PATH,
        "query_present": False,
        "request": {
            "complete": True,
            "bytes": 128,
            "reason": None,
            "value": {
                "name": "Cove Data Protection",
                "scope": "global",
                "solution": SOLUTION,
            },
        },
        "verification": {
            "outcome": "verified",
            "reason": None,
            "claims": {
                "sub": RUN,
                "engine_execution_id": RUN,
                "engine_solution_id": SOLUTION,
                "org_id": RUN,
                "delegated_user_id": RUN,
                "engine": True,
                "is_superuser": True,
                "engine_global_repo_access": True,
                "delegated_is_superuser": False,
                "delegated_is_provider_org": True,
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


def arm():
    return {
        "schema": "bifrost.agent-reference.case-arm/v1",
        "lane_id": LANE,
        "case_id": CASE,
        "mode": "nominal-capacity",
        "solution_id": SOLUTION,
        "deployment_id": RUN,
        "agent_id": RUN,
        "declaration_sha256": wire.DECLARATION_SHA256,
    }


def bind():
    return {
        "schema": "bifrost.agent-reference.case-bind/v1",
        "lane_id": LANE,
        "case_id": CASE,
        "run_id": RUN,
    }


def close(disposition="finish", run_id=RUN):
    return {
        "schema": "bifrost.agent-reference.case-close/v1",
        "lane_id": LANE,
        "case_id": CASE,
        "run_id": run_id,
        "finish_id": FINISH,
        "disposition": disposition,
    }


def counters(receipts, requests):
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
        "seq_allocated": receipts,
        "last_ack_seq": receipts,
        "capacity_rejected": 0,
    }


def closed(role="api", seq=2, requests=0):
    return receipt(
        "closed",
        role,
        seq,
        {
            "shutdown_forwarded": True,
            "prior": counters(seq - 1, requests),
            "first_failure": None,
        },
    )


def send(collector, value, *, emit=True):
    response = collector.ingest(wire.encode_private("receipt", value))
    if emit:
        collector.emitted(response)
    return response


def started(*, armed=True):
    collector = fixture.Collector(LANE, clock=lambda: 101)
    for role in wire.ROLES:
        send(collector, receipt(role=role))
    if armed:
        collector.control("arm", CASE, wire.encode_private("arm", arm()))
    return collector


def snapshot(collector, case_id=None, cursor=0):
    return wire.decode_private("readback", collector.readback(cursor, case_id))


def headers(body=b"", *, key=INGEST, method="POST"):
    value = [("X-Agent-Reference-Observer-Key", key.decode("ascii"))]
    if method == "POST":
        value.extend(
            [("Content-Length", str(len(body))), ("Content-Type", "application/json")]
        )
    return value


def test_ready_registration_requires_full_response_emission():
    collector = fixture.Collector(LANE, clock=lambda: 101)
    first = send(collector, receipt(), emit=False)
    send(collector, receipt(role="api-replica"))
    assert snapshot(collector)["lane"]["roles_ready"] == ["api-replica"]
    assert len(snapshot(collector)["records"]) == 2
    collector.emitted(first)
    assert snapshot(collector)["lane"]["roles_ready"] == list(wire.ROLES)
    ack = collector.control("arm", CASE, wire.encode_private("arm", arm()))
    assert wire.decode_private("control_ack", ack.body)["state"] == "armed"


def test_failed_ready_emission_keeps_admission_without_ready_or_retry():
    collector = fixture.Collector(LANE, clock=lambda: 101)
    response = send(collector, receipt(), emit=False)
    collector.emission_failed(response)
    assert snapshot(collector)["total"] == 1
    assert snapshot(collector)["lane"]["roles_ready"] == []
    assert snapshot(collector)["lane"]["first_failure"] == "transport_failure"
    with pytest.raises(wire.ContractError, match="sequence_mismatch"):
        send(collector, receipt())


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("duplicate", "sequence_mismatch"),
        ("gap", "sequence_mismatch"),
        ("nonce", "identity_mismatch"),
        ("lane", "identity_mismatch"),
    ],
)
def test_ingestion_history_rejects_identity_and_sequence_drift(mutation, code):
    collector = started()
    value = receipt("sdk_request", seq=2, payload=sdk_payload())
    if mutation == "duplicate":
        value["seq"] = 1
    elif mutation == "gap":
        value["seq"] = 3
    elif mutation == "nonce":
        value["nonce"] = "f" * 32
    else:
        value["lane_id"] = "e" * 32
    with pytest.raises(wire.ContractError, match=code):
        send(collector, value)
    assert snapshot(collector)["total"] == 2
    assert snapshot(collector)["lane"]["first_failure"] is not None


def test_actual_signed_identity_association_is_pending_not_db_proof():
    collector = started()
    response = send(collector, receipt("sdk_request", seq=2, payload=sdk_payload()))
    assert wire.decode_private("ack", response.body)["case_id"] == CASE
    page = snapshot(collector, CASE)
    assert page["records"][0]["association"] == "active-arm-pending-db"
    assert page["records"][0]["index"] == 2
    assert page["case"]["run_id"] is None
    assert page["case"]["sdk_receipt_count"] == page["case"]["request_count"] == 1


@pytest.mark.parametrize(
    "identity", ["solution", "execution", "engine", "verification"]
)
def test_unknown_signed_identity_is_retained_unbound(identity):
    collector = started()
    payload = sdk_payload()
    if identity == "verification":
        payload["verification"] = {
            "outcome": "unverified",
            "reason": "jwt_rejected",
            "claims": None,
        }
        payload["failures"] = ["jwt_rejected"]
    else:
        key, value = {
            "solution": ("engine_solution_id", RUN),
            "execution": ("engine_execution_id", None),
            "engine": ("engine", False),
        }[identity]
        payload["verification"]["claims"][key] = value
    response = send(collector, receipt("sdk_request", seq=2, payload=payload))
    assert wire.decode_private("ack", response.body)["case_id"] is None
    assert snapshot(collector)["records"][-1]["association"] == "unbound"
    assert snapshot(collector)["lane"]["first_failure"] == "unbound_sdk"
    assert snapshot(collector, CASE)["total"] == 0


def test_invalid_body_with_matching_signed_identity_is_failed_associated():
    collector = started()
    payload = sdk_payload()
    payload["request"].update(value=None, reason="body_invalid")
    payload["failures"] = ["body_invalid"]
    send(collector, receipt("sdk_request", seq=2, payload=payload))
    assert snapshot(collector, CASE)["total"] == 1
    assert snapshot(collector, CASE)["case"]["first_failure"] == "body_invalid"


def test_body_solution_conflict_is_retained_without_claiming_lineage():
    collector = started()
    payload = sdk_payload()
    payload["request"]["value"]["solution"] = RUN
    send(collector, receipt("sdk_request", seq=2, payload=payload))
    assert snapshot(collector, CASE)["case"]["first_failure"] == "cross_case_sdk"


def test_immutable_ledger_does_not_follow_input_or_readback_mutation():
    collector = started()
    value = receipt("sdk_request", seq=2, payload=sdk_payload())
    send(collector, value)
    original = collector.readback(0, CASE)
    value["payload"]["verification"]["claims"]["sub"] = SOLUTION
    page = snapshot(collector, CASE)
    page["records"][0]["receipt"]["payload"]["verification"]["claims"]["sub"] = SOLUTION
    assert collector.readback(0, CASE) == original


def test_closed_state_requires_both_actual_response_emissions():
    collector = started()
    collector.control("bind", CASE, wire.encode_private("bind", bind()))
    collector.control("close", CASE, wire.encode_private("close", close()))
    first = send(collector, closed(), emit=False)
    second = send(collector, closed(role="api-replica"), emit=False)
    assert snapshot(collector)["lane"]["roles_closed"] == []
    collector.emitted(first)
    assert snapshot(collector, CASE)["case"]["state"] == "closing"
    collector.emitted(second)
    assert snapshot(collector, CASE)["case"]["state"] == "closed"
    assert snapshot(collector)["lane"]["roles_closed"] == list(wire.ROLES)
    # This server emission is still not proof the API received its ACK.
    assert (
        wire.decode_private("finish_request", collector.finish_request())["finish_id"]
        == FINISH
    )


def test_failed_closed_write_never_publishes_closed_state():
    collector = started()
    collector.control("bind", CASE, wire.encode_private("bind", bind()))
    collector.control("close", CASE, wire.encode_private("close", close()))
    failed = send(collector, closed(), emit=False)
    collector.emission_failed(failed)
    send(collector, closed(role="api-replica"))
    assert snapshot(collector, CASE)["case"]["state"] == "closing"
    assert snapshot(collector)["lane"]["roles_closed"] == ["api-replica"]
    assert snapshot(collector)["lane"]["first_failure"] == "transport_failure"


def test_closed_counter_parity_cannot_hide_an_omitted_sdk_record():
    collector = started()
    collector.control("bind", CASE, wire.encode_private("bind", bind()))
    collector.control("close", CASE, wire.encode_private("close", close()))
    value = closed(requests=1)
    with pytest.raises(wire.ContractError, match="invalid_state"):
        send(collector, value)
    assert snapshot(collector)["lane"]["roles_closed"] == []


def test_late_sdk_remains_visible_and_fails_closed_nominal():
    collector = started()
    collector.control("bind", CASE, wire.encode_private("bind", bind()))
    collector.control("close", CASE, wire.encode_private("close", close()))
    send(collector, receipt("sdk_request", seq=2, payload=sdk_payload()))
    page = snapshot(collector, CASE)
    assert page["records"][0]["association"] == "late"
    assert page["case"]["first_failure"] == "late_witness"


def test_sdk_capacity_is_per_case_across_roles_and_saturates_failure():
    collector = started()
    for index in range(wire.MAX_SDK_RECEIPTS_PER_CASE):
        send(collector, receipt("sdk_request", seq=index + 2, payload=sdk_payload()))
    value = receipt("sdk_request", role="api-replica", seq=2, payload=sdk_payload())
    with pytest.raises(wire.ContractError, match="capacity_exhausted"):
        send(collector, value)
    page = snapshot(collector, CASE)
    assert page["total"] == wire.MAX_SDK_RECEIPTS_PER_CASE
    assert page["case"]["sdk_receipt_count"] == wire.MAX_SDK_RECEIPTS_PER_CASE + 1
    assert page["case"]["first_failure"] == "collector_capacity"


@pytest.mark.parametrize(
    "family,value", [("arm", arm()), ("bind", bind()), ("close", close())]
)
def test_duplicate_controls_are_never_idempotent(family, value):
    collector = started(armed=False)
    collector.control("arm", CASE, wire.encode_private("arm", arm()))
    if family in ("bind", "close"):
        collector.control("bind", CASE, wire.encode_private("bind", bind()))
    if family == "close":
        collector.control("close", CASE, wire.encode_private("close", close()))
    with pytest.raises(wire.ContractError, match="invalid_state"):
        collector.control(family, CASE, wire.encode_private(family, value))


@pytest.mark.parametrize(
    "body", [b"{}", b'{"x":1,"x":2}', b"[]", b"NaN", b"\xef\xbb\xbf{}"]
)
def test_malformed_receipt_cannot_allocate_ledger_identity(body):
    collector = fixture.Collector(LANE)
    with pytest.raises(wire.ContractError):
        collector.ingest(body)
    assert snapshot(collector)["total"] == 0


@pytest.mark.parametrize(
    "extra",
    [
        ("X-Agent-Reference-Observer-Key", INGEST.decode()),
        ("Content-Length", "2"),
        ("Content-Type", "application/json"),
        ("Transfer-Encoding", "chunked"),
        ("Content-Encoding", "identity"),
    ],
)
def test_duplicate_or_encoded_private_headers_rejected_before_body(extra):
    route = fixture.PrivateRoute("receipt")
    with pytest.raises(wire.ContractError):
        fixture.private_length("POST", headers(b"{}") + [extra], route, INGEST, CONTROL)


@pytest.mark.parametrize("key", [CONTROL, b"c" * 64, b"a" * 63, b"A" * 64])
def test_wrong_or_cross_role_capability_never_admits_ingress(key):
    with pytest.raises(wire.ContractError, match="unauthorized"):
        fixture.private_length(
            "POST",
            headers(b"{}", key=key),
            fixture.PrivateRoute("receipt"),
            INGEST,
            CONTROL,
        )


@pytest.mark.parametrize("name", ["Host", "Accept"])
def test_all_duplicate_header_names_fail_case_insensitively_before_body(name):
    values = headers(b"{}") + [(name, "ordinary"), (name.lower(), "ordinary")]
    with pytest.raises(wire.ContractError, match="invalid_json"):
        fixture.private_length(
            "POST", values, fixture.PrivateRoute("receipt"), INGEST, CONTROL
        )


def test_control_capability_cannot_be_replaced_with_ingestion():
    with pytest.raises(wire.ContractError, match="unauthorized"):
        fixture.private_length(
            "GET",
            headers(method="GET"),
            fixture.PrivateRoute("finish_request"),
            INGEST,
            CONTROL,
        )


@pytest.mark.parametrize(
    "target",
    [
        "/__agent-reference/observer/sdk-receipts?cursor=00",
        "/__agent-reference/observer/sdk-receipts?cursor=257",
        "/__agent-reference/observer/sdk-receipts?cursor=1&cursor=1",
        "/__agent-reference/observer/sdk-receipts?unknown=0",
        "/__agent-reference/observer/host-disposition?cursor=0",
        "https://scheduler-fixtures:8443/__agent-reference/observer/host-disposition",
    ],
)
def test_private_targets_reject_duplicate_alias_or_arbitrary_url(target):
    with pytest.raises(wire.ContractError):
        fixture.private_route("GET", target)


@pytest.mark.parametrize(
    "value", [[("Content-Length", "1")], [("Content-Type", "application/json")]]
)
def test_private_get_has_no_declared_body_or_content_type(value):
    with pytest.raises(wire.ContractError, match="invalid_json"):
        fixture.private_length(
            "GET",
            headers(key=CONTROL, method="GET") + value,
            fixture.PrivateRoute("finish_request"),
            INGEST,
            CONTROL,
        )


def test_public_credential_files_are_not_accepted_as_private_material(tmp_path):
    path = tmp_path / "cap"
    path.write_bytes(INGEST)
    path.chmod(0o600)
    assert fixture.load_private_file(path, capability=True) == INGEST
    path.chmod(0o644)
    with pytest.raises(wire.ContractError, match="internal_failure"):
        fixture.load_private_file(path, capability=True)


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "wrong-owner", "newline", "uppercase"]
)
def test_capability_loading_rejects_unsafe_material_without_value_errors(
    tmp_path, monkeypatch, kind
):
    path = tmp_path / "cap"
    path.write_bytes(INGEST)
    path.chmod(0o600)
    if kind == "symlink":
        path = tmp_path / "link"
        path.symlink_to(tmp_path / "cap")
    elif kind == "hardlink":
        os.link(path, tmp_path / "other")
    elif kind == "wrong-owner":
        monkeypatch.setattr(fixture.os, "geteuid", lambda: os.stat(path).st_uid + 1)
    else:
        path.write_bytes(INGEST + b"\n" if kind == "newline" else INGEST.upper())
    with pytest.raises(wire.ContractError) as error:
        fixture.load_private_file(path, capability=True)
    assert error.value.args == ("internal_failure",)
    assert INGEST.decode() not in str(error.value)


def test_private_error_is_canonical_static_and_value_free():
    response = fixture.private_error(wire.ContractError("unauthorized"))
    assert response.status == 401
    assert json.loads(response.body) == {
        "schema": "bifrost.agent-reference.private-error/v1",
        "error": "unauthorized",
    }


@pytest.mark.parametrize("kind", ["capability", "private-pem", "public-ca"])
def test_material_loaders_reject_fifo_without_writer_nonblocking(
    tmp_path, monkeypatch, kind
):
    path = tmp_path / "material-fifo"
    os.mkfifo(path, 0o600)
    original_open = os.open
    observed = []

    def checked_open(selected, flags, *args, **kwargs):
        if selected == path:
            # Fail before a blocking open even if the source guard regresses.
            assert flags & os.O_NONBLOCK
            assert flags & os.O_NOFOLLOW
            observed.append(selected)
        return original_open(selected, flags, *args, **kwargs)

    monkeypatch.setattr(fixture.os, "open", checked_open)
    with pytest.raises(wire.ContractError) as error:
        if kind == "public-ca":
            fixture.load_public_certificate(path)
        else:
            fixture.load_private_file(path, capability=kind == "capability")
    assert observed == [path]
    assert error.value.args == ("internal_failure",)
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "ingestion,control",
    [
        (INGEST, INGEST),
        (b"", CONTROL),
        (INGEST, CONTROL[:-1]),
        (INGEST.upper(), CONTROL),
        (INGEST, CONTROL + b"\n"),
        (INGEST.decode(), CONTROL),
        (INGEST, bytearray(CONTROL)),
        (None, CONTROL),
    ],
)
def test_private_server_rejects_invalid_or_equal_keys_before_binding(
    monkeypatch, ingestion, control
):
    def forbidden_bind(*args, **kwargs):
        pytest.fail("capability rejection must precede socket binding")

    monkeypatch.setattr(fixture.socketserver.TCPServer, "__init__", forbidden_bind)
    with pytest.raises(wire.ContractError) as error:
        fixture.PrivateServer(
            ("127.0.0.1", 0), fixture.Collector(LANE), object(), ingestion, control
        )
    assert error.value.args == ("internal_failure",)
    assert error.value.__context__ is None


def test_private_server_accepts_distinct_shaped_keys_before_owned_bind(monkeypatch):
    observed = []

    def owned_bind(server, address, handler):
        observed.append((address, handler))

    monkeypatch.setattr(fixture.socketserver.TCPServer, "__init__", owned_bind)
    server = fixture.PrivateServer(
        ("127.0.0.1", 0), fixture.Collector(LANE), object(), INGEST, CONTROL
    )
    assert observed == [(("127.0.0.1", 0), fixture.PrivateHandler)]
    assert server.ingestion_key == INGEST and server.control_key == CONTROL


class UnitSocket:
    """Finite parser instrument; not an actual TLS or nominal witness."""

    def __init__(self, frame, *, fail_write=False, before_write=None):
        self.frame = bytearray(frame)
        self.sent = None
        self.fail_write = fail_write
        self.before_write = before_write
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, size):
        value = bytes(self.frame[:size])
        del self.frame[:size]
        return value

    def sendall(self, value):
        if self.before_write:
            self.before_write()
        if self.fail_write:
            raise OSError("UNIT write failure")
        self.sent = value


def unit_frame(body, *, extra_headers="", size=None):
    return (
        "POST /__agent-reference/observer/sdk-receipts HTTP/1.1\r\n"
        "Host: scheduler-fixtures:8443\r\n"
        f"X-Agent-Reference-Observer-Key: {INGEST.decode()}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body) if size is None else size}\r\n"
        f"{extra_headers}\r\n"
    ).encode() + body


def unit_handle(collector, connection):
    server = SimpleNamespace(
        collector=collector, ingestion_key=INGEST, control_key=CONTROL
    )
    fixture.PrivateHandler(connection, ("UNIT", 0), server)


def test_handler_publishes_ready_only_after_complete_write():
    collector = fixture.Collector(LANE)
    body = wire.encode_private("receipt", receipt())

    def before_write():
        assert collector._lane()["roles_ready"] == []
        assert snapshot(collector)["total"] == 1

    connection = UnitSocket(unit_frame(body), before_write=before_write)
    unit_handle(collector, connection)
    assert connection.sent.startswith(b"HTTP/1.1 201 ")
    assert collector._lane()["roles_ready"] == ["api"]
    assert all(
        0 < value <= wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS
        for value in connection.timeouts
    )


def test_handler_failed_ack_write_retains_receipt_without_ready():
    collector = fixture.Collector(LANE)
    body = wire.encode_private("receipt", receipt())
    connection = UnitSocket(unit_frame(body), fail_write=True)
    unit_handle(collector, connection)
    assert connection.sent is None
    assert snapshot(collector)["total"] == 1
    assert collector._lane()["roles_ready"] == []
    assert collector.first_failure == "transport_failure"


@pytest.mark.parametrize(
    "kind",
    [
        "short",
        "trailing-json",
        "pipeline",
        "duplicate-host",
        "duplicate-accept",
        "encoding",
    ],
)
def test_handler_rejects_detected_bad_frames_without_admission(kind):
    collector = fixture.Collector(LANE)
    body = wire.encode_private("receipt", receipt())
    options = {}
    if kind == "short":
        options["size"] = len(body) + 1
    elif kind == "trailing-json":
        body += b"x"
    elif kind == "pipeline":
        options["size"] = len(body)
        body += b"GET / HTTP/1.1\r\n\r\n"
    elif kind == "duplicate-host":
        options["extra_headers"] = "host: UNIT\r\n"
    elif kind == "duplicate-accept":
        options["extra_headers"] = "Accept: */*\r\naccept: */*\r\n"
    else:
        options["extra_headers"] = "Content-Encoding: gzip\r\n"
    connection = UnitSocket(unit_frame(body, **options))
    unit_handle(collector, connection)
    assert connection.sent.startswith(b"HTTP/1.1 400 ")
    assert snapshot(collector)["total"] == 0
    assert collector._lane()["roles_ready"] == []
    if kind in ("duplicate-host", "duplicate-accept", "encoding"):
        assert bytes(connection.frame) == body


def test_private_handler_does_not_wait_for_eof_after_exact_declared_frame():
    collector = fixture.Collector(LANE)
    body = wire.encode_private("receipt", receipt())
    connection = UnitSocket(unit_frame(body))
    unit_handle(collector, connection)
    assert connection.sent.startswith(b"HTTP/1.1 201 ")
    # This demonstrates framing only, never future-byte or TCP EOF absence.
    assert not connection.frame


def unit_certificates(tmp_path, *, name="scheduler-fixtures", expired=False):
    """Existing locked cryptography only; synthetic UNIT TLS identities."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UNIT task CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, None, None),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(
            now - timedelta(days=1) if expired else now + timedelta(days=1)
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(name), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    paths = tuple(tmp_path / basename for basename in ("ca.pem", "leaf.pem", "key.pem"))
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    paths[2].chmod(0o600)
    return paths


def unit_handshake(client_context, server_context):
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    peer_incoming, peer_outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_context.wrap_bio(
        incoming, outgoing, server_hostname="scheduler-fixtures"
    )
    server = server_context.wrap_bio(peer_incoming, peer_outgoing, server_side=True)
    done = set()
    for _ in range(128):
        for name, connection in (("client", client), ("server", server)):
            if name not in done:
                try:
                    connection.do_handshake()
                except ssl.SSLWantReadError:
                    pass
                else:
                    done.add(name)
        if outgoing.pending:
            peer_incoming.write(outgoing.read())
        if peer_outgoing.pending:
            incoming.write(peer_outgoing.read())
        if len(done) == 2:
            return client, server
    pytest.fail("bounded UNIT TLS handshake did not complete")


def test_task_tls_uses_only_explicit_ca_and_verified_name(tmp_path, monkeypatch):
    ca, leaf, key = unit_certificates(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "absent-ambient-ca"))
    client = fixture.client_tls(ca)
    server = fixture.server_tls(leaf, key)
    assert client.verify_mode == ssl.CERT_REQUIRED
    assert client.check_hostname
    assert client.minimum_version == ssl.TLSVersion.TLSv1_2
    assert client.cert_store_stats()["x509_ca"] == 1
    client_connection, _ = unit_handshake(client, server)
    assert client_connection.version() in ("TLSv1.2", "TLSv1.3")


@pytest.mark.parametrize("kind", ["wrong-ca", "wrong-san", "expired"])
def test_task_tls_rejects_untrusted_identity_before_http(tmp_path, kind):
    ca, leaf, key = unit_certificates(
        tmp_path,
        name="wrong.invalid" if kind == "wrong-san" else "scheduler-fixtures",
        expired=kind == "expired",
    )
    if kind == "wrong-ca":
        alternate = tmp_path / "alternate"
        alternate.mkdir()
        ca, _, _ = unit_certificates(alternate)
    with pytest.raises(ssl.SSLCertVerificationError):
        unit_handshake(fixture.client_tls(ca), fixture.server_tls(leaf, key))


def test_task_tls_rejects_plaintext_http_downgrade(tmp_path):
    _, leaf, key = unit_certificates(tmp_path)
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    server = fixture.server_tls(leaf, key).wrap_bio(
        incoming, outgoing, server_side=True
    )
    incoming.write(b"GET / HTTP/1.1\r\nHost: UNIT\r\n\r\n")
    with pytest.raises(ssl.SSLError):
        server.do_handshake()


def test_task_tls_rejects_symlink_ca_and_encrypted_key_without_prompt(tmp_path):
    from cryptography.hazmat.primitives import serialization

    ca, leaf, key = unit_certificates(tmp_path)
    link = tmp_path / "ca-link.pem"
    link.symlink_to(ca)
    with pytest.raises(wire.ContractError, match="internal_failure"):
        fixture.client_tls(link)
    private_key = serialization.load_pem_private_key(key.read_bytes(), password=None)
    key.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"UNIT-only-password"),
        )
    )
    with pytest.raises(wire.ContractError) as error:
        fixture.server_tls(leaf, key)
    assert error.value.args == ("internal_failure",)


class UnitHostResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status_code = status
        self.headers = {"content-type": "application/json"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *unused):
        return None

    async def aiter_raw(self, *, chunk_size):
        assert chunk_size == wire.FAMILY_BYTE_CAPS["finish_request"] + 1
        for index in range(0, len(self.body), 7):
            yield self.body[index : index + 7]


def unit_host_client(monkeypatch, response):
    seen = []
    context = object()
    monkeypatch.setattr(fixture, "client_tls", lambda path: context)

    def private_file(path, *, capability):
        assert str(path) == "/run/agent-reference/observer-control-key"
        assert capability
        return CONTROL

    monkeypatch.setattr(fixture, "load_private_file", private_file)

    class Client:
        def __init__(self, **options):
            assert options == {
                "verify": context,
                "trust_env": False,
                "follow_redirects": False,
                "timeout": wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS,
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, *unused):
            return None

        def stream(self, method, url, *, headers):
            assert method == "GET"
            assert (
                url
                == "https://127.0.0.1:8443/__agent-reference/observer/host-disposition"
            )
            assert headers == {"X-Agent-Reference-Observer-Key": CONTROL.decode()}
            seen.append(url)
            return response

    monkeypatch.setattr(fixture.httpx, "AsyncClient", Client)
    return seen


async def test_fixed_host_reader_returns_exact_shared_canonical_snapshot(monkeypatch):
    body = fixture.Collector(LANE).finish_request()
    seen = unit_host_client(monkeypatch, UnitHostResponse(body))
    assert await fixture.host_disposition(LANE) == body
    assert len(seen) == 1


@pytest.mark.parametrize(
    "kind", [301, 302, 307, 308, "oversized", "noncanonical", "identity", "encoding"]
)
async def test_fixed_host_reader_fails_without_redirect_retry_or_raw_error(
    monkeypatch, kind
):
    body = fixture.Collector("9" * 32 if kind == "identity" else LANE).finish_request()
    response = UnitHostResponse(body, kind if type(kind) is int else 200)
    if kind == "oversized":
        response.body = b"x" * (wire.FAMILY_BYTE_CAPS["finish_request"] + 1)
    elif kind == "noncanonical":
        response.body += b"\n"
    elif kind == "encoding":
        response.headers["content-type"] = "text/html"
    seen = unit_host_client(monkeypatch, response)
    with pytest.raises(wire.ContractError) as error:
        await fixture.host_disposition(LANE)
    assert error.value.args == ("internal_failure",)
    assert error.value.__context__ is None
    assert len(seen) == 1
    assert CONTROL.decode() not in str(error.value)


# Pure UNIT configuration. Never loads, arms or mutates the live fixture instance.
MODEL_CONFIG = {
    "schema": "bifrost.agent-reference.model-oracle-input/v1",
    "lane_id": LANE,
    "case_id": CASE,
    "model_key": "6" * 64,
    "password": "7" * 64,
    "visa": "8" * 64,
    "partner_name": "C1R Synthetic Partner",
    "username": "c1r-reference@example.invalid",
}
UNIT_FACTS = {
    "os": "Linux",
    "arch": "x64",
    "runtime": "CPython",
    "version": "3.14.7",
    "model_encoding": "gzip, deflate, zstd",
    "cove_encoding": "gzip, deflate",
}


def oracle_started():
    collector = fixture.Collector(LANE)
    oracle = fixture.Oracle(collector, MODEL_CONFIG, UNIT_FACTS)
    for role in wire.ROLES:
        send(collector, receipt(role=role))
    collector.control("arm", CASE, wire.encode_private("arm", arm()))
    collector.control("bind", CASE, wire.encode_private("bind", bind()))
    return oracle


def unit_tool_content():
    # A synthetic UNIT input, never a response generated by the real fixture.
    return json.dumps(
        {
            "success": True,
            "observed_at": "2026-10-01T12:34:56.123456+00:00",
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
    )


def oracle_input(kind, *, tool_content=None):
    from urllib.parse import urlencode

    base = f"/__agent-reference/{CASE}"
    method = "POST"
    target = base + "/model/v1/chat/completions"
    if kind == "responses_probe":
        target = base + "/model/v1/responses"
        value = {
            "model": "gpt-5.4-mini",
            "input": "Reply with OK.",
            "max_output_tokens": 64,
            "store": False,
        }
    elif kind == "chat_probe":
        value = {
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_completion_tokens": 64,
        }
    elif kind in ("agent_first", "agent_final"):
        messages = [
            {
                "role": "system",
                "content": fixture.AUTHORED_PROMPT + fixture.AUTONOMOUS_SUFFIX,
            },
            {"role": "user", "content": json.dumps(fixture.TASK)},
        ]
        if kind == "agent_final":
            messages += [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_c1r_capacity",
                            "type": "function",
                            "function": {
                                "name": "cove_data_protection_recovery_steward_inspect_capacity",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_c1r_capacity",
                    "content": unit_tool_content()
                    if tool_content is None
                    else tool_content,
                },
            ]
        value = {
            "model": "gpt-5.4-mini",
            "messages": messages,
            "tools": fixture.TOOLS,
            "tool_choice": "auto",
            "stream": False,
            "max_completion_tokens": 8192,
            "store": False,
        }
    elif kind == "summary":
        summary_input = {
            "agent_name": "Cove Recovery Steward Escalation Analyst",
            "agent_system_prompt": fixture.AUTHORED_PROMPT[:2000],
            "input": fixture.TASK,
            "output": {"text": fixture.FINAL_TEXT},
        }
        value = {
            "model": "gpt-5.4-mini",
            "messages": [
                {"role": "system", "content": fixture.SUMMARY_PROMPT},
                {"role": "user", "content": json.dumps(summary_input, default=str)},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "store": False,
        }
    elif kind == "cove_login":
        target = base + "/cove/jsonapi"
        value = {
            "jsonrpc": "2.0",
            "method": "Login",
            "params": {
                "partner": "C1R Synthetic Partner",
                "username": "c1r-reference@example.invalid",
                "password": MODEL_CONFIG["password"],
            },
            "id": "12345678-1234-4234-8234-123456789abc",
        }
    else:
        method = "GET"
        query = {"offset": "0", "limit": "100"}
        if kind == "cove_agents":
            target = (
                base + "/cove/draas/actual-statistics/v1/dashboard/recovery-agents/"
            )
            query.update(
                {
                    "filter[agent_state.in]": "ONLINE,OFFLINE,STORAGE_NOT_CONFIGURED",
                    "filter[materialized_path.contains]": "/7/",
                    "sort": "name",
                }
            )
        else:
            target = base + "/cove/draas/actual-statistics/v1/dashboard/"
            query.update(
                {
                    "fields": "",
                    "sort": "-start_restore_timestamp",
                    "filter[type.in]": "AZURE,ESXI_ON_DEMAND,SELF_HOSTED_ON_DEMAND",
                    "filter[partner_materialized_path.contains]": "/7/",
                }
            )
        target += "?" + urlencode(query)
        value = None
    body = (
        b""
        if value is None
        else json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    )
    cove = kind.startswith("cove_")
    headers = {
        "Host": "scheduler-fixtures:8080",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*" if cove else "application/json",
        "Accept-Encoding": "gzip, deflate" if cove else "gzip, deflate, zstd",
        "Connection": "keep-alive",
        "User-Agent": "python-httpx/0.28.1"
        if cove
        else (
            "AsyncOpenAI/Python 3.3.0"
            if kind.endswith("probe")
            else "pydantic-ai/2.35.3"
        ),
    }
    if method == "POST":
        headers["Content-Length"] = str(len(body))
    if cove:
        headers.update(
            Origin="https://backup.management", Referer="https://backup.management/"
        )
        if kind != "cove_login":
            headers["Authorization"] = "Bearer " + MODEL_CONFIG["visa"]
    else:
        headers.update(
            {
                "Authorization": "Bearer " + MODEL_CONFIG["model_key"],
                "X-Stainless-Lang": "python",
                "X-Stainless-Package-Version": "3.3.0",
                "X-Stainless-OS": "Linux",
                "X-Stainless-Arch": "x64",
                "X-Stainless-Runtime": "CPython",
                "X-Stainless-Runtime-Version": "3.14.7",
                "X-Stainless-Async": "async:asyncio",
                "X-Stainless-Retry-Count": "0",
                "X-Stainless-Read-Timeout": "600",
            }
        )
    return method, target, list(headers.items()), body


PROGRAM = (
    "responses_probe",
    "chat_probe",
    "agent_first",
    "cove_login",
    "cove_agents",
    "cove_dashboard",
    "agent_final",
    "summary",
)


def advance(oracle, stop):
    for kind in PROGRAM[: PROGRAM.index(stop)]:
        index = oracle.reserve()
        response = oracle.prepare(index, *oracle_input(kind))
        assert response.matched
        oracle.settle(index, response, write_complete=True)


@pytest.mark.parametrize("second_pair", [False, True])
def test_finite_program_and_independent_response_envelopes(second_pair):
    oracle = oracle_started()
    sequence = list(PROGRAM)
    if second_pair:
        sequence[-1:-1] = ["responses_probe", "chat_probe"]
    responses = []
    for kind in sequence:
        if kind == "cove_login":
            send(oracle.collector, receipt("sdk_request", seq=2, payload=sdk_payload()))
        index = oracle.reserve()
        pending = wire.decode_private("model_readback", oracle.readback(CASE, index))
        assert pending["records"][0] == {
            "index": index,
            "kind": "unexpected",
            "settled": False,
            "matched": None,
            "status": None,
            "write_complete": False,
            "tool_content": None,
        }
        response = oracle.prepare(index, *oracle_input(kind))
        assert response.matched and response.kind == kind
        # Semantic validation cannot publish a premature matched or emitted status.
        assert (
            wire.decode_private("model_readback", oracle.readback(CASE, index))[
                "records"
            ][0]
            == pending["records"][0]
        )
        oracle.settle(index, response, write_complete=True)
        responses.append(response)
    assert oracle.phase == "done" and oracle.pending == 0
    assert oracle.collector._case["request_count"] == len(sequence) + 1
    assert oracle.collector.first_failure is None
    page = wire.decode_private("model_readback", oracle.readback(CASE, 0))
    assert page["next_offset"] == 4 and page["attempt_count"] == len(sequence) + 1
    rows = []
    offset = 0
    while True:
        page = wire.decode_private("model_readback", oracle.readback(CASE, offset))
        rows.extend(page["records"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert [r["kind"] for r in rows] == sequence
    assert [r["tool_content"] for r in rows if r["kind"] == "agent_final"] == [
        unit_tool_content()
    ]
    assert all(r["settled"] and r["matched"] and r["write_complete"] for r in rows)
    chat = json.loads(responses[1].body)
    assert chat["id"] == f"chatcmpl-c1r-probe-{CASE}-1" and chat["usage"] == {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }
    first = json.loads(responses[2].body)
    assert set(first) == {"id", "object", "created", "model", "choices", "usage"}
    assert first["choices"][0]["message"]["content"] is None
    assert (
        first["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == "{}"
    )
    assert first["usage"] == {
        "prompt_tokens": 17,
        "completion_tokens": 5,
        "total_tokens": 22,
    }
    final = json.loads(responses[sequence.index("agent_final")].body)
    assert final["choices"][0]["message"]["content"] == fixture.FINAL_TEXT
    assert final["usage"] == {
        "prompt_tokens": 29,
        "completion_tokens": 11,
        "total_tokens": 40,
    }
    frames = responses[-1].body.split(b"\n\n")
    assert responses[-1].content_type == "text/event-stream"
    assert frames[-2] == b"data: [DONE]" and frames[-1] == b""
    chunks = [json.loads(f.removeprefix(b"data: ")) for f in frames[:-2]]
    assert len(chunks) == 4 and all(
        chunk["object"] == "chat.completion.chunk" for chunk in chunks
    )
    assert (
        "usage" not in chunks[0]
        and "usage" not in chunks[1]
        and "usage" not in chunks[2]
    )
    assert chunks[3]["choices"] == [] and chunks[3]["usage"] == {
        "prompt_tokens": 37,
        "completion_tokens": 13,
        "total_tokens": 50,
    }
    summary = json.loads(chunks[1]["choices"][0]["delta"]["content"])
    assert summary["confidence"] == 0.4 and summary["metadata"] == {
        "fixture_case": CASE
    }
    assert MODEL_CONFIG["model_key"].encode() not in oracle.readback(CASE, 0)
    oracle.collector.control("close", CASE, wire.encode_private("close", close()))
    send(oracle.collector, closed("api", seq=3, requests=1))
    send(oracle.collector, closed("api-replica", seq=2, requests=0))
    final_page = wire.decode_private("model_readback", oracle.readback(CASE, 0))
    assert final_page["state"] == "closed" and final_page["attempt_count"] in (9, 11)
    assert final_page["first_failure"] is None


def test_all_prompts_equal_complete_frozen_source_literal_pins():
    import hashlib

    # Derived statically from pinned declaration/helpers/summary source; no product imports.
    assert (
        hashlib.sha256(fixture.AUTHORED_PROMPT.encode()).hexdigest()
        == "053913cb8b0c7ec72f6c9aff818ac0adb9c868614cd94bf142bfd38e8bbd864f"
    )
    assert (
        hashlib.sha256(fixture.AUTONOMOUS_SUFFIX.encode()).hexdigest()
        == "c559909dedfcc3ccfb365635faa29d15c0ccaac3f035503a8e35762c0de63ad5"
    )
    assert (
        hashlib.sha256(fixture.SUMMARY_PROMPT.encode()).hexdigest()
        == "d879b469a223a71ad404a22812a6235f96a20b7b707d0b8d2c2bfff3c0d426ab"
    )
    assert (
        fixture.AUTHORED_PROMPT.endswith("\n") and len(fixture.AUTHORED_PROMPT) < 2000
    )
    capacity, preview = fixture.TOOLS
    assert capacity["function"]["parameters"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert "strict" not in capacity["function"] and "strict" not in preview["function"]
    parameters = preview["function"]["parameters"]
    assert parameters["required"] == [
        "device_id",
        "recovery_agent_id",
        "vm_name",
        "halo_ticket_id",
    ]
    assert parameters["properties"]["vhd_path"] == {
        "type": "string",
        "title": "Vhd Path",
        "default": "E:\\",
    }
    assert parameters["properties"]["cpu_count"] == {
        "type": "integer",
        "title": "Cpu Count",
        "default": 2,
    }
    assert parameters["properties"]["ram_size_mb"] == {
        "type": "integer",
        "title": "Ram Size Mb",
        "default": 4096,
    }


@pytest.mark.parametrize(
    "defect",
    [
        "drop-tool",
        "extra-tool",
        "rename",
        "default",
        "title",
        "strict",
        "capacity-required",
        "preview-required",
        "prompt",
        "suffix",
        "extra-field",
        "stream",
        "store",
        "max",
        "bool-max",
        "user-json",
    ],
)
def test_agent_closed_catalog_prompt_and_body_rejections(defect):
    oracle = oracle_started()
    advance(oracle, "agent_first")
    method, target, headers, raw = oracle_input("agent_first")
    value = json.loads(raw)
    if defect == "drop-tool":
        value["tools"].pop()
    elif defect == "extra-tool":
        value["tools"].append(value["tools"][0])
    elif defect == "rename":
        value["tools"][0]["function"]["name"] = "preview"
    elif defect in ("default", "title"):
        value["tools"][1]["function"]["parameters"]["properties"]["cpu_count"][
            defect
        ] = 3 if defect == "default" else "Wrong"
    elif defect == "strict":
        value["tools"][0]["function"]["strict"] = True
    elif defect == "capacity-required":
        value["tools"][0]["function"]["parameters"]["required"] = []
    elif defect == "preview-required":
        value["tools"][1]["function"]["parameters"]["required"].append("cpu_count")
    elif defect == "prompt":
        value["messages"][0]["content"] = (
            "shortened authored prompt" + fixture.AUTONOMOUS_SUFFIX
        )
    elif defect == "suffix":
        value["messages"][0]["content"] = fixture.AUTHORED_PROMPT
    elif defect == "extra-field":
        value["reasoning_effort"] = "low"
    elif defect == "user-json":
        value["messages"][1]["content"] = json.dumps(
            fixture.TASK, separators=(",", ":")
        )
    else:
        key = {
            "stream": "stream",
            "store": "store",
            "max": "max_completion_tokens",
            "bool-max": "max_completion_tokens",
        }[defect]
        value[key] = (
            True
            if defect in ("stream", "bool-max")
            else (0 if defect == "store" else 4096)
        )
    body = fixture.compact(value)
    headers = [
        (key, str(len(body)) if key == "Content-Length" else val)
        for key, val in headers
    ]
    index = oracle.reserve()
    response = oracle.prepare(index, method, target, headers, body)
    assert not response.matched
    oracle.settle(index, response, write_complete=True)
    record = wire.decode_private("model_readback", oracle.readback(CASE, index))[
        "records"
    ][0]
    assert (
        record["settled"] and not record["matched"] and record["tool_content"] is None
    )
    assert oracle.collector.first_failure == "correlation_failed"
    assert body not in oracle.readback(CASE, index)


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate",
        "extra",
        "missing",
        "wrong-ua",
        "timeout-float",
        "wrong-async",
        "wrong-encoding",
        "wrong-key",
        "organization",
        "comma-key",
    ],
)
def test_public_model_header_rejections_are_retained_attempts(defect):
    oracle = oracle_started()
    method, target, headers, body = oracle_input("responses_probe")
    if defect == "duplicate":
        headers.append(("host", "scheduler-fixtures:8080"))
    elif defect in ("extra", "organization"):
        headers.append(
            ("OpenAI-Organization" if defect == "organization" else "X-Unknown", "UNIT")
        )
    elif defect == "missing":
        headers = [(key, val) for key, val in headers if key != "Accept"]
    else:
        key, value = {
            "wrong-ua": ("User-Agent", "OpenAI/Python 3.3.0"),
            "timeout-float": ("X-Stainless-Read-Timeout", "600.0"),
            "wrong-async": ("X-Stainless-Async", "false"),
            "wrong-encoding": ("Accept-Encoding", "gzip, deflate"),
            "wrong-key": ("Authorization", "Bearer " + CONTROL.decode()),
            "comma-key": (
                "Authorization",
                "Bearer " + MODEL_CONFIG["model_key"] + ",alias",
            ),
        }[defect]
        headers = [(name, value if name == key else val) for name, val in headers]
    index = oracle.reserve()
    response = oracle.prepare(index, method, target, headers, body)
    oracle.settle(index, response, write_complete=True)
    assert not response.matched and oracle.records[index]["matched"] is False
    assert (
        oracle.collector._case["request_count"] == 1
        and oracle.collector.first_failure == "correlation_failed"
    )


@pytest.mark.parametrize(
    "kind,defect",
    [
        ("cove_login", "password"),
        ("cove_login", "partner"),
        ("cove_login", "id"),
        ("cove_login", "visa"),
        ("cove_agents", "root"),
        ("cove_agents", "duplicate"),
        ("cove_agents", "visa"),
        ("cove_dashboard", "blank"),
        ("cove_dashboard", "extra"),
    ],
)
def test_cove_exact_login_root_blank_query_and_visa(kind, defect):
    oracle = oracle_started()
    advance(oracle, kind)
    method, target, headers, body = oracle_input(kind)
    if kind == "cove_login":
        value = json.loads(body)
        if defect == "visa":
            value["visa"] = MODEL_CONFIG["visa"]
        elif defect == "id":
            value["id"] = 7
        else:
            value["params"][defect] = "wrong"
        body = fixture.compact(value)
        headers = [
            (key, str(len(body)) if key == "Content-Length" else val)
            for key, val in headers
        ]
    elif defect == "visa":
        headers = [
            (key, "Bearer wrong" if key == "Authorization" else val)
            for key, val in headers
        ]
    elif defect == "root":
        target = target.replace("%2F7%2F", "%2F2674794%2F")
    elif defect == "duplicate":
        target += "&offset=0"
    elif defect == "blank":
        target = target.replace("&fields=", "")
    else:
        target += "&backup_device_id=1"
    index = oracle.reserve()
    response = oracle.prepare(index, method, target, headers, body)
    oracle.settle(index, response, write_complete=True)
    assert (
        not response.matched and oracle.collector.first_failure == "correlation_failed"
    )


@pytest.mark.parametrize(
    "defect", ["call-id", "false", "extra", "utc", "error-string", "count-bool"]
)
def test_final_tool_content_requires_actual_closed_nonsecret_received_shape(defect):
    oracle = oracle_started()
    advance(oracle, "agent_final")
    result = json.loads(unit_tool_content())
    if defect == "false":
        result["success"] = False
    elif defect == "extra":
        result["customer"] = "must-not-export"
    elif defect == "utc":
        result["observed_at"] = "2026-10-01T12:34:56Z"
    elif defect == "count-bool":
        result["capacity"]["agent_count"] = False
    content = (
        "Error: hidden failure" if defect == "error-string" else json.dumps(result)
    )
    method, target, headers, body = oracle_input("agent_final", tool_content=content)
    if defect == "call-id":
        value = json.loads(body)
        value["messages"][3]["tool_call_id"] = "wrong"
        body = fixture.compact(value)
        headers = [
            (key, str(len(body)) if key == "Content-Length" else val)
            for key, val in headers
        ]
    index = oracle.reserve()
    response = oracle.prepare(index, method, target, headers, body)
    oracle.settle(index, response, write_complete=True)
    assert not response.matched and oracle.records[index]["tool_content"] is None


def test_summary_full_prompt_and_object_output_are_closed():
    for defect in ("prompt", "bare-output", "suffix", "max", "stream", "tool-array"):
        oracle = oracle_started()
        advance(oracle, "summary")
        method, target, headers, body = oracle_input("summary")
        value = json.loads(body)
        if defect == "prompt":
            value["messages"][0]["content"] = (
                "You summarize what an AI agent did on a single run."
            )
        elif defect == "bare-output":
            content = json.loads(value["messages"][1]["content"])
            content["output"] = fixture.FINAL_TEXT
            value["messages"][1]["content"] = json.dumps(content)
        elif defect == "suffix":
            content = json.loads(value["messages"][1]["content"])
            content["agent_system_prompt"] += fixture.AUTONOMOUS_SUFFIX
            value["messages"][1]["content"] = json.dumps(content)
        else:
            value[
                {
                    "max": "max_completion_tokens",
                    "stream": "stream",
                    "tool-array": "tools",
                }[defect]
            ] = (
                8192
                if defect == "max"
                else (False if defect == "stream" else fixture.TOOLS)
            )
        body = fixture.compact(value)
        headers = [
            (key, str(len(body)) if key == "Content-Length" else val)
            for key, val in headers
        ]
        index = oracle.reserve()
        response = oracle.prepare(index, method, target, headers, body)
        oracle.settle(index, response, write_complete=True)
        assert (
            not response.matched
            and oracle.collector.first_failure == "correlation_failed"
        )


def test_shared_global_budget_reservation_failure_and_exact_once_settlement():
    oracle = oracle_started()
    send(oracle.collector, receipt("sdk_request", seq=2, payload=sdk_payload()))
    indices = [oracle.reserve() for _ in range(wire.MAX_REQUESTS_PER_CASE - 1)]
    assert oracle.collector._case["request_count"] == wire.MAX_REQUESTS_PER_CASE
    assert oracle.pending == wire.MAX_REQUESTS_PER_CASE - 1
    with pytest.raises(wire.ContractError, match="capacity_exhausted"):
        oracle.reserve()
    assert len(oracle.records) == wire.MAX_REQUESTS_PER_CASE - 1
    assert oracle.collector._case["request_count"] == wire.MAX_REQUESTS_PER_CASE + 1
    for index in reversed(indices):
        oracle.settle(index, fixture.ProgramResponse(400, b"{}"), write_complete=False)
    assert (
        oracle.pending == 0 and oracle.collector.first_failure == "collector_capacity"
    )
    before = oracle.readback(CASE, 0)
    with pytest.raises(wire.ContractError, match="invalid_state"):
        oracle.settle(0, fixture.ProgramResponse(200, b"{}"), write_complete=True)
    assert oracle.readback(CASE, 0) == before
    with pytest.raises(wire.ContractError, match="capacity_exhausted"):
        oracle.reserve()
    assert oracle.collector._case["request_count"] == wire.MAX_REQUESTS_PER_CASE + 1


def test_emission_failure_keeps_semantic_match_but_no_emitted_status():
    oracle = oracle_started()
    index = oracle.reserve()
    response = oracle.prepare(index, *oracle_input("responses_probe"))
    oracle.settle(index, response, write_complete=False)
    record = wire.decode_private("model_readback", oracle.readback(CASE, 0))["records"][
        0
    ]
    assert (
        record["matched"] is True
        and record["status"] is None
        and not record["write_complete"]
    )
    assert oracle.collector.first_failure == "transport_failure"


def test_finish_requires_quiescent_complete_program_and_closes_admission():
    oracle = oracle_started()
    with pytest.raises(wire.ContractError, match="invalid_state"):
        oracle.collector.control("close", CASE, wire.encode_private("close", close()))
    assert oracle.collector.first_failure == "product_not_settled"
    oracle = oracle_started()
    for kind in PROGRAM:
        index = oracle.reserve()
        response = oracle.prepare(index, *oracle_input(kind))
        oracle.settle(index, response, write_complete=True)
    oracle.collector.control("close", CASE, wire.encode_private("close", close()))
    assert oracle.collector._case["state"] == "closing"
    index = oracle.reserve()
    response = oracle.prepare(index, *oracle_input("responses_probe"))
    oracle.settle(index, response, write_complete=True)
    assert not response.matched and oracle.collector.first_failure == "late_witness"


def test_control_only_oracle_path_rejects_wrong_role_query_case_and_offset():
    oracle = oracle_started()
    route = fixture.private_route("GET", f"/__agent-reference/{CASE}/oracle?offset=0")
    assert route.family == "model_readback"
    with pytest.raises(wire.ContractError, match="unauthorized"):
        fixture.private_length("GET", headers_for_get(INGEST), route, INGEST, CONTROL)
    response = fixture.dispatch_private(oracle.collector, route, b"")
    assert wire.decode_private("model_readback", response.body)["case_id"] == CASE
    for path in (
        f"/__agent-reference/{CASE}/oracle?offset=00",
        f"/__agent-reference/{CASE}/oracle?offset=65",
        f"/__agent-reference/{CASE}/oracle?offset=0&offset=0",
        f"/__agent-reference/{CASE}/oracle",
        f"/__agent-reference/{CASE}/oracle?cursor=0",
    ):
        with pytest.raises(wire.ContractError):
            fixture.private_route("GET", path)
    with pytest.raises(wire.ContractError, match="unknown_case"):
        oracle.readback("9" * 32, 0)
    with pytest.raises(wire.ContractError, match="invalid_state"):
        oracle.readback(CASE, 1)


def headers_for_get(key):
    return [("X-Agent-Reference-Observer-Key", key.decode())]


def test_public_handler_reserves_before_body_and_settles_failed_write():
    oracle = oracle_started()
    method, target, headers, body = oracle_input("responses_probe")
    frame = (
        f"{method} {target} HTTP/1.1\r\n"
        + "".join(f"{name}: {value}\r\n" for name, value in headers)
        + "\r\n"
    ).encode() + body
    connection = UnitSocket(
        frame, fail_write=True, before_write=lambda: pytest_assert_pending(oracle)
    )
    fixture.PublicHandler(connection, ("UNIT", 0), SimpleNamespace(oracle=oracle))
    assert len(oracle.records) == 1 and oracle.pending == 0
    assert oracle.records[0]["matched"] is True and oracle.records[0]["status"] is None
    assert oracle.collector.first_failure == "transport_failure"


def pytest_assert_pending(oracle):
    assert oracle.collector._case["request_count"] == 1
    assert oracle.pending == 1 and oracle.records[0]["matched"] is None


@pytest.mark.parametrize(
    "defect", ["short", "duplicate", "chunked", "oversized", "deep", "nonfinite"]
)
def test_public_handler_bad_input_always_retains_settled_rejected_slot(defect):
    oracle = oracle_started()
    method, target, headers, body = oracle_input("responses_probe")
    if defect == "short":
        body = body[:-1]
    elif defect == "duplicate":
        headers.append(("host", "scheduler-fixtures:8080"))
    elif defect == "chunked":
        headers.append(("Transfer-Encoding", "chunked"))
    else:
        body = (
            b"x" * (wire.MAX_FIXTURE_INPUT_BYTES + 1)
            if defect == "oversized"
            else (b"[" * 2000 + b"0" + b"]" * 2000 if defect == "deep" else b"NaN")
        )
        headers = [
            (key, str(len(body)) if key == "Content-Length" else val)
            for key, val in headers
        ]
    frame = (
        f"{method} {target} HTTP/1.1\r\n"
        + "".join(f"{name}: {value}\r\n" for name, value in headers)
        + "\r\n"
    ).encode() + body
    connection = UnitSocket(frame)
    fixture.PublicHandler(connection, ("UNIT", 0), SimpleNamespace(oracle=oracle))
    assert oracle.records[0]["settled"] and oracle.records[0]["matched"] is False
    assert oracle.collector._case["request_count"] == 1 and oracle.pending == 0
    assert oracle.collector.first_failure == "correlation_failed"


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["--host-disposition", "--host-disposition"],
        ["--url", "must-not-print"],
    ],
)
def test_fixed_cli_has_no_selector_or_raw_argument_echo(argv, capsys):
    assert fixture.main(argv) == 1
    assert capsys.readouterr().err == "agent-reference fixture failed\n"


def test_selected_sse_uses_locked_sdk_decoder_with_fragmented_bytes():
    from openai._streaming import SSEDecoder

    body = fixture.summary_sse(CASE)
    events = list(
        SSEDecoder().iter_bytes(iter(body[i : i + 7] for i in range(0, len(body), 7)))
    )
    assert len(events) == 5 and events[-1].data == "[DONE]"
    chunks = [event.json() for event in events[:-1]]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert chunks[2]["choices"][0]["finish_reason"] == "stop"
    assert chunks[3]["choices"] == [] and chunks[3]["usage"]["total_tokens"] == 50
    assert (
        json.loads(chunks[1]["choices"][0]["delta"]["content"])["answered"]
        == "HUMAN_REVIEW_REQUIRED"
    )


def test_public_cancellation_settles_reserved_slot_then_propagates():
    oracle = oracle_started()
    error = KeyboardInterrupt("UNIT-only cancellation")

    class CancelledSocket(UnitSocket):
        def recv(self, size):
            raise error

    with pytest.raises(KeyboardInterrupt) as caught:
        fixture.PublicHandler(
            CancelledSocket(b""), ("UNIT", 0), SimpleNamespace(oracle=oracle)
        )
    assert caught.value is error
    assert oracle.pending == 0 and oracle.records[0]["settled"]
    assert oracle.records[0]["matched"] is False and oracle.records[0]["status"] is None
    assert oracle.collector.first_failure == "transport_failure"


@pytest.mark.parametrize(
    "defect", ["preview", "method", "wrong-case", "third-pair", "early-summary"]
)
def test_extra_or_cross_case_request_is_never_normal_program(defect):
    oracle = oracle_started()
    if defect == "third-pair":
        for kind in PROGRAM[:-1] + ("responses_probe", "chat_probe"):
            index = oracle.reserve()
            response = oracle.prepare(index, *oracle_input(kind))
            oracle.settle(index, response, write_complete=True)
    method, target, headers, body = oracle_input(
        "summary" if defect == "early-summary" else "responses_probe"
    )
    if defect == "preview":
        target = f"/__agent-reference/{CASE}/cove/jsonapi/preview"
    elif defect == "method":
        method = "PUT"
    elif defect == "wrong-case":
        target = target.replace(CASE, "9" * 32)
    index = oracle.reserve()
    response = oracle.prepare(index, method, target, headers, body)
    oracle.settle(index, response, write_complete=True)
    assert (
        not response.matched and oracle.collector.first_failure == "correlation_failed"
    )
    assert len(oracle.records) == index + 1


def test_configuration_case_and_input_custody_do_not_follow_caller_mutation():
    oracle = oracle_started()
    other = arm()
    other["case_id"] = "9" * 32
    with pytest.raises(wire.ContractError, match="identity_mismatch"):
        oracle.collector.control(
            "arm", other["case_id"], wire.encode_private("arm", other)
        )
    assert oracle.collector.first_failure == "control_invalid"
    config = MODEL_CONFIG.copy()
    independent = fixture.Oracle(fixture.Collector(LANE), config, UNIT_FACTS)
    config["model_key"] = "9" * 64
    assert independent.config["model_key"] == MODEL_CONFIG["model_key"]


def test_model_input_cap_precedes_read(tmp_path, monkeypatch):
    path = tmp_path / "input.json"
    path.write_bytes(b"x" * (wire.MAX_MODEL_INPUT_BYTES + 1))
    path.chmod(0o600)
    monkeypatch.setattr(
        fixture.os, "read", lambda *args: pytest.fail("over-cap input must not be read")
    )
    with pytest.raises(wire.ContractError, match="internal_failure"):
        fixture.load_private_file(
            path, capability=False, maximum=wire.MAX_MODEL_INPUT_BYTES
        )


def test_main_two_listener_wiring_and_private_join_are_owned_unit_instruments(
    monkeypatch, capsys
):
    monkeypatch.setenv("BIFROST_AGENT_REFERENCE_LANE_ID", LANE)
    events = []

    def material(path, *, capability, maximum=wire.MAX_FIXTURE_INPUT_BYTES):
        if capability:
            return INGEST if str(path).endswith("ingest-key") else CONTROL
        assert (
            str(path) == "/app/reference-model-oracle/input.json"
            and maximum == wire.MAX_MODEL_INPUT_BYTES
        )
        return wire.encode_private("model_input", MODEL_CONFIG)

    class Private:
        def __init__(self, address, collector, tls, ingest, control):
            assert (
                address == ("0.0.0.0", 8443) and ingest == INGEST and control == CONTROL
            )
            assert collector.oracle.config == MODEL_CONFIG
            events.append("private-bind")

        def serve_forever(self):
            pass

        def shutdown(self):
            events.append("private-shutdown")

        def server_close(self):
            events.append("private-close")

    class Public:
        def __init__(self, address, oracle):
            assert address == ("0.0.0.0", 8080) and oracle.case_id == CASE
            events.append("public-bind")

        def serve_forever(self):
            events.append("public-serve")
            raise KeyboardInterrupt

        def server_close(self):
            events.append("public-close")

    class Thread:
        def __init__(self, *, target, name, daemon):
            assert (
                name == "agent-reference-private"
                and not daemon
                and target.__name__ == "serve_forever"
            )

        def start(self):
            events.append("private-start")

        def join(self, timeout):
            assert timeout == wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS
            events.append("private-join")

    monkeypatch.setattr(fixture, "load_private_file", material)
    monkeypatch.setattr(fixture, "derive_header_facts", lambda: UNIT_FACTS)
    monkeypatch.setattr(fixture, "server_tls", lambda *args: object())
    monkeypatch.setattr(fixture, "PrivateServer", Private)
    monkeypatch.setattr(fixture, "PublicServer", Public)
    monkeypatch.setattr(fixture.threading, "Thread", Thread)
    monkeypatch.setattr(fixture.signal, "signal", lambda *args: None)
    assert fixture.main([]) == 0
    assert events[-6:] == [
        "private-start",
        "public-serve",
        "private-shutdown",
        "private-join",
        "private-close",
        "public-close",
    ]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "defect", ["missing-lane", "wrong-lane", "capability-reuse", "invalid-input"]
)
def test_main_rejects_construction_before_bind_without_secret_echo(
    monkeypatch, capsys, defect
):
    monkeypatch.setenv(
        "BIFROST_AGENT_REFERENCE_LANE_ID", "A" * 32 if defect == "wrong-lane" else LANE
    )
    if defect == "missing-lane":
        monkeypatch.delenv("BIFROST_AGENT_REFERENCE_LANE_ID")
    config = MODEL_CONFIG.copy()
    if defect == "capability-reuse":
        config["model_key"] = INGEST.decode()
    raw = (
        b"{}"
        if defect == "invalid-input"
        else wire.encode_private("model_input", config)
    )

    def material(path, **kwargs):
        return (
            INGEST
            if str(path).endswith("ingest-key")
            else (CONTROL if str(path).endswith("control-key") else raw)
        )

    monkeypatch.setattr(fixture, "load_private_file", material)
    monkeypatch.setattr(
        fixture, "PrivateServer", lambda *args: pytest.fail("premature private bind")
    )
    monkeypatch.setattr(
        fixture, "PublicServer", lambda *args: pytest.fail("premature public bind")
    )
    assert fixture.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "agent-reference fixture failed\n"


def test_host_cli_emits_only_canonical_bytes_without_newline(monkeypatch, capsysbinary):
    monkeypatch.setenv("BIFROST_AGENT_REFERENCE_LANE_ID", LANE)
    raw = fixture.Collector(LANE).finish_request()

    async def read(lane_id):
        assert lane_id == LANE
        return raw

    monkeypatch.setattr(fixture, "host_disposition", read)
    monkeypatch.setattr(
        fixture,
        "load_private_file",
        lambda *args, **kwargs: pytest.fail(
            "host CLI must not load ingress/model files"
        ),
    )
    assert fixture.main(["--host-disposition"]) == 0
    captured = capsysbinary.readouterr()
    assert captured.out == raw and captured.err == b"" and not raw.endswith(b"\n")


@pytest.mark.parametrize(
    "path",
    ["/health", "/health?arbitrary=unchanged", "http://localhost:8080/health?other=1"],
)
def test_existing_process_health_prearm_never_mutates_or_authenticates_oracle(path):
    collector = fixture.Collector(LANE)
    oracle = fixture.Oracle(collector, MODEL_CONFIG, UNIT_FACTS)
    frame = f"GET {path} HTTP/1.1\r\nHost: localhost:8080\r\nUser-Agent: curl/UNIT\r\nAccept: */*\r\n\r\n".encode()
    connection = UnitSocket(frame)
    before = collector.finish_request()
    fixture.PublicHandler(connection, ("UNIT", 0), SimpleNamespace(oracle=oracle))
    assert connection.sent.startswith(b"HTTP/1.1 200 ")
    assert connection.sent.split(b"\r\n\r\n", 1)[1] == b'{"status": "ok"}'
    assert collector.finish_request() == before
    assert (
        collector._case is None and collector._roles == {} and collector._ledger == []
    )
    assert oracle.records == [] and oracle.phase == "responses_probe"
    assert collector.first_failure is None


def test_nonhealth_method_does_not_inherit_health_exemption():
    oracle = oracle_started()
    frame = (
        b"POST /health HTTP/1.1\r\nHost: localhost:8080\r\nContent-Length: 1\r\n\r\nx"
    )
    connection = UnitSocket(frame)
    fixture.PublicHandler(connection, ("UNIT", 0), SimpleNamespace(oracle=oracle))
    assert oracle.collector._case["request_count"] == 1
    assert oracle.records[0]["settled"] and oracle.records[0]["kind"] == "unexpected"
    assert oracle.collector.first_failure == "correlation_failed"


@pytest.mark.parametrize("index", [-1, True, 1, "0"])
def test_settlement_cannot_alias_an_unreserved_ordinal(index):
    oracle = oracle_started()
    oracle.reserve()
    before = oracle.readback(CASE, 0)
    with pytest.raises(wire.ContractError, match="invalid_state"):
        oracle.settle(index, fixture.ProgramResponse(400, b"{}"), write_complete=False)
    assert oracle.readback(CASE, 0) == before


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "os",
        "arch",
        "runtime",
        "version",
        "model_encoding",
        "cove_encoding",
        "package",
    ],
)
def test_passive_header_derivation_rejects_unknown_facts_without_async_guess(
    monkeypatch, defect
):
    from httpx import _client as ordinary
    from httpx2 import _client as model
    from openai import _base_client as sdk

    sdk_client = importlib.import_module("openai._client")

    facts = UNIT_FACTS.copy()
    if defect is not None and defect != "package":
        facts[defect] = "unknown"
    for helper, name in (
        ("get_platform", "os"),
        ("get_architecture", "arch"),
        ("get_python_runtime", "runtime"),
        ("get_python_version", "version"),
    ):
        monkeypatch.setattr(sdk, helper, lambda name=name: facts[name])
    monkeypatch.setattr(
        sdk_client,
        "get_async_library",
        lambda: pytest.fail("sync fixture must not guess worker async context"),
    )
    monkeypatch.setattr(model, "ACCEPT_ENCODING", facts["model_encoding"])
    monkeypatch.setattr(ordinary, "ACCEPT_ENCODING", facts["cove_encoding"])
    versions = {
        "openai": "3.3.0",
        "pydantic-ai-slim": "2.35.3",
        "httpx": "0.28.1",
        "httpx2": "2.12.0",
    }
    if defect == "package":
        versions["httpx2"] = "2.12.1"
    monkeypatch.setattr(
        fixture.importlib.metadata, "version", lambda package: versions[package]
    )
    if defect is None:
        assert fixture.derive_header_facts() == UNIT_FACTS
    else:
        with pytest.raises(wire.ContractError, match="internal_failure"):
            fixture.derive_header_facts()
