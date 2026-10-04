from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.execution import async_executor
from src.services.execution.async_executor import (
    _dispatch_request_identity,
    _pending_dispatch_envelope,
    _persist_execution_pin,
    _publish_pending,
    _validated_pending_dispatch,
    enqueue_workflow_execution_once,
)
from src.services.solutions.deployment_manifest import canonical_json, sha256_digest


class _FakeSpan:
    def __init__(self, name: str, attributes: dict):
        self.name = name
        self.attributes = dict(attributes)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def set_attribute(self, key: str, value):
        self.attributes[key] = value


class _FakeTracer:
    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def start_as_current_span(self, name: str, attributes: dict):
        span = _FakeSpan(name, attributes)
        self.spans.append(span)
        return span


@pytest.mark.asyncio
async def test_retry_reuses_execution_without_republishing():
    context = AsyncMock()
    context.solution_deployment_id = None
    context.event = None

    with (
        patch(
            "src.services.execution.async_executor._persist_execution_pin",
            new=AsyncMock(return_value=({
                "execution_id": "22222222-2222-2222-2222-222222222222",
            }, False)),
        ),
        patch(
            "src.services.execution.async_executor._publish_scheduled_once",
            new_callable=AsyncMock,
        ) as publish,
    ):
        execution_id, reused = await enqueue_workflow_execution_once(
            context=context,
            workflow_id="11111111-1111-1111-1111-111111111111",
            parameters={"ticket_id": 42},
            execution_id="22222222-2222-2222-2222-222222222222",
        )

    assert execution_id == "22222222-2222-2222-2222-222222222222"
    assert reused is True
    publish.assert_awaited_once()


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        solution_deployment_id=None,
        event=None,
        org_id="33333333-3333-3333-3333-333333333333",
        user_id="44444444-4444-4444-4444-444444444444",
        name="Operator",
        email="operator@example.test",
        startup=None,
        form_inputs={"field": "value"},
        embed={"ticket_id": "1001"},
        is_platform_admin=True,
        is_provider_org=False,
        is_external=False,
    )


def test_pending_dispatch_replays_durable_runtime_and_rejects_parameter_drift():
    context = _context()
    execution_id = "22222222-2222-2222-2222-222222222222"
    workflow_id = "11111111-1111-1111-1111-111111111111"
    request = _dispatch_request_identity(
        context,
        execution_id,
        workflow_id,
        {"ticket_id": 42},
        form_id=None,
        sync=False,
        api_key_id=None,
        file_path="features/demo.py",
        org_id_override=None,
    )
    old_runtime = {"workspace_release_id": "sha256:" + "a" * 64}
    envelope = _pending_dispatch_envelope(
        request,
        solution_deployment_id=None,
        runtime_evidence=old_runtime,
        runtime_mode="workspace-release-v1",
    )
    execution = SimpleNamespace(
        id=UUID(execution_id),
        workflow_id=UUID(workflow_id),
        parameters={"ticket_id": 42},
        organization_id=UUID(context.org_id),
        executed_by=UUID(context.user_id),
        executed_by_name=context.name,
        form_id=None,
        api_key_id=None,
        runtime_evidence=old_runtime,
        runtime_evidence_hash=sha256_digest(canonical_json(old_runtime)),
        runtime_mode="workspace-release-v1",
        solution_deployment_id=None,
        dispatch_evidence=envelope,
        dispatch_evidence_hash=sha256_digest(canonical_json(envelope)),
    )

    publish = _validated_pending_dispatch(execution, request)

    assert publish["runtime_evidence"] == old_runtime
    changed = {**request, "parameters": {"ticket_id": 99}}
    with pytest.raises(ValueError, match="different dispatch evidence"):
        _validated_pending_dispatch(execution, changed)


def test_pending_dispatch_records_explicit_org_override():
    context = _context()

    request = _dispatch_request_identity(
        context,
        "22222222-2222-2222-2222-222222222222",
        "11111111-1111-1111-1111-111111111111",
        {},
        form_id=None,
        sync=False,
        api_key_id=None,
        file_path=None,
        org_id_override="55555555-5555-5555-5555-555555555555",
    )

    assert request["org_id"] == "55555555-5555-5555-5555-555555555555"
    assert request["org_id_overridden"] is True


@pytest.mark.asyncio
async def test_existing_execution_is_rehydrated_before_current_runtime_is_pinned(
    monkeypatch,
):
    context = _context()
    execution_id = "22222222-2222-2222-2222-222222222222"
    workflow_id = "11111111-1111-1111-1111-111111111111"
    request = _dispatch_request_identity(
        context,
        execution_id,
        workflow_id,
        {"ticket_id": 42},
        form_id=None,
        sync=False,
        api_key_id=None,
        file_path=None,
        org_id_override=None,
    )
    runtime = {"workspace_release_id": "sha256:" + "a" * 64}
    execution = SimpleNamespace(
        id=UUID(execution_id),
        workflow_id=UUID(workflow_id),
        parameters={"ticket_id": 42},
        organization_id=UUID(context.org_id),
        executed_by=UUID(context.user_id),
        executed_by_name=context.name,
        form_id=None,
        api_key_id=None,
        runtime_evidence=runtime,
        runtime_evidence_hash=sha256_digest(canonical_json(runtime)),
        runtime_mode="workspace-release-v1",
        solution_deployment_id=None,
        dispatch_evidence=_pending_dispatch_envelope(
            request,
            solution_deployment_id=None,
            runtime_evidence=runtime,
            runtime_mode="workspace-release-v1",
        ),
        dispatch_evidence_hash=None,
    )
    execution.dispatch_evidence_hash = sha256_digest(
        canonical_json(execution.dispatch_evidence)
    )

    class Database:
        async def execute(self, _statement, _parameters=None):
            return None

        async def get(self, _model, identity):
            assert identity == UUID(execution_id)
            return execution

    @asynccontextmanager
    async def db_context():
        yield Database()

    deployment_pin = AsyncMock(
        side_effect=AssertionError("retry must not resolve current runtime")
    )
    workspace_pin = AsyncMock(
        side_effect=AssertionError("retry must not resolve current runtime")
    )
    monkeypatch.setattr("src.core.database.get_db_context", db_context)
    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.pin_workflow_runtime",
        deployment_pin,
    )
    monkeypatch.setattr(
        "src.services.workspace_release_runtime.pin_workspace_runtime",
        workspace_pin,
    )

    publish, created = await _persist_execution_pin(
        context,
        execution_id,
        workflow_id,
        {"ticket_id": 42},
        None,
        form_id=None,
        sync=False,
        api_key_id=None,
        file_path=None,
    )

    assert created is False
    assert publish["runtime_evidence"] == runtime
    deployment_pin.assert_not_awaited()
    workspace_pin.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters", [
    {"stale_parameter": 42},
    {"ticket_id": "wrong-type"},
    {},
    {"ticket_id": 42, "_event": {"body": {}}},
])
async def test_new_execution_validates_arguments_against_the_pinned_registration(monkeypatch, parameters):
    class Database:
        async def execute(self, *_):
            return None
        async def get(self, *_):
            return None
        def add(self, *_):
            raise AssertionError("Invalid arguments must not create an execution")
    @asynccontextmanager
    async def db_context():
        yield Database()
    monkeypatch.setattr("src.core.database.get_db_context", db_context)
    monkeypatch.setattr("src.services.solutions.deployment_runtime.pin_workflow_runtime", AsyncMock(return_value=SimpleNamespace(
        parameters_schema={"type": "object", "properties": {"ticket_id": {"type": "integer"}},
            "required": ["ticket_id"], "additionalProperties": False})))
    with pytest.raises(ValueError, match="pinned input contract"):
        await _persist_execution_pin(_context(), "22222222-2222-2222-2222-222222222222",
            "11111111-1111-1111-1111-111111111111", parameters, None,
            form_id=None, sync=False, api_key_id=None, file_path=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mapping,accepted", [
    ({"payload": "{{ payload }}", "apply": True}, True),
    ({"payload": "{{ payload }}", "apply": True, "unexpected": 1}, False),
    ({"payload": "{{ payload }}", "apply": True, "_other": 1}, False),
    ({"payload": "{{ payload }}", "apply": "true"}, False),
    ({"apply": True}, False),
])
async def test_event_dispatch_obeys_strict_pinned_schema_and_retains_metadata(
    monkeypatch, mapping, accepted,
):
    from src.models.enums import EventDeliveryStatus
    from src.services.events.processor import EventProcessor

    event = SimpleNamespace(
        id=uuid4(), event_type="ticket.updated", data={"ticket_id": 42},
        headers={"x-source": "halo"}, source_ip="192.0.2.1",
        received_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
        organization_id=None,
        event_source=SimpleNamespace(organization_id=None, schedule_source=None),
    )
    deployment_id = uuid4()
    workflow_id = uuid4()
    delivery = SimpleNamespace(
        id=uuid4(), event_id=event.id, workflow_id=workflow_id, execution_id=None,
        workflow=SimpleNamespace(id=workflow_id, type="workflow", organization_id=None),
        subscription=SimpleNamespace(input_mapping=mapping),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.get.side_effect = [delivery, None]

    @asynccontextmanager
    async def db_context():
        yield db

    runtime = SimpleNamespace(
        name="Strict webhook", deployment_id=deployment_id, runtime_mode="deployment-v1",
        parameters_schema={
            "type": "object", "additionalProperties": False,
            "properties": {"payload": {"type": "object"}, "apply": {"type": "boolean"}},
            "required": ["payload", "apply"],
        },
        queue_evidence=lambda: {"solution_deployment_id": str(deployment_id)},
    )
    monkeypatch.setattr("src.core.database.get_db_context", db_context)
    monkeypatch.setattr("src.services.solutions.deployment_runtime.pin_workflow_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr("src.services.execution.retry_policy.workflow_retry_policy_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr("src.services.execution.attempts.ensure_dispatch_attempt", AsyncMock())
    publish = AsyncMock()
    monkeypatch.setattr(async_executor, "_publish_scheduled_once", publish)

    processor = EventProcessor(AsyncMock())
    if not accepted:
        with pytest.raises(ValueError, match="pinned input contract"):
            await processor._queue_workflow_execution(delivery, event)
        db.add.assert_not_called()
        publish.assert_not_awaited()
        assert delivery.execution_id is None
        return

    await processor._queue_workflow_execution(delivery, event)

    execution = db.add.call_args.args[0]
    assert execution.solution_deployment_id == deployment_id
    assert execution.parameters["payload"] == event.data
    assert execution.parameters["apply"] is True
    metadata = execution.parameters["_event"]
    assert metadata["id"] == str(event.id)
    assert metadata["body"] == event.data
    assert metadata["headers"] == event.headers
    assert metadata["source_ip"] == event.source_ip
    assert execution.dispatch_evidence["request"]["parameters"]["_event"] == metadata
    assert execution.dispatch_evidence["publish"]["parameters"]["_event"] == metadata
    assert execution.dispatch_evidence["request"]["event"]["id"] == str(event.id)
    assert delivery.execution_id == execution.id
    assert delivery.status == EventDeliveryStatus.QUEUED
    db.commit.assert_awaited_once()
    publish.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("display_unavailable", [False, True])
async def test_publish_pending_writes_redis_then_publishes(display_unavailable):
    from redis.exceptions import TimeoutError as RedisTimeoutError

    redis = AsyncMock()
    display_redis = AsyncMock()
    display_redis.zadd.side_effect = RedisTimeoutError("Timeout connecting to server")
    tracking = AsyncMock(wraps=async_executor.add_to_queue) if display_unavailable else AsyncMock()
    with (
        patch("src.services.execution.queue_tracker._get_redis", new=AsyncMock(return_value=display_redis)),
        patch(
            "src.services.execution.async_executor.get_redis_client", return_value=redis
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue", new=tracking
        ) as q,
        patch(
            "src.services.execution.async_executor.publish_message", new=AsyncMock()
        ) as pub,
    ):
        await _publish_pending(
            execution_id="e1",
            workflow_id="wf",
            parameters={"x": 1},
            org_id="org",
            user_id="u",
            user_name="Name",
            user_email="n@e",
            form_id=None,
            startup=None,
            form_inputs={"field": "value"},
            embed={"ticket_id": "1001"},
            api_key_id=None,
            sync=False,
            is_platform_admin=False,
            is_provider_org=False,
            is_external=True,
            file_path=None,
        )

    redis.set_pending_execution.assert_awaited_once()
    assert redis.set_pending_execution.await_args.kwargs["is_provider_org"] is False
    assert redis.set_pending_execution.await_args.kwargs["is_external"] is True
    assert redis.set_pending_execution.await_args.kwargs["form_inputs"] == {
        "field": "value"
    }
    assert redis.set_pending_execution.await_args.kwargs["embed"] == {
        "ticket_id": "1001"
    }
    q.assert_awaited_once_with("e1")
    pub.assert_awaited_once()
    queue_name, message = pub.await_args.args
    assert queue_name == "workflow-executions"
    assert message == {
        "execution_id": "e1",
        "workflow_id": "wf",
        "sync": False,
        "execution_record_exists": False,
    }


@pytest.mark.asyncio
async def test_enqueue_code_execution_persists_sync_and_custom_queue():
    redis = AsyncMock()
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client",
            return_value=redis,
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue",
            new=AsyncMock(),
        ),
        patch(
            "src.services.execution.async_executor.publish_message",
            new=AsyncMock(),
        ) as publish,
    ):
        execution_id = await async_executor.enqueue_code_execution(
            _context(),
            "canary.py",
            "cHJpbnQoJ29rJyk=",
            {},
            execution_id="canary-execution",
            sync=True,
            queue_name="workflow-executions-deployment-canary",
        )

    assert execution_id == "canary-execution"
    assert redis.set_pending_execution.await_args.kwargs["sync"] is True
    publish.assert_awaited_once()
    assert publish.await_args.args[0] == "workflow-executions-deployment-canary"


@pytest.mark.asyncio
async def test_publish_pending_emits_enqueue_span(monkeypatch):
    fake_tracer = _FakeTracer()
    monkeypatch.setattr(async_executor, "tracer", fake_tracer)
    redis = AsyncMock()
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client", return_value=redis
        ),
        patch("src.services.execution.async_executor.add_to_queue", new=AsyncMock()),
        patch("src.services.execution.async_executor.publish_message", new=AsyncMock()),
    ):
        await _publish_pending(
            execution_id="e1",
            workflow_id="wf",
            parameters={},
            org_id="org",
            user_id="u",
            user_name="Name",
            user_email="n@e",
            form_id=None,
            startup=None,
            form_inputs={},
            embed={},
            api_key_id=None,
            sync=True,
            is_platform_admin=False,
            file_path="workflows/foo.py",
            event={"source": "topic"},
        )

    assert len(fake_tracer.spans) == 1
    span = fake_tracer.spans[0]
    assert span.name == "bifrost.workflow.enqueue"
    assert span.attributes["bifrost.execution.id"] == "e1"
    assert span.attributes["bifrost.workflow.id"] == "wf"
    assert span.attributes["bifrost.execution.organization_id"] == "org"
    assert span.attributes["bifrost.execution.sync"] is True
    assert span.attributes["bifrost.execution.has_file_path"] is True
    assert span.attributes["bifrost.execution.event.source"] == "topic"
    assert span.attributes["bifrost.execution.enqueue.status"] == "queued"


@pytest.mark.asyncio
async def test_publish_pending_marks_enqueue_span_failed(monkeypatch):
    fake_tracer = _FakeTracer()
    monkeypatch.setattr(async_executor, "tracer", fake_tracer)
    redis = AsyncMock()
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client", return_value=redis
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue",
            new=AsyncMock(side_effect=RuntimeError("full")),
        ),
        patch("src.services.execution.async_executor.publish_message", new=AsyncMock()),
    ):
        with pytest.raises(RuntimeError):
            await _publish_pending(
                execution_id="e1",
                workflow_id="wf",
                parameters={},
                org_id="org",
                user_id="u",
                user_name="Name",
                user_email="n@e",
                form_id=None,
                startup=None,
                form_inputs={},
                embed={},
                api_key_id=None,
                sync=False,
                is_platform_admin=False,
                file_path=None,
            )

    span = fake_tracer.spans[0]
    assert span.attributes["bifrost.execution.enqueue.status"] == "failed"
    assert span.attributes["bifrost.execution.error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_publish_pending_includes_file_path_when_present():
    redis = AsyncMock()
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client", return_value=redis
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue", new=AsyncMock()
        ) as q,
        patch(
            "src.services.execution.async_executor.publish_message", new=AsyncMock()
        ) as pub,
    ):
        await _publish_pending(
            execution_id="e1",
            workflow_id="wf",
            parameters={},
            org_id="org",
            user_id="u",
            user_name="n",
            user_email="",
            form_id=None,
            startup=None,
            form_inputs={},
            embed={},
            api_key_id=None,
            sync=True,
            is_platform_admin=False,
            file_path="workflows/foo.py",
        )
    _, message = pub.await_args.args
    q.assert_not_awaited()
    assert message["file_path"] == "workflows/foo.py"
    assert message["sync"] is True


@pytest.mark.asyncio
async def test_enqueue_system_workflow_execution_defaults_to_provider_org():
    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new=AsyncMock(return_value="exec-1"),
        ) as enqueue,
    ):
        from src.services.execution.async_executor import (
            enqueue_system_workflow_execution,
        )

        execution_id = await enqueue_system_workflow_execution(
            workflow_id="wf-1",
            parameters={"apply": True},
            source="Event System",
        )

    assert execution_id == "exec-1"
    enqueue.assert_awaited_once()
    assert (
        enqueue.await_args.kwargs["org_id_override"]
        == "00000000-0000-0000-0000-000000000002"
    )


@pytest.mark.asyncio
async def test_enqueue_system_workflow_execution_preserves_explicit_org():
    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new=AsyncMock(return_value="exec-2"),
        ) as enqueue,
    ):
        from src.services.execution.async_executor import (
            enqueue_system_workflow_execution,
        )

        await enqueue_system_workflow_execution(
            workflow_id="wf-1",
            parameters={},
            source="Event System",
            org_id="11111111-1111-1111-1111-111111111111",
        )

    assert (
        enqueue.await_args.kwargs["org_id_override"]
        == "11111111-1111-1111-1111-111111111111"
    )


@pytest.mark.asyncio
async def test_enqueue_code_execution_sync_skips_ui_queue_tracking():
    redis = AsyncMock()
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client",
            return_value=redis,
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue",
            new=AsyncMock(),
        ) as add,
        patch(
            "src.services.execution.async_executor.publish_message",
            new=AsyncMock(),
        ) as publish,
    ):
        execution_id = await async_executor.enqueue_code_execution(
            context=_context(),
            script_name="inline.py",
            code_base64="cHJpbnQoJ2hpJyk=",
            parameters={"x": 1},
            execution_id="exec-sync",
            sync=True,
        )

    assert execution_id == "exec-sync"
    redis.set_pending_execution.assert_awaited_once()
    assert redis.set_pending_execution.await_args.kwargs["sync"] is True
    assert redis.set_pending_execution.await_args.kwargs["script_name"] == "inline.py"
    add.assert_not_awaited()
    publish.assert_awaited_once_with(
        "workflow-executions",
        {
            "execution_id": "exec-sync",
            "code": "cHJpbnQoJ2hpJyk=",
            "script_name": "inline.py",
            "sync": True,
        },
    )


@pytest.mark.asyncio
async def test_enqueue_code_execution_async_tracks_ui_queue():
    redis = AsyncMock()
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client",
            return_value=redis,
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue",
            new=AsyncMock(),
        ) as add,
        patch(
            "src.services.execution.async_executor.publish_message",
            new=AsyncMock(),
        ) as publish,
    ):
        execution_id = await async_executor.enqueue_code_execution(
            context=_context(),
            script_name="inline.py",
            code_base64="cHJpbnQoJ2hpJyk=",
            parameters={"x": 1},
            execution_id="exec-async",
            sync=False,
        )

    assert execution_id == "exec-async"
    redis.set_pending_execution.assert_awaited_once()
    assert redis.set_pending_execution.await_args.kwargs["sync"] is False
    add.assert_awaited_once_with("exec-async")
    publish.assert_awaited_once()
