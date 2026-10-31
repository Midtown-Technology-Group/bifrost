"""Private transport preserves bytes and TLS hostnames without owning jobs."""
import asyncio
import json
import struct
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from src.worker.network import (
    CREDIT, DATA, FIN, PROTOCOL, WINDOW, Tunnel, bridge_connection, configure_hosts,
    configured_tunnels,
)


def configuration(**changes):
    return json.dumps([{
        "hostname": "database.example.test", "address": "127.0.0.2",
        "port": 15432, "url": "wss://gateway.example.test/network/postgres",
        **changes,
    }])


@pytest.mark.parametrize("changes", [
    {"address": "0.0.0.0"}, {"address": "::1"}, {"port": True},
    {"port": 0}, {"hostname": "bad\nhost"}, {"hostname": "-bad.test"},
    {"url": "ws://gateway.test/"}, {"url": "wss://user:secret@gateway.test/"},
    {"url": "wss://gateway.test/?token=secret"},
    {"url": "wss://gateway.test:99999/"},
])
def test_rejects_unsafe_network_configuration(changes):
    with pytest.raises(ValueError):
        configured_tunnels(configuration(**changes))


def test_network_is_explicit_and_hosts_are_idempotent(tmp_path):
    assert configured_tunnels(None) == []
    tunnels = configured_tunnels(configuration())
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    configure_hosts(tunnels, hosts)
    first = hosts.read_text()
    configure_hosts(tunnels, hosts)
    assert hosts.read_text() == first
    assert "127.0.0.2 database.example.test" in first
    with pytest.raises(ValueError, match="conflicts"):
        configure_hosts([Tunnel(tunnels[0].hostname, "127.0.0.3", 15432, tunnels[0].url)], hosts)
    assert hosts.read_text() == first


@pytest.mark.parametrize("field,value", [
    ("hostname", "DATABASE.EXAMPLE.TEST"), ("url", "wss://other.test/"),
])
def test_duplicate_names_or_listeners_are_rejected(field, value):
    first = json.loads(configuration())[0]
    with pytest.raises(ValueError, match="Duplicate"):
        configured_tunnels(json.dumps([first, {**first, field: value}]))


@pytest.mark.asyncio
async def test_network_outlives_launcher_execution_and_drains_on_failure(monkeypatch):
    from src.worker import network
    from src.worker.remote import launcher

    events = []

    @asynccontextmanager
    async def transport():
        events.append("network-start")
        try:
            yield
        finally:
            events.append("network-stop")

    async def run(**kwargs):
        events.append("launcher-drained")
        raise RuntimeError("launcher failed")

    monkeypatch.setattr(network, "worker_network", transport)
    monkeypatch.setattr(launcher, "_run_launcher", AsyncMock(side_effect=run))
    with pytest.raises(RuntimeError, match="launcher failed"):
        await launcher.run_launcher()
    assert events == ["network-start", "launcher-drained", "network-stop"]


@pytest.mark.asyncio
async def test_duplex_stream_larger_than_window_preserves_bytes_and_half_close():
    payload = bytes(range(256)) * (WINDOW // 256 * 8)
    failures = []
    tasks = set()

    async def echo(reader, writer):
        try:
            while data := await reader.read(32768):
                writer.write(data)
                await writer.drain()
            writer.write_eof()
        finally:
            writer.close()
            await writer.wait_closed()

    async def track_bridge(reader, writer, websocket):
        current = asyncio.current_task()
        tasks.add(current)
        try:
            await bridge_connection(reader, writer, websocket)
        except Exception as exc:
            failures.append(exc)
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(current)

    async with await asyncio.start_server(echo, "127.0.0.1", 0) as upstream:
        upstream_port = upstream.sockets[0].getsockname()[1]

        async def gateway(websocket):
            reader, writer = await asyncio.open_connection("127.0.0.1", upstream_port)
            await track_bridge(reader, writer, websocket)

        async with serve(gateway, "127.0.0.1", 0, subprotocols=[PROTOCOL]) as gateway_server:
            gateway_port = gateway_server.sockets[0].getsockname()[1]

            async def local(reader, writer):
                async with connect(f"ws://127.0.0.1:{gateway_port}", subprotocols=[PROTOCOL]) as websocket:
                    await track_bridge(reader, writer, websocket)

            async with await asyncio.start_server(local, "127.0.0.1", 0) as local_server:
                reader, writer = await asyncio.open_connection("127.0.0.1", local_server.sockets[0].getsockname()[1])

                async def produce():
                    writer.write(payload)
                    await writer.drain()
                    writer.write_eof()

                try:
                    async with asyncio.timeout(10):
                        _, result = await asyncio.gather(produce(), reader.read())
                        assert result == payload
                        while tasks:
                            await asyncio.gather(*tuple(tasks))
                finally:
                    writer.close()
                    await writer.wait_closed()
    assert failures == []


@pytest.mark.asyncio
async def test_unsolicited_credit_terminates_connection():
    class InvalidPeer:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return bytes([CREDIT]) + struct.pack("!I", 1)

    reader = asyncio.StreamReader()
    with pytest.raises(ExceptionGroup) as caught:
        await bridge_connection(reader, None, InvalidPeer())
    assert any(isinstance(exc, ValueError) for exc in caught.value.exceptions)


@pytest.mark.asyncio
async def test_tiny_frames_use_byte_credit_without_a_false_frame_limit():
    class FragmentedPeer:
        async def __aiter__(self):
            for _ in range(1024):
                yield bytes([DATA]) + b"x"
            yield bytes([FIN])
            await asyncio.Event().wait()

        async def send(self, frame):
            pass

        async def close(self):
            pass

    class Writer:
        def __init__(self):
            self.data = bytearray()
            self.eof = False

        def write(self, payload):
            self.data.extend(payload)

        async def drain(self):
            pass

        def write_eof(self):
            self.eof = True

    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = Writer()
    async with asyncio.timeout(5):
        await bridge_connection(reader, writer, FragmentedPeer())
    assert writer.data == b"x" * 1024
    assert writer.eof
