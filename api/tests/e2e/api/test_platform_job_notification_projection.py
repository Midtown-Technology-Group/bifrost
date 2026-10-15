"""Real Redis version fences for delayed shared-job notification projections."""
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from src.models.contracts.notifications import NotificationCreate, NotificationCategory, NotificationStatus, NotificationUpdate
from src.services.notification_service import NotificationService, NOTIFICATION_KEY_PREFIX, USER_NOTIFICATIONS_PREFIX

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_delayed_terminal_write_cannot_replace_resumed_job_projection(monkeypatch):
    service = NotificationService()
    monkeypatch.setattr(service, "_publish_notification", AsyncMock())
    user_id, job_id = str(uuid4()), str(uuid4())
    notification = await service.create_notification(
        user_id=user_id,
        request=NotificationCreate(category=NotificationCategory.SYSTEM, title="Projection ordering fixture", metadata={"job_id": job_id}),
    )
    client = await service._get_redis()
    try:
        terminal = NotificationUpdate(status=NotificationStatus.COMPLETED, result={"job_id": job_id})
        assert await service.update_platform_job_notification(notification.id, terminal, job_id=job_id, revision=2)
        resumed = NotificationUpdate(status=NotificationStatus.RUNNING, result=None, error=None, percent=None)
        assert await service.update_platform_job_notification(notification.id, resumed, job_id=job_id, revision=3)
        # Simulate a previously timed-out write reaching Redis after the retry.
        assert await service.update_platform_job_notification(notification.id, terminal, job_id=job_id, revision=2) is None
        assert await service.update_platform_job_notification(notification.id, terminal, job_id=job_id, revision=3) is None
        current = await service.get_notification(notification.id)
        assert current is not None and current.status == NotificationStatus.RUNNING
        assert current.result is None and current.percent is None and current.error is None
        assert "_platform_job_revision" not in current.model_dump()
        assert await service.update_platform_job_notification(notification.id, terminal, job_id=str(uuid4()), revision=4) is None
        assert await service.update_platform_job_notification(notification.id, terminal, job_id=job_id, revision=4)
        current = await service.get_notification(notification.id)
        assert current is not None and current.status == NotificationStatus.COMPLETED
    finally:
        await client.delete(NOTIFICATION_KEY_PREFIX + notification.id, USER_NOTIFICATIONS_PREFIX + user_id)
        await client.aclose()
