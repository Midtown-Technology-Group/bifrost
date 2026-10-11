"""A lost/cancelled provisioning attempt must revoke its known nonce."""

import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.jobs.platform.device_peer_sessions import DEVICE_PEER_START_DEFINITION, revoke_peer_session, start_peer_session
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
