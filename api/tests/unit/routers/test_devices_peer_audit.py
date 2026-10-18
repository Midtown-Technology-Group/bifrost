"""Peer-session admin observation follows the existing requester boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from shared import access_checks
from src.core.principal import UserPrincipal
from src.routers.devices import get_peer_revoke_status_route, revoke_peer_session_route


@pytest.mark.parametrize("read_status", [False, True])
@pytest.mark.parametrize("admin,owner", [(False, True), (True, True), (True, False), (False, False)])
async def test_peer_admin_bypass_notes_exact_power_at_session_org(read_status, admin, owner):
    actor_id, device_id, session_id, receipt_id = uuid4(), uuid4(), uuid4(), uuid4()
    target_org = uuid4()
    user = UserPrincipal(
        user_id=actor_id, email="operator@example.com", organization_id=uuid4(), is_superuser=admin,
    )
    session = SimpleNamespace(
        id=session_id, job_type="device.peer_lighthouse.start", resource_id=str(device_id),
        requested_by_user_id=str(actor_id if owner else uuid4()), organization_id=target_org,
    )
    receipt = SimpleNamespace(
        id=receipt_id, job_type="device.peer_lighthouse.revoke", resource_type="device",
        resource_id=str(device_id), organization_id=target_org,
        payload={"device_id": str(device_id), "session_job_id": str(session_id)},
        status="queued", notification_id=None,
    )
    device = SimpleNamespace(id=device_id, organization_id=target_org)
    db = AsyncMock()
    db.get.side_effect = [session, receipt] if read_status else [session]
    accepted_public = MagicMock()
    accepted_public.model_copy.return_value = SimpleNamespace(can_cancel=False)
    token = access_checks.collect_person(actor_id)
    try:
        with patch("src.routers.devices.require_device_execute_permission", new=AsyncMock(return_value=user)), \
             patch("src.routers.devices.get_device_scoped", new=AsyncMock(return_value=device)), \
             patch("src.services.platform_jobs.enqueue_platform_job", new=AsyncMock(return_value=(receipt, False))) as enqueue, \
             patch("src.services.platform_jobs.request_platform_job_cancel", new=AsyncMock()) as cancel, \
             patch("src.services.platform_jobs.platform_job_to_public", return_value=accepted_public):
            if read_status:
                response = await get_peer_revoke_status_route(device_id, session_id, receipt_id, SimpleNamespace(db=db), user)
            else:
                response = await revoke_peer_session_route(device_id, session_id, SimpleNamespace(db=db), user)
            if not (admin or owner):
                assert response.status_code == 404
                enqueue.assert_not_awaited()
                cancel.assert_not_awaited()
            elif read_status:
                assert response.can_cancel is False
                enqueue.assert_not_awaited()
                cancel.assert_not_awaited()
            else:
                assert response.job_id == receipt_id
                cancel.assert_awaited_once_with(db, session)
            collector = access_checks.current()
            assert collector is not None
            notes = collector.notes
            if admin and not owner:
                assert len(notes) == 1
                assert notes[0].kind == "permission"
                assert notes[0].target == target_org
                assert notes[0].facts == {
                    "permission": "platformjobs.read.all" if read_status else "platformjobs.readwrite.all",
                    "subject": f"platform_job:{session_id}",
                }
            else:
                assert notes == []
    finally:
        access_checks.stop_collecting(token)
