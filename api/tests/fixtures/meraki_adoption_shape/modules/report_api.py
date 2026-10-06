"""Bounded vendor reads for Meraki report routing."""

from __future__ import annotations

from typing import Any

import httpx

from bifrost import config, integrations


class VendorAPI:
    """One bounded connection; never retries writes or follows report URLs."""

    def __init__(self, client: httpx.AsyncClient, base: str, headers: dict[str, str], max_calls: int = 40):
        self.client, self.base, self.headers = client, base.rstrip("/"), headers
        self.calls = 0
        self.max_calls = max_calls

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls > self.max_calls:
            raise RuntimeError("Vendor call limit exceeded.")
        response = await self.client.request(method, self.base + path, headers=self.headers, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"Vendor {method} {path} failed with HTTP {response.status_code}.")
        if len(response.content) > 4_000_000:
            raise RuntimeError("Vendor response exceeds the routing size limit.")
        if response.headers.get("link") and 'rel="next"' in response.headers["link"]:
            raise RuntimeError("Incomplete vendor inventory; pagination requires review.")
        return response.json()


def rows(payload: Any, key: str) -> list[dict]:
    result = payload if isinstance(payload, list) else payload.get(key)
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise RuntimeError("Unexpected vendor collection shape.")
    if isinstance(payload, dict) and int(payload.get("record_count") or len(result)) > len(result):
        raise RuntimeError("Incomplete vendor collection.")
    return result


async def halo_connection() -> tuple[str, str]:
    integration = await integrations.get("HaloPSA")
    if integration and integration.oauth and integration.oauth.access_token:
        return str(integration.config["base_url"]).rstrip("/"), integration.oauth.access_token
    values = {}
    for key in ("halopsa_api_base_url", "halopsa_api_auth_url", "halopsa_api_client_id", "halopsa_api_client_secret"):
        values[key] = await config.get(key, scope="global")
    if not all(values.values()):
        raise RuntimeError("HaloPSA credentials are unavailable.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            str(values["halopsa_api_auth_url"]).rstrip("/") + "/token",
            data={
                "grant_type": "client_credentials",
                "scope": "all",
                "client_id": values["halopsa_api_client_id"],
                "client_secret": values["halopsa_api_client_secret"],
            },
        )
        response.raise_for_status()
        return str(values["halopsa_api_base_url"]).rstrip("/") + "/api", response.json()["access_token"]


async def halo_get(path: str, params: dict | None = None) -> Any:
    base, token = await halo_connection()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(base + path, headers={"Authorization": "Bearer " + token}, params=params)
        response.raise_for_status()
        return response.json()
