"""E2E: WebSocket device principal (M2.3 #835).

Freeze under test (feedback #6): Bearer device key header only — query
`device_key` rejected with 4001 — device may hold only its own
`device:{id}` channel; user JWTs can never gain `device:*`; the lossy
`device_job_available` hint actually reaches a subscribed device.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from tests.e2e.api.test_device_protocol import _enroll


@pytest.mark.e2e
async def _recv(ws, timeout: float = 5):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def _expect_close_4001(ws_url: str, headers: dict, query: str = ""):
    with pytest.raises(ConnectionClosedError) as exc:
        async with connect(f"{ws_url}{query}", additional_headers=headers) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)
            pytest.fail("expected a close frame, not a message")
    assert exc.value.rcvd is not None, f"no close frame received: {exc.value}"
    assert exc.value.rcvd.code == 4001, f"close code {exc.value.rcvd.code}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_device_connect_subscribe_and_isolation(
    e2e_ws_url, e2e_client, platform_admin, org1
):
    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "ws-device"
    )
    ws_url = f"{e2e_ws_url}/ws/connect"
    headers = {"Authorization": f"Bearer {key}"}
    own = f"device:{device_id}"

    async with connect(ws_url, additional_headers=headers) as ws:
        data = await _recv(ws)
        assert data["type"] == "connected"
        assert data["deviceId"] == device_id
        assert "userId" not in data
        assert data["channels"] == [own]

        # Subscribe to own channel: acknowledged.
        await ws.send(json.dumps({"type": "subscribe", "channels": [own]}))
        ack = await _recv(ws)
        assert ack == {"type": "subscribed", "channel": own}

        # Subscribe to a foreign device channel: denied.
        await ws.send(
            json.dumps({"type": "subscribe", "channels": [f"device:{uuid4()}"]})
        )
        denied = await _recv(ws)
        assert denied["type"] == "error"
        assert denied["message"] == "Access denied"

        # Subscribe to a user channel: denied.
        await ws.send(
            json.dumps({"type": "subscribe", "channels": [f"user:{uuid4()}"]})
        )
        denied = await _recv(ws)
        assert denied["type"] == "error"
        assert denied["message"] == "Access denied"

        # Unknown message types are rejected, connection stays open.
        await ws.send(json.dumps({"type": "table_subscribe", "channel": "table:x"}))
        unsupported = await _recv(ws)
        assert unsupported["type"] == "error"
        assert "Unsupported" in unsupported["message"]

        # ping/pong keepalive works.
        await ws.send(json.dumps({"type": "ping"}))
        pong = await _recv(ws)
        assert pong == {"type": "pong"}

        # Unsubscribe own channel.
        await ws.send(json.dumps({"type": "unsubscribe", "channel": own}))
        un = await _recv(ws)
        assert un == {"type": "unsubscribed", "channel": own}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_query_device_key_is_rejected_with_4001(
    e2e_ws_url, e2e_client, platform_admin, org1
):
    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "ws-query"
    )
    ws_url = f"{e2e_ws_url}/ws/connect"
    headers = {"Authorization": f"Bearer {key}"}

    # Query credential rejected even when a valid header is also present.
    await _expect_close_4001(ws_url, headers, query=f"?device_key={key}")
    # Query-only credential (no header) also rejected.
    await _expect_close_4001(ws_url, {}, query=f"?device_key={key}")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_bad_device_key_rejected_4001(
    e2e_ws_url, e2e_client, platform_admin, org1
):
    _device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "ws-badkey"
    )
    wrong = key[:-1] + ("b" if key[-1] != "b" else "c")
    await _expect_close_4001(
        f"{e2e_ws_url}/ws/connect", {"Authorization": f"Bearer {wrong}"}
    )
    # Malformed bearer is rejected too.
    await _expect_close_4001(
        f"{e2e_ws_url}/ws/connect", {"Authorization": "Bearer not-a-device-key"}
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_user_jwt_cannot_gain_device_channel(e2e_ws_url, platform_admin):
    target = f"device:{uuid4()}"
    ws_url = f"{e2e_ws_url}/ws/connect?channels={target}"
    headers = {"Authorization": f"Bearer {platform_admin.access_token}"}

    async with connect(ws_url, additional_headers=headers) as ws:
        data = await _recv(ws)
        assert data["type"] == "connected"
        # Query-time grant is denied: the device channel is absent.
        assert target not in data["channels"]
        assert any(ch.startswith("user:") for ch in data["channels"])

        # Runtime subscribe is denied as well.
        await ws.send(json.dumps({"type": "subscribe", "channels": [target]}))
        denied = await _recv(ws)
        assert denied["type"] == "error"
        assert denied["message"] == "Access denied"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_job_available_hint_reaches_subscribed_device(
    e2e_ws_url, e2e_client, platform_admin, org1
):
    from src.services.device_jobs import broadcast_job_available

    device_id, key = _enroll(
        e2e_client, platform_admin.headers, org1["id"], "ws-hint"
    )
    ws_url = f"{e2e_ws_url}/ws/connect"
    headers = {"Authorization": f"Bearer {key}"}
    own = f"device:{device_id}"

    async with connect(ws_url, additional_headers=headers) as ws:
        first = await _recv(ws)
        assert first["type"] == "connected"
        await ws.send(json.dumps({"type": "subscribe", "channels": [own]}))
        ack = await _recv(ws)
        assert ack["type"] == "subscribed"

        # Lossy hint delivery: test process publishes through Redis; the API's
        # pubsub listener relays it to this subscribed socket.
        await broadcast_job_available(__import__("uuid").UUID(device_id), uuid4())
        hint = await _recv(ws, timeout=10)
        assert hint["type"] == "device_job_available"
        assert hint["device_id"] == device_id
        assert hint["job_id"]
