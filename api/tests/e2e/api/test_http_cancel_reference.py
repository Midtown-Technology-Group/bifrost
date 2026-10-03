"""Actual HTTP-local cancellation, separate from helper and workflow-route parity.

H cases call the booted API; P cases run the complete application through ASGI
without lifespan. The latter measure inner ASGI/request-TX ordering, not socket
ACKs, booted-process fan-out or native Rust ownership. Raw failure XML/logs must
remain private; the emitted properties contain only fixed labels and counts.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import inspect
import os
import platform
import socket
import stat
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import redis.asyncio as redis
from fastapi.routing import APIRoute, iter_route_contexts
from redis.asyncio.client import PubSub
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, SessionTransaction
from starlette.types import Message, Receive, Scope, Send

from src.config import get_settings
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.routers import executions as execution_router
from tests.e2e.fixtures.users import E2EUser

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_CASE_SECONDS = 90.0
_WORK_SECONDS = 75.0
_MAX_LABELS = 64
_CANCEL_CHANNEL = "bifrost:cancel"
_ROUTE_PATH = "/api/executions/{execution_id}/cancel"
_OBSERVATION: ContextVar[_Observation | None] = ContextVar(
    "http_cancel_reference_observation", default=None
)

_MISSING = object()
_RETAINED_CUSTODY = False
_CONTROLS_ATTEMPTED = False
_CONTROLS_PASSED = False
_STOP_REASON = "http_cancel_reference_retained_custody"
_TASK_CONSTRUCTOR: Callable[[Coroutine[Any, Any, Any]], asyncio.Task[Any]] = (
    asyncio.create_task
)


def _require(condition: bool, label: str) -> None:
    # Avoid pytest assertion rewriting exporting private row/header values.
    if not condition:
        raise AssertionError(label)


_METADATA_MEMBERS = {
    "fastapi/routing.py": "4fdf951bbf9ca943a5fc26f63ed83e8c424ff5d1debe10e8d1b73732bc8f2c7b",
    "fastapi/dependencies/utils.py": "5c8a5130beedb71e44866721816cfa47908cb01b9ea71eca1dd9a2dfc17f7bfb",
    "fastapi/params.py": "d5f34d48ae49ecf339170f85e7ff6e3b2b00d8d802fbad666f72e5ac15a9a893",
    "fastapi/middleware/asyncexitstack.py": "44a1a54291b383718ba2ca9586bc41cbf34267da894bbcd078d1ede58df1fb4d",
    "fastapi/dependencies/models.py": "5cf22411aba41da4da173f4ba1e23d7ff4fede8b4e015928382d75dd3a456d62",
    "starlette/routing.py": "b95e6a47be6cf10a89bcd1a0ace73ba8de8c6ef5b7340fde4b2de2453b2f5844",
    "starlette/responses.py": "5d52ab008ef7d9ce4c514f13b8ec62e15a1ea78f29a116f0e8cbee6f4eea9112",
    "starlette/_exception_handler.py": "f159bdd797d661ef712e8ff36b9890e56cab02f141bb690410973b5eb793c8e9",
    "starlette/middleware/exceptions.py": "ece812522060c074b854c9a9696970db5b8328d0e638cccb559ad60cb0d4a96a",
    "starlette/middleware/base.py": "abdd13dec1d08e1af9912209c8ce07357262f405acb6548d9a8cea4f6fca51cd",
    "_pytest/main.py": "7ca2b20cc41fbfc2761bcb231eecb26967d553651035c40000f44d70e174b693",
    "_pytest/runner.py": "8d784b2b3e23b05f4c4edd3f5263eb11964e86b6e16d2c814c5fcb2125dc4ea4",
}


def _metadata_file(path: Path) -> str:
    fd: int | None = None
    original: BaseException | None = None
    observed: str | None = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode), "metadata_not_regular")
        _require(0 < before.st_size <= 1048576, "metadata_file_bound")
        data = bytearray()
        while len(data) <= before.st_size:
            block = os.read(fd, min(65536, before.st_size + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
        _require(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            and len(data) == before.st_size,
            "metadata_identity_changed",
        )
        observed = hashlib.sha256(data).hexdigest()
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    if observed is None:
        raise AssertionError("metadata_unobserved")
    return observed


def _installed_metadata() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for member, expected in _METADATA_MEMBERS.items():
        package = "pytest" if member.startswith("_pytest/") else member.split("/")[0]
        distribution = importlib.metadata.distribution(package)
        path = Path(distribution.locate_file(member))
        base = Path(distribution.locate_file(""))
        observed = _metadata_file(path)
        result[member] = {
            "sha256": observed,
            "distribution_matches": observed == expected,
            "path_matches": path.resolve() == base.resolve() / member,
        }
    return result


def _runner_metadata() -> str:
    test_path = Path(__file__)
    value = {
        "schema": "http-cancel-reference-runner/v1",
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
        "pytest": importlib.metadata.version("pytest"),
        "fastapi": importlib.metadata.version("fastapi"),
        "starlette": importlib.metadata.version("starlette"),
        "test_source": {
            "sha256": _metadata_file(test_path),
            "path_matches": test_path.resolve()
            == Path("/app/tests/e2e/api/test_http_cancel_reference.py"),
        },
        "installed": _installed_metadata(),
        "controls": {"completed": _CONTROLS_PASSED, "families": 15, "branches": 19},
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    _require(len(encoded.encode()) <= 16384, "metadata_output_bound")
    return encoded


def _application_metadata(app: object, case_id: str) -> str:
    names = (
        "src.main",
        "src.routers.executions",
        "fastapi.routing",
        "fastapi.dependencies.utils",
        "fastapi.dependencies.models",
        "starlette.responses",
    )
    modules: dict[str, dict[str, object]] = {}
    for name in names:
        module = sys.modules.get(name)
        if module is None:
            raise AssertionError("application_metadata_not_loaded")
        path = Path(getattr(module, "__file__", ""))
        if name.startswith("src."):
            expected_path = Path("/app") / (name.replace(".", "/") + ".py")
        else:
            member = name.replace(".", "/") + ".py"
            expected_path = Path(
                importlib.metadata.distribution(name.split(".")[0]).locate_file(member)
            )
        modules[name] = {
            "sha256": _metadata_file(path),
            "path_matches": path.resolve() == expected_path.resolve(),
            "loaded": True,
        }
    contexts = [
        item
        for item in iter_route_contexts(getattr(app, "routes"))
        if item.path == _ROUTE_PATH and item.methods == {"POST"}
    ]
    _require(len(contexts) == 1, "application_metadata_route_count")
    route = contexts[0].original_route
    if not isinstance(route, APIRoute):
        raise AssertionError("application_metadata_route_type")
    value = {
        "schema": "http-cancel-reference-application/v1",
        "case": case_id,
        "modules": modules,
        "shared_route": {
            "endpoint_module_matches": route.endpoint.__module__
            == "src.routers.executions",
            "handle_module_matches": route.handle.__module__ == "fastapi.routing",
            "source_sha256": modules["src.routers.executions"]["sha256"],
        },
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    _require(len(encoded.encode()) <= 16384, "application_metadata_output_bound")
    return encoded


def _matches_observation(
    app: object, scope_app: object, owned_id: str, path_id: object
) -> bool:
    return scope_app is app and path_id == owned_id


def _restore_attribute(
    owner: Any, name: str, observer: object, present: bool, previous: object
) -> None:
    # Never replace another owner's value, including an inherited replacement.
    _require(
        vars(owner).get(name, _MISSING) is observer, "owned_attribute_interference"
    )
    if present:
        setattr(owner, name, previous)
    else:
        delattr(owner, name)


def _cleanup_eligibility(
    completed: list[bool], failed: bool, stop_target: Any
) -> tuple[bool, bool]:
    allowed = all(completed)
    if not allowed and not stop_target.shouldstop:
        stop_target.shouldstop = _STOP_REASON
    return allowed, failed or not allowed


def _submit_task(
    operation: Coroutine[Any, Any, Any],
    owned: list[asyncio.Task[Any]],
    note_error: Callable[[BaseException], None],
) -> asyncio.Task[Any]:
    try:
        task = _TASK_CONSTRUCTOR(operation)
    except BaseException:
        try:
            operation.close()
        except BaseException as secondary:
            note_error(secondary)
        # The original construction error/control object has priority.
        raise
    owned.append(task)
    return task


class _CloseControl(Coroutine[Any, Any, None]):
    """Control-only close branch, always closes its real native coroutine first."""

    def __init__(
        self, operation: Coroutine[Any, Any, None], secondary: BaseException
    ) -> None:
        self.operation = operation
        self.secondary = secondary

    def __await__(self) -> Any:
        return self.operation.__await__()

    def send(self, value: Any) -> Any:
        return self.operation.send(value)

    def throw(self, error: Any, value: Any = None, traceback: Any = None) -> Any:
        return self.operation.throw(error, value, traceback)

    def close(self) -> None:
        self.operation.close()
        raise self.secondary


async def _unsubmitted_control() -> None:
    # Native coroutine creation/closure only: this body must never execute.
    raise AssertionError("source_control_coroutine_body_executed")


def _constructor_control(original: BaseException, secondary_close: bool) -> None:
    global _TASK_CONSTRUCTOR
    owned: list[asyncio.Task[Any]] = []
    noted: list[BaseException] = []
    observed: BaseException | None = None
    pending: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    saved = _TASK_CONSTRUCTOR
    injected = False

    def fail_constructor(_operation: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        raise original

    native = _unsubmitted_control()
    try:
        secondary = RuntimeError("synthetic_secondary_close")
        operation = _CloseControl(native, secondary) if secondary_close else native
        # Helper-local seam only. No await/product call/submission while installed.
        injected = True
        _TASK_CONSTRUCTOR = fail_constructor
        try:
            _submit_task(operation, owned, noted.append)
        except BaseException as error:
            observed = error
        _require(observed is original, "constructor_control_original_identity")
        _require(not owned, "constructor_control_submitted_task")
        _require(
            inspect.getcoroutinestate(native) == inspect.CORO_CLOSED,
            "constructor_control_native_not_closed",
        )
        _require(
            len(noted) == int(secondary_close)
            and (not secondary_close or noted[0] is secondary),
            "constructor_control_secondary_priority",
        )
    except BaseException as error:
        pending = error
        raise
    finally:
        # Independent restoration and closure; a pending original still wins.
        if injected:
            try:
                _restore_attribute(
                    sys.modules[__name__],
                    "_TASK_CONSTRUCTOR",
                    fail_constructor,
                    True,
                    saved,
                )
            except BaseException as error:
                cleanup_errors.append(error)
        try:
            native.close()
        except BaseException as error:
            cleanup_errors.append(error)
    if pending is None:
        _require(not cleanup_errors, "constructor_control_cleanup_failed")


def _source_controls() -> None:
    """15 inert families/19 branches; not real task settlement or auth proof."""
    app, foreign_app = object(), object()
    owned_id = "owned-synthetic-id"
    for scope_app, path_id, expected in (
        (app, owned_id, True),
        (foreign_app, owned_id, False),
        (app, "foreign-synthetic-id", False),
        (app, None, False),
    ):
        _require(
            _matches_observation(app, scope_app, owned_id, path_id) is expected,
            "observation_predicate_control",
        )

    class Holder:
        def handle(self) -> None:
            return None

    def observer() -> None:
        return None

    def previous() -> None:
        return None

    def foreign() -> None:
        return None

    inherited = Holder()
    inherited.handle = observer
    _restore_attribute(inherited, "handle", observer, False, _MISSING)
    _require("handle" not in vars(inherited), "inherited_restore_control")
    _require(
        inherited.handle.__self__ is inherited
        and inherited.handle.__func__ is Holder.handle,
        "inherited_descriptor_restore_control",
    )
    present = Holder()
    present.handle = observer
    _restore_attribute(present, "handle", observer, True, previous)
    _require(present.handle is previous, "present_restore_control")
    replaced = Holder()
    replaced.handle = foreign
    denied = False
    try:
        _restore_attribute(replaced, "handle", observer, False, _MISSING)
    except AssertionError:
        denied = True
    _require(denied and replaced.handle is foreign, "foreign_restore_control")

    for completed, failed, previous_stop, allowed, expected_failed, expected_stop in (
        ([True], False, False, True, False, False),
        ([True], True, False, True, True, False),
        ([False], False, False, False, True, _STOP_REASON),
        ([False], False, "existing-static-stop", False, True, "existing-static-stop"),
    ):
        local_stop = SimpleNamespace(shouldstop=previous_stop)
        result = _cleanup_eligibility(completed, failed, local_stop)
        _require(result == (allowed, expected_failed), "cleanup_eligibility_control")
        _require(local_stop.shouldstop == expected_stop, "cleanup_stop_control")

    for original in (
        RuntimeError("synthetic_constructor"),
        asyncio.CancelledError("synthetic_constructor"),
        KeyboardInterrupt("synthetic_constructor"),
        SystemExit(17),
    ):
        _constructor_control(original, secondary_close=False)
        _constructor_control(original, secondary_close=True)


class _Case:
    """Owned handles and the one work/cleanup deadline for a single case."""

    def __init__(
        self,
        engine: AsyncEngine,
        case_id: str,
        record_property: Callable[[str, object], None],
        started: float,
        pytest_session: pytest.Session,
    ) -> None:
        self.work_end = started + _WORK_SECONDS
        self.case_end = started + _CASE_SECONDS
        self.case_id = case_id
        self.execution_id = uuid4()
        self.attempt_id = uuid4()
        self.factory = async_sessionmaker(
            engine, expire_on_commit=False, autoflush=False
        )
        self.record_property = record_property
        self.pytest_session = pytest_session
        self.seed_started = False
        self.retained_custody = False
        self.sessions: list[AsyncSession] = []
        self.clients: list[httpx.AsyncClient] = []
        self.tasks: list[asyncio.Task[Any]] = []
        self.barriers: list[asyncio.Event] = []
        self.restorers: list[Callable[[], None]] = []
        self.labels: list[str] = []
        self.redis: redis.Redis | None = None
        self.subscriber: PubSub | None = None
        self.cleanup_failed = False
        self.cleanup_control: BaseException | None = None

    def label(self, label: str) -> None:
        _require(len(self.labels) < _MAX_LABELS, "phase_label_overflow")
        self.labels.append(label)

    def remaining_work(self) -> float:
        remaining = self.work_end - asyncio.get_running_loop().time()
        _require(remaining > 0, "work_budget_exhausted")
        return remaining

    def session(self) -> AsyncSession:
        session = self.factory()
        self.sessions.append(session)
        return session

    def client(
        self, base_url: str, app: Callable[..., Any] | None = None
    ) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app) if app is not None else None
        client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=min(60.0, self.remaining_work()),
            follow_redirects=False,
            trust_env=False,
        )
        self.clients.append(client)
        return client

    def redis_client(self) -> redis.Redis:
        if self.redis is None:
            self.redis = redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
            )
        return self.redis

    def task(self, operation: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        return _submit_task(operation, self.tasks, self.cleanup_error)

    def cleanup_error(self, error: BaseException) -> None:
        self.cleanup_failed = True
        if not isinstance(error, Exception) and self.cleanup_control is None:
            self.cleanup_control = error

    def cleanup_sync(self, operation: Callable[[], Any]) -> None:
        try:
            operation()
        except BaseException as error:
            self.cleanup_error(error)

    async def attempt_cleanup(self, operation: Callable[[], Awaitable[Any]]) -> None:
        try:
            async with asyncio.timeout_at(self.case_end):
                await operation()
        except BaseException as error:
            self.cleanup_error(error)

    async def cleanup(self) -> None:
        global _RETAINED_CUSTODY
        # All releases/cancellations happen before any await can consume reserve.
        for barrier in self.barriers:
            self.cleanup_sync(barrier.set)
        for task in self.tasks:
            if not task.done():
                self.cleanup_sync(task.cancel)
        for task in self.tasks:
            await self.attempt_cleanup(lambda task=task: _settle(task, self.case_end))
        completed = [task.done() for task in self.tasks]
        if not all(completed):
            self.retained_custody = True
            _RETAINED_CUSTODY = True
        # A failed completed Task differs from a Task still using dependencies.
        allowed = False
        try:
            allowed, self.cleanup_failed = _cleanup_eligibility(
                completed, self.cleanup_failed, self.pytest_session
            )
        except BaseException as error:
            self.cleanup_error(error)
        if allowed:
            for restore in reversed(self.restorers):
                self.cleanup_sync(restore)
            for session in self.sessions:
                await self.attempt_cleanup(session.rollback)
                await self.attempt_cleanup(session.close)
            if self.seed_started:
                await self.attempt_cleanup(self.delete_cohort)
            if self.redis is not None:
                await self.attempt_cleanup(
                    lambda: self.redis.delete(_flag_key(self.execution_id))
                )
            if self.subscriber is not None:
                await self.attempt_cleanup(
                    lambda: self.subscriber.unsubscribe(_CANCEL_CHANNEL)
                )
                await self.attempt_cleanup(self.subscriber.aclose)
            if self.redis is not None:
                await self.attempt_cleanup(self.redis.aclose)
            for client in self.clients:
                await self.attempt_cleanup(client.aclose)
        # Retained dependencies remain untouched. Current pytest fixture teardown
        # still runs; only the normal loop's NEXT setup is stopped by shouldstop.
        try:
            self.record_property("http_cancel_reference_case", self.case_id)
            self.record_property("http_cancel_reference_phases", ",".join(self.labels))
            status = (
                "retained"
                if self.retained_custody
                else "failed"
                if self.cleanup_failed
                else "ok"
            )
            self.record_property("http_cancel_reference_cleanup", status)
        except BaseException as error:
            self.cleanup_error(error)

    async def delete_cohort(self) -> None:
        # Never acquire deletion resources after pre-seed pure control failure.
        _require(self.seed_started, "cohort_cleanup_without_seed_acquisition")
        session = self.factory()
        pending = False
        try:
            await session.execute(
                delete(WorkflowExecutionAttempt).where(
                    WorkflowExecutionAttempt.execution_id == self.execution_id
                )
            )
            await session.execute(
                delete(Execution).where(Execution.id == self.execution_id)
            )
            await session.commit()
        except BaseException:
            pending = True
            raise
        finally:
            # Independent close attempts and original error/control precedence.
            await self.attempt_cleanup(session.rollback)
            await self.attempt_cleanup(session.close)
        if not pending:
            self.label("owned_rows_removed")


async def _settle(task: asyncio.Task[Any], deadline: float) -> None:
    # wait() does not turn a timeout into an unbounded await of a child that
    # suppresses cancellation. Missing settlement is red, never cleanup success.
    remaining = max(0.0, deadline - asyncio.get_running_loop().time())
    done, _ = await asyncio.wait({task}, timeout=remaining)
    _require(task in done, "owned_task_settlement_incomplete")
    try:
        await task
    except asyncio.CancelledError:
        if not task.cancelled():
            raise
    # Other task errors remain red (the original work error still wins).


@asynccontextmanager
async def _case_scope(
    engine: AsyncEngine,
    case_id: str,
    record_property: Callable[[str, object], None],
    request: pytest.FixtureRequest,
    record_testsuite_property: Callable[[str, object], None],
) -> AsyncIterator[_Case]:
    global _CONTROLS_ATTEMPTED, _CONTROLS_PASSED
    _require(
        not _RETAINED_CUSTODY and not request.session.shouldstop,
        "prior_case_retained_or_session_stopped",
    )
    _require(
        not _CONTROLS_ATTEMPTED or _CONTROLS_PASSED,
        "prior_source_controls_failed",
    )
    started = asyncio.get_running_loop().time()
    work_end = started + _WORK_SECONDS
    case: _Case | None = None
    original = False
    try:
        async with asyncio.timeout_at(work_end):
            if not _CONTROLS_ATTEMPTED:
                _CONTROLS_ATTEMPTED = True
                _source_controls()
                _CONTROLS_PASSED = True
                record_testsuite_property(
                    "http_cancel_reference_runner_identity", _runner_metadata()
                )
            _require(
                asyncio.get_running_loop().time() < work_end,
                "source_controls_exhausted_work_budget",
            )
            # No owned acquisition precedes completed pure controls.
            case = _Case(engine, case_id, record_property, started, request.session)
            yield case
    except BaseException:
        original = True
        raise
    finally:
        if case is not None:
            try:
                await case.cleanup()
            except BaseException as error:
                case.cleanup_error(error)
    if not original and case is not None:
        if case.cleanup_control is not None:
            raise case.cleanup_control
        _require(not case.cleanup_failed, "owned_cleanup_failed")
        _require(
            asyncio.get_running_loop().time() < case.case_end,
            "whole_case_budget_exhausted",
        )


def _flag_key(execution_id: UUID) -> str:
    return f"bifrost:exec:{execution_id}:cancel"


async def _snapshot(case: _Case) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session = case.session()
    execution_rows = (
        (
            await session.execute(
                select(Execution.__table__).where(Execution.id == case.execution_id)
            )
        )
        .mappings()
        .all()
    )
    _require(len(execution_rows) == 1, "owned_execution_cardinality")
    attempt_rows = (
        (
            await session.execute(
                select(WorkflowExecutionAttempt.__table__)
                .where(WorkflowExecutionAttempt.execution_id == case.execution_id)
                .order_by(WorkflowExecutionAttempt.attempt_number)
            )
        )
        .mappings()
        .all()
    )
    await session.rollback()
    return dict(execution_rows[0]), [dict(row) for row in attempt_rows]


async def _seed(
    case: _Case, owner: E2EUser, prior_status: ExecutionStatus, queued_attempt: bool
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _require(owner.user_id is not None, "owner_fixture_identity_missing")
    _require(owner.organization_id is not None, "owner_fixture_organization_missing")
    _require(not owner.is_superuser, "owner_fixture_not_ordinary")
    now = datetime.now(timezone.utc)
    completed = (
        now
        if prior_status in {ExecutionStatus.CANCELLED, ExecutionStatus.SUCCESS}
        else None
    )
    case.seed_started = True
    session = case.session()
    session.add(
        Execution(
            id=case.execution_id,
            workflow_name="http-cancel-reference",
            workflow_version="reference-v1",
            executed_by=owner.user_id,
            executed_by_name="Synthetic cancellation owner",
            organization_id=owner.organization_id,
            status=prior_status,
            parameters={"synthetic_input": 17},
            result={"synthetic_result": 23},
            result_type="dict",
            variables={"synthetic_variable": 31},
            execution_context={"synthetic_context": 47},
            error_message="synthetic preserved diagnostic",
            duration_ms=19,
            peak_memory_bytes=101,
            process_rss_bytes=103,
            cpu_user_seconds=0.25,
            cpu_system_seconds=0.5,
            cpu_total_seconds=0.75,
            time_saved=2,
            value=Decimal("3.25"),
            runtime_mode="legacy",
            attempt_tracking_version="v1" if queued_attempt else None,
            retry_policy={
                "version": "execution-retry/v1",
                "enabled": False,
                "max_attempts": 2,
                "retry_on": [],
            },
            created_at=now,
            started_at=now
            if prior_status in {ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING}
            else None,
            completed_at=completed,
            scheduled_at=now + timedelta(hours=1)
            if prior_status == ExecutionStatus.SCHEDULED
            else None,
        )
    )
    if queued_attempt:
        session.add(
            WorkflowExecutionAttempt(
                id=case.attempt_id,
                execution_id=case.execution_id,
                attempt_number=1,
                status="published",
                phase="queue",
                claim_token=None,
                policy_version="workflow-attempt/v1",
                published_at=now,
                heartbeat_at=now,
                created_at=now,
            )
        )
    await session.commit()
    before = await _snapshot(case)
    _require(before[0]["status"] == prior_status, "seed_status_readback")
    _require(before[0]["executed_by"] == owner.user_id, "seed_owner_readback")
    _require(before[0]["organization_id"] == owner.organization_id, "seed_org_readback")
    _require(len(before[1]) == int(queued_attempt), "seed_attempt_cardinality")
    _require(
        await case.redis_client().get(_flag_key(case.execution_id)) is None,
        "seed_flag_not_absent",
    )
    case.label("committed_seed_readback")
    return before


def _verify_rows(
    before: tuple[dict[str, Any], list[dict[str, Any]]],
    after: tuple[dict[str, Any], list[dict[str, Any]]],
    changed: bool,
    prior_status: ExecutionStatus,
    started: datetime,
    finished: datetime,
) -> None:
    expected_execution = before[0].copy()
    expected_attempts = [row.copy() for row in before[1]]
    if changed:
        queued = prior_status in {ExecutionStatus.SCHEDULED, ExecutionStatus.PENDING}
        expected_execution["status"] = (
            ExecutionStatus.CANCELLED if queued else ExecutionStatus.CANCELLING
        )
        if queued:
            completed = after[0]["completed_at"]
            _require(isinstance(completed, datetime), "completed_time_missing")
            _require(started <= completed <= finished, "completed_time_outside_request")
            expected_execution["completed_at"] = completed
            for attempt in expected_attempts:
                attempt.update(
                    status="cancelled",
                    phase="terminal",
                    failure_phase="cancellation",
                    failure_code="cancelled_before_claim",
                    completed_at=completed,
                    heartbeat_at=completed,
                )
    _require(after[0] == expected_execution, "execution_full_row_projection_mismatch")
    _require(after[1] == expected_attempts, "attempt_full_row_projection_mismatch")


def _verify_dto(response: httpx.Response, row: dict[str, Any], actor: E2EUser) -> None:
    dto = response.json()
    _require(type(dto) is dict, "response_object_missing")
    _require(dto.get("execution_id") == str(row["id"]), "dto_identity_mismatch")
    _require(dto.get("status") == row["status"], "dto_status_mismatch")
    _require(dto.get("executed_by") == str(row["executed_by"]), "dto_owner_mismatch")
    _require(dto.get("org_id") == str(row["organization_id"]), "dto_org_mismatch")
    _require(dto.get("input_data") == row["parameters"], "dto_input_mismatch")
    _require(dto.get("result") == row["result"], "dto_result_mismatch")
    _require(
        dto.get("variables") == (row["variables"] if actor.is_superuser else None),
        "dto_variable_visibility_mismatch",
    )
    _require(
        dto.get("execution_context")
        == (row["execution_context"] if actor.is_superuser else None),
        "dto_context_visibility_mismatch",
    )


_HTTP_CASES = [
    ("H01", ExecutionStatus.SCHEDULED, "owner", 200, True, True, False),
    ("H02", ExecutionStatus.PENDING, "owner", 200, True, False, False),
    ("H03", ExecutionStatus.RUNNING, "owner", 200, True, False, True),
    ("H04", ExecutionStatus.PENDING, "nonowner", 403, False, False, False),
    ("H05", ExecutionStatus.PENDING, "superuser", 200, True, False, False),
    ("H06", ExecutionStatus.CANCELLING, "owner", 200, False, False, True),
    ("H07", ExecutionStatus.CANCELLED, "owner", 200, False, False, False),
    ("H08", ExecutionStatus.SUCCESS, "owner", 400, False, False, False),
    ("H09", ExecutionStatus.PENDING, "anonymous", 401, False, False, False),
]


@pytest.mark.parametrize(
    "case_id,prior_status,actor_kind,http_status,changed,queued_attempt,flag_present",
    _HTTP_CASES,
    ids=[item[0] for item in _HTTP_CASES],
)
async def test_booted_http_cancel_reference(
    async_engine: AsyncEngine,
    e2e_api_url: str,
    org1_user: E2EUser,
    non_admin_user: E2EUser,
    platform_admin: E2EUser,
    record_property: Callable[[str, object], None],
    request: pytest.FixtureRequest,
    record_testsuite_property: Callable[[str, object], None],
    case_id: str,
    prior_status: ExecutionStatus,
    actor_kind: str,
    http_status: int,
    changed: bool,
    queued_attempt: bool,
    flag_present: bool,
) -> None:
    async with _case_scope(
        async_engine, case_id, record_property, request, record_testsuite_property
    ) as case:
        _require(
            non_admin_user.user_id != org1_user.user_id
            and non_admin_user.organization_id == org1_user.organization_id
            and not non_admin_user.is_superuser,
            "nonowner_fixture_admission",
        )
        _require(platform_admin.is_superuser, "superuser_fixture_admission")
        before = await _seed(case, org1_user, prior_status, queued_attempt)
        actor = {
            "owner": org1_user,
            "nonowner": non_admin_user,
            "superuser": platform_admin,
        }.get(actor_kind)
        client = case.client(e2e_api_url)
        # H09 gets a newly constructed jar with no auth, never fixture cookies.
        _require(not client.cookies and client.auth is None, "client_not_fresh")
        headers = actor.headers if actor is not None else {}
        started = datetime.now(timezone.utc)
        response = await client.post(
            f"/api/executions/{case.execution_id}/cancel",
            headers=headers,
            timeout=min(60.0, case.remaining_work()),
        )
        finished = datetime.now(timezone.utc)
        _require(response.status_code == http_status, "http_status_mismatch")
        case.label(f"http_{http_status}")
        after = await _snapshot(case)
        _verify_rows(before, after, changed, prior_status, started, finished)
        flag = await case.redis_client().get(_flag_key(case.execution_id))
        _require(flag == ("1" if flag_present else None), "cancel_flag_mismatch")
        if actor is not None and http_status == 200:
            _verify_dto(response, after[0], actor)
        case.label("full_row_and_flag_readback")


class _Observation:
    """Scoped forwarding of the shared route, real repository and publishers."""

    def __init__(self, case: _Case, mutation: bool) -> None:
        self.case = case
        self.mutation = mutation
        # Defer the real application import until test environment fixtures have
        # run; do not activate lifespan or override any dependency.
        from src.main import create_app

        self.app = create_app()
        case.record_property(
            "http_cancel_reference_application_identity",
            _application_metadata(self.app, case.case_id),
        )
        self.body_reached = asyncio.Event()
        self.body_release = asyncio.Event()
        self.publisher_reached = asyncio.Event()
        self.publisher_release = asyncio.Event()
        case.barriers.extend([self.body_release, self.publisher_release])
        self.session: Session | None = None
        self.roots: list[SessionTransaction] = []
        self.commits: list[SessionTransaction] = []
        self.repository_calls = 0
        self.route_calls = 0
        self.execution_publish_calls = 0
        self.history_publish_calls = 0

    def active(self) -> bool:
        return _OBSERVATION.get() is self

    def install(self) -> None:
        contexts = [
            context
            for context in iter_route_contexts(self.app.routes)
            if context.path == _ROUTE_PATH and context.methods == {"POST"}
        ]
        _require(len(contexts) == 1, "cancel_route_context_cardinality")
        route = contexts[0].original_route
        _require(isinstance(route, APIRoute), "cancel_original_route_type")
        _require(
            route.endpoint is execution_router.cancel_execution,
            "cancel_endpoint_identity",
        )
        original_handle = route.handle
        handle_present = "handle" in vars(route)
        handle_previous = vars(route).get("handle", _MISSING)

        async def route_handle(scope: Scope, receive: Receive, send: Send) -> None:
            params = scope.get("path_params", {})
            if not _matches_observation(
                self.app,
                scope.get("app"),
                str(self.case.execution_id),
                params.get("execution_id"),
            ):
                await original_handle(scope, receive, send)
                return
            self.route_calls += 1
            _require(self.route_calls == 1, "observed_route_call_cardinality")
            token = _OBSERVATION.set(self)

            async def inner_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    self.case.label("inner_response_start")
                terminal = message["type"] == "http.response.body" and not message.get(
                    "more_body", False
                )
                if terminal and not self.mutation:
                    self.case.label("inner_terminal_body_barrier")
                    self.body_reached.set()
                    await self.body_release.wait()
                await send(message)
                if terminal:
                    self.case.label("inner_terminal_body_send_return")

            try:
                # Included APIRoute.handle creates the genuine effective handler
                # and dependency exit stacks. No context/dependant is rebuilt here.
                await original_handle(scope, receive, inner_send)
                self.case.label("original_route_handle_return")
            finally:
                _OBSERVATION.reset(token)

        def restore_route() -> None:
            _restore_attribute(
                route, "handle", route_handle, handle_present, handle_previous
            )

        # Instance-level on a SHARED module-global route, not a class override.
        route.handle = route_handle
        self.case.restorers.append(restore_route)
        original_cancel = execution_router.ExecutionRepository.cancel_execution
        cancel_present = "cancel_execution" in vars(
            execution_router.ExecutionRepository
        )
        cancel_previous = vars(execution_router.ExecutionRepository).get(
            "cancel_execution", _MISSING
        )

        async def cancel(
            repository: execution_router.ExecutionRepository,
            execution_id: UUID,
            user: UserPrincipal,
        ) -> Any:
            if not self.active() or execution_id != self.case.execution_id:
                return await original_cancel(repository, execution_id, user)
            self.repository_calls += 1
            _require(self.repository_calls == 1, "observed_repository_call_cardinality")
            _require(not repository.db.autoflush, "request_session_autoflush_changed")
            _require(
                not repository.db.sync_session.expire_on_commit,
                "request_session_expiry_changed",
            )
            self.capture_session(repository.db.sync_session)
            return await original_cancel(repository, execution_id, user)

        def restore_cancel() -> None:
            _restore_attribute(
                execution_router.ExecutionRepository,
                "cancel_execution",
                cancel,
                cancel_present,
                cancel_previous,
            )

        execution_router.ExecutionRepository.cancel_execution = cancel
        self.case.restorers.append(restore_cancel)
        original_execution_publish = execution_router.publish_execution_update
        original_history_publish = execution_router.publish_history_update
        execution_publish_present = "publish_execution_update" in vars(execution_router)
        execution_publish_previous = vars(execution_router).get(
            "publish_execution_update", _MISSING
        )
        history_publish_present = "publish_history_update" in vars(execution_router)
        history_publish_previous = vars(execution_router).get(
            "publish_history_update", _MISSING
        )

        async def execution_publish(*args: Any, **kwargs: Any) -> Any:
            if self.active() and kwargs.get("execution_id") == self.case.execution_id:
                self.execution_publish_calls += 1
                self.case.label("execution_publisher_entry")
                if self.mutation:
                    self.publisher_reached.set()
                    await self.publisher_release.wait()
            return await original_execution_publish(*args, **kwargs)

        async def history_publish(*args: Any, **kwargs: Any) -> Any:
            if self.active() and kwargs.get("execution_id") == self.case.execution_id:
                self.history_publish_calls += 1
                self.case.label("history_publisher_entry")
            return await original_history_publish(*args, **kwargs)

        def restore_execution_publish() -> None:
            _restore_attribute(
                execution_router,
                "publish_execution_update",
                execution_publish,
                execution_publish_present,
                execution_publish_previous,
            )

        def restore_history_publish() -> None:
            _restore_attribute(
                execution_router,
                "publish_history_update",
                history_publish,
                history_publish_present,
                history_publish_previous,
            )

        execution_router.publish_execution_update = execution_publish
        self.case.restorers.append(restore_execution_publish)
        execution_router.publish_history_update = history_publish
        self.case.restorers.append(restore_history_publish)

    def capture_session(self, session: Session) -> None:
        _require(self.session is None, "observed_session_cardinality")
        self.session = session
        root = session.get_transaction()
        if root is not None:
            _require(root.parent is None, "request_transaction_not_outermost")
            self.roots.append(root)

        def created(observed: Session, transaction: SessionTransaction) -> None:
            if observed is session and transaction.parent is None:
                self.roots.append(transaction)

        def committed(observed: Session) -> None:
            transaction = observed.get_transaction()
            _require(
                observed is session
                and transaction is not None
                and transaction.parent is None
                and any(transaction is root for root in self.roots),
                "request_commit_not_known_outer_transaction",
            )
            self.commits.append(transaction)
            self.case.label("request_outer_commit")

        def rolled_back(observed: Session) -> None:
            if observed is session:
                self.case.label("request_rollback")

        def ended(observed: Session, transaction: SessionTransaction) -> None:
            if observed is session and transaction.parent is None:
                _require(
                    any(transaction is root for root in self.roots),
                    "request_transaction_end_identity",
                )
                self.case.label("request_outer_transaction_end")

        for name, listener in (
            ("after_transaction_create", created),
            ("after_commit", committed),
            ("after_rollback", rolled_back),
            ("after_transaction_end", ended),
        ):
            event.listen(session, name, listener)
            self.case.restorers.append(
                lambda name=name, listener=listener: event.remove(
                    session, name, listener
                )
            )

    async def outer_app(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def outer_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.case.label("outer_response_start")
            terminal = message["type"] == "http.response.body" and not message.get(
                "more_body", False
            )
            await send(message)
            if terminal:
                self.case.label("outer_terminal_body_send_return")

        await self.app(scope, receive, outer_send)
        self.case.label("outer_app_return")


async def _wait_for_barrier(
    case: _Case, request: asyncio.Task[httpx.Response], barrier: asyncio.Event
) -> None:
    waiting = case.task(barrier.wait())
    await asyncio.wait({request, waiting}, return_when=asyncio.FIRST_COMPLETED)
    if request.done():
        # Surface the actual request error/control before a static missed barrier.
        await request
        _require(False, "request_finished_before_observation_barrier")
    await waiting


def _sqlstate(error: DBAPIError) -> str | None:
    # Fixed diagnostic code only; never exception arguments/messages/SQL/binds.
    current: BaseException | None = error.orig
    for _ in range(4):
        if current is None:
            break
        code = getattr(current, "sqlstate", None)
        if isinstance(code, str):
            return code
        current = current.__cause__
    return None


async def _probe_lock(case: _Case, advisory: bool, expected_available: bool) -> None:
    session = case.session()
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    if advisory:
        available = await session.scalar(
            text(
                "SELECT pg_try_advisory_xact_lock("
                "hashtext('bifrost:workflow-execution:' || :execution_id))"
            ),
            {"execution_id": str(case.execution_id)},
        )
        _require(type(available) is bool, "advisory_probe_type")
    else:
        try:
            row_id = await session.scalar(
                select(Execution.id)
                .where(Execution.id == case.execution_id)
                .with_for_update(nowait=True)
            )
        except DBAPIError as error:
            _require(_sqlstate(error) == "55P03", "row_probe_unexpected_diagnostic")
            available = False
        else:
            _require(row_id == case.execution_id, "row_probe_identity")
            available = True
    # An expected NOWAIT denial aborts this probe TX. Rollback before any reuse;
    # each advisory/row probe is a different, independently owned transaction.
    await session.rollback()
    _require(available is expected_available, "lock_probe_availability_mismatch")
    case.label(
        ("advisory" if advisory else "row")
        + ("_lock_available" if expected_available else "_lock_held")
    )


async def _subscribe_ack(case: _Case) -> None:
    subscriber = case.redis_client().pubsub()
    case.subscriber = subscriber
    await subscriber.subscribe(_CANCEL_CHANNEL)
    for _ in range(64):
        message = await subscriber.get_message(
            ignore_subscribe_messages=False, timeout=case.remaining_work()
        )
        _require(message is not None, "cancel_subscription_ack_missing")
        if message["type"] == "subscribe" and message["channel"] == _CANCEL_CHANNEL:
            _require(
                type(message["data"]) is int and message["data"] == 1,
                "cancel_subscription_ack_shape",
            )
            case.label("cancel_subscribe_ack")
            return
    _require(False, "cancel_subscription_ack_overflow")


async def _owned_delivery(case: _Case) -> None:
    subscriber = case.subscriber
    _require(subscriber is not None, "cancel_subscriber_missing")
    for _ in range(64):
        message = await subscriber.get_message(
            ignore_subscribe_messages=False, timeout=case.remaining_work()
        )
        _require(message is not None, "owned_cancel_message_missing")
        if message["type"] != "message" or message["channel"] != _CANCEL_CHANNEL:
            continue
        payload = message["data"]
        _require(type(payload) is str and len(payload) <= 512, "cancel_message_bound")
        decoded = json.loads(payload)
        if type(decoded) is dict and decoded.get("execution_id") == str(
            case.execution_id
        ):
            _require(set(decoded) == {"execution_id"}, "owned_cancel_message_shape")
            case.label("cancel_message_owned")
            return
        # No retention of unrelated message bodies or subscriber modification.
    _require(False, "owned_cancel_message_overflow")


@pytest.mark.parametrize("case_id", ["P01", "P02", "P03"])
async def test_asgi_http_cancel_reference_phases(
    async_engine: AsyncEngine,
    org1_user: E2EUser,
    record_property: Callable[[str, object], None],
    request: pytest.FixtureRequest,
    record_testsuite_property: Callable[[str, object], None],
    case_id: str,
) -> None:
    async with _case_scope(
        async_engine, case_id, record_property, request, record_testsuite_property
    ) as case:
        mutation = case_id == "P01"
        prior_status = {
            "P01": ExecutionStatus.PENDING,
            "P02": ExecutionStatus.CANCELLED,
            "P03": ExecutionStatus.CANCELLING,
        }[case_id]
        before = await _seed(case, org1_user, prior_status, queued_attempt=False)
        observer = _Observation(case, mutation)
        observer.install()
        if case_id == "P03":
            await _subscribe_ack(case)
        client = case.client("http://http-cancel-reference", app=observer.outer_app)
        started = datetime.now(timezone.utc)
        http_request = case.task(
            client.post(
                f"/api/executions/{case.execution_id}/cancel",
                headers=org1_user.headers,
                timeout=min(60.0, case.remaining_work()),
            )
        )
        if mutation:
            await _wait_for_barrier(case, http_request, observer.publisher_reached)
            _require(
                len(observer.commits) == 1, "mutation_commit_before_publish_missing"
            )
            committed = await _snapshot(case)
            _verify_rows(
                before,
                committed,
                True,
                prior_status,
                started,
                datetime.now(timezone.utc),
            )
            _require(
                case.labels.index("request_outer_commit")
                < case.labels.index("execution_publisher_entry"),
                "commit_execution_publish_order",
            )
            case.label("independent_committed_mutation_readback")
            observer.publisher_release.set()
        else:
            await _wait_for_barrier(case, http_request, observer.body_reached)
            _require(not observer.commits, "no_mutation_settled_before_inner_body")
            _require(observer.session is not None, "request_session_not_captured")
            transaction = observer.session.get_transaction()
            _require(
                transaction is not None
                and transaction.parent is None
                and any(transaction is root for root in observer.roots),
                "held_request_transaction_identity",
            )
            await _probe_lock(case, advisory=True, expected_available=False)
            await _probe_lock(case, advisory=False, expected_available=False)
            if case_id == "P03":
                await _owned_delivery(case)
            observer.body_release.set()
        response = await http_request
        finished = datetime.now(timezone.utc)
        _require(response.status_code == 200, "asgi_http_status_mismatch")
        _require(
            observer.route_calls == observer.repository_calls == 1,
            "actual_route_repository_cardinality",
        )
        after = await _snapshot(case)
        _verify_rows(before, after, mutation, prior_status, started, finished)
        _verify_dto(response, after[0], org1_user)
        _require(
            await case.redis_client().get(_flag_key(case.execution_id))
            == ("1" if case_id == "P03" else None),
            "asgi_cancel_flag_mismatch",
        )
        _require("request_rollback" not in case.labels, "normal_request_rolled_back")
        if mutation:
            _require(
                observer.execution_publish_calls == observer.history_publish_calls == 1,
                "mutation_publish_entry_cardinality",
            )
            _require(
                case.labels.index("request_outer_commit")
                < case.labels.index("history_publisher_entry"),
                "commit_history_publish_order",
            )
            # Refresh opens a second real transaction; request settlement is not
            # the earlier mutation commit and cannot undo that committed row.
            _require(
                len(observer.commits) == 2,
                "mutation_and_refresh_settlement_cardinality",
            )
        else:
            _require(
                len(observer.commits) == 1, "no_mutation_request_commit_cardinality"
            )
            _require(
                observer.execution_publish_calls == observer.history_publish_calls == 0,
                "replay_unexpected_projection_publish",
            )
            _require(
                case.labels.index("inner_terminal_body_send_return")
                < case.labels.index("request_outer_commit")
                < case.labels.index("original_route_handle_return"),
                "inner_body_commit_route_return_order",
            )
            await _probe_lock(case, advisory=True, expected_available=True)
            await _probe_lock(case, advisory=False, expected_available=True)
        case.label("full_row_phase_readback")
