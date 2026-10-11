"""Public contract and bounded launcher response tests; no private endpoint keys."""

import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from src.models.contracts.device_peer_sessions import DevicePeerSessionCreate
from src.services.device_peer_launcher import PeerLauncherClient, PeerLauncherError


def payload():
    return DevicePeerSessionCreate(request_id="01234567-89ab-4def-8123-456789abcdef",
                                  operator_key=base64.b64encode(b"o" * 32).decode(),
                                  target_key=base64.b64encode(b"t" * 32).decode(),
                                  destination="127.0.0.1:443", expires=datetime.now(timezone.utc) + timedelta(minutes=5))


@pytest.mark.parametrize("destination", ["localhost:443", "0.0.0.0:22", "224.0.0.1:22", "[::1]:22", "127.0.0.1:0"])
def test_rejects_unbounded_destination(destination):
    values = payload().model_dump()
    values["destination"] = destination
    with pytest.raises(ValidationError):
        DevicePeerSessionCreate(**values)


def settings(url="https://router.example:9443"):
    return SimpleNamespace(device_peer_launcher_url=url, device_peer_launcher_ca="ca",
                           device_peer_launcher_certificate="cert", device_peer_launcher_key="key")


@pytest.mark.parametrize("url", ["http://router", "https://user:secret@router", "https://router/path", "https://router?query=1"])
def test_rejects_ambient_launcher_destination(url):
    with pytest.raises(PeerLauncherError):
        PeerLauncherClient(settings(url))


@pytest.mark.asyncio
async def test_returns_only_matching_finite_policy():
    body = payload()
    response = {"id": "a" * 32, **{role: {"id": "session", "role": role, "target": body.destination,
                                         "expires": body.expires.isoformat()} for role in ("operator", "target")}}
    with patch("src.services.device_peer_launcher.ssl.create_default_context", return_value=MagicMock()):
        client = PeerLauncherClient(settings())
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
    async with client:
        assert (await client.start("a" * 32, body))["id"] == "a" * 32
        response["target"]["target"] = "127.0.0.1:22"
        with pytest.raises(PeerLauncherError):
            await client.start("a" * 32, body)


@pytest.mark.asyncio
async def test_redirect_and_oversized_response_fail_closed():
    with patch("src.services.device_peer_launcher.ssl.create_default_context", return_value=MagicMock()):
        client = PeerLauncherClient(settings())
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": "https://other"})))
    async with client:
        with pytest.raises(PeerLauncherError):
            await client.start("a" * 32, payload())
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b" " * 65537)))
    async with client:
        with pytest.raises(PeerLauncherError):
            await client.start("a" * 32, payload())
