"""Finite issuer preimage/replay tests; fixture models do not prove live authority."""

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.core.runtime_sdk_credentials import RuntimeSDKDenied, decode_runtime_sdk_access
from src.services.isolated_runtime_sdk_bridge import _start_ticks
from src.services.isolated_runtime_sdk_issuer import FiniteIssuerServer, _canonical
from tests.unit.services.test_isolated_runtime_sdk_tokens import inputs


def fixture():
    snapshot, caller, source, policy, reference, _ = inputs()
    load = AsyncMock(return_value=snapshot)
    server = FiniteIssuerServer(
        directory=Path("/tmp/unused-issuer-unit-fixture"),
        owner_pid=os.getpid(),
        owner_uid=os.geteuid(),
        owner_start_ticks=_start_ticks(os.getpid()),
        grant_id=snapshot.id,
        load_snapshot=load,
        caller=caller,
        source=source,
        policy=policy,
        ca_pem="accepted fixture CA",
    )
    request = {
        "snapshot": snapshot.model_dump(mode="json"),
        "caller": caller.model_dump(mode="json"),
        "grant_id": str(snapshot.id),
        "grant_digest": reference.grant_digest,
        "expires_at": snapshot.initial_access_expires_at.isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z"),
    }
    return server, load, request, snapshot


@pytest.mark.asyncio
async def test_fixed_preimages_produce_only_finite_scoped_configuration():
    server, load, request, snapshot = fixture()
    raw = _canonical(request)
    response = json.loads(await server._signed_response(raw))
    assert set(response) == {"request_sha256", "expires_at", "sdk_configuration"}
    assert response["request_sha256"] == hashlib.sha256(raw).hexdigest()
    assert response["expires_at"] == request["expires_at"]
    config = response["sdk_configuration"]
    assert set(config) == {
        "endpoint",
        "bearer",
        "organization_id",
        "solution_id",
        "test_ca_pem",
    }
    assert config["endpoint"] == "https://127.0.0.1:8443"
    claims = decode_runtime_sdk_access(config["bearer"])
    assert (
        claims.sub == str(snapshot.id)
        and claims.grant_digest == request["grant_digest"]
    )
    load.assert_awaited_once()
    with pytest.raises(RuntimeSDKDenied):
        await server._signed_response(raw)
    load.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["duplicate", "unknown", "grant", "snapshot", "caller", "digest", "expiry"],
)
async def test_incoming_models_cannot_rebind_fixed_issuer_preimages(change):
    server, load, request, _ = fixture()
    if change == "unknown":
        request["renewal"] = True
    elif change == "grant":
        request["grant_id"] = "00000000-0000-0000-0000-000000000001"
    elif change == "snapshot":
        assert isinstance(request["snapshot"], dict)
        request["snapshot"]["caller_name"] = "different caller"
    elif change == "caller":
        assert isinstance(request["caller"], dict)
        request["caller"]["caller_name"] = "different caller"
    elif change == "digest":
        request["grant_digest"] = "b" * 64
    elif change == "expiry":
        request["expires_at"] = "2100-01-01T00:00:00.000000Z"
    raw = _canonical(request)
    if change == "duplicate":
        raw = b'{"expires_at":"other",' + raw[1:]
    with pytest.raises((RuntimeSDKDenied, ValueError)):
        await server._signed_response(raw)
    with pytest.raises(RuntimeSDKDenied):
        await server._signed_response(_canonical(request))
    assert load.await_count <= 1


@pytest.mark.asyncio
async def test_uncertain_read_cannot_retry_sign_or_change_grant():
    server, load, request, _ = fixture()
    load.side_effect = OSError("synthetic uncertain read")
    with pytest.raises(OSError):
        await server._signed_response(_canonical(request))
    with pytest.raises(RuntimeSDKDenied):
        await server._signed_response(_canonical(request))
    load.assert_awaited_once()
