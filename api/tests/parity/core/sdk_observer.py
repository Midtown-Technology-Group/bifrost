"""Test-only transparent SDK HTTP observer, run by the supported Docker lane.

The SDK and real API are unchanged. Only executions explicitly registered in
Redis are recorded. No bearer credential or encrypted envelope is retained.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, Request, Response
from src.config import get_settings
from src.core.security import decode_token

PREFIX = "parity:core:sdk:"
HOP_HEADERS = {
    "host",
    "connection",
    "transfer-encoding",
    "content-length",
    "content-encoding",
}
CLAIMS = (
    "engine",
    "is_superuser",
    "engine_execution_id",
    "org_id",
    "engine_solution_id",
    "engine_global_repo_access",
    "delegated_user_id",
    "delegated_email",
    "delegated_name",
    "delegated_is_superuser",
    "delegated_is_provider_org",
    "delegated_is_external",
)
FIXTURE_VALUE = "core-parity-synthetic-config-value"


def assert_synthetic_request(
    method: str, path: str, query: bytes, raw: bytes, registry: dict
) -> dict:
    """Validate the complete owned A request before forwarding or retention."""
    assert method == "POST", "Unapproved owned SDK method"
    assert path in {"api/sdk/integrations/get", "api/sdk/integrations/get_mapping"}, (
        "Unapproved owned SDK path"
    )
    assert query == b"", "Unapproved owned SDK query"
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise AssertionError("Unapproved owned SDK JSON") from None
    expected = {"name": registry["request_name"], "scope": registry["org_id"]}
    if path == "api/sdk/integrations/get_mapping":
        expected["entity_id"] = None
    assert body == expected, "Unapproved owned SDK request body"
    return body


def target_url(upstream: httpx.URL, path: str, query: bytes) -> httpx.URL:
    """Construct a path without URL-joining network-path/absolute references."""
    assert (
        upstream.scheme == "http" and upstream.host == "api" and upstream.port == 8000
    )
    assert not path.startswith("/") and "://" not in path and "\\" not in path, (
        "Unsupported observer request path"
    )
    target = upstream.copy_with(path=f"/{path}", query=query)
    assert target.scheme == "http" and target.host == "api" and target.port == 8000
    assert not target.username and not target.password
    return target


def assert_synthetic_response(path: str, status: int, body) -> None:
    """Refuse to retain credentials or unapproved fixture values."""
    assert path in {"api/sdk/integrations/get", "api/sdk/integrations/get_mapping"}, (
        "Unexpected owned SDK path"
    )
    assert status == 200, "Unexpected upstream SDK error; refuse response retention"
    if body is None:
        return
    assert isinstance(body, dict)
    allowed = {
        "integration_id",
        "entity_id",
        "entity_name",
        "config",
        "oauth",
        "config_secret_keys",
    }
    if path == "api/sdk/integrations/get_mapping":
        allowed |= {
            "id",
            "organization_id",
            "oauth_token_id",
            "created_at",
            "updated_at",
        }
    assert set(body) <= allowed, "Unexpected credential-bearing response field"
    assert body.get("oauth") is None and body.get("oauth_token_id") is None, (
        "Unexpected OAuth material"
    )
    assert body.get("config_secret_keys", []) == [], "Unexpected secret config"
    config = body.get("config") or {}
    assert isinstance(config, dict) and set(config) <= {"fixture_value"}, (
        "Unexpected synthetic config key"
    )
    assert all(value == FIXTURE_VALUE for value in config.values()), (
        "Unexpected credential/config value"
    )
    assert body.get("entity_id") in {None, "synthetic-entity"} and body.get(
        "entity_name"
    ) in {None, "Synthetic entity"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    assert settings.environment == "testing", "SDK observer is restricted to testing"
    upstream = httpx.URL(os.environ["CORE_SDK_UPSTREAM_URL"])
    assert (
        upstream.scheme == "http" and upstream.host == "api" and upstream.port == 8000
    )
    assert upstream.path == "/" and not upstream.username and not upstream.password
    app.state.client = httpx.AsyncClient(
        base_url=upstream, follow_redirects=False, timeout=30
    )
    app.state.redis = redis.from_url(
        os.environ["BIFROST_REDIS_URL"], decode_responses=True
    )
    try:
        yield
    finally:
        await app.state.client.aclose()
        await app.state.redis.aclose()


app = FastAPI(lifespan=lifespan)


@app.api_route(
    "/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
)
async def forward(request: Request, path: str) -> Response:
    raw = await request.body()
    authorization = request.headers.get("authorization", "")
    claims = (
        decode_token(authorization[7:], expected_type="access")
        if authorization.startswith("Bearer ")
        else None
    )
    registry = None
    execution = None
    if claims and claims.get("engine") is True and claims.get("engine_execution_id"):
        execution = str(UUID(claims["engine_execution_id"]))
        registry_raw = await request.app.state.redis.get(f"{PREFIX}{execution}:owner")
        if registry_raw:
            registry = json.loads(registry_raw)
            # Verify signed attribution against the separately seeded owner.
            assert claims.get("delegated_user_id") == registry["user_id"], (
                "SDK caller attribution differs"
            )
            assert claims.get("org_id") == registry["org_id"], (
                "SDK organization attribution differs"
            )
            UUID(claims["engine_attempt_token"])
    approved_request = None
    if registry is not None:
        approved_request = assert_synthetic_request(
            request.method,
            path,
            request.scope["query_string"],
            raw,
            registry,
        )
    before = datetime.now(UTC).isoformat()
    upstream = await request.app.state.client.request(
        request.method,
        target_url(
            request.app.state.client.base_url, path, request.scope["query_string"]
        ),
        content=raw,
        headers={
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_HEADERS
        },
    )
    observed_status = upstream.status_code
    observed_body = (
        upstream.json()
        if registry is not None and path.startswith("api/sdk/") and upstream.content
        else None
    )
    if registry is not None and path.startswith("api/sdk/"):
        assert claims is not None
        assert_synthetic_response(path, upstream.status_code, observed_body)
    mutation = registry.get("sdk_response_mutation") if registry else None
    if mutation == "forbidden" and path == "api/sdk/integrations/get":
        # Explicit test-owned wire self-test: real upstream request still runs.
        observed_status = 403
        observed_body = {"detail": "Synthetic reference SDK scope denial"}
    if registry is not None and path.startswith("api/sdk/"):
        safe_claims = {key: claims.get(key) for key in CLAIMS}
        safe_claims["engine_attempt_token_present"] = bool(
            claims.get("engine_attempt_token")
        )
        safe_claims["engine_attempt_token_digest"] = hashlib.sha256(
            claims["engine_attempt_token"].encode()
        ).hexdigest()
        record = {
            "method": request.method,
            "path": f"/{path}",
            "query": [],
            "request": approved_request,
            "authorization": {
                "scheme": "Bearer",
                "signature_valid": True,
                "owner_verified": True,
                "claims": safe_claims,
            },
            "upstream_status": upstream.status_code,
            "status": observed_status,
            "response": observed_body,
            "synthetic_mutation": mutation,
            "before": before,
            "after": datetime.now(UTC).isoformat(),
        }
        await request.app.state.redis.rpush(
            f"{PREFIX}{execution}:requests", json.dumps(record)
        )
        await request.app.state.redis.expire(f"{PREFIX}{execution}:requests", 600)
    if mutation == "forbidden" and path == "api/sdk/integrations/get":
        return Response(
            content=json.dumps(observed_body),
            status_code=observed_status,
            media_type="application/json",
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in HOP_HEADERS
        },
    )
