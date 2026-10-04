"""Real Python/PG field effects versus the actual Rust kernel; no owner proof.

The fixture contains synthetic inputs, never expected plans or final rows.
Redis events are reference observations only: the kernel emits no events.
Payload conversions, delivery/source authority and mixed writers are outside
this domain-plan comparison. Nothing starts a queue, process pool or workload.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import select as io_select
import signal
import socket
import ssl
import stat
import time
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import redis.asyncio as redis
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from src.core.cache.keys import TTL_ACTIVE_EXECUTION, active_execution_key
from src.core.database import get_session_factory
from src.core.principal import UserPrincipal
from src.core.redis_client import ActiveExecution
from src.jobs.consumers import workflow_execution as consumer_module
from src.models.enums import ExecutionStatus
from src.models.orm.events import Event
from src.models.orm.executions import Execution, ExecutionLog, WorkflowExecutionAttempt
from src.models.orm.metrics import ExecutionMetricsDaily
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.repositories.events import EventSourceRepository, EventSubscriptionRepository
from src.repositories.executions import ExecutionRepository
from src.services.execution import process_pool as pool_module
from src.services.execution.attempts import has_recorded_attempt, mark_attempt_running

SCHEMA = "bifrost.test.workflow-domain/v1"
FIXTURE = Path(__file__).parent / "fixtures/workflow-domain-v1.json"
EVIDENCE = Path("/tmp/bifrost/workflow-domain-parity")
DRIVER = EVIDENCE / "driver"
RECEIPT = EVIDENCE / "receipt.json"
API_ROOT = Path(__file__).resolve().parents[2]
LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
LOGICAL = {
    "Scheduled",
    "Pending",
    "Running",
    "Success",
    "Failed",
    "Timeout",
    "Stuck",
    "CompletedWithErrors",
    "Cancelling",
    "Cancelled",
}
ATTEMPT_ENUM = {
    "Dispatching": "dispatching",
    "Published": "published",
    "Claimed": "claimed",
    "Running": "running",
    "Succeeded": "succeeded",
    "Failed": "failed",
    "TimedOut": "timed_out",
    "Cancelled": "cancelled",
    "WorkerLost": "worker_lost",
    "AdmissionRejected": "admission_rejected",
}
PHASE_ENUM = {
    s.title(): s
    for s in (
        "dispatch",
        "queue",
        "claim",
        "admission",
        "execution",
        "result",
        "terminal",
    )
}
FAILURE_PHASE_ENUM = {
    s.title(): s
    for s in (
        "dispatch",
        "queue",
        "claim",
        "admission",
        "execution",
        "result",
        "worker",
        "cancellation",
    )
}
FAILURE_CODE_ENUM = {
    "ExecutionTimeout": "execution_timeout",
    "Cancelled": "cancelled",
    "ResultPersistFailed": "result_persist_failed",
    "TenantCodeError": "tenant_code_error",
    "CancelledBeforeClaim": "cancelled_before_claim",
}
REASONS = {
    "MissingExecution",
    "MissingAttempt",
    "InvalidAttemptFence",
    "InvalidAttemptState",
    "InvalidLogicalState",
    "MissingFence",
    "LegacyUnfencedOutsideTrackedPath",
    "RequiresCoordinatorPolicy",
    "InconsistentRows",
}
SOURCE_PATHS = {
    "api/entrypoint.sh",
    "test.sh",
    "core-rs/Cargo.lock",
    "api/src/models/orm/work_deliveries.py",
    "api/tests/parity/workflow_domain_harness.py",
    "api/tests/parity/test_workflow_domain.py",
    "core-rs/crates/bifrost-domain/src/workflow/mod.rs",
    "core-rs/Dockerfile",
    "api/src/repositories/executions.py",
    "api/src/config.py",
    "api/src/services/execution/attempts.py",
    "requirements.lock",
    "scripts/ci/workflow-domain-parity.sh",
    "core-rs/crates/bifrost-domain/examples/workflow_domain_vectors.rs",
    "api/src/models/orm/executions.py",
    "api/src/jobs/consumers/workflow_execution.py",
    "api/alembic/versions/20260919_postgres_delivery.py",
    "api/tests/parity/fixtures/workflow-claim-v1.json",
    "core-rs/crates/bifrost-domain/src/workflow/tests.rs",
    ".github/workflows/workflow-domain-parity.yml",
    "api/tests/conftest.py",
    "api/pytest.ini",
    "api/src/services/work_delivery_store.py",
    "api/alembic/versions/20260831_execution_attempts.py",
    "api/Dockerfile.dev",
    "core-rs/crates/bifrost-domain/src/lib.rs",
    "docker-compose.test.yml",
    "api/tests/parity/fixtures/workflow-domain-v1.json",
    "core-rs/crates/bifrost-domain/Cargo.toml",
    "api/src/services/execution/process_pool.py",
    "scripts/lib/test_helpers.sh",
    "api/src/models/enums.py",
    "api/src/core/database.py",
    "core-rs/rust-toolchain.toml",
}

REFERENCE_HASHES = {
    "api/src/services/execution/attempts.py": "82ed8abc28a1a51d17ddcdaeeabf9473468ef8474c172bb01e7bc5b66b067afa",
    "api/src/jobs/consumers/workflow_execution.py": "c285f2d315b36bcdfc81dc0b8e23705f22a7dbb6f740311233612068e10cf218",
    "api/src/repositories/executions.py": "6767c4462d07e5286f6a5245dc8942272792e81588b13af8a663ef018e165007",
    "api/src/models/orm/executions.py": "9d398b075384ff402631b98d53a1df0cadd962baa3bccb20f3144770b6c2ce7f",
}
KERNEL_HASHES = {
    "core-rs/crates/bifrost-domain/src/lib.rs": "aeea8c98c1da4fdb7ad790bc56648a6f0165b51257f7780e90704f425ac481a5",
    "core-rs/crates/bifrost-domain/src/workflow/mod.rs": "066c2799c634b59448973b75b8878a9f6253591b7244a89c3d34cafae5031d11",
    "core-rs/crates/bifrost-domain/src/workflow/tests.rs": "1b9f5708e7f0b1a0ee61b346199ac7b007b47cca79ea01259f0424d5eedf16c4",
}
SEED_TIME = datetime(2020, 1, 1, tzinfo=timezone.utc)
MISSING_FENCE = "durable workflow result is missing its attempt fence"


def check(condition: bool, message: str) -> None:
    """Fixed diagnostics do not dump token-bearing rows or captured payloads."""
    if not condition:
        raise AssertionError(message)


def closed(value: Any, keys: set[str]) -> None:
    check(isinstance(value, dict) and set(value) == keys, "closed schema mismatch")


def unique_json(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            check(key not in result, "duplicate JSON field")
            result[key] = value
        return result

    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)


def load_cases() -> list[dict[str, Any]]:
    with FIXTURE.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    check(len(data) <= 1024 * 1024, "fixture size exceeded")
    manifest = unique_json(data)
    closed(manifest, {"schema", "synthetic", "description", "cases"})
    check(
        manifest["schema"] == "bifrost.test.workflow-domain-fixtures/v1",
        "fixture schema mismatch",
    )
    check(manifest["synthetic"] is True, "fixture must be synthetic")
    cases = manifest["cases"]
    check(isinstance(cases, list) and bool(cases), "fixture cases missing")
    labels: set[str] = set()
    cohort_labels: set[str] = set()
    for case in cases:
        closed(case, {"case_id", "arrangement", "steps"})
        label = case["case_id"]
        check(
            isinstance(label, str) and bool(LABEL.fullmatch(label)),
            "invalid fixture label",
        )
        check(label not in cohort_labels, "duplicate cohort label")
        cohort_labels.add(label)
        arrangement = case["arrangement"]
        check(
            set(arrangement) <= {"status", "attempt", "previous_completed_attempt", "tracking"},
            "unknown arrangement",
        )
        check(arrangement["status"] in LOGICAL, "invalid arrangement status")
        check(
            arrangement["attempt"]
            in {
                "none",
                "dispatching",
                "published",
                "claimed",
                "claimed_started",
                "running",
                "completed",
            },
            "invalid legal attempt arrangement",
        )
        check(
            isinstance(case["steps"], list) and bool(case["steps"]),
            "fixture steps missing",
        )
        for step in case["steps"]:
            check(
                set(step)
                <= {
                    "case_id",
                    "kind",
                    "status",
                    "error_type",
                    "token",
                    "execution",
                    "process_id",
                    "duration_ms",
                    "metrics",
                    "result",
                    "error",
                    "variables",
                    "execution_context",
                    "roi",
                },
                "unknown step field",
            )
            label = step["case_id"]
            check(
                isinstance(label, str) and bool(LABEL.fullmatch(label)),
                "invalid operation label",
            )
            check(label not in labels, "duplicate operation label")
            labels.add(label)
            check(
                step["kind"] in {"running", "success", "failure", "coordinator_loss", "cancel"},
                "invalid fixture operation",
            )
    return cases


def sha_file(path: Path) -> str:
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        check(stat.S_ISREG(info.st_mode), "custody input is not a regular file")
        digest = hashlib.sha256()
        remaining = info.st_size
        while remaining:
            chunk = stream.read(min(65536, remaining))
            check(bool(chunk), "custody input changed during read")
            digest.update(chunk)
            remaining -= len(chunk)
        check(not stream.read(1), "custody input grew during read")
        check(
            os.fstat(stream.fileno()).st_mtime_ns == info.st_mtime_ns,
            "custody input changed during read",
        )
        return digest.hexdigest()


def verify_receipt() -> dict[str, Any]:
    with RECEIPT.open("rb") as stream:
        data = stream.read(65537)
    check(len(data) <= 65536, "receipt size exceeded")
    receipt = unique_json(data)
    closed(
        receipt,
        {
            "schema",
            "candidate_sha",
            "candidate_tree",
            "driver_sha256",
            "fixture_sha256",
            "source_sha256",
            "claim_frontend_policy",
        },
    )
    check(
        receipt["schema"] == "bifrost.test.workflow-domain-receipt/v2",
        "receipt schema mismatch",
    )
    check(receipt["claim_frontend_policy"] == "required/v1", "claim frontend policy missing")
    for key in ("candidate_sha", "candidate_tree"):
        check(
            isinstance(receipt[key], str) and bool(re.fullmatch(r"[0-9a-f]{40}", receipt[key])),
            "candidate custody missing",
        )
    sources = receipt["source_sha256"]
    check(
        isinstance(sources, dict) and set(sources) == SOURCE_PATHS,
        "source custody scope mismatch",
    )
    for digest in [
        receipt["driver_sha256"],
        receipt["fixture_sha256"],
        *sources.values(),
    ]:
        check(
            isinstance(digest, str) and bool(HEX64.fullmatch(digest)),
            "invalid custody hash",
        )
    check(sha_file(FIXTURE) == receipt["fixture_sha256"], "fixture custody mismatch")
    for path, expected in {**REFERENCE_HASHES, **KERNEL_HASHES}.items():
        check(sources[path] == expected, "frozen reference source drift: " + path)
    # Core-rs is not mounted in the API runner: those are producer bindings,
    # never invented API-side source/image readbacks. Verify actual API mounts.
    for path in SOURCE_PATHS:
        if path.startswith("api/"):
            check(
                sha_file(API_ROOT / path.removeprefix("api/")) == sources[path],
                "mounted source custody mismatch: " + path,
            )
    check(sha_file(DRIVER) == receipt["driver_sha256"], "driver custody mismatch")
    return receipt


async def read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes:
    captured = bytearray()
    while True:
        chunk = await stream.read(min(1024, limit + 1 - len(captured)))
        if not chunk:
            return bytes(captured)
        captured.extend(chunk)
        check(len(captured) <= limit, "driver output exceeded limit")


async def invoke_driver(request: dict[str, Any]) -> dict[str, Any]:
    labels = {s["case_id"] for c in load_cases() for s in c["steps"]} | set(CLAIM_IDS)
    check(request["case_id"] in labels, "operation is outside closed fixture")
    payload = json.dumps(request, separators=(",", ":")).encode()
    check(len(payload) <= 65536, "driver input exceeded limit")
    verify_receipt()  # Includes the actual executable hash, immediately pre-exec.
    process: asyncio.subprocess.Process | None = None
    tasks: list[asyncio.Task[Any]] = []
    try:
        # Creation and I/O share the existing invocation budget. asyncio owns
        # transport disposal if cancellation occurs before Process is returned.
        async with asyncio.timeout(5):
            process = await asyncio.create_subprocess_exec(
                str(DRIVER),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                cwd=str(EVIDENCE),
                start_new_session=True,
                limit=4096,
            )
            check(
                process.stdin is not None and process.stdout is not None and process.stderr is not None,
                "driver pipes unavailable",
            )

            async def send() -> None:
                process.stdin.write(payload)
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()

            tasks = [
                asyncio.create_task(send()),
                asyncio.create_task(read_bounded(process.stdout, 4096)),
                asyncio.create_task(read_bounded(process.stderr, 4096)),
                asyncio.create_task(process.wait()),
            ]
            _, stdout, stderr, returncode = await asyncio.gather(*tasks)
            check(returncode == 0 and not stderr, "driver invocation failed")
            try:
                response = unique_json(stdout)
            except (ValueError, UnicodeError, RecursionError):
                raise AssertionError("driver response is not one bounded JSON value") from None
            closed(response, {"schema", "case_id", "outcome"})
            check(
                response["schema"] == SCHEMA and response["case_id"] == request["case_id"],
                "driver response custody mismatch",
            )
            validate_response(response["outcome"], request["operation"]["kind"])
            return response
    finally:
        if process is not None:
            # Covers timeout, overflow, cancellation, bad JSON and nonzero exit.
            # Kill the owned process group; do not wait on unbounded pipe EOF.
            try:
                if process.returncode is None or any(not task.done() for task in tasks):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                finally:
                    if tasks:
                        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2)


RUNNING_FIELDS = {"status", "phase", "started_at", "heartbeat_at", "process_id"}
RESULT_ATTEMPT_FIELDS = {
    "status",
    "phase",
    "failure_phase",
    "failure_code",
    "started_at",
    "heartbeat_at",
    "completed_at",
    "duration_ms",
    "peak_memory_bytes",
    "cpu_total_seconds",
}
RESULT_EXECUTION_FIELDS = {
    "status",
    "duration_ms",
    "completed_at",
    "result",
    "result_type",
    "error_message",
    "time_saved",
    "value",
    "variables",
    "execution_context",
    "metrics",
    "logs",
}
CANCEL_ATTEMPT_FIELDS = {
    "status",
    "phase",
    "failure_phase",
    "failure_code",
    "completed_at",
    "heartbeat_at",
}


def validate_response(outcome: dict[str, Any], kind: str) -> None:
    if kind == "claim":
        validate_claim_response(outcome)
        return
    check(isinstance(outcome, dict), "invalid driver outcome")
    if outcome.get("kind") == "rejected":
        closed(outcome, {"kind", "reason"})
        check(outcome["reason"] in REASONS, "unknown driver rejection")
        return
    closed(outcome, {"kind", "plan"})
    check(outcome["kind"] == "accepted", "unknown driver outcome")
    plan = outcome["plan"]
    check(
        isinstance(plan, dict) and plan.get("kind") == kind,
        "driver plan operation mismatch",
    )
    if kind == "running":
        closed(plan, {"kind", *RUNNING_FIELDS})
    elif kind == "result":
        closed(plan, {"kind", "execution", "attempt"})
        closed(plan["execution"], RESULT_EXECUTION_FIELDS)
        closed(plan["attempt"], RESULT_ATTEMPT_FIELDS)
    else:
        closed(plan, {"kind", "status", "completed_at", "attempt"})
        if plan["attempt"] is not None:
            closed(plan["attempt"], CANCEL_ATTEMPT_FIELDS)


def dormant(pool: Any) -> None:
    check(not pool._started and pool._template is None, "pool is not unstarted")
    check(
        not pool.processes and not pool.service_processes and not pool._result_tasks,
        "pool has active ownership",
    )
    check(
        all(
            getattr(pool, name) is None
            for name in (
                "_monitor_task",
                "_heartbeat_task",
                "_cancel_task",
                "_command_task",
            )
        ),
        "pool has background tasks",
    )
    check(pool._redis is None, "pool has a pre-existing Redis resource")


def unstarted_consumer(consumer: Any, pool: Any) -> None:
    check(
        consumer._pool is pool and not consumer._pool_started and not consumer._running,
        "consumer constructor isolation failed",
    )
    check(not consumer._inflight, "consumer has in-flight work")
    check(
        all(
            getattr(consumer, key) is None
            for key in (
                "_channel",
                "_queue",
                "_connection_ctx",
                "_postgres",
                "_consumer_tag",
            )
        ),
        "consumer has started resources",
    )


@asynccontextmanager
async def real_consumer():
    from src.core import redis_client as redis_module

    previous_pool = pool_module._pool
    previous_redis = redis_module._redis_client
    pool = pool_module.get_process_pool()
    previous_callback = pool.on_result
    try:
        dormant(pool)
        consumer = consumer_module.WorkflowExecutionConsumer()
        unstarted_consumer(consumer, pool)
        try:
            yield consumer
        finally:
            dormant(pool)
            unstarted_consumer(consumer, pool)
    finally:
        # Restore the exact callback even if construction or an operation fails.
        pool.on_result = previous_callback
        try:
            if previous_redis is None and redis_module._redis_client is not None:
                await redis_module.close_redis_client()
        finally:
            if previous_pool is None:
                check(pool_module._pool is pool, "owned pool identity changed")
                dormant(pool)
                pool_module._pool = None
            else:
                check(
                    pool_module._pool is previous_pool,
                    "pre-existing pool identity changed",
                )
        # No pool.stop(): a pre-existing pool is never stopped by this harness.


@contextmanager
def projection_observer(execution_id: str):
    """Root-approved read-only forwarding seam; genuine repo remains the oracle."""
    original = consumer_module.update_execution
    calls: list[dict[str, Any]] = []

    async def forward(*args: Any, **kwargs: Any):
        if kwargs.get("execution_id") == execution_id:
            calls.append(deepcopy({key: value for key, value in kwargs.items() if key != "session"}))
        return await original(*args, **kwargs)

    consumer_module.update_execution = forward
    try:
        yield calls
    finally:
        consumer_module.update_execution = original


@dataclass(repr=False)
class StepObservation:
    case_id: str
    request: dict[str, Any]
    response: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    arguments: dict[str, Any]
    reference: str
    projection_calls: list[dict[str, Any]]
    events: list[dict[str, Any]]
    started: datetime
    ended: datetime


class WorkflowCohort:
    def __init__(self, engine: Any, case: dict[str, Any]):
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.case = case
        self.ids = {
            name: uuid4()
            for name in (
                "org",
                "user",
                "execution",
                "foreign",
                "missing",
                "attempt",
                "foreign_attempt",
                "old_attempt",
                "current_token",
                "foreign_token",
                "stale_token",
                "wrong_token",
            )
        }
        self.redis = redis.from_url(os.environ["BIFROST_REDIS_URL"], decode_responses=True)
        self.pubsub = self.redis.pubsub()
        self.barrier = "workflow-parity-barrier:" + str(uuid4())
        self.observations: list[StepObservation] = []
        self.principal: UserPrincipal | None = None

    async def seed(self) -> None:
        now = datetime.now(timezone.utc)
        arrangement = self.case["arrangement"]
        async with self.sessions() as db:
            db.add(
                Organization(
                    id=self.ids["org"],
                    name="synthetic-domain-parity",
                    created_by="synthetic-domain-parity",
                )
            )
            await db.flush()
            user = User(
                id=self.ids["user"],
                email=f"{self.ids['user']}@synthetic.example.test",
                name="Synthetic parity",
                organization_id=self.ids["org"],
                is_active=True,
                is_superuser=False,
            )
            db.add(user)
            await db.flush()
            for role, status in (
                ("execution", arrangement["status"]),
                ("foreign", "Running"),
            ):
                db.add(
                    Execution(
                        id=self.ids[role],
                        status=ExecutionStatus(status),
                        workflow_name="synthetic-domain-parity",
                        executed_by=user.id,
                        executed_by_name=user.name,
                        organization_id=self.ids["org"],
                        attempt_tracking_version=None
                        if role == "execution" and arrangement.get("tracking") is False
                        else "v1",
                        parameters={"synthetic": True},
                        started_at=now,
                        completed_at=SEED_TIME,
                        result={"parity": "retained"},
                        result_type="text",
                        error_message="synthetic-retained-error",
                        variables={"parity": "retained"},
                        execution_context={"parity_context": "retained"},
                        duration_ms=17,
                        peak_memory_bytes=4096,
                        process_rss_bytes=2048,
                        cpu_user_seconds=0.5,
                        cpu_system_seconds=0.25,
                        cpu_total_seconds=0.75,
                        time_saved=7,
                        value=Decimal("12.50"),
                    )
                )
            await db.flush()
            self.add_attempt(db, "foreign", "claimed", now, 1)
            previous = arrangement.get("previous_completed_attempt") == "seed"
            if previous:
                self.add_attempt(db, "execution", "completed", now, 1, old=True)
            if arrangement["attempt"] != "none":
                self.add_attempt(db, "execution", arrangement["attempt"], now, 2 if previous else 1)
            await db.commit()
        # Principal is built from the independently committed real user row.
        async with self.sessions() as db:
            user = await db.get(User, self.ids["user"])
            check(
                user is not None and user.is_active and not user.is_superuser,
                "fixture principal missing",
            )
            self.principal = UserPrincipal(
                user_id=user.id,
                email=user.email,
                organization_id=user.organization_id,
                name=user.name or "",
                is_active=user.is_active,
                is_superuser=user.is_superuser,
            )
        app_factory = get_session_factory()
        check(
            app_factory.kw["bind"].url == self.sessions.kw["bind"].url,
            "reference factory is not the disposable fixture database",
        )
        for role in ("execution", "foreign"):
            metadata = ActiveExecution(
                execution_id=str(self.ids[role]),
                workflow_id=None,
                workflow_name="synthetic-domain-parity",
                org_id=str(self.ids["org"]),
                user_id=str(self.ids["user"]),
                user_name="Synthetic parity",
                user_email="synthetic@synthetic.example.test",
                sync=False,
                event=None,
            )
            # Actual production key and TypedDict shape; real Redis fixture SET,
            # matching the parent's existing lease writer, not a loader override.
            await self.redis.setex(
                active_execution_key(str(self.ids[role])),
                TTL_ACTIVE_EXECUTION,
                json.dumps(metadata),
            )
        await self.pubsub.subscribe(self.barrier)
        await self.pubsub.psubscribe("bifrost:*")
        for kind in ("subscribe", "psubscribe"):
            ack = await self.pubsub.get_message(timeout=5)
            check(bool(ack) and ack["type"] == kind, "reference event subscription failed")

    def add_attempt(
        self,
        db: Any,
        role: str,
        arrangement: str,
        now: datetime,
        number: int,
        *,
        old: bool = False,
    ) -> None:
        completed = arrangement == "completed"
        status = "succeeded" if completed else "claimed" if arrangement == "claimed_started" else arrangement
        token = (
            None
            if status in {"dispatching", "published"}
            else self.ids["stale_token" if old else "current_token" if role == "execution" else "foreign_token"]
        )
        db.add(
            WorkflowExecutionAttempt(
                id=self.ids["old_attempt" if old else "attempt" if role == "execution" else "foreign_attempt"],
                execution_id=self.ids[role],
                attempt_number=number,
                claim_token=token,
                status=status,
                phase="terminal"
                if completed
                else "execution"
                if status == "running"
                else "claim"
                if status == "claimed"
                else "queue"
                if status == "published"
                else "dispatch",
                failure_phase="execution",
                failure_code="synthetic-retained-code",
                published_at=None if status == "dispatching" else now,
                claimed_at=now if token is not None else None,
                started_at=now if arrangement in {"claimed_started", "running", "completed"} else None,
                heartbeat_at=now,
                completed_at=now if completed else None,
                process_id="synthetic-retained-process",
                worker_id="synthetic-worker",
                worker_incarnation_id=uuid4(),
                duration_ms=19,
                peak_memory_bytes=1024,
                cpu_total_seconds=0.125,
            )
        )

    async def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        async with self.sessions() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            for model, label in (
                (Execution, "executions"),
                (WorkflowExecutionAttempt, "attempts"),
                (ExecutionLog, "logs"),
            ):
                predicate = (
                    model.id.in_([self.ids["execution"], self.ids["foreign"]])
                    if model is Execution
                    else model.execution_id.in_([self.ids["execution"], self.ids["foreign"]])
                )
                rows = (await db.scalars(select(model).where(predicate).order_by(model.id))).all()
                result[label] = [
                    {column.name: getattr(row, column.name) for column in model.__table__.columns} for row in rows
                ]
            # Reference-only derived observations, never kernel row plans.
            for model, label in (
                (Event, "reference_topic_events"),
                (ExecutionMetricsDaily, "reference_org_metrics"),
            ):
                rows = (
                    await db.scalars(select(model).where(model.organization_id == self.ids["org"]).order_by(model.id))
                ).all()
                result[label] = [
                    {column.name: getattr(row, column.name) for column in model.__table__.columns} for row in rows
                ]
            result["history"] = {
                str(self.ids[role]): await has_recorded_attempt(db, self.ids[role])
                for role in ("execution", "foreign", "missing")
            }
        return result

    def request(self, step: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        execution_id = self.ids[step.get("execution", "execution")]
        execution = next((r for r in snapshot["executions"] if r["id"] == execution_id), None)
        attempts = [r for r in snapshot["attempts"] if r["execution_id"] == execution_id]
        active = [r for r in attempts if r["completed_at"] is None]
        check(len(active) <= 1, "fixture violated one-active-attempt constraint")
        attempt = active[0] if active else max(attempts, key=lambda r: r["attempt_number"], default=None)
        selector = step.get("token", "current")
        token = None if selector == "missing" else self.ids[selector + "_token"]
        kind = step["kind"]
        arguments = deepcopy(step)
        arguments["execution_id"] = str(execution_id)
        arguments["attempt_token"] = str(token) if token is not None else None
        if kind == "running":
            check(token is not None, "running requires a submitted synthetic token")
            operation = {
                "kind": "running",
                "execution_id": str(execution_id),
                "claim_token": str(token),
                "process_id": step.get("process_id"),
            }
        elif kind == "cancel":
            operation = {"kind": "cancel", "execution_id": str(execution_id)}
        else:
            # These are actual source arguments, not a copied outcome classifier.
            # Success/failure normalization occurs in the real consumer below.
            duration = step.get("duration_ms", 0)
            arguments["duration_ms"] = duration
            outcome = (
                {"kind": "success", "status": step.get("status", "Success")}
                if kind == "success"
                else {"kind": "coordinator_loss"}
                if kind == "coordinator_loss"
                else {"kind": "failure", "error_type": step["error_type"]}
            )
            operation = {
                "kind": "result",
                "execution_id": str(execution_id),
                "claim_token": str(token) if token is not None else None,
                "outcome": outcome,
                "duration_ms": duration,
            }
        request = {
            "schema": SCHEMA,
            "case_id": step["case_id"],
            "operation": operation,
            "rows": {
                "execution": {
                    "id": str(execution["id"]),
                    "status": enum_value(execution["status"]),
                }
                if execution is not None
                else None,
                "attempt": {
                    "id": str(attempt["id"]),
                    "execution_id": str(attempt["execution_id"]),
                    "claim_token": str(attempt["claim_token"]) if attempt["claim_token"] is not None else None,
                    "status": attempt["status"],
                    "phase": attempt["phase"],
                    "started_at_present": attempt["started_at"] is not None,
                    "completed_at_present": attempt["completed_at"] is not None,
                }
                if attempt is not None
                else None,
                "history": "recorded" if snapshot["history"][str(execution_id)] else "unrecorded",
            },
        }
        return request, arguments

    async def execute(
        self, step: dict[str, Any], arguments: dict[str, Any], consumer: Any
    ) -> tuple[str, list[dict[str, Any]]]:
        execution_id = arguments["execution_id"]
        if step["kind"] == "running":
            async with self.sessions() as db:
                options = {"process_id": step["process_id"]} if "process_id" in step else {}
                accepted = await mark_attempt_running(
                    db, UUID(execution_id), UUID(arguments["attempt_token"]), **options
                )
                await db.commit()
            return "accepted" if accepted else "rejected", []
        if step["kind"] == "cancel":
            check(self.principal is not None, "fixture principal unavailable")
            async with self.sessions() as db:
                _, error = await ExecutionRepository(db).cancel_execution(UUID(execution_id), self.principal)
            return "accepted" if error is None else "rejected", []
        result = {
            "sync": False,
            "execution_id": execution_id,
            "attempt_token": arguments["attempt_token"],
        }
        # Exercise the consumer's real omitted-key defaults, not just explicit
        # zero/None substitutes. The driver receives the effective duration.
        if "duration_ms" in step:
            result["duration_ms"] = step["duration_ms"]
        if "metrics" in step:
            result["metrics"] = deepcopy(step["metrics"])
        if step["kind"] == "success":
            result.update(
                status=step.get("status", "Success"),
                result=deepcopy(step.get("result", {"parity": "supplied"})),
                error=step.get("error", "synthetic-supplied-error"),
                variables=deepcopy(step.get("variables", {"parity": "supplied"})),
                execution_context=deepcopy(step.get("execution_context", {"parity_context": "supplied"})),
                roi=deepcopy(step.get("roi", {"time_saved": 3, "value": 2.5})),
            )
            method = consumer._process_success
        else:
            await self.topic_isolation()
            result.update(
                error="synthetic-failure",
                error_type=step["error_type"],
                execution_context={"parity_context": "failure"},
            )
            method = consumer._process_failure
        with projection_observer(execution_id) as calls:
            try:
                await method(execution_id, result)
            except RuntimeError as exc:
                if str(exc) != MISSING_FENCE:
                    raise
                return "missing_fence", calls
        return "returned", calls

    async def topic_isolation(self) -> None:
        """Do not let real failure fan-out launch a pre-existing subscriber.

        Read the actual repositories and scope helper used by emit_topic; never
        disable sources, subscriptions, the emitter or normal derived effects.
        This is test resource isolation, not a new product admission policy.
        """
        from src.services.events.processor import _subscription_matches_event_org

        async with self.sessions() as db:
            sources = EventSourceRepository(db)
            subscriptions = EventSubscriptionRepository(db)
            for topic in ("workflow.failed", "workflow.retry_exhausted"):
                source = await sources.get_by_topic(topic, organization_id=self.ids["org"])
                if source is None:
                    continue
                eligible = await subscriptions.get_active_for_event(
                    source_id=source.id,
                    event_type=topic,
                    organization_id=self.ids["org"],
                )
                check(
                    not any(_subscription_matches_event_org(item, self.ids["org"]) for item in eligible),
                    "fixture isolation: existing topic subscriber could launch extra execution",
                )

    async def events(self) -> list[dict[str, Any]]:
        await self.redis.publish(self.barrier, "barrier")
        result = []
        deadline = time.monotonic() + 5
        identities = {str(self.ids["execution"]), str(self.ids["foreign"])}
        while time.monotonic() < deadline:
            message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
            if not message:
                continue
            if message["channel"] == self.barrier:
                return result
            # Only retain publications mentioning an explicitly owned execution.
            payload = json.loads(message["data"])
            if isinstance(payload, dict) and any(
                isinstance(payload.get(key), str) and payload[key] in identities
                for key in ("executionId", "execution_id")
            ):
                result.append({"channel": message["channel"], "payload": payload})
                check(len(result) <= 100, "reference event capture exceeded bound")
        raise AssertionError("reference event FIFO barrier unavailable")

    async def run(self) -> list[StepObservation]:
        async with real_consumer() as consumer:
            for step in self.case["steps"]:
                before = await self.snapshot()
                request, arguments = self.request(step, before)
                response = await invoke_driver(request)
                started = datetime.now(timezone.utc)
                reference, calls = await self.execute(step, arguments, consumer)
                ended = datetime.now(timezone.utc)
                after = await self.snapshot()
                observation = StepObservation(
                    step["case_id"],
                    request,
                    response,
                    before,
                    after,
                    arguments,
                    reference,
                    calls,
                    await self.events(),
                    started,
                    ended,
                )
                self.observations.append(observation)
                compare(observation)
        return self.observations

    async def close(self) -> None:
        try:
            try:
                # Delete only fixture keys. No wildcard/global Redis reset.
                for role in ("execution", "foreign"):
                    await self.redis.delete(active_execution_key(str(self.ids[role])))
            finally:
                async with self.sessions() as db:
                    ids = [self.ids["execution"], self.ids["foreign"]]
                    await db.execute(delete(ExecutionLog).where(ExecutionLog.execution_id.in_(ids)))
                    await db.execute(delete(Execution).where(Execution.id.in_(ids)))
                    # Event.organization_id is SET NULL on org deletion; remove
                    # only genuine events stamped with this fresh fixture org.
                    await db.execute(delete(Event).where(Event.organization_id == self.ids["org"]))
                    await db.execute(delete(User).where(User.id == self.ids["user"]))
                    await db.execute(delete(Organization).where(Organization.id == self.ids["org"]))
                    await db.commit()
        finally:
            try:
                await self.pubsub.aclose()
            finally:
                await self.redis.aclose()

    def retain(self) -> None:
        # No request/claim token or repository session is exported. All values
        # below are this cohort's labeled synthetic fixture data, not customers.
        def safe(value: Any, key: str = "") -> Any:
            if key in {"claim_token", "attempt_token"}:
                return "synthetic-fence-present" if value is not None else None
            if isinstance(value, dict):
                return {k: safe(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [safe(v) for v in value]
            if isinstance(value, (UUID, Decimal)):
                return str(value)
            if isinstance(value, (datetime, date)):
                return value.isoformat()
            return enum_value(value)

        records = [
            {
                "case_id": o.case_id,
                "rust": o.response,
                "python_return": o.reference,
                "before": safe(o.before),
                "after": safe(o.after),
                "provided": safe(o.projection_calls),
                "python_events": o.events,
                "event_parity": "not-applicable-kernel-emits-no-events",
                "started": o.started.isoformat(),
                "ended": o.ended.isoformat(),
            }
            for o in self.observations
        ]
        observations = EVIDENCE / "observations"
        observations.mkdir(exist_ok=True)
        (observations / (self.case["case_id"] + "-" + str(self.ids["org"]) + ".json")).write_text(
            json.dumps(
                {
                    "case_id": self.case["case_id"],
                    "cohort_id": str(self.ids["org"]),
                    "observations": records,
                },
                indent=2,
            )
            + "\n"
        )


def enum_value(value: Any) -> Any:
    return value.value if isinstance(value, ExecutionStatus) else value


def same(left: Any, right: Any, field: str) -> None:
    check(enum_value(left) == enum_value(right), "field differs: " + field)


def clock(directive: str, before: Any, after: Any, observation: StepObservation, field: str) -> None:
    check(directive in {"Keep", "Now"}, "unknown time directive")
    if directive == "Keep":
        same(before, after, field)
    else:
        check(
            isinstance(after, datetime)
            and after.tzinfo is not None
            and observation.started <= after <= observation.ended,
            "new clock outside actual operation: " + field,
        )


def scalar(directive: str, before: Any, after: Any, supplied: Any, field: str) -> None:
    check(directive in {"Keep", "SetSupplied"}, "unknown input directive")
    same(before if directive == "Keep" else supplied, after, field)


def unchanged(before: dict[str, Any], after: dict[str, Any], except_fields: set[str]) -> None:
    check(set(before) == set(after), "row columns differ")
    for field in set(before) - except_fields:
        same(before[field], after[field], field)


def row(snapshot: dict[str, Any], table: str, identity: str) -> dict[str, Any]:
    values = [r for r in snapshot[table] if str(r["id"]) == identity]
    check(len(values) == 1, "selected committed row missing")
    return values[0]


def attempt_fields(
    plan: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    observation: StepObservation,
) -> None:
    for field, directive in plan.items():
        if field == "kind":
            continue
        if field in {"started_at", "heartbeat_at", "completed_at"}:
            clock(directive, before[field], after[field], observation, field)
        elif field in {
            "duration_ms",
            "peak_memory_bytes",
            "cpu_total_seconds",
            "process_id",
        }:
            supplied = (
                observation.arguments.get(field)
                if field in {"duration_ms", "process_id"}
                else (observation.arguments.get("metrics") or {}).get(field)
            )
            scalar(directive, before[field], after[field], supplied, field)
        else:
            mapping = {
                "status": ATTEMPT_ENUM,
                "phase": PHASE_ENUM,
                "failure_phase": FAILURE_PHASE_ENUM,
                "failure_code": FAILURE_CODE_ENUM,
            }[field]
            check(directive is None or directive in mapping, "unknown static attempt enum")
            same(None if directive is None else mapping[directive], after[field], field)
    unchanged(before, after, set(plan) - {"kind"})
    if plan.get("completed_at") == "Now":
        same(after["heartbeat_at"], after["completed_at"], "one attempt time sample")
    if plan.get("started_at") == "Now":
        same(after["started_at"], after["heartbeat_at"], "first running sample")


def projection_fields(
    plan: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    observation: StepObservation,
) -> set[str]:
    check(
        len(observation.projection_calls) == 1,
        "accepted result did not call genuine repository once",
    )
    provided = observation.projection_calls[0]
    same(
        observation.arguments["duration_ms"],
        provided["duration_ms"],
        "resolved consumer duration",
    )
    touched = {"status", "duration_ms", "completed_at"}
    for field in {
        "result",
        "result_type",
        "error_message",
        "time_saved",
        "value",
        "variables",
        "execution_context",
    }:
        permission = plan[field]
        check(permission in {"Keep", "AllowSupplied"}, "unknown projection permission")
        supplied = provided.get("result" if field == "result_type" else field)
        if permission == "Keep" or supplied is None:
            same(before[field], after[field], field)
        elif field == "result_type":
            # Limited characterized profiles; conversion is Python adapter work,
            # not an implemented Rust serialization contract.
            check(
                isinstance(supplied, (dict, str)),
                "uncharacterized synthetic result profile",
            )
            expected = "json" if isinstance(supplied, dict) else "html" if supplied.strip().startswith("<") else "text"
            same(expected, after[field], field)
        elif field == "execution_context":
            # The real failure consumer may supply an already-merged dictionary.
            # Fixture has no server-owned Teams subscription; compare observed
            # supplied data, never reconstruct consumer merge/classification.
            same(supplied, after[field], field)
        else:
            same(supplied, after[field], field)
        touched.add(field)
    check(plan["metrics"] in {"Keep", "AllowSupplied"}, "unknown metric permission")
    for field in (
        "peak_memory_bytes",
        "process_rss_bytes",
        "cpu_user_seconds",
        "cpu_system_seconds",
        "cpu_total_seconds",
    ):
        metrics = provided.get("metrics")
        if plan["metrics"] == "Keep" or metrics is None or field not in metrics:
            same(before[field], after[field], field)
        else:
            same(metrics[field], after[field], field)
        touched.add(field)
    check(plan["logs"] in {"Keep", "AllowSupplied"}, "unknown log permission")
    # No buffered log/source adapter is implemented in this package. Observe
    # and enforce the genuinely empty fixture log rows; do not claim log parity.
    same(observation.before["logs"], observation.after["logs"], "unbuffered logs")
    return touched


def compare(observation: StepObservation) -> None:
    outcome = observation.response["outcome"]
    if outcome["kind"] == "rejected":
        reason = outcome["reason"]
        if reason in {"RequiresCoordinatorPolicy", "LegacyUnfencedOutsideTrackedPath"}:
            # Actual reference still runs; retained effects are characterization,
            # not agreement with a nonexistent combined domain field plan.
            check(
                observation.reference == "returned",
                "outside-plan reference did not run",
            )
            return
        check(
            observation.reference in {"rejected", "returned", "missing_fence"},
            "Python accepted a rejected plan",
        )
        if reason == "MissingFence":
            check(
                observation.reference == "missing_fence",
                "missing fence precedence differs",
            )
        same(observation.before, observation.after, "rejected authoritative snapshot")
        check(
            not observation.projection_calls and not observation.events,
            "rejected result had projection or fan-out",
        )
        return
    plan = outcome["plan"]
    request_rows = observation.request["rows"]
    execution_id = observation.request["operation"]["execution_id"]
    execution_before = row(observation.before, "executions", execution_id)
    execution_after = row(observation.after, "executions", execution_id)
    attempt_id = request_rows["attempt"]["id"] if request_rows["attempt"] is not None else None
    changed_execution: set[str] = set()
    changed_attempt: str | None = None
    if plan["kind"] == "running":
        check(
            observation.reference == "accepted" and attempt_id is not None,
            "Python running acceptance differs",
        )
        attempt_fields(
            plan,
            row(observation.before, "attempts", attempt_id),
            row(observation.after, "attempts", attempt_id),
            observation,
        )
        changed_attempt = attempt_id
    elif plan["kind"] == "result":
        check(
            observation.reference == "returned" and attempt_id is not None,
            "Python result acceptance differs",
        )
        before = row(observation.before, "attempts", attempt_id)
        after = row(observation.after, "attempts", attempt_id)
        attempt_fields(plan["attempt"], before, after, observation)
        check(after["completed_at"] is not None, "accepted result did not finalize")
        changed_attempt = attempt_id
        logical = plan["execution"]
        check(logical["status"] in LOGICAL, "unknown logical plan status")
        same(logical["status"], execution_after["status"], "logical status")
        scalar(
            logical["duration_ms"],
            execution_before["duration_ms"],
            execution_after["duration_ms"],
            observation.arguments["duration_ms"],
            "logical duration",
        )
        clock(
            logical["completed_at"],
            execution_before["completed_at"],
            execution_after["completed_at"],
            observation,
            "logical completion",
        )
        if logical["completed_at"] == "Now":
            check(
                after["completed_at"] <= execution_after["completed_at"],
                "logical/attempt clock order differs",
            )
        changed_execution = projection_fields(logical, execution_before, execution_after, observation)
    else:
        check(observation.reference == "accepted", "Python cancel acceptance differs")
        check(plan["status"] in LOGICAL, "unknown cancel status")
        same(plan["status"], execution_after["status"], "cancel status")
        clock(
            plan["completed_at"],
            execution_before["completed_at"],
            execution_after["completed_at"],
            observation,
            "cancel completion",
        )
        changed_execution = {"status", "completed_at"}
        if plan["attempt"] is not None:
            check(attempt_id is not None, "cancel plan has no actual selected attempt")
            before = row(observation.before, "attempts", attempt_id)
            after = row(observation.after, "attempts", attempt_id)
            attempt_fields(plan["attempt"], before, after, observation)
            same(
                after["completed_at"],
                execution_after["completed_at"],
                "queued cancel time sample",
            )
            changed_attempt = attempt_id
    unchanged(execution_before, execution_after, changed_execution)
    for table in ("executions", "attempts", "logs"):
        before_rows = observation.before[table]
        after_rows = observation.after[table]
        check(
            [r["id"] for r in before_rows] == [r["id"] for r in after_rows],
            "operation changed row identity/count",
        )
        for before, after in zip(before_rows, after_rows, strict=True):
            if table == "executions" and str(before["id"]) == execution_id:
                continue
            if table == "attempts" and str(before["id"]) == changed_attempt:
                continue
            same(before, after, "unselected committed row")
    same(observation.before["history"], observation.after["history"], "recorded history")


CLAIM_IDS = (
    "claim-missing",
    "claim-allocation-history",
    "claim-worker-null",
    "claim-worker-empty",
    "claim-delivery-valid",
    "claim-delivery-wrong-token",
    "claim-delivery-expired",
    "claim-delivery-nonclaimed",
    "claim-advisory-blocker",
    "claim-logical-scheduled",
    "claim-logical-pending",
    "claim-logical-running",
    "claim-logical-success",
    "claim-logical-failed",
    "claim-logical-timeout",
    "claim-logical-stuck",
    "claim-logical-completed_with_errors",
    "claim-logical-cancelling",
    "claim-logical-cancelled",
    "claim-attempt-dispatching-null",
    "claim-attempt-dispatching-present",
    "claim-attempt-published-null",
    "claim-attempt-published-present",
    "claim-attempt-claimed-null",
    "claim-attempt-claimed-present",
    "claim-attempt-running-null",
    "claim-attempt-running-present",
    "claim-history-succeeded-null",
    "claim-history-succeeded-present",
    "claim-history-failed-null",
    "claim-history-failed-present",
    "claim-history-timed_out-null",
    "claim-history-timed_out-present",
    "claim-history-cancelled-null",
    "claim-history-cancelled-present",
    "claim-history-worker_lost-null",
    "claim-history-worker_lost-present",
    "claim-history-admission_rejected-null",
    "claim-history-admission_rejected-present",
    "claim-phase-dispatch-unstarted",
    "claim-phase-dispatch-started",
    "claim-phase-queue-unstarted",
    "claim-phase-queue-started",
    "claim-phase-claim-unstarted",
    "claim-phase-claim-started",
    "claim-phase-admission-unstarted",
    "claim-phase-admission-started",
    "claim-phase-execution-unstarted",
    "claim-phase-execution-started",
    "claim-phase-result-unstarted",
    "claim-phase-result-started",
    "claim-phase-terminal-unstarted",
    "claim-phase-terminal-started",
)

CLAIM_FIXTURE = Path(__file__).parent / "fixtures/workflow-claim-v1.json"
CLAIM_SOCKET = EVIDENCE / "frontend.sock"
CLAIM_CONTROL_SCHEMA = "bifrost.test.claim-frontend-control/v1"
CLAIM_ACTOR: ContextVar[str | None] = ContextVar("claim_characterization_actor", default=None)


def load_claim_cases() -> list[dict[str, Any]]:
    with CLAIM_FIXTURE.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    check(len(data) <= 1024 * 1024, "claim fixture size exceeded")
    root = unique_json(data)
    closed(root, {"schema", "scenarios"})
    check(root["schema"] == "bifrost.test.workflow-claim-fixtures/v1", "claim fixture schema")
    scenarios = root["scenarios"]
    check(isinstance(scenarios, list), "claim scenarios shape")
    check(tuple(item["case_id"] for item in scenarios) == CLAIM_IDS, "claim scenario membership")
    # The candidate receipt admits these exact bytes, including every scenario
    # field. Categories choose venues, never supply expected final plans.
    for item in scenarios:
        closed(
            item,
            {
                "case_id",
                "execution_selector",
                "arrangement",
                "execution_path",
                "native_comparison",
                "behavior_category",
                "sql_seed",
                "direct_worker_values",
            },
        )
        closed(
            item["arrangement"],
            {
                "status",
                "attempt_status",
                "attempt_token_present",
                "attempt_phase",
                "started_at_present",
                "completed_history_numbers",
                "delivery",
            },
        )
    return scenarios


def claim_control(value: Any, phase: str, seq: int) -> dict[str, Any]:
    closed(value, {"schema", "phase", "seq"})
    check(value["schema"] == CLAIM_CONTROL_SCHEMA, "claim control schema")
    check(value["phase"] == phase, "claim control phase")
    check(type(value["seq"]) is int and value["seq"] == seq, "claim control sequence")
    return value


def claim_frame(value: dict[str, Any]) -> bytes:
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    check(len(data) <= 1024, "claim control frame bound")
    return data


class ClaimFrontendBroker:
    """Loop-neutral test-only observer; no engine or connection is created here."""

    def __init__(self) -> None:
        from sqlalchemy import event
        from sqlalchemy.orm import Session
        from src.core import database

        self.database = database
        self.event = event
        self.session_class = Session
        self.original_constructor = database.create_async_engine
        self.original_close = database.close_db
        self.original_get_engine = database.get_engine
        self.failure: BaseException | None = None
        self.socket: socket.socket | None = None
        self.socket_closed = False
        self.engines: list[Any] = []
        self.drivers: list[Any] = []
        self.listeners: list[tuple[Any, str, Any]] = []
        self.contexts: dict[str, dict[str, Any]] = {}
        self.pending: set[str] = set()
        self.finished: set[str] = set()
        self.granted = False
        self.endpoint: tuple[str, int] | None = None
        self.bytes_sent = 0
        self.bytes_received = 0
        self.wrapper_constructor = self.constructor
        self.wrapper_close = self.close_db
        self.session_callback = self.after_begin
        self.installed = False
        self.close_calls: list[dict[str, Any]] = []

    def poison(self, error: BaseException) -> None:
        if self.failure is None:
            self.failure = error

    def install(self) -> None:
        receipt = verify_receipt()
        for function in (self.original_get_engine, self.original_close):
            claim_loaded_function(function, self.database, "api/src/core/database.py", receipt)
        from sqlalchemy.ext.asyncio import create_async_engine

        check(self.original_constructor is create_async_engine, "claim original constructor binding differs")
        check(not self.installed, "claim observer already installed")
        self.installed = True
        self.database.create_async_engine = self.wrapper_constructor
        self.database.close_db = self.wrapper_close
        self.listeners.append((self.session_class, "after_begin", self.session_callback))
        self.event.listen(self.session_class, "after_begin", self.session_callback)

    def exchange(self, value: dict[str, Any], phase: str, seq: int) -> None:
        if self.failure is not None:
            raise self.failure
        try:
            self.exchange_once(value, phase, seq)
        except BaseException as error:
            self.poison(error)
            raise

    def exchange_once(self, value: dict[str, Any], phase: str, seq: int) -> None:
        end = time.monotonic() + 20
        if self.socket is None:
            actual = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket = actual
            actual.setblocking(False)
            with suppress(BlockingIOError):
                actual.connect(str(CLAIM_SOCKET))
            _, writable, _ = io_select.select([], [actual], [], max(0, end - time.monotonic()))
            check(
                bool(writable) and actual.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0,
                "claim frontend connect failed",
            )
        actual = self.socket
        raw = claim_frame(value)
        offset = 0
        while offset < len(raw):
            check(time.monotonic() < end, "claim frontend deadline")
            _, writable, _ = io_select.select([], [actual], [], max(0, end - time.monotonic()))
            check(bool(writable), "claim frontend write deadline")
            count = actual.send(raw[offset:])
            check(type(count) is int and 0 < count <= len(raw) - offset, "claim frontend write progress")
            offset += count
            self.bytes_sent += count
            check(self.bytes_sent <= 4096, "claim frontend aggregate sent bound")
        received = bytearray()
        while not received.endswith(b"\n"):
            check(time.monotonic() < end, "claim frontend deadline")
            readable, _, _ = io_select.select([actual], [], [], max(0, end - time.monotonic()))
            check(bool(readable), "claim frontend read deadline")
            chunk = actual.recv(1)
            check(bool(chunk), "claim frontend partial EOF")
            received.extend(chunk)
            self.bytes_received += len(chunk)
            check(len(received) <= 1024 and self.bytes_received <= 4096, "claim frontend frame bound")
        decoded = decode_claim_frame(bytes(received))
        claim_control(decoded, phase, seq)
        readable, _, _ = io_select.select([actual], [], [], 0)
        check(not readable, "claim frontend extra stream")

    def constructor(self, *args: Any, **kwargs: Any) -> Any:
        actor = CLAIM_ACTOR.get()
        if actor is None:
            return self.original_constructor(*args, **kwargs)
        if self.failure is not None:
            raise self.failure
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        try:
            check(caller is not None and caller.f_code is self.original_get_engine.__code__, "claim constructor caller")
            check(caller.f_globals is self.database.__dict__, "claim constructor globals")
            check(
                self.original_get_engine.__code__.co_filename == self.database.__file__,
                "claim constructor code source spelling",
            )
            receipt = verify_receipt()
            check(
                sha_file(Path(self.database.__file__)) == receipt["source_sha256"]["api/src/core/database.py"],
                "claim loaded database source differs",
            )
            check(
                Path(self.database.__file__).resolve(strict=True)
                == (API_ROOT / "src/core/database.py").resolve(strict=True),
                "claim constructor source origin",
            )
            settings = caller.f_locals["settings"]
            db_url = caller.f_locals["db_url"]
            connect_args = caller.f_locals["connect_args"]
            check(args == (db_url,), "claim constructor URL association")
            check(
                set(kwargs)
                == {
                    "echo",
                    "pool_size",
                    "max_overflow",
                    "pool_pre_ping",
                    "pool_timeout",
                    "pool_recycle",
                    "connect_args",
                },
                "claim constructor option scope",
            )
            check(
                kwargs
                == {
                    "echo": settings.debug,
                    "pool_size": settings.database_pool_size,
                    "max_overflow": settings.database_max_overflow,
                    "pool_pre_ping": True,
                    "pool_timeout": 30,
                    "pool_recycle": 1800,
                    "connect_args": connect_args,
                },
                "claim constructor options differ",
            )
            from sqlalchemy.engine import make_url

            prepared = make_url(db_url)
            check(
                prepared.drivername == "postgresql+asyncpg" and not prepared.query,
                "claim constructor unsupported URL profile",
            )
            check(
                prepared.host is not None and prepared.port is not None, "claim constructor explicit endpoint missing"
            )
            check(
                prepared.username and prepared.password and prepared.database,
                "claim constructor explicit credentials missing",
            )
            check(type(connect_args) is dict and set(connect_args) <= {"ssl"}, "claim constructor connect args")
            policy = connect_args.get("ssl")
            if isinstance(policy, ssl.SSLContext):
                check(
                    policy.protocol == ssl.PROTOCOL_TLS_CLIENT
                    and policy.verify_mode == ssl.CERT_REQUIRED
                    and policy.check_hostname is True,
                    "claim weakened TLS context",
                )
            else:
                check(policy is None or policy == "prefer", "claim unsupported TLS policy")
            expected = {
                "host": prepared.host,
                "port": prepared.port,
                "user": prepared.username,
                "password": prepared.password,
                "database": prepared.database,
                **connect_args,
            }
            # No live frame reference crosses constructor forwarding or IPC.
            caller = None
            frame = None
            engine = self.original_constructor(*args, **kwargs)
            self.engines.append(engine)
            qualify_claim_dependencies()
            check(
                len(self.engines) <= 128 and actor not in self.contexts, "claim constructor capture bound or duplicate"
            )
            record = {
                "engine": engine,
                "expected": expected,
                "connections": [],
                "settings": settings,
                "connect_records": [],
            }
            self.contexts[actor] = record

            def do_connect(_dialect: Any, _connection_record: Any, cargs: Any, cparams: Any) -> None:
                try:
                    record["connect_records"].append(_connection_record)
                    check(len(record["connect_records"]) <= 128, "claim preconnect capture bound")
                    check(CLAIM_ACTOR.get() == actor, "claim preconnect actor")
                    check(
                        cargs == [] and type(cparams) is dict and cparams == expected,
                        "claim preconnect parameters differ",
                    )
                    if isinstance(policy, ssl.SSLContext):
                        check(cparams["ssl"] is policy, "claim preconnect TLS identity")
                except BaseException as error:
                    self.poison(error)
                    raise

            self.listeners.append((engine.sync_engine, "do_connect", do_connect))
            self.event.listen(engine.sync_engine, "do_connect", do_connect)

            def pool_connect(_dbapi_connection: Any, connection_record: Any) -> None:
                try:
                    driver = connection_record.driver_connection
                    if not any(driver is retained for retained in self.drivers):
                        retain_claim_driver(self, driver)
                    from asyncpg import Connection

                    check(len(self.drivers) <= 512 and isinstance(driver, Connection), "claim pool driver association")
                    check(
                        CLAIM_ACTOR.get() == actor
                        and any(connection_record is retained for retained in record["connect_records"]),
                        "claim physical connect record association",
                    )
                except BaseException as error:
                    self.poison(error)
                    raise

            self.listeners.append((engine.sync_engine, "connect", pool_connect))
            self.event.listen(engine.sync_engine, "connect", pool_connect)
            record["advisory_dispatched"] = False

            def before_cursor(
                _connection: Any, _cursor: Any, _statement: Any, _parameters: Any, context: Any, _executemany: Any
            ) -> None:
                if CLAIM_ACTOR.get() != actor:
                    return
                clause = getattr(getattr(context, "compiled", None), "statement", None)
                if getattr(clause, "text", None) == (
                    "SELECT pg_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || :execution_id))"
                ):
                    parameters = getattr(context, "compiled_parameters", None)
                    check(
                        type(parameters) is list
                        and len(parameters) == 1
                        and type(parameters[0]) is dict
                        and parameters[0].get("execution_id") == record.get("selected_execution_id")
                        and record.get("selected_execution_id") is not None,
                        "claim advisory selected execution association",
                    )
                    record["advisory_dispatched"] = True

            self.listeners.append((engine.sync_engine, "before_cursor_execute", before_cursor))
            self.event.listen(engine.sync_engine, "before_cursor_execute", before_cursor)
            endpoint = claim_endpoint(prepared.host, prepared.port)
            if self.endpoint is None:
                self.endpoint = endpoint
                self.exchange(
                    {
                        "schema": CLAIM_CONTROL_SCHEMA,
                        "phase": "before",
                        "seq": 1,
                        "container_pid": os.getpid(),
                        "host": endpoint[0],
                        "port": endpoint[1],
                        "source_role": "original_pooled_constructor",
                    },
                    "before",
                    2,
                )
                self.granted = True
            else:
                check(endpoint == self.endpoint and self.granted, "claim subsequent endpoint differs")
            return engine
        except BaseException as error:
            self.poison(error)
            raise
        finally:
            del caller
            del frame

    def after_begin(self, session: Any, _transaction: Any, connection: Any) -> None:
        actor = CLAIM_ACTOR.get()
        if actor is None:
            return
        try:
            record = self.contexts.get(actor)
            driver = connection.connection.driver_connection
            if not any(driver is previous for previous in self.drivers):
                retain_claim_driver(self, driver)
                check(len(self.drivers) <= 512, "claim driver capture bound")
            check(record is not None and self.granted, "claim session constructor absent")
            engine = record["engine"]
            check(
                session.bind is engine.sync_engine and connection.engine is engine.sync_engine,
                "claim session engine association",
            )
            check(session.autoflush is False and session.expire_on_commit is False, "claim session factory options")
            factory = self.database._async_session_factory
            check(factory is not None and factory.kw["bind"] is engine, "claim original factory association")
            from asyncpg import Connection

            check(isinstance(driver, Connection), "claim actual driver class")
            record["connections"].append(driver)
        except BaseException as error:
            self.poison(error)
            raise

    async def close_db(self, *args: Any, **kwargs: Any) -> None:
        observation: dict[str, Any] | None = None
        observation_error: BaseException | None = None
        try:
            observation = {
                "engine_before": self.database._engine,
                "factory_before": self.database._async_session_factory,
                "returned": False,
                "error": None,
            }
            self.close_calls.append(observation)
            check(len(self.close_calls) <= 512, "claim close observation bound")
        except BaseException as error:
            observation_error = error
            self.poison(error)
        # Observation failure cannot suppress the incumbent cleanup invocation.
        try:
            await self.original_close(*args, **kwargs)
        except BaseException as error:
            if observation is not None:
                observation["error"] = error
            self.poison(error)
            raise
        else:
            try:
                check(observation is not None, "claim close initial observation absent")
                observation["engine_after"] = self.database._engine
                observation["factory_after"] = self.database._async_session_factory
                observation["returned"] = True
            except BaseException as error:
                self.poison(error)
                if observation_error is None:
                    observation_error = error
            if observation_error is not None:
                raise observation_error

    def finish(self) -> None:
        first = self.failure
        try:
            validate_claim_settlement(self)
            self.exchange({"schema": CLAIM_CONTROL_SCHEMA, "phase": "after", "seq": 3}, "after", 4)
        except BaseException as error:
            if first is None:
                first = error
            self.poison(error)
        finally:
            for target, name, callback in reversed(self.listeners):
                try:
                    self.event.remove(target, name, callback)
                except BaseException as error:
                    self.poison(error)
                    if first is None:
                        first = error
            for name, wrapper, original in [
                ("close_db", self.wrapper_close, self.original_close),
                ("create_async_engine", self.wrapper_constructor, self.original_constructor),
            ]:
                try:
                    check(getattr(self.database, name) is wrapper, "claim foreign restored alias")
                    setattr(self.database, name, original)
                except BaseException as error:
                    self.poison(error)
                    if first is None:
                        first = error
            if self.socket is not None:
                try:
                    self.socket.close()
                    self.socket_closed = True
                except BaseException as error:
                    self.poison(error)
                    if first is None:
                        first = error
        if first is not None:
            raise first


def validate_claim_response(outcome: Any) -> None:
    check(isinstance(outcome, dict), "claim outcome shape")
    kind = outcome.get("kind")
    if kind == "no_claim":
        closed(outcome, {"kind"})
    elif kind == "deferred":
        closed(outcome, {"kind", "reason"})
        check(outcome["reason"] in {"DeferLegacyInline", "DeferAttemptAllocation"}, "claim defer reason")
    elif kind == "rejected":
        closed(outcome, {"kind", "reason"})
        check(outcome["reason"] in REASONS, "claim rejection reason")
    else:
        closed(outcome, {"kind", "plan"})
        check(kind == "accepted", "claim outcome kind")
        plan = outcome["plan"]
        closed(
            plan,
            {
                "kind",
                "logical_status",
                "attempt_status",
                "attempt_phase",
                "claim_token",
                "worker_id",
                "worker_incarnation_id",
                "claimed_at",
                "heartbeat_at",
            },
        )
        check(plan["kind"] == "claim", "claim plan kind")
        check(
            plan["logical_status"] in LOGICAL
            and plan["attempt_status"] in ATTEMPT_ENUM.values()
            and plan["attempt_phase"] in PHASE_ENUM.values(),
            "claim plan status enum",
        )
        check(plan["claim_token"] == "SetParentNonNull", "claim token directive")
        for name in ("worker_id", "worker_incarnation_id"):
            check(plan[name] in {"Keep", "SetSupplied"}, "claim worker directive")
        for name in ("claimed_at", "heartbeat_at"):
            check(plan[name] in {"Keep", "Now"}, "claim clock directive")


class ClaimCohort(WorkflowCohort):
    """Own only fresh legal rows; call the original consumer or helper unchanged."""

    def __init__(self, engine: Any, scenario: dict[str, Any], broker: ClaimFrontendBroker):
        super().__init__(engine, {"arrangement": {"status": scenario["arrangement"]["status"]}})
        self.scenario = scenario
        self.broker = broker
        self.lease = None
        self.delivery_id = uuid4()
        self.worker_values: tuple[Any, Any] | None = None
        self.reference_error: str | None = None
        self.returned_token: UUID | None = None
        self.token_text: str | None = None
        self.closed = False
        self.original_task: asyncio.Task[Any] | None = None
        self.blocker = None
        self.before: dict[str, Any] | None = None
        self.after: dict[str, Any] | None = None
        self.start: datetime | None = None
        self.end: datetime | None = None
        self.seed_pins: dict[str, dict[str, Any]] = {}

    async def seed(self) -> None:
        from src.models.orm.work_deliveries import WorkDelivery
        from src.services.work_delivery_store import DeliveryLease

        arrangement = self.scenario["arrangement"]
        check(self.scenario["execution_path"] != "none_native_boundary_only", "claim impossible SQL fixture")
        async with self.sessions() as db:
            db.add(Organization(id=self.ids["org"], name="synthetic-claim", created_by="synthetic-claim"))
            await db.flush()
            user = User(
                id=self.ids["user"],
                email=f"{self.ids['user']}@synthetic.example.test",
                name="Synthetic claim",
                organization_id=self.ids["org"],
                is_active=True,
                is_superuser=False,
            )
            db.add(user)
            await db.flush()
            import sys

            from src.services.execution.attempts import _policy_digest

            claim_loaded_function(
                _policy_digest,
                sys.modules[_policy_digest.__module__],
                "api/src/services/execution/attempts.py",
                verify_receipt(),
            )
            for role, status in (("execution", arrangement["status"]), ("foreign", "Running")):
                execution = Execution(
                    id=self.ids[role],
                    status=ExecutionStatus(status),
                    workflow_name="synthetic-claim",
                    executed_by=user.id,
                    executed_by_name=user.name,
                    organization_id=self.ids["org"],
                    attempt_tracking_version="v1",
                    parameters={"synthetic": True},
                    started_at=SEED_TIME,
                    completed_at=SEED_TIME,
                    result={"retained": True},
                    result_type="text",
                    error_message="synthetic-retained-error",
                    variables={"retained": True},
                    execution_context={"retained": True},
                    duration_ms=17,
                    peak_memory_bytes=4096,
                    process_rss_bytes=2048,
                    cpu_user_seconds=0.5,
                    cpu_system_seconds=0.25,
                    cpu_total_seconds=0.75,
                    time_saved=7,
                    value=Decimal("12.50"),
                    runtime_mode="repo-v1",
                    runtime_evidence_hash="sha256:"
                    + hashlib.sha256(("synthetic-runtime-" + role).encode()).hexdigest(),
                    dispatch_evidence_hash="sha256:"
                    + hashlib.sha256(("synthetic-dispatch-" + role).encode()).hexdigest(),
                )
                db.add(execution)
                self.seed_pins[role] = {"execution": execution}
            await db.flush()
            for values in self.seed_pins.values():
                execution = values.pop("execution")
                values.update(
                    {
                        "runtime_mode": execution.runtime_mode,
                        "runtime_evidence_hash": execution.runtime_evidence_hash,
                        "dispatch_evidence_hash": execution.dispatch_evidence_hash,
                        # Genuine original pure derivation, not a copied digest
                        # algorithm or an expected native plan/allocation oracle.
                        "policy_digest": _policy_digest(execution),
                    }
                )
            self.seed_claim_attempt(
                db,
                self.ids["foreign_attempt"],
                1,
                "claimed",
                True,
                "claim",
                False,
                False,
                execution_role="foreign",
            )
            for number in arrangement["completed_history_numbers"]:
                self.seed_claim_attempt(db, uuid4(), number, "succeeded", True, "terminal", False, True)
            status = arrangement["attempt_status"]
            if status is not None:
                completed = status not in {"dispatching", "published", "claimed", "running"}
                self.seed_claim_attempt(
                    db,
                    self.ids["attempt"],
                    1,
                    status,
                    arrangement["attempt_token_present"],
                    arrangement["attempt_phase"],
                    arrangement["started_at_present"],
                    completed,
                )
            delivery = arrangement["delivery"]
            if delivery != "absent":
                now = await db.scalar(select(func.clock_timestamp()))
                from datetime import timedelta

                lease_token = uuid4()
                db.add(
                    WorkDelivery(
                        id=self.delivery_id,
                        queue_name="workflow_execution",
                        message_id=str(self.delivery_id),
                        encrypted_envelope="synthetic-unread-envelope",
                        status="queued" if delivery == "nonclaimed" else "claimed",
                        claim_count=1,
                        available_at=now,
                        created_at=now,
                        lease_owner=None if delivery == "nonclaimed" else "synthetic-claim-owner",
                        lease_token=None if delivery == "nonclaimed" else lease_token,
                        lease_expires_at=None
                        if delivery == "nonclaimed"
                        else cast(datetime, now) + timedelta(seconds=-1 if delivery == "expired" else 60),
                    )
                )
                self.lease = DeliveryLease(
                    self.delivery_id,
                    uuid4() if delivery == "wrong_token" else lease_token,
                    "workflow_execution",
                    str(self.delivery_id),
                    {},
                    1,
                )
            await db.commit()
        # The genuine source factory construction is observed before first
        # production SQL; fixture NullPool seeding above is not substituted.
        actor_token = CLAIM_ACTOR.set(self.scenario["case_id"])
        try:
            factory = get_session_factory()
            record = self.broker.contexts.get(self.scenario["case_id"])
            check(record is not None and factory.kw["bind"] is record["engine"], "claim seed factory unobserved")
        finally:
            CLAIM_ACTOR.reset(actor_token)
        await self.pubsub.subscribe(self.barrier)
        await self.pubsub.psubscribe("bifrost:*")
        for kind in ("subscribe", "psubscribe"):
            ack = await self.pubsub.get_message(timeout=5)
            check(bool(ack) and ack["type"] == kind, "claim event subscription failed")

    def seed_claim_attempt(
        self,
        db: Any,
        identity: UUID,
        number: int,
        status: str,
        token_present: bool,
        phase: str,
        started: bool,
        completed: bool,
        *,
        execution_role: str = "execution",
    ) -> None:
        from datetime import timedelta

        token = uuid4() if token_present else None
        db.add(
            WorkflowExecutionAttempt(
                id=identity,
                execution_id=self.ids[execution_role],
                attempt_number=number,
                **self.seed_pins[execution_role],
                claim_token=token,
                status=status,
                phase=phase,
                published_at=None if status == "dispatching" else SEED_TIME,
                claimed_at=SEED_TIME + timedelta(days=1) if token_present else None,
                heartbeat_at=SEED_TIME + timedelta(days=2),
                started_at=SEED_TIME + timedelta(days=3) if started else None,
                completed_at=SEED_TIME + timedelta(days=4) if completed else None,
                process_id="synthetic-retained-process",
                worker_id="synthetic-old-worker",
                worker_incarnation_id=uuid4(),
                failure_phase="execution",
                failure_code="synthetic-retained-code",
                duration_ms=19,
                peak_memory_bytes=1024,
                cpu_total_seconds=0.125,
            )
        )

    async def snapshot(self) -> dict[str, Any]:
        from src.models.orm.work_deliveries import WorkDelivery

        result = await super().snapshot()
        async with self.sessions() as db:
            delivery = await db.get(WorkDelivery, self.delivery_id)
            result["deliveries"] = (
                []
                if delivery is None
                else [{column.name: getattr(delivery, column.name) for column in WorkDelivery.__table__.columns}]
            )
        return result

    def claim_request(self, before: dict[str, Any]) -> dict[str, Any]:
        selected_id = self.ids["missing"] if self.scenario["execution_selector"] == "missing" else self.ids["execution"]
        logical = next((item for item in before["executions"] if item["id"] == selected_id), None)
        active = [
            item for item in before["attempts"] if item["execution_id"] == selected_id and item["completed_at"] is None
        ]
        check(len(active) <= 1, "claim active selection ambiguous")
        attempt = active[0] if active else None
        return {
            "schema": SCHEMA,
            "case_id": self.scenario["case_id"],
            "operation": {"kind": "claim", "execution_id": str(selected_id)},
            "rows": {
                "execution": None
                if logical is None
                else {"id": str(logical["id"]), "status": enum_value(logical["status"])},
                "attempt": None
                if attempt is None
                else {
                    "id": str(attempt["id"]),
                    "execution_id": str(attempt["execution_id"]),
                    "claim_token": None if attempt["claim_token"] is None else str(attempt["claim_token"]),
                    "status": attempt["status"],
                    "phase": attempt["phase"],
                    "started_at_present": attempt["started_at"] is not None,
                    "completed_at_present": attempt["completed_at"] is not None,
                },
                "history": "recorded" if before["history"][str(selected_id)] else "unrecorded",
            },
        }

    async def original_claim(self, consumer: Any) -> Any:
        import sys

        from src.services.execution.attempts import create_claimed_attempt
        from src.services.work_delivery_store import current_delivery

        claim_loaded_function(
            create_claimed_attempt,
            sys.modules[create_claimed_attempt.__module__],
            "api/src/services/execution/attempts.py",
            verify_receipt(),
        )
        self.worker_values = (consumer._pool.worker_id, consumer._pool.worker_incarnation_id)
        context = current_delivery.set(self.lease)
        actor = CLAIM_ACTOR.set(self.scenario["case_id"])
        try:
            if self.scenario["execution_path"] == "original_create_claimed_attempt_owned_session":
                values = self.scenario["direct_worker_values"]
                self.worker_values = (values["worker_id"], values["worker_incarnation_id"])
                # Use original production context/factory for genuine helper.
                async with self.broker.database.get_db_context() as db:
                    execution = await db.get(Execution, self.ids["execution"], with_for_update=True)
                    result = await create_claimed_attempt(
                        db, execution, worker_id=self.worker_values[0], worker_incarnation_id=self.worker_values[1]
                    )
                    await db.commit()
                    return str(result.claim_token)
            selected = (
                self.ids["missing"] if self.scenario["execution_selector"] == "missing" else self.ids["execution"]
            )
            self.broker.contexts[self.scenario["case_id"]]["selected_execution_id"] = str(selected)
            return await consumer._claim_durable_execution(str(selected))
        finally:
            CLAIM_ACTOR.reset(actor)
            current_delivery.reset(context)

    async def run_claim(self) -> dict[str, Any]:
        from src.services.work_delivery_store import DeliveryOwnershipLost

        self.before = await self.snapshot()
        request = self.claim_request(self.before)
        native_venue = self.scenario["native_comparison"] in {
            "actual_planner",
            "actual_planner_defer_vs_reference_allocation",
        }
        response = await invoke_driver(request) if native_venue else None
        if response is not None:
            validate_claim_response(response["outcome"])
        receipt = verify_receipt()
        for function in (
            consumer_module.WorkflowExecutionConsumer.__init__,
            consumer_module.WorkflowExecutionConsumer._claim_durable_execution,
        ):
            claim_loaded_function(function, consumer_module, "api/src/jobs/consumers/workflow_execution.py", receipt)
        async with real_consumer() as consumer:
            self.start = datetime.now(UTC)
            try:
                if self.scenario["case_id"] == "claim-advisory-blocker":
                    self.blocker = self.sessions()
                    await self.blocker.begin()
                    await consumer._lock_execution(self.blocker, str(self.ids["execution"]))
                    self.original_task = asyncio.create_task(self.original_claim(consumer))
                    # A recorded original advisory statement, not a time-based
                    # sleep, must show genuine invocation blocked before release.
                    record = self.broker.contexts[self.scenario["case_id"]]
                    while not record["advisory_dispatched"] and not self.original_task.done():
                        await asyncio.sleep(0)
                    if self.original_task.done():
                        await self.original_task
                    check(
                        record["advisory_dispatched"] and not self.original_task.done(),
                        "claim advisory blocker did not block",
                    )
                    await self.blocker.rollback()
                    result = await self.original_task
                else:
                    result = await self.original_claim(consumer)
                self.token_text = result
                if result not in {None, ""}:
                    self.returned_token = UUID(result)
            except DeliveryOwnershipLost:
                self.reference_error = "DeliveryOwnershipLost"
            except ValueError as error:
                check(
                    type(error) is ValueError and str(error) == "execution already has an active attempt",
                    "claim unexpected ValueError",
                )
                self.reference_error = "InvalidAttemptState"
            finally:
                self.end = datetime.now(UTC)
        self.after = await self.snapshot()
        compare_claim(self, request, response)
        check(not await self.events(), "claim reference unexpectedly published")
        return {"request": request, "response": response}

    async def close(self) -> None:
        from src.models.orm.work_deliveries import WorkDelivery

        first: BaseException | None = None
        if self.original_task is not None:
            try:
                if not self.original_task.done():
                    self.original_task.cancel()
                await self.original_task
            except asyncio.CancelledError:
                pass
            except BaseException as error:
                first = error
        if self.blocker is not None:
            try:
                await self.blocker.close()
            except BaseException as error:
                if first is None:
                    first = error
        try:
            async with self.sessions() as db:
                await db.execute(delete(WorkDelivery).where(WorkDelivery.id == self.delivery_id))
                await db.commit()
        except BaseException as error:
            if first is None:
                first = error
        try:
            await super().close()
        except BaseException as error:
            if first is None:
                first = error
        if first is not None:
            self.broker.poison(first)
            raise first
        self.closed = True


def compare_claim(cohort: ClaimCohort, request: dict[str, Any], response: dict[str, Any] | None) -> None:
    before, after = cohort.before, cohort.after
    check(before is not None and after is not None, "claim snapshots absent")
    native_venue = cohort.scenario["native_comparison"] in {
        "actual_planner",
        "actual_planner_defer_vs_reference_allocation",
    }
    check((response is not None) == native_venue, "claim native venue response presence")
    outcome = response["outcome"] if response is not None else None
    if outcome is not None:
        validate_claim_response(outcome)
    kind = outcome["kind"] if outcome is not None else None
    delivery = cohort.scenario["arrangement"]["delivery"]
    if delivery in {"wrong_token", "expired", "nonclaimed"}:
        check(cohort.reference_error == "DeliveryOwnershipLost", "claim invalid delivery did not reject")
    else:
        check(cohort.reference_error != "DeliveryOwnershipLost", "claim valid or absent delivery lost ownership")
    if cohort.reference_error is not None:
        check(
            cohort.reference_error in {"InvalidAttemptState", "DeliveryOwnershipLost"}, "claim reference error category"
        )
        if cohort.reference_error == "InvalidAttemptState":
            check(kind == "rejected" and outcome["reason"] == "InvalidAttemptState", "claim rejection class differs")
        same(before, after, "claim failed authoritative snapshot")
        return
    if kind == "no_claim":
        check(cohort.token_text is None, "claim no-claim return differs")
        same(before, after, "claim no-claim snapshot")
        return
    if kind == "deferred" and outcome["reason"] == "DeferLegacyInline":
        check(cohort.token_text == "", "claim legacy return differs")
        same(before, after, "claim legacy snapshot")
        return
    helper = cohort.scenario["execution_path"] == "original_create_claimed_attempt_owned_session"
    check(
        cohort.returned_token is not None and cohort.start is not None and cohort.end is not None,
        "claim returned capability or clock absent",
    )
    execution_id = request["operation"]["execution_id"]
    logical_before = row(before, "executions", execution_id)
    logical_after = row(after, "executions", execution_id)
    same("Pending" if helper else "Running", enum_value(logical_after["status"]), "claim logical write")
    unchanged(logical_before, logical_after, set() if helper else {"status"})
    request_attempt = request["rows"]["attempt"]
    allocation = kind == "deferred" and outcome["reason"] == "DeferAttemptAllocation"
    if allocation:
        check(not helper and request_attempt is None, "claim allocation venue differs")
        existing_ids = {item["id"] for item in before["attempts"]}
        added = [item for item in after["attempts"] if item["id"] not in existing_ids]
        check(len(added) == 1 and added[0]["execution_id"] == UUID(execution_id), "claim allocation identity differs")
        active_after = added[0]
        history = [item["attempt_number"] for item in before["attempts"] if str(item["execution_id"]) == execution_id]
        same(max(history, default=0) + 1, active_after["attempt_number"], "claim historical maximum allocation")
        for field in ("runtime_mode", "runtime_evidence_hash", "dispatch_evidence_hash"):
            same(logical_before[field], active_after[field], "claim allocated source pin")
        same(
            cohort.seed_pins["execution"]["policy_digest"],
            active_after["policy_digest"],
            "claim reference allocation policy derivation",
        )
    else:
        check((kind == "accepted" or outcome is None) and request_attempt is not None, "claim acceptance class differs")
        plan = outcome["plan"] if outcome is not None else None
        active_before = row(before, "attempts", request_attempt["id"])
        active_after = row(after, "attempts", request_attempt["id"])
        if plan is not None:
            same(plan["logical_status"], "Running", "claim logical directive")
            same(plan["attempt_status"], active_after["status"], "claim attempt status directive")
            same(plan["attempt_phase"], active_after["phase"], "claim attempt phase directive")
        for field, supplied in zip(("worker_id", "worker_incarnation_id"), cohort.worker_values, strict=True):
            if plan is None:
                same(active_after[field], supplied, "claim reference worker write")
            else:
                scalar(plan[field], active_before[field], active_after[field], supplied, "claim " + field)
        for field in ("claimed_at", "heartbeat_at"):
            if plan is not None and plan[field] == "Keep":
                same(active_before[field], active_after[field], "claim " + field)
            else:
                check(cohort.start <= active_after[field] <= cohort.end, "claim clock window differs")
        unchanged(
            active_before,
            active_after,
            {"status", "phase", "claim_token", "worker_id", "worker_incarnation_id", "claimed_at", "heartbeat_at"},
        )
    same(active_after["claim_token"], cohort.returned_token, "claim returned token row association")
    check(str(cohort.returned_token) == cohort.token_text, "claim returned token canonical spelling")
    check(
        all(item["claim_token"] != cohort.returned_token for item in before["attempts"]), "claim capability not fresh"
    )
    same(active_after["claimed_at"], active_after["heartbeat_at"], "claim single time sample")
    same(active_after["status"], "claimed", "claim final status")
    same(active_after["phase"], "claim", "claim final phase")
    same(
        (active_after["worker_id"], active_after["worker_incarnation_id"]),
        cohort.worker_values,
        "claim supplied workers",
    )
    if allocation:
        check(cohort.start <= active_after["claimed_at"] <= cohort.end, "claim allocated clock window")
        same(active_after["published_at"], active_after["claimed_at"], "claim allocation publication sample")
    for table in ("executions", "attempts", "logs"):
        allowed_added = {active_after["id"]} if table == "attempts" and allocation else set()
        before_ids = {item["id"] for item in before[table]}
        after_ids = {item["id"] for item in after[table]}
        same(after_ids, before_ids | allowed_added, "claim row membership")
        for original in before[table]:
            if table == "executions" and str(original["id"]) == execution_id:
                continue
            if table == "attempts" and original["id"] == active_after["id"]:
                continue
            same(original, row(after, table, str(original["id"])), "claim unselected committed row")
    if cohort.scenario["arrangement"]["delivery"] == "valid":
        check(len(before["deliveries"]) == len(after["deliveries"]) == 1, "claim delivery row membership")
        unchanged(before["deliveries"][0], after["deliveries"][0], {"started_at"})
        check(after["deliveries"][0]["started_at"] is not None, "claim valid delivery not started")
    else:
        same(before["deliveries"], after["deliveries"], "claim unselected delivery")
    for table in ("reference_topic_events", "reference_org_metrics", "history"):
        if table == "history" and allocation:
            # Allocation changes the selected history presence, not foreign IDs.
            for identity, value in before[table].items():
                if identity != execution_id:
                    same(value, after[table][identity], "claim foreign history")
        else:
            same(before[table], after[table], "claim reference-only unchanged data")


def native_claim_request(scenario: dict[str, Any]) -> dict[str, Any]:
    check(scenario["execution_path"] == "none_native_boundary_only", "claim native-only venue")
    arrangement = scenario["arrangement"]
    execution_id = "00000000-0000-4000-8000-000000000001"
    return {
        "schema": SCHEMA,
        "case_id": scenario["case_id"],
        "operation": {"kind": "claim", "execution_id": execution_id},
        "rows": {
            "execution": {"id": execution_id, "status": arrangement["status"]},
            "attempt": {
                "id": "00000000-0000-4000-8000-000000000002",
                "execution_id": execution_id,
                "claim_token": "00000000-0000-4000-8000-000000000003" if arrangement["attempt_token_present"] else None,
                "status": arrangement["attempt_status"],
                "phase": arrangement["attempt_phase"],
                "started_at_present": arrangement["started_at_present"],
                "completed_at_present": False,
            },
            "history": "recorded",
        },
    }


def decode_claim_frame(data: bytes) -> dict[str, Any]:
    check(
        type(data) is bytes
        and 1 < len(data) <= 1024
        and data.endswith(b"\n")
        and b"\n" not in data[:-1]
        and b"\r" not in data,
        "claim frontend frame grammar",
    )

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            check(key not in result, "claim frontend duplicate field")
            result[key] = value
        return result

    def constant(_value: str) -> Any:
        raise AssertionError("claim frontend nonfinite constant")

    value = json.loads(data[:-1].decode("utf-8", errors="strict"), object_pairs_hook=pairs, parse_constant=constant)
    check(isinstance(value, dict), "claim frontend root shape")
    check(claim_frame(value) == data, "claim frontend noncompact frame")
    return value


def validate_claim_settlement(broker: Any) -> None:
    check(broker.failure is None and not broker.pending, "claim observer unsettled or failed")
    check(
        broker.finished
        == {item["case_id"] for item in load_claim_cases() if item["execution_path"] != "none_native_boundary_only"},
        "claim observer coverage missing",
    )
    check(
        bool(broker.drivers) and all(record["connections"] for record in broker.contexts.values()),
        "claim observer driver coverage",
    )
    for driver in broker.drivers:
        check(driver.is_closed() is True, "claim actual driver still open")
    check(
        broker.database.close_db is broker.wrapper_close
        and broker.database.create_async_engine is broker.wrapper_constructor,
        "claim foreign alias before finish",
    )
    check(
        all(broker.event.contains(target, name, callback) for target, name, callback in broker.listeners),
        "claim listener custody before finish",
    )
    check(
        broker.database._engine is None and broker.database._async_session_factory is None,
        "claim original globals not closed",
    )
    for engine in broker.engines:
        check(
            any(
                item["engine_before"] is engine
                and item["returned"]
                and item.get("engine_after") is None
                and item.get("factory_after") is None
                for item in broker.close_calls
            ),
            "claim original disposal return missing",
        )


CLAIM_DEPENDENCY_SOURCES = {
    "sqlalchemy.engine.create": "e7140e007d0f1b170db27a2b580da2f5cd69f6a64357579adde09a9b6244f6f6",
    "sqlalchemy.dialects.postgresql.asyncpg": "bcd9da5dd314d4e6e43ea16cb566544d5ebf58e5a9f38593a6924546e9e4c5c5",
    "sqlalchemy.pool.base": "5dbe91e32ec424f1e4cfee5db84896b96cfb23c601020e291d0a6644ff50883f",
    "asyncpg.connection": "e9abd5c153bc70cfadb61215f11140e90741c2fed5929e574b6548ef8092b48c",
    "asyncpg.connect_utils": "fa42ec6ca9faccee62c606ad1cddc0bf716f919acb2005c5fada5aa3f6e6cb9c",
}


def qualify_claim_dependencies() -> None:
    import sys
    from importlib.metadata import version

    for name, expected in {
        "SQLAlchemy": "2.0.49",
        "asyncpg": "0.31.0",
        "pytest": "9.0.3",
        "pytest-asyncio": "1.3.0",
    }.items():
        check(version(name) == expected, "claim installed dependency version differs")
    for name, expected in CLAIM_DEPENDENCY_SOURCES.items():
        module = sys.modules.get(name)
        check(module is not None, "claim original dependency module not loaded")
        check(sha_file(Path(module.__file__)) == expected, "claim loaded dependency source differs")


def claim_endpoint(host: Any, port: Any) -> tuple[str, int]:
    check(
        type(host) is str
        and 1 <= len(host) <= 253
        and host.isascii()
        and bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host)),
        "claim constructor host grammar",
    )
    check(type(port) is int and 1 <= port <= 65535, "claim constructor port grammar")
    return host, port


def retain_claim_driver(broker: Any, driver: Any) -> None:
    if not any(value is driver for value in broker.drivers):
        broker.drivers.append(driver)
    check(len(broker.drivers) <= 512, "claim physical driver capture bound")


def claim_loaded_function(function: Any, module: Any, source_path: str, receipt: dict[str, Any]) -> None:
    check(
        function.__globals__ is module.__dict__ and function.__module__ == module.__name__,
        "claim original callable globals association",
    )
    check(function.__code__.co_filename == module.__file__, "claim original callable source spelling")
    actual = Path(module.__file__).resolve(strict=True)
    expected = (API_ROOT / source_path.removeprefix("api/")).resolve(strict=True)
    check(actual == expected, "claim original callable resolved origin")
    same(sha_file(actual), receipt["source_sha256"][source_path], "claim original callable source bytes")
