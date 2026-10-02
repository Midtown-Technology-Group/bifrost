"""Instrumentation proof only: synthetic ASGI/TLS peers, never nominal acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import ssl
from datetime import UTC, datetime, timedelta
from typing import cast

import anyio
import httpx
import pytest

from scripts import agent_reference_contract as wire
from tests.e2e.platform import agent_reference_observer as obs

LANE, NONCE, CASE = "1" * 32, "2" * 32, "3" * 32
UUID = "11111111-2222-3333-4444-555555555555"
CLAIMS = {
    "sub": UUID,
    "engine_execution_id": UUID,
    "engine_solution_id": UUID,
    "org_id": None,
    "delegated_user_id": UUID,
    "engine": True,
    "is_superuser": True,
    "engine_global_repo_access": True,
    "delegated_is_superuser": False,
    "delegated_is_provider_org": False,
    "delegated_is_external": False,
}
BODY = json.dumps(
    {"name": "Cove Data Protection", "scope": "global", "solution": UUID}
).encode()


class MemoryStore:
    def __init__(self):
        self.raw = None
        self.closed = False
        self.block: asyncio.Event | None = None
        self.fail = False
        self.corrupt = False

    async def write(self, raw):
        wire.decode_private("status", raw)
        if self.block is not None:
            await self.block.wait()
        if self.fail:
            raise OSError("synthetic failure")
        self.raw = raw

    async def read(self):
        return b"{}" if self.corrupt else self.raw

    def close(self):
        self.closed = True


class Sender:
    def __init__(self):
        self.receipts = []
        self.block: asyncio.Event | None = None
        self.mutate = lambda value: value
        self.deadlines = []

    async def send(self, raw, deadline):
        receipt = wire.decode_private("receipt", raw)
        self.receipts.append(receipt)
        self.deadlines.append(deadline)
        if self.block is not None:
            await self.block.wait()
        return self.mutate(
            {
                "schema": "bifrost.agent-reference.observer-ack/v1",
                "lane_id": receipt["lane_id"],
                "role": receipt["role"],
                "nonce": receipt["nonce"],
                "seq": receipt["seq"],
                "accepted": True,
                "case_id": CASE
                if receipt["kind"] in {"sdk_request", "unexpected_sdk_path"}
                else None,
            }
        )


def observer(
    app,
    verifier=lambda _: {
        key: value for key, value in CLAIMS.items() if value is not None
    },
):
    return obs.Observer(
        app,
        lane_id=LANE,
        role="api",
        nonce=NONCE,
        verifier=verifier,
        sender=Sender(),
        store=MemoryStore(),
    )


def scope(**changes):
    result = {
        "type": "http",
        "method": "POST",
        "path": wire.SDK_PATH,
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer synthetic")],
    }
    result.update(changes)
    return result


async def spin(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("instrumentation did not settle")


def queued(owner):
    return wire.decode_private("receipt", owner._queue.get_nowait()[2])


async def exchange(owner, target, messages):
    seen = []
    iterator = iter(messages)

    async def receive():
        return next(iterator)

    async def send(message):
        seen.append(message)

    owner.phase = "active"
    await owner(target, receive, send)
    return seen


async def test_actual_message_identity_order_and_safe_projection():
    first = {"type": "http.request", "body": BODY[:8], "more_body": True}
    last = {"type": "http.request", "body": BODY[8:], "more_body": False}
    start = {"type": "http.response.start", "status": 200, "headers": []}
    reply = {
        "type": "http.response.body",
        "body": b"never-retained",
        "more_body": False,
    }
    target = scope()
    order = []

    async def app(actual, receive, send):
        assert actual is target
        assert await receive() is first
        order.append("receive-first")
        assert await receive() is last
        order.append("receive-last")
        await send(start)
        order.append("sent-start")
        await send(reply)
        order.append("sent-final")

    owner = observer(app)
    sent = await exchange(owner, target, [first, last])
    assert sent[0] is start and sent[1] is reply
    assert order == ["receive-first", "receive-last", "sent-start", "sent-final"]
    receipt = queued(owner)
    assert receipt["payload"]["request"]["value"]["solution"] == UUID
    assert receipt["payload"]["verification"]["claims"] == CLAIMS
    assert receipt["payload"]["response"]["bytes"] == len(reply["body"])
    assert receipt["payload"]["failures"] == []
    assert b"never-retained" not in wire.encode_private("receipt", receipt)
    assert (
        owner.counters["requests_started"] == owner.counters["requests_finished"] == 1
    )


@pytest.mark.parametrize(
    "error",
    [RuntimeError("product-original"), asyncio.CancelledError("product-original")],
)
async def test_original_exception_and_cancellation_identity(error):
    async def app(scope, receive, send):
        await receive()
        raise error

    owner = observer(app)
    with pytest.raises(type(error)) as caught:
        await exchange(owner, scope(), [{"type": "http.request", "body": BODY}])
    assert caught.value is error
    result = queued(owner)
    expected = "cancelled" if isinstance(error, asyncio.CancelledError) else "exception"
    assert result["payload"]["response"]["app_exception"] == expected
    assert b"product-original" not in wire.encode_private("receipt", result)
    assert owner.counters["requests_in_flight"] == owner.active_captures == 0


async def test_no_eager_receive_and_unknown_path_has_no_body_path_witness():
    reads = 0

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        nonlocal reads
        reads += 1
        raise AssertionError("body should remain unconsumed")

    async def send(message):
        pass

    owner = observer(app)
    owner.phase = "active"
    await owner(scope(), receive, send)
    captured = queued(owner)["payload"]["request"]
    assert reads == 0 and captured == {
        "complete": False,
        "bytes": 0,
        "reason": "body_incomplete",
        "value": None,
    }
    await owner(scope(path="/api/sdk/private-value"), receive, send)
    unknown = queued(owner)
    assert unknown["kind"] == "unexpected_sdk_path"
    assert "request" not in unknown["payload"] and "path" not in unknown["payload"]
    assert b"private-value" not in wire.encode_private("receipt", unknown)


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"headers": []}, "auth_missing"),
        (
            {
                "headers": [
                    (b"Authorization", b"Bearer a"),
                    (b"authorization", b"Bearer b"),
                ]
            },
            "auth_duplicate",
        ),
        (
            {"headers": [(b"authorization", b"x" * (wire.MAX_AUTH_HEADER_BYTES + 1))]},
            "auth_oversized",
        ),
        ({"headers": [(b"authorization", b"Basic private")]}, "auth_malformed"),
    ],
)
async def test_header_failure_is_single_safe_cause(change, reason):
    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 401})
        await send({"type": "http.response.body", "body": b""})

    owner = observer(app)
    await exchange(owner, scope(**change), [{"type": "http.request", "body": BODY}])
    value = queued(owner)
    assert value["payload"]["verification"] == {
        "outcome": "unverified",
        "reason": reason,
        "claims": None,
    }
    assert value["payload"]["failures"] == [reason]
    assert owner.first_failure == reason


@pytest.mark.parametrize(
    "claims",
    [
        None,
        {**CLAIMS, "delegated_is_superuser": 1},
        {**CLAIMS, "sub": "not-a-uuid"},
        {**CLAIMS, "org_id": None},
    ],
)
async def test_real_verifier_result_requires_shared_claim_validation(claims):
    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b""})

    owner = observer(app, verifier=lambda _: claims)
    await exchange(owner, scope(), [{"type": "http.request", "body": BODY}])
    value = queued(owner)["payload"]["verification"]
    assert value["claims"] is None
    assert value["reason"] == ("jwt_rejected" if claims is None else "claims_schema")


@pytest.mark.parametrize(
    "body, reason",
    [
        (b'{"name":"Cove Data Protection","name":"private"}', "body_invalid"),
        (b"\xff", "body_invalid"),
        (b"x" * (wire.MAX_SDK_BODY_BYTES + 1), "body_oversized"),
    ],
)
async def test_bounded_body_and_response_do_not_change_traffic(body, reason):
    message = {"type": "http.request", "body": body}
    reply = {
        "type": "http.response.body",
        "body": b"z" * (wire.MAX_SELECTED_RESPONSE_BYTES + 7),
    }

    async def app(scope, receive, send):
        assert await receive() is message
        await send({"type": "http.response.start", "status": 200})
        await send(reply)

    owner = observer(app)
    sent = await exchange(owner, scope(), [message])
    assert sent[-1] is reply
    payload = queued(owner)["payload"]
    assert payload["request"]["reason"] == reason
    assert payload["request"]["value"] is None
    assert payload["response"]["bytes"] == wire.MAX_SELECTED_RESPONSE_BYTES + 1
    assert "response_oversized" in payload["failures"]


async def test_capture_overflow_forwards_all_requests_and_counts_missing_witness():
    release = asyncio.Event()
    calls = 0

    async def app(scope, receive, send):
        await release.wait()
        await receive()
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b""})

    def verify(token):
        nonlocal calls
        calls += 1
        return {name: value for name, value in CLAIMS.items() if value is not None}

    owner = observer(app, verify)
    tasks = [
        asyncio.create_task(
            exchange(owner, scope(), [{"type": "http.request", "body": BODY}])
        )
        for _ in range(wire.MAX_ROLE_ACTIVE_CAPTURES + 1)
    ]
    await spin(lambda: owner.counters["requests_started"] == len(tasks))
    assert owner.active_captures == calls == wire.MAX_ROLE_ACTIVE_CAPTURES
    release.set()
    await asyncio.gather(*tasks)
    assert owner.counters["requests_finished"] == len(tasks)
    assert (
        owner.counters["enqueue_rejected"] == owner.counters["capacity_rejected"] == 1
    )
    assert owner.counters["queued"] == wire.MAX_ROLE_ACTIVE_CAPTURES
    wire.validate_private("status", owner.snapshot())


async def test_bad_ack_and_cancelled_sender_preserve_failed_and_pending_counts():
    owner = observer(None)
    owner.upstream["startup_forwarded"] = True
    owner.enqueue("ready", {"startup_forwarded": True})
    owner.sender.mutate = lambda value: {**value, "seq": value["seq"] + 1}
    sender = asyncio.create_task(owner._send_receipts())
    await spin(lambda: owner.counters["send_failed"] == 1)
    assert owner.first_failure == "ack_invalid"
    assert len(owner.sender.receipts) == 1
    owner.sender.block = asyncio.Event()
    owner.enqueue("failure", {"phase": "request", "code": "observer_exception"})
    owner.enqueue("failure", {"phase": "request", "code": "observer_exception"})
    await spin(lambda: owner.counters["sending"] == 1)
    sender.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sender
    assert owner.counters["send_failed"] == 2 and owner.counters["queue_pending"] == 1
    assert owner.counters["sending"] == 0
    wire.validate_private("status", owner.snapshot())


async def test_status_writer_coalesces_and_latches_io_failure():
    owner = observer(None)
    owner.store.block = asyncio.Event()
    owner.publish()
    writer = asyncio.create_task(owner._write_status())
    await asyncio.sleep(0)
    owner.publish()
    latest = owner._latest
    owner.store.block.set()
    await spin(lambda: owner._written_generation == latest[0])
    assert owner.store.raw == latest[1]
    owner.store.fail = True
    owner.publish()
    await writer
    assert owner.first_failure == "status_io_failure" and owner._status_failed
    assert cast(int, owner._written_generation) < owner.generation


def test_factory_rejects_missing_inputs_before_product_import(monkeypatch):
    monkeypatch.delenv("BIFROST_AGENT_REFERENCE_LANE_ID", raising=False)
    monkeypatch.setenv("BIFROST_AGENT_REFERENCE_ROLE", "api")
    monkeypatch.setattr(
        obs.PrivateSender, "from_mounts", lambda: pytest.fail("premature private setup")
    )
    with pytest.raises(obs.ObservationClosureError):
        obs.create_app()


def test_generation_saturation_is_sticky_failure():
    owner = observer(None)
    owner.generation = wire.MAX_COUNTER - 1
    owner.publish()
    assert owner.first_failure == "counter_exhausted"
    wire.validate_private("status", owner.snapshot())


async def test_status_store_fixed_atomic_generation_and_symlink_refusal(
    tmp_path, monkeypatch
):
    directory = tmp_path / "api"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(obs, "Path", lambda _: tmp_path)
    store = obs.StatusStore("api")
    owner = observer(None)
    first = wire.encode_private("status", owner.snapshot())
    await store.write(first)
    assert await store.read() == first
    assert (directory / "status.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(obs.ObservationClosureError):
        await store.write(first)
    (directory / "status.json").unlink()
    (directory / "status.json").symlink_to(tmp_path / "outside")
    owner.publish()
    with pytest.raises(OSError):
        await store.write(owner._latest[1])
    store.close()
    with pytest.raises(obs.ObservationClosureError):
        obs.StatusStore("api")


def test_private_mount_tls_context_has_no_ambient_trust(monkeypatch):
    events = []

    class Context:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED

        def load_verify_locations(self, path):
            events.append(path)

    monkeypatch.setattr(obs.ssl, "SSLContext", lambda protocol: Context())
    monkeypatch.setattr(obs, "_read_ingest_key", lambda: "a" * 64)
    value = obs.PrivateSender.from_mounts()
    assert events == ["/run/agent-reference/observer-ca.pem"]
    assert value._context.minimum_version == ssl.TLSVersion.TLSv1_2


def tls_contexts(tmp_path, defect):
    """Synthetic local certificates; no external trust, issuer or product model."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.now(UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unit-only-ca")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(
            now - timedelta(days=1) if defect == "expired" else now + timedelta(days=1)
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(
                        "wrong.invalid" if defect == "san" else "scheduler-fixtures"
                    )
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    # With no installed CA a real handshake must fail, even if ambient CA exists.
    if defect != "ca":
        client.load_verify_locations(
            cadata=ca.public_bytes(serialization.Encoding.PEM).decode()
        )
    return server, client


async def local_tls_exchange(tmp_path, monkeypatch, *, defect=None, redirect=None):
    server_context, client_context = tls_contexts(tmp_path, defect)
    seen = []
    connections = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        connections.add(task)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.split(b"\r\n")
            headers = dict(line.split(b": ", 1) for line in lines[1:] if line)
            seen.append(
                {
                    "selected_path": lines[0]
                    == b"POST /__agent-reference/observer/sdk-receipts HTTP/1.1",
                    "key_seen": headers.get(b"X-Agent-Reference-Observer-Key")
                    == b"a" * 64,
                }
            )
            raw = await reader.readexactly(int(headers[b"Content-Length"]))
            receipt = wire.decode_private("receipt", raw)
            if redirect is not None:
                status, location = redirect
                writer.write(
                    f"HTTP/1.1 {status} Redirect\r\nLocation: {location}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
                )
            else:
                ack = wire.encode_private(
                    "ack",
                    {
                        "schema": "bifrost.agent-reference.observer-ack/v1",
                        "lane_id": receipt["lane_id"],
                        "role": receipt["role"],
                        "nonce": receipt["nonce"],
                        "seq": receipt["seq"],
                        "accepted": True,
                        "case_id": None,
                    },
                )
                writer.write(
                    f"HTTP/1.1 201 Created\r\nContent-Length: {len(ack)}\r\nConnection: close\r\n\r\n".encode()
                    + ack
                )
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, ssl.SSLError):
                pass
            connections.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]
    original = anyio.connect_tcp
    connect_attempts = []

    async def connect(*, remote_host, remote_port, **kwargs):
        # AnyIO keeps remote_port rather than the port returned by getaddrinfo.
        # Map only TCP here: the real HTTP origin and subsequent TLS SNI stay fixed.
        connect_attempts.append((remote_host, remote_port))
        assert remote_host == "scheduler-fixtures" and remote_port == 8443
        return await original(remote_host="127.0.0.1", remote_port=port, **kwargs)

    monkeypatch.setattr(anyio, "connect_tcp", connect)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://ambient.invalid:1")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("NO_PROXY", "")
    owner = observer(None)
    receipt = wire.encode_private(
        "receipt", owner.receipt("ready", {"startup_forwarded": True}, 1)
    )
    sender = obs.PrivateSender("a" * 64, client_context)
    try:
        if defect:
            with pytest.raises(httpx.ConnectError):
                await sender.send(
                    receipt,
                    asyncio.get_running_loop().time() + wire.OBSERVER_CLOSE_SECONDS,
                )
            assert seen == []  # The ingestion credential never reached HTTP.
        elif redirect:
            with pytest.raises(obs.ObservationClosureError):
                await sender.send(receipt, None)
            assert seen == [{"selected_path": True, "key_seen": True}]
            assert len(connect_attempts) == 1  # No redirected TLS or plaintext request.
        else:
            ack = await sender.send(receipt, None)
            assert ack["seq"] == 1
            assert seen == [{"selected_path": True, "key_seen": True}]
        assert all(host == "scheduler-fixtures" for host, _ in connect_attempts)
    finally:
        server.close()
        await server.wait_closed()
        if connections:
            for task in tuple(connections):
                task.cancel()
            await asyncio.gather(*tuple(connections), return_exceptions=True)


async def test_private_tls_positive_ignores_ambient_proxy_and_ca(tmp_path, monkeypatch):
    await local_tls_exchange(tmp_path, monkeypatch)


@pytest.mark.parametrize("defect", ["ca", "san", "expired"])
async def test_actual_tls_rejects_wrong_ca_san_or_expiry_without_ingress_key(
    tmp_path, monkeypatch, defect
):
    await local_tls_exchange(tmp_path, monkeypatch, defect=defect)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
@pytest.mark.parametrize("scheme", ["https", "http"])
async def test_no_redirect_or_plaintext_downgrade_carries_ingress_key(
    tmp_path, monkeypatch, status, scheme
):
    await local_tls_exchange(
        tmp_path,
        monkeypatch,
        redirect=(status, f"{scheme}://scheduler-fixtures:8443/must-not-receive-key"),
    )


async def test_receive_disconnect_and_failed_send_are_actual_observations():
    disconnect = {"type": "http.disconnect"}
    product_error = OSError("upstream-original")

    async def app(scope, receive, send):
        assert await receive() is disconnect
        await send({"type": "http.response.start", "status": 200})

    async def receive():
        return disconnect

    async def send(message):
        raise product_error

    owner = observer(app)
    owner.phase = "active"
    with pytest.raises(OSError) as caught:
        await owner(scope(), receive, send)
    assert caught.value is product_error
    response = queued(owner)["payload"]["response"]
    assert response == {
        "status": None,
        "bytes": 0,
        "complete": False,
        "disconnect_seen": True,
        "app_exception": "exception",
    }
    assert owner.first_failure == "disconnect_seen"


@pytest.mark.parametrize(
    "lane, role",
    [
        (None, "api"),
        ("1" * 31, "api"),
        ("A" * 32, "api"),
        (LANE, None),
        (LANE, "worker"),
        (LANE, "API"),
    ],
)
def test_factory_nonsecret_construction_is_closed_before_private_product_setup(
    monkeypatch, lane, role
):
    for name, value in (
        ("BIFROST_AGENT_REFERENCE_LANE_ID", lane),
        ("BIFROST_AGENT_REFERENCE_ROLE", role),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        obs.PrivateSender, "from_mounts", lambda: pytest.fail("premature private setup")
    )
    with pytest.raises(obs.ObservationClosureError):
        obs.create_app()


@pytest.mark.parametrize(
    "defect", ["permissions", "short", "nonhex", "newline", "symlink", "fifo"]
)
def test_fixed_ingest_file_requires_bounded_regular_private_key(
    tmp_path, monkeypatch, defect
):
    target = tmp_path / "key"
    raw = b"a" * 64
    if defect == "short":
        raw = raw[:-1]
    elif defect == "nonhex":
        raw = b"A" * 64
    elif defect == "newline":
        raw += b"\n"
    if defect == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(raw)
        outside.chmod(0o600)
        target.symlink_to(outside)
    elif defect == "fifo":
        os.mkfifo(target, 0o600)
    else:
        target.write_bytes(raw)
        target.chmod(0o644 if defect == "permissions" else 0o600)
    original = os.open

    def open_fixed(path, flags, *args, **kwargs):
        if path == "/run/agent-reference/observer-ingest-key":
            path = target
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(obs.os, "open", open_fixed)
    with pytest.raises((obs.ObservationClosureError, OSError)):
        obs._read_ingest_key()


async def test_queue_and_whole_lane_caps_do_not_evict_or_hide_allocation_gaps():
    owner = observer(None)
    for _ in range(wire.MAX_ROLE_QUEUE + 1):
        owner.enqueue("failure", {"phase": "request", "code": "observer_exception"})
    assert owner.counters["queue_pending"] == wire.MAX_ROLE_QUEUE
    assert (
        owner.counters["enqueue_rejected"] == owner.counters["capacity_rejected"] == 1
    )
    assert owner.counters["seq_allocated"] == wire.MAX_ROLE_QUEUE + 1
    assert owner.first_failure == "queue_exhausted"
    wire.validate_private("status", owner.snapshot())
    # Independent saturated-role state: 128 real allocated attempts already failed admission.
    owner = observer(None)
    owner.counters.update(
        receipts_seen=wire.MAX_ROLE_RECEIPTS_PER_LANE,
        seq_allocated=wire.MAX_ROLE_RECEIPTS_PER_LANE,
        enqueue_rejected=wire.MAX_ROLE_RECEIPTS_PER_LANE,
    )
    owner.latch("observer_exception")
    owner.enqueue("failure", {"phase": "shutdown", "code": "shutdown_incomplete"})
    assert owner.counters["receipts_seen"] == wire.MAX_ROLE_RECEIPTS_PER_LANE + 1
    assert owner.counters["seq_allocated"] == wire.MAX_ROLE_RECEIPTS_PER_LANE
    assert owner.counters["enqueue_rejected"] == wire.MAX_ROLE_RECEIPTS_PER_LANE + 1
    assert owner.counters["capacity_rejected"] == 1 and owner._queue.empty()
    assert owner.first_failure == "observer_exception"
    wire.validate_private("status", owner.snapshot())


async def test_expired_absolute_sender_deadline_never_constructs_http_client(
    monkeypatch,
):
    monkeypatch.setattr(
        obs.httpx, "AsyncClient", lambda **_: pytest.fail("expired request")
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    sender = obs.PrivateSender("a" * 64, context)
    with pytest.raises(obs.ObservationClosureError):
        await sender.send(b"not-used", asyncio.get_running_loop().time() - 1)


@pytest.mark.parametrize(
    "error",
    [RuntimeError("lifespan-original"), asyncio.CancelledError("lifespan-original")],
)
async def test_original_lifespan_exception_or_cancellation_is_never_substituted(error):
    target = {"type": "lifespan"}
    request = {"type": "lifespan.startup"}

    async def app(actual, receive, send):
        assert actual is target
        assert await receive() is request
        raise error

    async def receive():
        return request

    async def send(message):
        pytest.fail("invented acknowledgement")

    owner = observer(app)
    try:
        with pytest.raises(type(error)) as caught:
            await owner(target, receive, send)
        assert caught.value is error
        assert owner.lifespan_task is asyncio.current_task()
        assert owner.first_failure == "shutdown_incomplete"
        assert owner.counters["receipts_seen"] == 0
        assert not owner.ready_acked and not owner.closed_acked
        assert not any(owner.upstream.values())
    finally:
        await asyncio.gather(
            owner._sender_task, owner._writer_task, return_exceptions=True
        )


async def test_original_startup_failure_ack_is_forwarded_without_ready_or_success():
    failed = {"type": "lifespan.startup.failed", "message": "product-original"}
    sent = []

    async def app(scope, receive, send):
        await send(failed)

    async def send(message):
        sent.append(message)

    owner = observer(app)
    try:
        with pytest.raises(obs.ObservationClosureError):
            await owner({"type": "lifespan"}, None, send)
        assert sent == [failed] and sent[0] is failed
        assert (
            owner.counters["receipts_seen"] == 0
            and owner.first_failure == "shutdown_incomplete"
        )
        assert not owner.closed and not owner.ready_acked
        assert b"product-original" not in owner._latest[1]
    finally:
        await asyncio.gather(
            owner._sender_task, owner._writer_task, return_exceptions=True
        )


async def assert_invalid_body_preserves_signed_sdk_witness(body):
    assert len(body) <= wire.MAX_SDK_BODY_BYTES
    message = {"type": "http.request", "body": body, "more_body": False}
    start = {"type": "http.response.start", "status": 200}
    final = {"type": "http.response.body", "body": b"original-response"}
    target = scope()
    order = []

    async def app(actual, receive, send):
        assert actual is target
        assert await receive() is message
        order.append("received-original")
        await send(start)
        order.append("sent-original-start")
        await send(final)
        order.append("sent-original-final")

    owner = observer(app)
    sent = await exchange(owner, target, [message])
    assert sent[0] is start and sent[1] is final
    assert order == ["received-original", "sent-original-start", "sent-original-final"]
    result = queued(owner)
    assert result["kind"] == "sdk_request"
    payload = result["payload"]
    assert payload["verification"] == {
        "outcome": "verified",
        "reason": None,
        "claims": CLAIMS,
    }
    assert payload["request"] == {
        "complete": True,
        "bytes": len(body),
        "reason": "body_invalid",
        "value": None,
    }
    assert payload["failures"] == ["body_invalid"]
    assert payload["response"] == {
        "status": 200,
        "bytes": len(final["body"]),
        "complete": True,
        "disconnect_seen": False,
        "app_exception": None,
    }
    assert owner.first_failure == "body_invalid"
    assert (
        owner.counters["requests_started"] == owner.counters["requests_finished"] == 1
    )
    assert owner.counters["requests_in_flight"] == owner.active_captures == 0
    assert (
        owner.counters["receipts_seen"]
        == owner.counters["queued"]
        == owner.counters["seq_allocated"]
        == 1
    )
    assert (
        owner.counters["enqueue_rejected"] == owner.counters["capacity_rejected"] == 0
    )
    assert owner.counters["queue_pending"] == 1
    wire.validate_private("status", owner.snapshot())
    assert b"original-response" not in wire.encode_private("receipt", result)


async def test_actual_bounded_deep_json_retains_signed_invalid_body_witness():
    depth = wire.MAX_SDK_BODY_BYTES // 2 - 1
    body = b"[" * depth + b"0" + b"]" * depth
    # Actual decoder is untouched: it may parse the list or reject recursion.
    # Either outcome must retain the signed, safely null invalid-body witness.
    await assert_invalid_body_preserves_signed_sdk_witness(body)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        0,
        [],
        "private-value",
        {"name": "Cove Data Protection", "scope": "global"},
        {"name": "private-value", "scope": "global", "solution": UUID},
        {"name": "Cove Data Protection", "scope": "private-value", "solution": UUID},
        {"name": "Cove Data Protection", "scope": "global", "solution": "invalid"},
        {"name": "Cove Data Protection", "scope": "global", "solution": True},
        {
            "name": "Cove Data Protection",
            "scope": "global",
            "solution": UUID,
            "extra": "private-value",
        },
    ],
)
async def test_parsed_invalid_body_shape_retains_signed_null_witness(value):
    await assert_invalid_body_preserves_signed_sdk_witness(json.dumps(value).encode())


async def test_injected_decoder_recursion_retains_signed_sdk_witness(monkeypatch):
    """Instrumentation only; this does not prove the C decoder reaches recursion."""
    original = json.loads

    def recursive_selected_body(value, *args, **kwargs):
        # Leave shared private-codec decoding intact; inject only the consumed body parser.
        if value == BODY.decode("utf-8"):
            raise RecursionError("unit-only-body-decoder")
        return original(value, *args, **kwargs)

    monkeypatch.setattr(obs.json, "loads", recursive_selected_body)
    await assert_invalid_body_preserves_signed_sdk_witness(BODY)
