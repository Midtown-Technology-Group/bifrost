"""Synchronous, socket-free checks of owned observer rejection evidence."""

from __future__ import annotations

import builtins
import json
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import httpx
import pytest
from fastapi import Request

from tests.parity.core import sdk_observer as observer
from tests.parity.core.environment import SOURCE_SHA256
from tests.parity.core.pinned import SOURCE_PATH


EXECUTION = str(UUID(int=1))
USER = str(UUID(int=2))
ORG = str(UUID(int=3))
SOLUTION = str(UUID(int=4))
DEPLOYMENT = str(UUID(int=5))
ATTEMPT = str(UUID(int=6))
FAILURE_KEY = f"{observer.PREFIX}{EXECUTION}:observer-failures"


def complete_without_io(coroutine):
    """Drive immediate fake awaits directly; no event loop or sockets exist."""
    try:
        try:
            coroutine.send(None)
        except StopIteration as completed:
            return completed.value
        pytest.fail("Diagnostic unit unexpectedly attempted suspended I/O")
    finally:
        coroutine.close()


class MemoryRedis:
    def __init__(self, registry):
        self.registry = registry
        self.records = {}
        self.ttls = {}

    async def get(self, key):
        assert key == f"{observer.PREFIX}{EXECUTION}:owner"
        return json.dumps(self.registry)

    async def rpush(self, key, value):
        self.records.setdefault(key, []).append(value)

    async def ltrim(self, key, start, stop):
        assert stop == -1
        self.records[key] = self.records[key][start:]

    async def expire(self, key, ttl):
        self.ttls[key] = ttl


class Upstream:
    base_url = httpx.URL("http://api:8000")

    def __init__(self):
        self.calls = 0

    async def request(self, *args, **kwargs):
        self.calls += 1
        return httpx.Response(503, json={"private": "unapproved-response-material"})


def arrangement(monkeypatch, *, source=False, mismatch=False, active=True):
    registry = {"user_id": USER, "org_id": ORG, "request_name": "fixture"}
    claims = {
        "engine": True, "engine_execution_id": EXECUTION,
        "delegated_user_id": "unapproved-user" if mismatch else USER,
        "org_id": ORG, "engine_attempt_token": ATTEMPT,
        "engine_solution_id": SOLUTION,
        "delegated_is_superuser": False, "delegated_is_provider_org": False,
        "delegated_is_external": False,
    }
    monkeypatch.setattr(observer, "decode_token", lambda *args, **kwargs: claims)
    path = "api/sdk/integrations/get"
    if source:
        registry.update(solution_id=SOLUTION, deployment_id=DEPLOYMENT, source={
            "path": f"_solutions/{SOLUTION}/{DEPLOYMENT}/{SOURCE_PATH}",
            "sha256": SOURCE_SHA256,
        })
        path = f"api/sdk/modules/{registry['source']['path']}"
        row = SimpleNamespace(
            executed_by=USER, organization_id=ORG, solution_deployment_id=DEPLOYMENT,
            runtime_mode="deployment-v1", runtime_evidence={
                "solution_id": SOLUTION, "solution_deployment_id": DEPLOYMENT,
                "workflow_source_hash": f"sha256:{SOURCE_SHA256}",
            },
        )

        class CommittedSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def get(self, *_):
                return row

            async def scalars(self, *_):
                attempts = [SimpleNamespace(claim_token=UUID(ATTEMPT))] if active else []
                return SimpleNamespace(all=lambda: attempts)

        monkeypatch.setattr("src.core.database.get_db_context", CommittedSession)

    async def body():
        return b""

    redis = MemoryRedis(registry)
    upstream = Upstream()
    request = cast(Request, SimpleNamespace(
        body=body, method="GET" if source else "POST",
        headers={"authorization": "Bearer synthetic-unit-value"},
        scope={"query_string": b"" if source else b"unapproved-query-material"},
        app=SimpleNamespace(state=SimpleNamespace(redis=redis, client=upstream)),
    ))
    return request, path, redis, upstream


def test_rejected_request_still_raises_without_forwarding_or_accepted_transport(monkeypatch):
    request, path, redis, upstream = arrangement(monkeypatch)
    for _ in range(observer.FAILURE_LIMIT + 2):
        with pytest.raises(AssertionError, match="Unapproved owned SDK query"):
            complete_without_io(observer.forward(request, path))
    assert upstream.calls == 0
    assert set(redis.records) == {FAILURE_KEY}
    assert len(redis.records[FAILURE_KEY]) == observer.FAILURE_LIMIT
    assert redis.ttls[FAILURE_KEY] == 600
    assert json.loads(redis.records[FAILURE_KEY][0]) == {
        "stage": "request_guard", "reason": "sdk_query",
        "request_kind": "integration_get", "exception_class": "AssertionError",
        "upstream_status": None,
    }
    assert "unapproved-query-material" not in json.dumps(redis.records)


def test_rejected_source_response_retains_status_and_guard_without_source_bytes(monkeypatch):
    request, path, redis, upstream = arrangement(monkeypatch, source=True)
    with pytest.raises(AssertionError, match="Unapproved source response"):
        complete_without_io(observer.forward(request, path))
    assert upstream.calls == 1
    assert set(redis.records) == {FAILURE_KEY}
    serialized = redis.records[FAILURE_KEY][0]
    assert json.loads(serialized) == {
        "stage": "response_guard", "reason": "source_response_status_shape",
        "request_kind": "source", "exception_class": "AssertionError",
        "upstream_status": 503,
    }
    assert "unapproved-response-material" not in serialized
    assert SOURCE_PATH not in serialized and ATTEMPT not in serialized


def test_unverified_owner_is_rejected_without_diagnostic_or_forwarding(monkeypatch):
    request, path, redis, upstream = arrangement(monkeypatch, mismatch=True)
    with pytest.raises(AssertionError, match="SDK caller attribution differs"):
        complete_without_io(observer.forward(request, path))
    assert redis.records == {} and upstream.calls == 0


def test_source_attempt_fence_still_rejects_before_forwarding(monkeypatch):
    request, path, redis, upstream = arrangement(monkeypatch, source=True, active=False)
    with pytest.raises(AssertionError, match="Source active signed attempt differs"):
        complete_without_io(observer.forward(request, path))
    assert upstream.calls == 0 and set(redis.records) == {FAILURE_KEY}
    assert json.loads(redis.records[FAILURE_KEY][0]) == {
        "stage": "source_authority", "reason": "active_attempt",
        "request_kind": "source", "exception_class": "AssertionError",
        "upstream_status": None,
    }


@pytest.mark.parametrize("import_class", [ImportError, ModuleNotFoundError])
def test_source_guard_import_failure_is_classified_without_import_details(monkeypatch, import_class):
    request, path, redis, upstream = arrangement(monkeypatch, source=True)
    private = "unapproved-private-import-details"
    failure = import_class(private)
    original_import = builtins.__import__

    def missing_guard(name, module_globals=None, module_locals=None, fromlist=(), level=0):
        if name == "tests.parity.core.pinned" and "assert_source_request" in fromlist:
            raise failure
        return original_import(name, module_globals, module_locals, fromlist, level)

    # Fail only the observer's lazy source-guard import, without adding mounts,
    # changing import paths, or bypassing the existing caller/attempt guards.
    monkeypatch.setattr(builtins, "__import__", missing_guard)
    with pytest.raises(import_class) as caught:
        complete_without_io(observer.forward(request, path))
    assert caught.value is failure
    assert upstream.calls == 0 and set(redis.records) == {FAILURE_KEY}
    serialized = redis.records[FAILURE_KEY][0]
    assert private not in serialized
    assert json.loads(serialized) == {
        "stage": "request_guard", "reason": "unclassified", "request_kind": "source",
        "exception_class": import_class.__name__, "upstream_status": None,
    }


def test_module_index_probe_is_characterized_without_admitting_get(monkeypatch):
    request, _, redis, upstream = arrangement(monkeypatch, source=True)
    # clear_workspace_modules -> get_module_index_sync uses this GET fallback
    # for Solution context too. It remains outside the owned A request contract.
    request.scope["query_string"] = f"solution_id={SOLUTION}".encode()
    with pytest.raises(AssertionError, match="Unapproved owned SDK method"):
        complete_without_io(observer.forward(request, "api/sdk/modules-index"))
    assert upstream.calls == 0 and set(redis.records) == {FAILURE_KEY}
    serialized = redis.records[FAILURE_KEY][0]
    assert SOLUTION not in serialized and "modules-index" not in serialized
    assert json.loads(serialized) == {
        "stage": "request_guard", "reason": "sdk_method", "request_kind": "other",
        "exception_class": "AssertionError", "upstream_status": None,
    }


@pytest.mark.parametrize("invalid_json", [False, True])
def test_source_upstream_and_decode_failure_stages_do_not_retain_error_text(monkeypatch, invalid_json):
    request, path, redis, upstream = arrangement(monkeypatch, source=True)
    private = "unapproved-upstream-material"

    async def failure(*args, **kwargs):
        if invalid_json:
            return httpx.Response(502, content=private.encode())
        raise httpx.ConnectError(private)

    monkeypatch.setattr(upstream, "request", failure)
    with pytest.raises((httpx.ConnectError, json.JSONDecodeError)):
        complete_without_io(observer.forward(request, path))
    assert set(redis.records) == {FAILURE_KEY}
    serialized = redis.records[FAILURE_KEY][0]
    assert private not in serialized
    assert json.loads(serialized) == {
        "stage": "response_decode" if invalid_json else "upstream_request",
        "reason": "unclassified", "request_kind": "source",
        "exception_class": "JSONDecodeError" if invalid_json else "ConnectError",
        "upstream_status": 502 if invalid_json else None,
    }


def test_diagnostic_storage_failure_does_not_replace_original_rejection(monkeypatch):
    request, path, redis, upstream = arrangement(monkeypatch)

    async def unavailable(*_):
        raise RuntimeError("unapproved-storage-error-material")

    monkeypatch.setattr(redis, "rpush", unavailable)
    with pytest.raises(AssertionError, match="Unapproved owned SDK query"):
        complete_without_io(observer.forward(request, path))
    assert upstream.calls == 0 and redis.records == {}


def test_failure_projection_rejects_unapproved_fields_classes_and_status():
    private = "unapproved-private-material"
    record = observer.failure_record({
        "stage": private, "request_kind": private, "upstream_status": True,
        "body": private, "path": private, "claims": private,
    }, AssertionError(private))
    assert record == {
        "stage": "unclassified", "reason": "unclassified",
        "request_kind": "unclassified", "exception_class": "AssertionError",
        "upstream_status": None,
    }
    assert private not in json.dumps(record)
    record["exception_class"] = "PrivateMaterialClass"
    record["upstream_status"] = 999
    assert observer.failure_projection(record)["exception_class"] == "unclassified"
    assert observer.failure_projection(record)["upstream_status"] is None
