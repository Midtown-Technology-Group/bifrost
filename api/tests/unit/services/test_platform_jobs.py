from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform.application_deploy import (
    APPLICATION_DEPLOY_DEFINITION,
    ApplicationDeployPayload,
)
from src.jobs.platform.application_publish import (
    APPLICATION_PUBLISH_DEFINITION,
    ApplicationPublishPayload,
)
from src.jobs.platform.base import (
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
    PlatformJobRequiresAction,
)
from src.jobs.platform import runner
from src.jobs.schedulers import platform_jobs as scheduler
from src.models.contracts.platform_jobs import PlatformJobStatus
from src.models.orm.platform_jobs import PlatformJob
from src.services import platform_jobs as service


async def _enqueue(db_session: AsyncSession) -> PlatformJob:
    app_id = uuid4()
    job, reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id),
        dedupe_key=str(app_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(app_id),
        title="Publishing Test",
        action_url="/apps/test/edit",
    )
    assert reused is False
    return job


@pytest.mark.asyncio
async def test_enqueue_routes_build_class_jobs_to_configured_backend(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(platform_build_backend="kubernetes"),
    )
    app_id = uuid4()
    job, reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_DEPLOY_DEFINITION,
        ApplicationDeployPayload(
            application_id=app_id,
            deployment_id=uuid4(),
            input_sha256="a" * 64,
        ),
        dedupe_key=str(app_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(app_id),
        title="Deploying App",
        action_url=None,
    )

    assert reused is False
    assert job.execution_backend == "kubernetes"


@pytest.mark.asyncio
async def test_enqueue_keeps_default_class_jobs_local_when_build_backend_is_remote(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(platform_build_backend="kubernetes"),
    )

    job = await _enqueue(db_session)

    assert job.execution_backend == "local"


@pytest.mark.asyncio
async def test_enqueue_honors_remote_job_type_allowlist(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            platform_build_backend="kubernetes",
            kubernetes_build_job_types="application.sdk_update",
        ),
    )
    app_id = uuid4()
    job, _ = await service.enqueue_platform_job(
        db_session,
        APPLICATION_DEPLOY_DEFINITION,
        ApplicationDeployPayload(
            application_id=app_id,
            deployment_id=uuid4(),
            input_sha256="a" * 64,
        ),
        dedupe_key=str(app_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(app_id),
        title="Deploying App",
        action_url=None,
    )

    assert job.execution_backend == "local"


def test_remote_job_type_allowlist_parsing() -> None:
    assert service.kubernetes_remote_job_types(
        SimpleNamespace(
            kubernetes_build_job_types=" application.deploy ,,application.sdk_update "
        )
    ) == frozenset({"application.deploy", "application.sdk_update"})
    # An empty allowlist cannot silently disable remote execution; it falls
    # back to the default set.
    assert service.kubernetes_remote_job_types(
        SimpleNamespace(kubernetes_build_job_types="  , ")
    ) == service.DEFAULT_KUBERNETES_JOB_TYPES
    assert service.kubernetes_remote_job_types(SimpleNamespace()) == (
        service.DEFAULT_KUBERNETES_JOB_TYPES
    )


@pytest.mark.asyncio
async def test_enqueue_reuses_only_active_dedupe_key(
    db_session: AsyncSession,
) -> None:
    app_id = uuid4()
    kwargs = {
        "dedupe_key": str(app_id),
        "organization_id": None,
        "requested_by_user_id": uuid4(),
        "requested_by_email": "dev@example.com",
        "requested_by_name": "Dev",
        "resource_type": "application",
        "resource_id": str(app_id),
        "title": "Publishing Test",
        "action_url": "/apps/test/edit",
    }
    first, first_reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id),
        **kwargs,
    )
    second, second_reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id),
        **kwargs,
    )
    assert first_reused is False
    assert second_reused is True
    assert second.id == first.id

    first.status = "failed"
    await db_session.commit()
    retry, retry_reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id, message="Retry"),
        **kwargs,
    )
    assert retry_reused is False
    assert retry.id != first.id


def test_requires_action_status_is_terminal_and_not_active() -> None:
    assert "requires_action" in service.TERMINAL_PLATFORM_JOB_STATUSES
    assert "requires_action" not in service.ACTIVE_PLATFORM_JOB_STATUSES
    assert PlatformJobStatus.REQUIRES_ACTION.value == "requires_action"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["requires_action", "failed", "cancelled"])
@pytest.mark.parametrize("same_requester", [True, False])
async def test_readback_checkpoint_blocks_fresh_publish_and_preserves_requester(
    db_session: AsyncSession, status: str, same_requester: bool,
) -> None:
    job = await _enqueue(db_session)
    job.status = status
    job.attempt = job.max_attempts
    proof = {"schema_version": "bifrost.application-publication-intent/v1", "artifact_hashes": {}}
    job.result = proof
    if status == "cancelled":
        job.cancel_requested_at = datetime.now(timezone.utc)
    await db_session.commit()
    original_id, original_payload = job.id, dict(job.payload)
    resumed, reused = await service.enqueue_platform_job(
        db_session, APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=uuid4(), message="new source must not replace intent"),
        dedupe_key=job.dedupe_key, organization_id=job.organization_id,
        requested_by_user_id=job.requested_by_user_id if same_requester else uuid4(),
        requested_by_email=job.requested_by_email, requested_by_name=job.requested_by_name,
        resource_type=job.resource_type, resource_id=job.resource_id,
        title="Readback", action_url=None,
    )
    assert reused and resumed.id == original_id
    assert resumed.payload == original_payload and resumed.result == proof
    assert resumed.status == ("queued" if same_requester else status)
    if same_requester:
        assert resumed.max_attempts == resumed.attempt + 1
        assert resumed.cancel_requested_at is None


@pytest.mark.asyncio
async def test_handler_requires_action_finishes_with_result_and_releases_lease(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    job.lease_owner = "test-runner"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    async def requires_confirmation(*_args):
        raise PlatformJobRequiresAction(
            phase="Confirm deletes",
            result={"requires_action": "confirm_deletes", "pending_deletes": []},
        )

    definition = PlatformJobDefinition(
        job_type=job.job_type,
        payload_version=job.payload_version,
        payload_model=APPLICATION_PUBLISH_DEFINITION.payload_model,
        handler=requires_confirmation,
        policy=PlatformJobPolicy(timeout_seconds=30),
        operations_policy=APPLICATION_PUBLISH_DEFINITION.operations_policy,
    )
    published = AsyncMock()
    monkeypatch.setattr(runner, "get_platform_job_definition", lambda _: definition)
    monkeypatch.setattr(service, "publish_platform_job_update", published)

    assert await runner.run_claimed_platform_job(job.id, token) is True

    await db_session.refresh(job)
    assert job.status == "requires_action"
    assert job.phase == "Confirm deletes"
    assert job.result == {"requires_action": "confirm_deletes", "pending_deletes": []}
    assert job.completed_at is not None
    assert job.lease_owner is None
    assert job.lease_token is None
    assert job.heartbeat_at is None
    assert job.lease_expires_at is None
    assert published.await_count == 1

    cancelled, accepted = await service.request_platform_job_cancel(db_session, job)
    assert cancelled.id == job.id
    assert accepted is False

    replacement, reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=uuid4()),
        dedupe_key=job.dedupe_key,
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(uuid4()),
        title="Replacement",
        action_url=None,
    )
    assert reused is False
    assert replacement.id != job.id


@pytest.mark.asyncio
async def test_handler_failure_persists_a_durable_result(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed git publication retains its server-owned retry-plan result."""
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    job.lease_owner = "test-runner"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    async def publication_failed(*_args):
        raise PlatformJobFailure(
            "git_operation_failed",
            "Push failed",
            retryable=True,
            result={"sync_result": {"retryable": True, "retry_plan": {"db_applied": True}}},
        )

    definition = PlatformJobDefinition(
        job_type=job.job_type,
        payload_version=job.payload_version,
        payload_model=APPLICATION_PUBLISH_DEFINITION.payload_model,
        handler=publication_failed,
        policy=PlatformJobPolicy(timeout_seconds=30),
        operations_policy=APPLICATION_PUBLISH_DEFINITION.operations_policy,
    )
    monkeypatch.setattr(runner, "get_platform_job_definition", lambda _: definition)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    assert await runner.run_claimed_platform_job(job.id, token) is True

    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_retryable is True
    assert job.result == {
        "sync_result": {"retryable": True, "retry_plan": {"db_applied": True}}
    }

@pytest.mark.asyncio
async def test_requires_action_phase_is_bounded_to_two_hundred_characters(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    await db_session.commit()

    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    assert await service.finish_platform_job(
        job.id,
        token,
        status="requires_action",
        phase="x" * 201,
        result={"requires_action": "confirm_deletes"},
    )

    await db_session.refresh(job)
    assert job.phase == "x" * 200


@pytest.mark.asyncio
async def test_requires_action_projects_result_to_public_job_and_notification(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    job.status = "requires_action"
    job.phase = "Confirm deletes"
    job.result = {"requires_action": "confirm_deletes"}
    job.notification_id = uuid4()
    notifications = MagicMock()
    notifications.update_notification = AsyncMock()
    monkeypatch.setattr(service, "get_notification_service", lambda: notifications)
    monkeypatch.setattr(service.pubsub_manager, "broadcast", AsyncMock())

    await service.publish_platform_job_update(job)

    public = service.platform_job_to_public(job)
    assert public.status is PlatformJobStatus.REQUIRES_ACTION
    assert public.result == {"requires_action": "confirm_deletes"}
    update = notifications.update_notification.await_args.args[1]
    assert update.status.value == "awaiting_action"
    assert update.result == {
        "job_id": str(job.id),
        "requires_action": "confirm_deletes",
    }


@pytest.mark.asyncio
async def test_notification_is_one_projection_of_durable_job(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    notification_id = uuid4()
    notifications = MagicMock()
    notifications.create_notification = AsyncMock(
        return_value=SimpleNamespace(id=str(notification_id))
    )
    monkeypatch.setattr(
        service,
        "get_notification_service",
        lambda: notifications,
    )

    first = await service.ensure_platform_job_notification(db_session, job)
    second = await service.ensure_platform_job_notification(db_session, job)

    assert first == second == notification_id
    notifications.create_notification.assert_awaited_once()
    request = notifications.create_notification.await_args.kwargs["request"]
    assert request.metadata == {
        "job_id": str(job.id),
        "job_type": "application.publish",
        "resource_type": "application",
        "resource_id": job.resource_id,
        "action_url": "/apps/test/edit",
    }


@pytest.mark.asyncio
async def test_websocket_event_matches_public_http_contract_and_hides_payload(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    await db_session.commit()
    broadcast = AsyncMock()
    monkeypatch.setattr(service.pubsub_manager, "broadcast", broadcast)

    await service.publish_platform_job_update(job)

    assert broadcast.await_count == 2
    channels = {call.args[0] for call in broadcast.await_args_list}
    assert channels == {
        f"notification:{job.requested_by_user_id}",
        "notification:admins",
    }
    for call in broadcast.await_args_list:
        event = call.args[1]
        assert event["type"] == "platform_job_updated"
        assert event["job"] == service.platform_job_to_public(job).model_dump(
            mode="json"
        )
        assert "payload" not in event["job"]
        assert "requested_by_email" not in event["job"]


@pytest.mark.asyncio
async def test_notification_projection_precedes_platform_job_broadcasts(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    job.notification_id = uuid4()
    await db_session.commit()

    call_order: list[str] = []
    notifications = MagicMock()
    notifications.update_notification = AsyncMock(
        side_effect=lambda *_args, **_kwargs: call_order.append("notification")
    )
    broadcast = AsyncMock(
        side_effect=lambda *_args, **_kwargs: call_order.append("platform_job")
    )
    monkeypatch.setattr(service, "get_notification_service", lambda: notifications)
    monkeypatch.setattr(service.pubsub_manager, "broadcast", broadcast)

    await service.publish_platform_job_update(job)

    assert call_order == ["notification", "platform_job", "platform_job"]


@pytest.mark.asyncio
async def test_notification_projection_preserves_canonical_job_id(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    job.notification_id = uuid4()
    job.status = "succeeded"
    job.result = {"job_id": "untrusted", "ok": True}
    await db_session.commit()

    notifications = MagicMock()
    notifications.update_notification = AsyncMock()
    monkeypatch.setattr(service, "get_notification_service", lambda: notifications)
    monkeypatch.setattr(service.pubsub_manager, "broadcast", AsyncMock())

    await service.publish_platform_job_update(job)

    update = notifications.update_notification.await_args.args[1]
    assert update.result == {"job_id": str(job.id), "ok": True}
@pytest.mark.asyncio
async def test_notification_projection_is_persisted_before_websocket_broadcasts(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    job.notification_id = uuid4()
    events: list[str] = []
    notifications = MagicMock()
    notifications.update_notification = AsyncMock(
        side_effect=lambda *_args, **_kwargs: events.append("notification")
    )
    monkeypatch.setattr(service, "get_notification_service", lambda: notifications)
    monkeypatch.setattr(
        service.pubsub_manager,
        "broadcast",
        AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("broadcast")),
    )

    await service.publish_platform_job_update(job)

    assert events == ["notification", "broadcast", "broadcast"]


@pytest.mark.asyncio
async def test_progress_and_terminal_writes_are_fenced(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    # The test stack also runs the real scheduler. Model a live lease so its
    # recovery loop cannot correctly reclaim this synthetic in-flight job.
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    published = AsyncMock()
    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", published)

    initial_revision = job.revision
    assert not await service.update_platform_job_progress(
        job.id,
        uuid4(),
        phase="stale",
        current=1,
        total=2,
        percent=50,
    )
    assert await service.update_platform_job_progress(
        job.id,
        token,
        phase="working",
        current=1,
        total=2,
        percent=50,
    )
    assert not await service.finish_platform_job(
        job.id,
        uuid4(),
        status="succeeded",
    )
    assert await service.finish_platform_job(
        job.id,
        token,
        status="succeeded",
        result={"ok": True},
    )

    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.phase == "Completed"
    assert job.progress_percent == 100
    assert job.result == {"ok": True}
    assert job.lease_token is None
    assert job.revision == initial_revision + 2
    assert published.await_count == 2


@pytest.mark.asyncio
async def test_finish_platform_job_bounds_error_code_for_job_and_attempt(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    assert await service.finish_platform_job(
        job.id,
        token,
        status="failed",
        error_code="x" * 200,
        error_message="failed",
    )

    await db_session.refresh(job)
    assert job.error_code == "x" * 100


@pytest.mark.asyncio
async def test_cancel_is_idempotent(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    monkeypatch.setattr(
        service,
        "publish_platform_job_update",
        AsyncMock(),
    )
    job, first = await service.request_platform_job_cancel(db_session, job)
    job, second = await service.request_platform_job_cancel(db_session, job)
    assert first is True
    assert second is False
    assert job.status == "cancelled"
    assert job.completed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_source", ["requires_action", "runner_loss"])
async def test_cancel_cannot_release_queued_publication_intent(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    recovery_source: str,
) -> None:
    job = await _enqueue(db_session)
    original_payload = dict(job.payload)
    proof = {"schema_version": APPLICATION_PUBLISH_DEFINITION.readback_checkpoint_schema,
             "artifact_hashes": {"manifest.json": "a" * 64}}
    job.result = proof
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())
    kwargs = dict(
        dedupe_key=job.dedupe_key, organization_id=job.organization_id,
        requested_by_user_id=job.requested_by_user_id,
        requested_by_email=job.requested_by_email, requested_by_name=job.requested_by_name,
        resource_type=job.resource_type, resource_id=job.resource_id,
        title="Readback", action_url=None,
    )
    if recovery_source == "requires_action":
        job.status = "requires_action"
        await db_session.commit()
        recovered, reused = await service.enqueue_platform_job(
            db_session, APPLICATION_PUBLISH_DEFINITION,
            ApplicationPublishPayload(application_id=uuid4()), **kwargs,
        )
        assert reused and recovered.id == job.id
        await db_session.commit()
    else:
        token = uuid4()
        job.status = "running"
        job.attempt = 1
        job.lease_token = token
        job.lease_owner = "lost-runner"
        await db_session.commit()

        @asynccontextmanager
        async def test_context() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        monkeypatch.setattr(scheduler, "get_db_context", test_context)
        monkeypatch.setattr(scheduler, "publish_platform_job_update", AsyncMock())
        assert await scheduler._handle_runner_loss(
            job.id, token, error_code="runner_lost", error_message="Runner stopped",
        )

    assert job.status == "queued"
    assert service.platform_job_to_public(job).can_cancel is False
    cancelled, accepted = await service.request_platform_job_cancel(db_session, job)
    assert not accepted and cancelled.status == "queued"
    assert cancelled.cancel_requested_at is None
    next_publish, reused = await service.enqueue_platform_job(
        db_session, APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=uuid4(), message="must not rebuild"), **kwargs,
    )
    assert reused and next_publish.id == job.id
    assert next_publish.payload == original_payload and next_publish.result == proof


@pytest.mark.asyncio
async def test_cancel_reads_current_checkpoint_instead_of_stale_http_row(
    db_session: AsyncSession,
) -> None:
    from sqlalchemy import update

    job = await _enqueue(db_session)
    await db_session.commit()
    proof = {"schema_version": APPLICATION_PUBLISH_DEFINITION.readback_checkpoint_schema}
    await db_session.execute(
        update(PlatformJob).where(PlatformJob.id == job.id).values(result=proof)
        .execution_options(synchronize_session=False)
    )
    await db_session.commit()
    assert job.result is None
    refreshed, accepted = await service.request_platform_job_cancel(db_session, job)
    assert not accepted and refreshed.status == "queued" and refreshed.result == proof


@pytest.mark.asyncio
async def test_non_interruptible_handler_rejects_running_cancel(
    db_session: AsyncSession,
) -> None:
    job = await _enqueue(db_session)
    job.status = "running"
    job.lease_token = uuid4()
    job.lease_owner = "scheduler-a"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()
    job, accepted = await service.request_platform_job_cancel(db_session, job)
    assert accepted is False
    assert job.status == "running"
    assert job.cancel_requested_at is None


@pytest.mark.asyncio
async def test_deferred_job_releases_lease_and_finishes_from_child_work(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    job.lease_owner = "scheduler-a"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    published = AsyncMock()
    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", published)

    assert await service.defer_platform_job(
        job.id,
        token,
        phase="Waiting for child work",
        result={"children": 2},
    )
    await db_session.refresh(job)
    assert job.status == "waiting"
    assert job.lease_token is None
    assert job.lease_owner is None
    assert service.platform_job_to_public(job).can_cancel is True

    assert await service.update_deferred_platform_job_progress(
        job.id,
        phase="Completed 1/2",
        current=1,
        total=2,
    )
    assert await service.finish_deferred_platform_job(
        job.id,
        status="succeeded",
        result={"children": 2, "succeeded": 2},
    )
    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.progress_percent == 100
    assert job.result == {"children": 2, "succeeded": 2}
    assert job.completed_at is not None
    assert published.await_count == 3


@pytest.mark.asyncio
async def test_stale_runner_cannot_defer_job(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    job.status = "running"
    job.lease_token = uuid4()
    job.lease_owner = "scheduler-a"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    assert not await service.defer_platform_job(
        job.id,
        uuid4(),
        phase="Stale child work",
    )
    await db_session.refresh(job)
    assert job.status == "running"


@pytest.mark.asyncio
async def test_pre_manifest_disposition_fences_old_lease_and_admits_new_publication(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID
    from src.jobs.platform.application_publish import _publication_not_applied
    from src.jobs.platform.base import PlatformJobCancelled, PlatformJobContext

    job = await _enqueue(db_session)
    first_lease, reclaimed_lease = uuid4(), uuid4()
    app_id = UUID(job.resource_id)
    job.status, job.lease_token = "running", first_lease
    await db_session.commit()

    @asynccontextmanager
    async def test_context():
        yield db_session

    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())
    first = PlatformJobContext(job.id, first_lease, job.organization_id,
        job.requested_by_user_id, job.requested_by_email, job.requested_by_name)
    intent = {"schema_version": APPLICATION_PUBLISH_DEFINITION.readback_checkpoint_schema,
        "application_id": str(app_id), "manifest_write_started": False,
        "expected_live_etag": "old-revision", "artifact_hashes": {"manifest.json": "sha256:" + "a" * 64}}
    await first.save_checkpoint(intent, phase="Intent before output writes")
    job.lease_token = reclaimed_lease
    await db_session.commit()
    # The old attempt cannot advance the durable boundary after its lease is
    # reclaimed. The storage regression separately proves no PUT follows this.
    with pytest.raises(PlatformJobCancelled):
        await first.save_checkpoint({**intent, "manifest_write_started": True}, phase="Before pointer write")
    assert job.result == intent
    reclaimed = PlatformJobContext(job.id, reclaimed_lease, job.organization_id,
        job.requested_by_user_id, job.requested_by_email, job.requested_by_name, intent)
    failure = _publication_not_applied(reclaimed, intent)
    assert await service.finish_platform_job(job.id, reclaimed_lease, status="failed",
        result=failure.result, error_code=failure.code, error_message=failure.message)
    new, reused = await service.enqueue_platform_job(db_session, APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id, message="New reviewed source"),
        dedupe_key=job.dedupe_key, organization_id=job.organization_id,
        requested_by_user_id=uuid4(), requested_by_email="next@example.com", requested_by_name="Next",
        resource_type=job.resource_type, resource_id=job.resource_id, title="New publication", action_url=None)
    assert not reused and new.id != job.id
    assert job.result["original_intent"] == intent
    assert job.result["publication_verified"] is False
