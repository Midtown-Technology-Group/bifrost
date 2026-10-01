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
from typing import Any
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
FAILURE_LIMIT = 16
FAILURE_STAGES = frozenset({
    "source_authority", "request_guard", "upstream_request", "response_decode",
    "response_guard", "transport_retention",
})
REQUEST_KINDS = frozenset({"source", "integration_get", "mapping_get", "other"})
FAILURE_CLASSES = frozenset({
    "AssertionError", "ValueError", "TypeError", "KeyError", "RuntimeError",
    "JSONDecodeError", "ConnectError", "ConnectTimeout", "ReadTimeout",
    "WriteTimeout", "PoolTimeout", "RemoteProtocolError", "ReadError",
    "WriteError", "UnsupportedProtocol", "InvalidURL", "DBAPIError",
    "OperationalError", "InterfaceError", "ProgrammingError",
})
# Only exact, fixed guard messages select a reason. Exception text is never
# formatted or retained, including for an unknown exception or guard message.
_GUARD_REASONS = {
    "Source signed Solution differs": "signed_solution",
    "Source caller is not normal tenant": "normal_tenant",
    "Source committed caller or pin differs": "committed_caller_pin",
    "Source durable mode or evidence differs": "durable_mode_evidence",
    "Source durable Solution differs": "durable_solution",
    "Source durable hash differs": "durable_hash",
    "Source active signed attempt differs": "active_attempt",
    "Unapproved owned SDK method": "sdk_method",
    "Unapproved owned SDK path": "sdk_path",
    "Unapproved owned SDK query": "sdk_query",
    "Unapproved owned SDK JSON": "sdk_json",
    "Unapproved owned SDK request body": "sdk_body",
    "Unsupported observer request path": "upstream_path",
    "Unexpected owned SDK path": "sdk_response_path",
    "Unexpected upstream SDK error; refuse response retention": "sdk_response_status",
    "Unexpected credential-bearing response field": "sdk_response_fields",
    "Unexpected OAuth material": "sdk_response_oauth",
    "Unexpected secret config": "sdk_response_secret_config",
    "Unexpected synthetic config key": "sdk_response_config_keys",
    "Unexpected credential/config value": "sdk_response_config_value",
    "Unapproved source registry": "source_registry",
    "Unapproved source pin": "source_pin",
    "Unapproved source request": "source_request",
    "Unapproved source request bytes": "source_request_bytes",
    "Unapproved source authority": "source_authority",
    "Unapproved source response": "source_response_status_shape",
    "Unapproved CachedModule fields": "source_response_fields",
    "Source response path differs": "source_response_path",
    "Source storage authority differs": "source_storage_authority",
    "Immutable source acquired workspace generation": "source_generation",
    "Source bytes differ from retained workspace blob": "source_bytes",
    "Source hash differs from pin": "source_hash",
}
FAILURE_REASONS = frozenset(_GUARD_REASONS.values()) | {"unclassified"}


def failure_projection(record: dict[str, Any]) -> dict[str, Any]:
    """Allowlist again at readback; even a corrupted diagnostic cannot print data."""
    def safe(value: Any, allowed: frozenset[str]) -> str:
        return value if isinstance(value, str) and value in allowed else "unclassified"

    status = record.get("upstream_status")
    return {
        "stage": safe(record.get("stage"), FAILURE_STAGES),
        "reason": safe(record.get("reason"), FAILURE_REASONS),
        "request_kind": safe(record.get("request_kind"), REQUEST_KINDS),
        "exception_class": safe(record.get("exception_class"), FAILURE_CLASSES),
        "upstream_status": status if type(status) is int and 100 <= status <= 599 else None,
    }


def failure_record(state: dict[str, Any], error: Exception) -> dict[str, Any]:
    reason = "unclassified"
    if type(error) is AssertionError and error.args and isinstance(error.args[0], str):
        reason = _GUARD_REASONS.get(error.args[0], reason)
    return failure_projection({
        **state, "reason": reason, "exception_class": type(error).__name__,
    })


def request_kind(path: str, registry: dict | None) -> str:
    source = registry.get("source") if registry else None
    if isinstance(source, dict) and path == f"api/sdk/modules/{source.get('path')}":
        return "source"
    return {
        "api/sdk/integrations/get": "integration_get",
        "api/sdk/integrations/get_mapping": "mapping_get",
    }.get(path, "other")


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
    elif registry.get("solution_id") is not None:
        expected["solution"] = registry["solution_id"]
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
    state = {
        "stage": "source_authority", "request_kind": request_kind(path, registry),
        "upstream_status": None,
    }
    try:
        return await _forward(request, path, raw, claims, registry, execution, state)
    except Exception as error:
        # This block is reached only after the existing signed owner attribution
        # guards above. Preserve the original exception even if diagnostics fail.
        if registry is not None and execution is not None:
            try:
                key = f"{PREFIX}{execution}:observer-failures"
                await request.app.state.redis.rpush(key, json.dumps(failure_record(state, error)))
                await request.app.state.redis.ltrim(key, -FAILURE_LIMIT, -1)
                await request.app.state.redis.expire(key, 600)
            except Exception:
                pass
        raise


async def _forward(
    request: Request, path: str, raw: bytes, claims: dict | None,
    registry: dict | None, execution: str | None, state: dict[str, Any],
) -> Response:
    source_request = False
    if registry is not None and registry.get("solution_id") is not None:
        assert claims is not None
        assert claims.get("engine_solution_id") == registry["solution_id"], (
            "Source signed Solution differs"
        )
        assert (
            claims.get("delegated_is_superuser") is False
            and claims.get("delegated_is_provider_org") is False
            and claims.get("delegated_is_external") is False
        ), "Source caller is not normal tenant"
        # This test-only read checks the bearer fence against committed active
        # authority; no raw token is retained in Redis, artifacts or errors.
        from sqlalchemy import select
        from src.core.database import get_db_context
        from src.models.orm.executions import Execution, WorkflowExecutionAttempt

        async with get_db_context() as db:
            row = await db.get(Execution, UUID(execution))
            assert (
                row is not None
                and str(row.executed_by) == registry["user_id"]
                and str(row.organization_id) == registry["org_id"]
                and str(row.solution_deployment_id) == registry["deployment_id"]
            ), "Source committed caller or pin differs"
            assert row.runtime_mode == "deployment-v1" and isinstance(
                row.runtime_evidence, dict
            ), "Source durable mode or evidence differs"
            evidence = row.runtime_evidence
            assert (
                evidence["solution_id"] == registry["solution_id"]
                and evidence["solution_deployment_id"] == registry["deployment_id"]
            ), "Source durable Solution differs"
            assert (
                evidence["workflow_source_hash"]
                == f"sha256:{registry['source']['sha256']}"
            ), "Source durable hash differs"
            attempts = (
                await db.scalars(
                    select(WorkflowExecutionAttempt).where(
                        WorkflowExecutionAttempt.execution_id == UUID(execution),
                        WorkflowExecutionAttempt.status.in_(["claimed", "running"]),
                        WorkflowExecutionAttempt.completed_at.is_(None),
                    )
                )
            ).all()
            assert len(attempts) == 1 and attempts[0].claim_token == UUID(
                claims["engine_attempt_token"]
            ), "Source active signed attempt differs"
        source_request = path == f"api/sdk/modules/{registry['source']['path']}"
    state["stage"] = "request_guard"
    approved_request = None
    if registry is not None and source_request:
        from tests.parity.core.pinned import assert_source_request

        assert_source_request(
            request.method, path, request.scope["query_string"], raw, registry
        )
        approved_request = None
    elif registry is not None:
        approved_request = assert_synthetic_request(
            request.method,
            path,
            request.scope["query_string"],
            raw,
            registry,
        )
    before = datetime.now(UTC).isoformat()
    state["stage"] = "upstream_request"
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
    state["upstream_status"] = upstream.status_code
    state["stage"] = "response_decode"
    observed_status = upstream.status_code
    observed_body = (
        upstream.json()
        if registry is not None and path.startswith("api/sdk/") and upstream.content
        else None
    )
    state["stage"] = "response_guard"
    if registry is not None and path.startswith("api/sdk/"):
        assert claims is not None
        if source_request:
            from tests.parity.core.pinned import assert_source_response

            assert_source_response(upstream.status_code, observed_body, registry)
        else:
            assert_synthetic_response(path, upstream.status_code, observed_body)
    mutation = registry.get("sdk_response_mutation") if registry else None
    if mutation == "forbidden" and path == "api/sdk/integrations/get":
        # Explicit test-owned wire self-test: real upstream request still runs.
        observed_status = 403
        observed_body = {"detail": "Synthetic reference SDK scope denial"}
    if registry is not None and path.startswith("api/sdk/"):
        state["stage"] = "transport_retention"
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
        if registry.get("solution_id") is not None:
            record["authorization"]["committed_attempt_verified"] = True
        await request.app.state.redis.rpush(
            f"{PREFIX}{execution}:{'sources' if source_request else 'requests'}",
            json.dumps(record),
        )
        await request.app.state.redis.expire(
            f"{PREFIX}{execution}:{'sources' if source_request else 'requests'}", 600
        )
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
