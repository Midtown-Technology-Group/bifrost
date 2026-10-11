"""Private ingress ordering/denial tests; fake owner is not runtime acceptance."""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from src.core.runtime_sdk_credentials import RuntimeSDKDenied
from src.models.contracts.cli import SDKIntegrationsGetResponse
from src.services import isolated_runtime_sdk_http as ingress
from tests.unit.services.test_isolated_runtime_sdk_tokens import ingress_inputs


def app_inputs(monkeypatch, load_snapshot=None):
    _, token, body, snapshot, caller, source, policy, _ = ingress_inputs()
    gate = AsyncMock()
    fetch = AsyncMock(
        return_value=SDKIntegrationsGetResponse(
            integration_id=str(uuid4()),
            config={"ready": True},
        )
    )
    sessions = []

    async def committed_snapshot():
        return snapshot

    @asynccontextmanager
    async def session_factory():
        session = object()
        sessions.append(session)
        yield session

    monkeypatch.setattr(ingress, "admit_live_integration_get", gate)
    monkeypatch.setattr(ingress, "fetch_synthetic_integration", fetch)
    app = ingress.build_isolated_sdk_app(
        grant_id=snapshot.id,
        load_snapshot=committed_snapshot if load_snapshot is None else load_snapshot,
        caller=caller,
        source=source,
        policy=policy,
        gate_path=Path("/private/gate.sock"),
        owner_pid=123,
        owner_uid=1001,
        owner_start_ticks="456",
        session_factory=session_factory,
        fixture_integration_id=uuid4(),
    )
    return app, token, body, gate, fetch, sessions


async def test_verified_request_reaches_owner_before_fetch(monkeypatch):
    app, token, body, gate, fetch, sessions = app_inputs(monkeypatch)

    async def admitted(*args, **kwargs):
        assert not sessions
        fetch.assert_not_awaited()
        assert kwargs["owner_pid"] == 123
        assert kwargs["owner_start_ticks"] == "456"
        assert args[0].grant_id is not None

    gate.side_effect = admitted
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://fixture"
    ) as client:
        response = await client.post(
            "/api/sdk/integrations/get",
            content=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200 and response.json()["config"] == {"ready": True}
    gate.assert_awaited_once()
    fetch.assert_awaited_once()
    assert len(sessions) == 1


@pytest.mark.parametrize(
    "change",
    ["auth", "duplicate-auth", "unknown-body", "oversize", "query", "owner-denied"],
)
async def test_denial_or_uncertain_owner_never_opens_fetch_session(monkeypatch, change):
    app, token, body, gate, fetch, sessions = app_inputs(monkeypatch)
    headers = [("Authorization", f"Bearer {token}")]
    path = "/api/sdk/integrations/get"
    if change == "auth":
        headers = []
    elif change == "duplicate-auth":
        headers *= 2
    elif change == "unknown-body":
        body = body[:-1] + b',"owner_pid":123}'
    elif change == "oversize":
        body = b" " * 8193
    elif change == "query":
        path += "?session=other"
    else:
        gate.side_effect = RuntimeSDKDenied("no observed admission")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://fixture"
    ) as client:
        response = await client.post(path, content=body, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "runtime SDK request denied"}
    fetch.assert_not_awaited()
    assert not sessions
    if change != "owner-denied":
        gate.assert_not_awaited()


@pytest.mark.parametrize(
    "path",
    [
        "/auth/refresh",
        "/api/executions/x",
        "/openapi.json",
        "/api/sdk/integrations/refresh_token",
    ],
)
async def test_no_ordinary_or_lifecycle_routes(monkeypatch, path):
    app, token, _, gate, fetch, _ = app_inputs(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://fixture"
    ) as client:
        response = await client.post(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    gate.assert_not_awaited()
    fetch.assert_not_awaited()


async def test_uncertain_snapshot_load_is_not_retried_or_promoted_to_admission(
    monkeypatch,
):
    loader = AsyncMock(side_effect=RuntimeSDKDenied("grant not committed"))
    app, token, body, gate, fetch, sessions = app_inputs(monkeypatch, loader)
    loader.assert_not_awaited()  # TLS socket can exist before Rust Start/provision.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://fixture"
    ) as client:
        for _ in range(2):
            response = await client.post(
                "/api/sdk/integrations/get",
                content=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 403
    loader.assert_awaited_once()
    gate.assert_not_awaited()
    fetch.assert_not_awaited()
    assert not sessions


async def test_invalid_bearer_cannot_trigger_committed_snapshot_read(monkeypatch):
    loader = AsyncMock(side_effect=AssertionError("unverified HTTP cannot load grant"))
    app, _, body, gate, fetch, _ = app_inputs(monkeypatch, loader)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://fixture"
    ) as client:
        response = await client.post(
            "/api/sdk/integrations/get",
            content=body,
            headers={"Authorization": "Bearer not-a-runtime-token"},
        )
    assert response.status_code == 403
    loader.assert_not_awaited()
    gate.assert_not_awaited()
    fetch.assert_not_awaited()
