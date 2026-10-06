"""Exercise real HTTP connection pooling at the E2E fixture's idle boundary."""

from types import SimpleNamespace

import httpcore
from httpcore._backends.mock import MockBackend, MockStream
import httpcore._sync.http11 as http11
import httpx
import pytest

from tests.e2e.conftest import e2e_client


class ClosingServerStream(MockStream):
    def __init__(self, backend):
        super().__init__([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"])
        self.backend = backend
        self.server_closed = False

    def write(self, buffer, timeout=None):
        if buffer.startswith(b"PUT "):
            self.backend.put_attempts += 1
        elif buffer and self.server_closed:
            # The FIN was not visible during the pool's last readability check;
            # the next body write discovers the server-initiated close.
            raise httpcore.WriteError("Broken pipe on an idle server connection")


class ClosingServerBackend(MockBackend):
    def __init__(self):
        super().__init__([])
        self.streams = []
        self.put_attempts = 0

    def connect_tcp(self, *args, **kwargs):
        stream = ClosingServerStream(self)
        self.streams.append(stream)
        return stream


def close_idle_server_connection(client, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(http11, "time", SimpleNamespace(monotonic=lambda: clock.now))
    backend = ClosingServerBackend()
    client._transport._pool._network_backend = backend
    assert client.get("/read-only").status_code == 200
    # The remote idle timer begins slightly before the client finishes reading
    # the response. Model that boundary deterministically, without sleeps/IO.
    clock.now = 4.99
    backend.streams[0].server_closed = True
    return backend


def test_previous_default_pool_reproduces_disconnected_write_without_retry(monkeypatch):
    with httpx.Client(base_url="http://api:8000", timeout=60.0) as client:
        backend = close_idle_server_connection(client, monkeypatch)
        with pytest.raises(httpx.RemoteProtocolError):
            client.put("/one-write", content=b"intent")
        assert backend.put_attempts == 1
        assert len(backend.streams) == 1


def test_e2e_fixture_discards_idle_socket_before_one_write_without_retry(monkeypatch):
    fixture = e2e_client.__wrapped__()
    wrapped = next(fixture)
    try:
        backend = close_idle_server_connection(wrapped._client, monkeypatch)
        response = wrapped.put("/one-write", content=b"intent")
        assert response.status_code == 200
        assert backend.put_attempts == 1
        assert len(backend.streams) == 2
        assert backend.streams[0]._closed
    finally:
        fixture.close()
