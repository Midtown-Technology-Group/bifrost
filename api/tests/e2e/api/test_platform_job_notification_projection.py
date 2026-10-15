"""Real Redis version fences for delayed shared-job notification projections."""
import asyncio
import json
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
        repeated = await service.update_platform_job_notification(notification.id, terminal, job_id=job_id, revision=3)
        assert repeated is not None and repeated.status == NotificationStatus.RUNNING
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


async def test_same_revision_repairs_delivery_interrupted_after_persistence(monkeypatch):
    service = NotificationService()
    delivery = AsyncMock()
    monkeypatch.setattr(service, "_publish_notification", delivery)
    user_id, job_id = str(uuid4()), str(uuid4())
    notification = await service.create_notification(
        user_id=user_id,
        request=NotificationCreate(category=NotificationCategory.SYSTEM, title="Interrupted delivery fixture", metadata={"job_id": job_id}),
    )
    client = await service._get_redis()
    try:
        terminal = NotificationUpdate(status=NotificationStatus.COMPLETED, result={"job_id": job_id})
        # Model a child interrupted between the atomic Redis write and pubsub.
        delivery.reset_mock()
        delivery.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await service.update_platform_job_notification(notification.id, terminal, job_id=job_id, revision=2)
        stored = await client.get(NOTIFICATION_KEY_PREFIX + notification.id)
        current = await service.get_notification(notification.id)
        assert current is not None and current.status == NotificationStatus.COMPLETED
        delivery.reset_mock()
        delivery.side_effect = None
        repaired = await service.update_platform_job_notification(notification.id, terminal, job_id=job_id, revision=2)
        assert repaired is not None and repaired.status == NotificationStatus.COMPLETED
        delivery.assert_awaited_once()
        assert delivery.await_args.kwargs["event_type"] == "notification_updated"
        assert delivery.await_args.kwargs["notification"].status == NotificationStatus.COMPLETED
        assert await client.get(NOTIFICATION_KEY_PREFIX + notification.id) == stored
    finally:
        await client.delete(NOTIFICATION_KEY_PREFIX + notification.id, USER_NOTIFICATIONS_PREFIX + user_id)
        await client.aclose()


async def test_projection_preserves_json_values_and_concurrent_metadata(monkeypatch):
    service = NotificationService()
    monkeypatch.setattr(service, "_publish_notification", AsyncMock())
    user_id, job_id = str(uuid4()), str(uuid4())
    metadata = {"job_id": job_id, "empty": [], "large": 123456789012345678}
    notification = await service.create_notification(
        user_id=user_id,
        request=NotificationCreate(category=NotificationCategory.SYSTEM, title="JSON fidelity fixture", metadata=metadata),
    )
    client = await service._get_redis()
    key = NOTIFICATION_KEY_PREFIX + notification.id
    original_eval = client.eval
    calls = 0

    async def concurrent_eval(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Another writer changes metadata after the service reads its snapshot.
            current = json.loads(await client.get(key))
            current["metadata"]["concurrent"] = {"empty": [], "large": 987654321098765432}
            await client.set(key, json.dumps(current), keepttl=True)
        return await original_eval(*args, **kwargs)

    monkeypatch.setattr(client, "eval", concurrent_eval)
    result = {"empty": [], "nested": [{"empty": []}], "large": 123456789012345678}
    try:
        updated = await service.update_platform_job_notification(
            notification.id, NotificationUpdate(status=NotificationStatus.COMPLETED, result=result),
            job_id=job_id, revision=2,
        )
        assert calls == 2
        assert updated is not None and updated.result == result
        current = await service.get_notification(notification.id)
        assert current is not None and current.result == result
        assert current.metadata == {**metadata, "concurrent": {"empty": [], "large": 987654321098765432}}
        # Same-revision replay returns the persisted payload without rewriting it.
        stored = await client.get(key)
        replay = await service.update_platform_job_notification(
            notification.id, NotificationUpdate(result={"different": True}), job_id=job_id, revision=2,
        )
        assert replay is not None and replay.result == result
        assert await client.get(key) == stored
    finally:
        await client.delete(key, USER_NOTIFICATIONS_PREFIX + user_id)
        await client.aclose()
