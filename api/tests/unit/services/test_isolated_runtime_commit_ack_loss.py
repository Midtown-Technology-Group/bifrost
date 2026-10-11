"""Hosted pure packet-fixture tests; actual PostgreSQL proof is the live lane."""

import asyncio
import json
import struct
from uuid import uuid4

import pytest

from src.services.isolated_runtime_commit_ack_loss import CommitAckLossProxy, publish


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_disconnects", [True, False])
async def test_idle_upstream_closes_on_owner_eof_or_proxy_stop(
    tmp_path, owner_disconnects
):
    connected = asyncio.Event()
    closed = asyncio.Event()

    async def backend(reader, writer):
        connected.set()
        try:
            assert await reader.read() == b""
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    server = await asyncio.start_server(backend, "127.0.0.1", 0)
    proxy = CommitAckLossProxy(
        "127.0.0.1", server.sockets[0].getsockname()[1], tmp_path, str(uuid4())
    )
    port = await proxy.start()
    _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        async with asyncio.timeout(3):
            await connected.wait()
            if owner_disconnects:
                writer.transport.abort()
            else:
                await proxy.stop()
            await closed.wait()
            while proxy.tasks or proxy.writers:
                await asyncio.sleep(0)
        assert not proxy.failed and not proxy.dropped
    finally:
        writer.close()
        await writer.wait_closed()
        if owner_disconnects:
            await proxy.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_only_one_armed_server_commit_response_is_lost(tmp_path):
    opaque = b"synthetic opaque client bytes"
    packet = b"C" + struct.pack("!I", 11) + b"COMMIT\0"

    async def backend(reader, writer):
        try:
            assert await reader.readexactly(len(opaque)) == opaque
            writer.write(packet)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(backend, "127.0.0.1", 0)
    session = str(uuid4())
    proxy = CommitAckLossProxy(
        "127.0.0.1", server.sockets[0].getsockname()[1], tmp_path, session
    )
    port = await proxy.start()
    try:
        for number in range(3):
            if number == 1:
                publish(
                    tmp_path / "commit-ack-arm.json",
                    {"event": "arm_commit_ack_loss", "session_id": session},
                )
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                writer.write(opaque)
                await writer.drain()
                async with asyncio.timeout(3):
                    raw = await reader.read()
                assert raw == (b"" if number == 1 else packet)
            finally:
                writer.close()
                await writer.wait_closed()
        dropped = json.loads((tmp_path / "commit-ack-dropped.json").read_text())
        assert dropped == {
            "event": "commit_ack_dropped",
            "session_id": session,
            "server_commit_observed": True,
            "client_commit_ack_forwarded": False,
            "forwarded_commits": 1,
        }
        assert proxy.forwarded_commits == 2 and proxy.dropped
    finally:
        await proxy.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["bad_packet", "wrong_session"])
async def test_packet_or_arm_drift_fails_closed(tmp_path, fault):
    session = str(uuid4())
    publish(
        tmp_path / "commit-ack-arm.json",
        {
            "event": "arm_commit_ack_loss",
            "session_id": str(uuid4()) if fault == "wrong_session" else session,
        },
    )

    async def backend(_reader, writer):
        writer.write(
            b"C" + struct.pack("!I", 2 if fault == "bad_packet" else 11) + b"COMMIT\0"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(backend, "127.0.0.1", 0)
    proxy = CommitAckLossProxy(
        "127.0.0.1", server.sockets[0].getsockname()[1], tmp_path, session
    )
    port = await proxy.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        async with asyncio.timeout(3):
            assert await reader.read() == b""
        assert proxy.failed and not proxy.dropped
        assert not (tmp_path / "commit-ack-dropped.json").exists()
    finally:
        writer.close()
        await writer.wait_closed()
        with pytest.raises(ValueError, match="owns connections"):
            await proxy.stop()
        server.close()
        await server.wait_closed()
