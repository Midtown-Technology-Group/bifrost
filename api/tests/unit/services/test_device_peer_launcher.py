"""Public contract and bounded launcher response tests; no private endpoint keys."""

import base64
import json
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


@pytest.mark.parametrize("value", ["!" * 44, base64.b64encode(b"\0" * 32).decode(), base64.b64encode(b"x" * 31).decode()])
def test_rejects_invalid_or_empty_public_keys(value):
    values = payload().model_dump()
    values["operator_key"] = value
    with pytest.raises(ValidationError):
        DevicePeerSessionCreate(**values)


def test_rejects_deadline_without_timezone():
    values = payload().model_dump()
    values["expires"] = datetime.now().replace(tzinfo=None)
    with pytest.raises(ValidationError):
        DevicePeerSessionCreate(**values)


def test_missing_configuration_and_unloadable_tls_fail_closed():
    incomplete = settings()
    incomplete.device_peer_launcher_key = None
    with pytest.raises(PeerLauncherError, match="not configured"):
        PeerLauncherClient(incomplete)
    with patch("src.services.device_peer_launcher.ssl.create_default_context", side_effect=OSError("private certificate path")):
        with pytest.raises(PeerLauncherError, match="invalid launcher TLS configuration") as caught:
            PeerLauncherClient(settings())
    assert "private certificate path" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["session", "../escape", "A" * 32])
async def test_invalid_session_nonce_cannot_reach_transport(invalid):
    with patch("src.services.device_peer_launcher.ssl.create_default_context", return_value=MagicMock()):
        async with PeerLauncherClient(settings()) as client:
            with pytest.raises(PeerLauncherError, match="invalid session identity"):
                await client.start(invalid, payload())
            with pytest.raises(PeerLauncherError, match="invalid session identity"):
                await client.stop(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["array", "nonce", "missing_grant", "role", "expired", "extended", "naive", "bad_date", "grant_identity", "bad_json"])
async def test_rejects_foreign_or_invalid_launcher_grants(fault):
    """A successful HTTP response cannot broaden the admitted lease policy."""
    request = payload()
    response = {"id": "a" * 32, **{
        role: {"id": "grant", "role": role, "target": request.destination, "expires": request.expires.isoformat()}
        for role in ("operator", "target")
    }}
    if fault == "array":
        response = []
    elif fault == "nonce":
        response["id"] = "b" * 32
    elif fault == "missing_grant":
        del response["target"]
    elif fault == "role":
        response["target"]["role"] = "operator"
    elif fault in {"expired", "extended", "naive", "bad_date"}:
        response["target"]["expires"] = {
            "expired": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            "extended": (request.expires + timedelta(seconds=1)).isoformat(),
            "naive": request.expires.replace(tzinfo=None).isoformat(),
            "bad_date": "untrusted-date",
        }[fault]
    elif fault == "grant_identity":
        response["target"]["id"] = "foreign-grant"
    with patch("src.services.device_peer_launcher.ssl.create_default_context", return_value=MagicMock()):
        client = PeerLauncherClient(settings())
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"untrusted-private-response" if fault == "bad_json" else json.dumps(response).encode())
    ))
    async with client:
        with pytest.raises(PeerLauncherError) as caught:
            await client.start("a" * 32, request)
    assert "untrusted-private-response" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [204, 403, "timeout"])
async def test_stop_requires_confirmed_no_content_without_leaking_transport_errors(outcome):
    requests = []

    def transport(request):
        requests.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("private TLS/network details", request=request)
        return httpx.Response(outcome, content=b"private response")

    with patch("src.services.device_peer_launcher.ssl.create_default_context", return_value=MagicMock()):
        client = PeerLauncherClient(settings())
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    async with client:
        if outcome == 204:
            await client.stop("a" * 32)
        else:
            with pytest.raises(PeerLauncherError) as caught:
                await client.stop("a" * 32)
            assert "private" not in str(caught.value)
    assert len(requests) == 1
    assert requests[0].method == "DELETE"
    assert requests[0].url.path == "/v1/sessions/" + "a" * 32
