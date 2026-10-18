"""A lost/cancelled provisioning attempt must revoke its known nonce."""

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.jobs.platform.device_peer_sessions import DEVICE_PEER_START_DEFINITION, revoke_peer_session, start_peer_session
from src.jobs.platform.base import PlatformJobFailure
from src.models.contracts.device_peer_sessions import DevicePeerRevokePayload, DevicePeerSessionPayload


@pytest.mark.asyncio
async def test_start_failure_revokes_unknown_outcome():
    context = MagicMock(job_id=uuid4())
    context.report = AsyncMock()
    context.log = AsyncMock()
    payload = DevicePeerSessionPayload(device_id=uuid4(), request_id=uuid4(), operator_key=base64.b64encode(b"o"*32).decode(),
                                      target_key="dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHQ=",
                                      destination="127.0.0.1:443", expires=datetime.now(timezone.utc) + timedelta(minutes=5))
    client = AsyncMock()
    client.start.side_effect = TimeoutError()
    with patch("src.jobs.platform.device_peer_sessions._authorize", new=AsyncMock()), patch("src.services.device_peer_launcher.PeerLauncherClient") as factory:
        factory.return_value.__aenter__.return_value = client
        with pytest.raises(TimeoutError):
            await start_peer_session(context, payload)
    client.stop.assert_awaited_once_with(context.job_id.hex)
    assert DEVICE_PEER_START_DEFINITION.policy.max_attempts == 1
    assert not DEVICE_PEER_START_DEFINITION.policy.retry_on_runner_loss


@pytest.mark.asyncio
async def test_revoke_does_not_claim_endpoint_disconnect():
    context = MagicMock(job_id=uuid4(), report=AsyncMock())
    payload = DevicePeerRevokePayload(device_id=uuid4(), session_job_id=uuid4())
    client = AsyncMock()
    with patch("src.jobs.platform.device_peer_sessions._authorize", new=AsyncMock()), patch("src.services.device_peer_launcher.PeerLauncherClient") as factory:
        factory.return_value.__aenter__.return_value = client
        result = await revoke_peer_session(context, payload)
    client.stop.assert_awaited_once_with(payload.session_job_id.hex)
    assert result["lighthouse_revoked"] is True
    assert result["endpoint_disconnect_confirmed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_state,device_state,foreign_scope,revoke,permitted",
    [
        ("missing", "active", False, False, False),
        ("inactive", "active", False, True, False),
        ("active", "active", True, False, False),
        ("active", "active", True, True, False),
        ("active", "disabled", False, False, False),
        ("active", "disabled", False, True, True),
        ("active", "active", False, False, True),
    ],
)
async def test_delayed_job_rechecks_actor_scope_and_disabled_device_cleanup(
    actor_state, device_state, foreign_scope, revoke, permitted,
):
    """Persisted admission never replaces authorization at the worker sink."""
    actor_id, organization_id, device_id = uuid4(), uuid4(), uuid4()
    context = SimpleNamespace(
        job_id=uuid4(), requested_by_user_id=str(actor_id),
        organization_id=str(organization_id), report=AsyncMock(), log=AsyncMock(),
    )
    actor = None if actor_state == "missing" else SimpleNamespace(
        id=actor_id, email="operator@example.com", organization_id=organization_id,
        is_superuser=False, is_active=actor_state == "active",
    )
    device = SimpleNamespace(
        organization_id=uuid4() if foreign_scope else organization_id, status=device_state,
    )
    db = MagicMock()

    @asynccontextmanager
    async def database():
        yield db

    client = AsyncMock()
    client.start.return_value = {"id": context.job_id.hex, "connectivity_verified": False}
    request = DevicePeerRevokePayload(device_id=device_id, session_job_id=uuid4()) if revoke else DevicePeerSessionPayload(
        device_id=device_id, request_id=uuid4(),
        operator_key=base64.b64encode(b"o" * 32).decode(),
        target_key=base64.b64encode(b"t" * 32).decode(), destination="127.0.0.1:443",
        expires=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    handler = revoke_peer_session if revoke else start_peer_session
    with patch("src.core.database.get_db_context", database), \
         patch("src.repositories.users.UserRepository") as repository, \
         patch("src.services.devices.require_device_execute_permission", new=AsyncMock()) as permission, \
         patch("src.services.devices.get_device_scoped", new=AsyncMock(return_value=device)) as scoped, \
         patch("src.services.device_peer_launcher.PeerLauncherClient") as launcher:
        repository.return_value.get_by_id = AsyncMock(return_value=actor)
        permission.side_effect = lambda _db, principal: principal
        launcher.return_value.__aenter__.return_value = client
        if permitted:
            result = await handler(context, request)
            if revoke:
                client.stop.assert_awaited_once_with(request.session_job_id.hex)
                assert result["endpoint_disconnect_confirmed"] is False
            else:
                client.start.assert_awaited_once_with(context.job_id.hex, request)
                assert result == client.start.return_value
        else:
            with pytest.raises(PlatformJobFailure):
                await handler(context, request)
            launcher.assert_not_called()
        repository.return_value.get_by_id.assert_awaited_once_with(actor_id)
        if actor_state == "active":
            principal = permission.await_args.args[1]
            assert principal.user_id == actor_id
            assert principal.organization_id == organization_id
            assert not principal.is_platform_admin
            scoped.assert_awaited_once_with(db, principal, device_id)
        else:
            permission.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_queued_job_never_contacts_launcher():
    context = SimpleNamespace(job_id=uuid4(), report=AsyncMock())
    request = DevicePeerSessionPayload(
        device_id=uuid4(), request_id=uuid4(), operator_key=base64.b64encode(b"o" * 32).decode(),
        target_key=base64.b64encode(b"t" * 32).decode(), destination="127.0.0.1:443",
        expires=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with patch("src.jobs.platform.device_peer_sessions._authorize", new=AsyncMock()), \
         patch("src.services.device_peer_launcher.PeerLauncherClient") as launcher:
        with pytest.raises(PlatformJobFailure):
            await start_peer_session(context, request)
        launcher.assert_not_called()


@pytest.mark.asyncio
async def test_unconfirmed_timeout_cleanup_preserves_original_failure():
    from src.services.device_peer_launcher import PeerLauncherError

    context = SimpleNamespace(job_id=uuid4(), report=AsyncMock(), log=AsyncMock())
    request = DevicePeerSessionPayload(
        device_id=uuid4(), request_id=uuid4(), operator_key=base64.b64encode(b"o" * 32).decode(),
        target_key=base64.b64encode(b"t" * 32).decode(), destination="127.0.0.1:443",
        expires=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    client = AsyncMock()
    original = TimeoutError("lost provision response")
    client.start.side_effect = original
    client.stop.side_effect = PeerLauncherError("stop unconfirmed")
    with patch("src.jobs.platform.device_peer_sessions._authorize", new=AsyncMock()), \
         patch("src.services.device_peer_launcher.PeerLauncherClient") as launcher:
        launcher.return_value.__aenter__.return_value = client
        with pytest.raises(TimeoutError) as caught:
            await start_peer_session(context, request)
    assert caught.value is original
    client.stop.assert_awaited_once_with(context.job_id.hex)
    assert context.log.await_args.args[:2] == ("warning", "peer_cleanup_pending")
