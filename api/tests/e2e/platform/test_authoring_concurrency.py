"""Exercise whole heartbeat/log and completion transactions, including FK locks."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from src.core.cache import get_redis
from src.core.cache.keys import service_logs_cursor_key, service_logs_stream_key
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition, ServiceLog
from src.models.orm.workflows import Workflow
from src.services import service_lifecycle as lifecycle
from src.services import service_log_flush

from tests.helpers.postgres import disposable_postgres, wait_for_blocked


@pytest_asyncio.fixture
async def isolated_service_sessions():
    """Keep global claim selection inside a private, migrated database."""
    async with disposable_postgres(with_test_schema=True) as engine:
        yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_heartbeat_log_flush_and_completion_commit_without_fk_deadlock(
    isolated_service_sessions,
):
    async_session_factory = isolated_service_sessions
    org_id, workflow_id = uuid4(), uuid4()
    service_id = attempt_id = None
    try:
        async with async_session_factory() as db:
            db.add(Organization(id=org_id, name=f"author-{org_id}", created_by="test"))
            await db.flush()
            workflow = Workflow(
                id=workflow_id,
                name="author service",
                function_name="author_service",
                path=f"author/{workflow_id}.py",
                type="service",
                organization_id=org_id,
                is_active=True,
            )
            db.add(workflow)
            await db.flush()
            definition = await lifecycle.ensure_definition_for_workflow(
                db, workflow, created_by="test"
            )
            definition.restart_policy = "never"
            await db.flush()
            attempt = await lifecycle.claim_eligible_service(
                db, worker_id="author-fixture"
            )
            assert attempt is not None
            assert attempt.service_id == definition.id
            service_id, attempt_id, lease_token = (
                definition.id,
                attempt.id,
                attempt.lease_token,
            )
            await db.commit()
        stream_key = service_logs_stream_key(str(attempt_id))
        async with get_redis() as redis:
            await redis.xadd(
                stream_key,
                {
                    "level": "INFO",
                    "message": "persisted heartbeat sentinel",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

        async with (
            async_session_factory() as beat,
            async_session_factory() as completion,
            async_session_factory() as observer,
        ):
            await lifecycle.heartbeat_attempt(
                beat, attempt_id=attempt_id, lease_token=lease_token
            )
            beat_pid = await beat.scalar(text("SELECT pg_backend_pid()"))
            completion_pid = await completion.scalar(text("SELECT pg_backend_pid()"))

            async def complete():
                await lifecycle.complete_attempt(
                    completion,
                    attempt_id=attempt_id,
                    lease_token=lease_token,
                    reason="clean_return",
                )
                await completion.commit()

            async with asyncio.TaskGroup() as tasks:
                pending = tasks.create_task(complete())
                await wait_for_blocked(
                    observer, waiter=completion_pid, blocker=beat_pid
                )
                # The pending production completion may already hold other row
                # locks. A real ServiceLog INSERT takes both implicit FK locks.
                count, cursor = await service_log_flush.flush_attempt_logs(
                    beat, service_id, attempt_id
                )
                assert count == 1, "log persistence failed inside heartbeat transaction"
                assert cursor is not None
                await beat.commit()
            assert pending.done()
            assert await service_log_flush.store_log_cursor(attempt_id, cursor)

        async with async_session_factory() as check:
            terminal = await check.get(ServiceAttempt, attempt_id)
            assert terminal is not None
            assert terminal.state == "stopped"
            assert terminal.stopped_at is not None
            definition = await check.get(ServiceDefinition, service_id)
            assert definition is not None
            assert definition.blocked_reason == "policy"
            logs = (
                (
                    await check.execute(
                        select(ServiceLog.message).where(
                            ServiceLog.attempt_id == attempt_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert logs == ["persisted heartbeat sentinel"]
    finally:
        if attempt_id is not None:
            async with get_redis() as redis:
                await redis.delete(
                    service_logs_stream_key(str(attempt_id)),
                    service_logs_cursor_key(str(attempt_id)),
                )
        async with async_session_factory() as cleanup:
            if service_id is not None:
                await cleanup.execute(
                    delete(ServiceDefinition).where(ServiceDefinition.id == service_id)
                )
            await cleanup.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await cleanup.execute(delete(Organization).where(Organization.id == org_id))
            await cleanup.commit()
