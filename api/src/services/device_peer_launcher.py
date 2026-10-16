"""Bounded mTLS client for the router launcher; never uses ambient proxies."""

from __future__ import annotations

import ssl
import re
from urllib.parse import urlsplit

import httpx

from src.config import Settings


class PeerLauncherError(Exception):
    """Curated, secret-safe launcher failure."""


def enabled(settings: Settings) -> bool:
    return all((settings.device_peer_launcher_url, settings.device_peer_launcher_ca,
                settings.device_peer_launcher_certificate, settings.device_peer_launcher_key))


class PeerLauncherClient:
    def __init__(self, settings: Settings):
        if not enabled(settings):
            raise PeerLauncherError("device peer launcher is not configured")
        url = settings.device_peer_launcher_url or ""
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise PeerLauncherError("invalid launcher configuration")
        self.url = url.rstrip("/")
        try:
            tls = ssl.create_default_context(cafile=settings.device_peer_launcher_ca)
            tls.minimum_version = ssl.TLSVersion.TLSv1_3
            tls.load_cert_chain(settings.device_peer_launcher_certificate or "", settings.device_peer_launcher_key)
        except (OSError, ssl.SSLError):
            raise PeerLauncherError("invalid launcher TLS configuration") from None
        self.client = httpx.AsyncClient(verify=tls, trust_env=False, follow_redirects=False, timeout=10)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def start(self, session_id: str, payload) -> dict:
        if re.fullmatch(r"[0-9a-f]{32}", session_id) is None:
            raise PeerLauncherError("invalid session identity")
        body = {"id": session_id, "operatorKey": payload.operator_key,
                "targetKey": payload.target_key, "destination": payload.destination,
                "expires": payload.expires.isoformat().replace("+00:00", "Z")}
        try:
            async with self.client.stream("POST", self.url + "/v1/sessions", json=body) as response:
                if response.status_code != 200:
                    raise PeerLauncherError("launcher rejected session")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 64 * 1024:
                        raise PeerLauncherError("invalid launcher response")
                import json
                result = json.loads(data)
            if not isinstance(result, dict) or result.get("id") != session_id:
                raise PeerLauncherError("invalid launcher response")
            for role in ("operator", "target"):
                grant = result.get(role)
                if not isinstance(grant, dict) or grant.get("role") != role or grant.get("target") != payload.destination:
                    raise PeerLauncherError("invalid launcher response")
                from datetime import datetime, timezone
                expiry = datetime.fromisoformat(grant["expires"].replace("Z", "+00:00"))
                if expiry <= datetime.now(timezone.utc) or expiry > payload.expires:
                    raise PeerLauncherError("invalid launcher response")
            if result["operator"]["id"] != result["target"]["id"]:
                raise PeerLauncherError("invalid launcher response")
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise PeerLauncherError("launcher session failed") from None

    async def stop(self, session_id: str) -> None:
        if re.fullmatch(r"[0-9a-f]{32}", session_id) is None:
            raise PeerLauncherError("invalid session identity")
        try:
            response = await self.client.delete(self.url + "/v1/sessions/" + session_id)
            if response.status_code != 204:
                raise PeerLauncherError("launcher revocation failed")
        except httpx.HTTPError:
            raise PeerLauncherError("launcher revocation failed") from None
