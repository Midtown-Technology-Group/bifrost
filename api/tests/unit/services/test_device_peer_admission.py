"""A completed provisioning job must retain the same immutable request identity."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.device_peer_admission import enqueue_peer_session


@pytest.mark.asyncio
async def test_completed_job_is_reused_without_second_provision():
    db = MagicMock()
    db.execute = AsyncMock()
    completed = MagicMock(status="succeeded")
    db.get = AsyncMock(return_value=completed)
    with patch("src.services.device_peer_admission.enqueue_platform_job", new=AsyncMock()) as enqueue:
        result, reused = await enqueue_peer_session(db, MagicMock(), MagicMock(), dedupe_key="actor:device:request")
    assert result is completed
    assert reused
    enqueue.assert_not_awaited()
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_request_has_stable_job_identity():
    identities = []
    for _ in range(2):
        db = MagicMock(execute=AsyncMock(), get=AsyncMock(return_value=None))
        with patch("src.services.device_peer_admission.enqueue_platform_job", new=AsyncMock(return_value=(MagicMock(), False))) as enqueue:
            await enqueue_peer_session(db, MagicMock(), MagicMock(), dedupe_key="actor:device:request")
        identities.append(enqueue.call_args.kwargs["job_id"])
    assert identities[0] == identities[1]
