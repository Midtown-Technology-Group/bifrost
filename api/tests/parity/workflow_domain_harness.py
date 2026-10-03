"""Real Python/PG field effects versus the actual Rust kernel; no owner proof.

The fixture contains synthetic inputs, never expected plans or final rows.
Redis events are reference observations only: the kernel emits no events.
Payload conversions, delivery/source authority and mixed writers are outside
this domain-plan comparison. Nothing starts a queue, process pool or workload.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import stat
import time
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as redis
from sqlalchemy import delete, select, text
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
from src.repositories.executions import ExecutionRepository
from src.repositories.events import EventSourceRepository, EventSubscriptionRepository
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
    "core-rs/crates/bifrost-domain/src/lib.rs",
    "core-rs/crates/bifrost-domain/src/workflow/mod.rs",
    "core-rs/crates/bifrost-domain/src/workflow/tests.rs",
    "api/src/services/execution/attempts.py",
    "api/src/jobs/consumers/workflow_execution.py",
    "api/src/repositories/executions.py",
    "api/src/models/orm/executions.py",
    "api/tests/parity/workflow_domain_harness.py",
    "api/tests/parity/test_workflow_domain.py",
    "core-rs/crates/bifrost-domain/examples/workflow_domain_vectors.rs",
    "core-rs/crates/bifrost-domain/Cargo.toml",
    "core-rs/Cargo.lock",
    "core-rs/Dockerfile",
    "scripts/ci/workflow-domain-parity.sh",
    ".github/workflows/workflow-domain-parity.yml",
}
REFERENCE_HASHES = {
    "api/src/services/execution/attempts.py": "82ed8abc28a1a51d17ddcdaeeabf9473468ef8474c172bb01e7bc5b66b067afa",
    "api/src/jobs/consumers/workflow_execution.py": "c285f2d315b36bcdfc81dc0b8e23705f22a7dbb6f740311233612068e10cf218",
    "api/src/repositories/executions.py": "6767c4462d07e5286f6a5245dc8942272792e81588b13af8a663ef018e165007",
    "api/src/models/orm/executions.py": "9d398b075384ff402631b98d53a1df0cadd962baa3bccb20f3144770b6c2ce7f",
}
KERNEL_HASHES = {
    "core-rs/crates/bifrost-domain/src/lib.rs": "aeea8c98c1da4fdb7ad790bc56648a6f0165b51257f7780e90704f425ac481a5",
    "core-rs/crates/bifrost-domain/src/workflow/mod.rs": "d7cbe9acce22278f9282408dda57bb542abec8592ecc74373ce9b70da750d2bb",
    "core-rs/crates/bifrost-domain/src/workflow/tests.rs": "625144d61ce9a7c0561c74bb6f48e0d890942ed6b7ce49a2178b91b5d1c2a772",
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
            set(arrangement)
            <= {"status", "attempt", "previous_completed_attempt", "tracking"},
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
                step["kind"]
                in {"running", "success", "failure", "coordinator_loss", "cancel"},
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
        },
    )
    check(
        receipt["schema"] == "bifrost.test.workflow-domain-receipt/v1",
        "receipt schema mismatch",
    )
    for key in ("candidate_sha", "candidate_tree"):
        check(
            isinstance(receipt[key], str)
            and bool(re.fullmatch(r"[0-9a-f]{40}", receipt[key])),
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
    labels = {s["case_id"] for c in load_cases() for s in c["steps"]}
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
                process.stdin is not None
                and process.stdout is not None
                and process.stderr is not None,
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
                raise AssertionError(
                    "driver response is not one bounded JSON value"
                ) from None
            closed(response, {"schema", "case_id", "outcome"})
            check(
                response["schema"] == SCHEMA
                and response["case_id"] == request["case_id"],
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
                        await asyncio.wait_for(
                            asyncio.gather(*tasks, return_exceptions=True), timeout=2
                        )


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
            calls.append(
                deepcopy(
                    {key: value for key, value in kwargs.items() if key != "session"}
                )
            )
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
        self.redis = redis.from_url(
            os.environ["BIFROST_REDIS_URL"], decode_responses=True
        )
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
                self.add_attempt(
                    db, "execution", arrangement["attempt"], now, 2 if previous else 1
                )
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
            check(
                bool(ack) and ack["type"] == kind, "reference event subscription failed"
            )

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
        status = (
            "succeeded"
            if completed
            else "claimed"
            if arrangement == "claimed_started"
            else arrangement
        )
        token = (
            None
            if status in {"dispatching", "published"}
            else self.ids[
                "stale_token"
                if old
                else "current_token"
                if role == "execution"
                else "foreign_token"
            ]
        )
        db.add(
            WorkflowExecutionAttempt(
                id=self.ids[
                    "old_attempt"
                    if old
                    else "attempt"
                    if role == "execution"
                    else "foreign_attempt"
                ],
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
                started_at=now
                if arrangement in {"claimed_started", "running", "completed"}
                else None,
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
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            for model, label in (
                (Execution, "executions"),
                (WorkflowExecutionAttempt, "attempts"),
                (ExecutionLog, "logs"),
            ):
                predicate = (
                    model.id.in_([self.ids["execution"], self.ids["foreign"]])
                    if model is Execution
                    else model.execution_id.in_(
                        [self.ids["execution"], self.ids["foreign"]]
                    )
                )
                rows = (
                    await db.scalars(select(model).where(predicate).order_by(model.id))
                ).all()
                result[label] = [
                    {
                        column.name: getattr(row, column.name)
                        for column in model.__table__.columns
                    }
                    for row in rows
                ]
            # Reference-only derived observations, never kernel row plans.
            for model, label in (
                (Event, "reference_topic_events"),
                (ExecutionMetricsDaily, "reference_org_metrics"),
            ):
                rows = (
                    await db.scalars(
                        select(model)
                        .where(model.organization_id == self.ids["org"])
                        .order_by(model.id)
                    )
                ).all()
                result[label] = [
                    {
                        column.name: getattr(row, column.name)
                        for column in model.__table__.columns
                    }
                    for row in rows
                ]
            result["history"] = {
                str(self.ids[role]): await has_recorded_attempt(db, self.ids[role])
                for role in ("execution", "foreign", "missing")
            }
        return result

    def request(
        self, step: dict[str, Any], snapshot: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        execution_id = self.ids[step.get("execution", "execution")]
        execution = next(
            (r for r in snapshot["executions"] if r["id"] == execution_id), None
        )
        attempts = [
            r for r in snapshot["attempts"] if r["execution_id"] == execution_id
        ]
        active = [r for r in attempts if r["completed_at"] is None]
        check(len(active) <= 1, "fixture violated one-active-attempt constraint")
        attempt = (
            active[0]
            if active
            else max(attempts, key=lambda r: r["attempt_number"], default=None)
        )
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
                    "claim_token": str(attempt["claim_token"])
                    if attempt["claim_token"] is not None
                    else None,
                    "status": attempt["status"],
                    "phase": attempt["phase"],
                    "started_at_present": attempt["started_at"] is not None,
                    "completed_at_present": attempt["completed_at"] is not None,
                }
                if attempt is not None
                else None,
                "history": "recorded"
                if snapshot["history"][str(execution_id)]
                else "unrecorded",
            },
        }
        return request, arguments

    async def execute(
        self, step: dict[str, Any], arguments: dict[str, Any], consumer: Any
    ) -> tuple[str, list[dict[str, Any]]]:
        execution_id = arguments["execution_id"]
        if step["kind"] == "running":
            async with self.sessions() as db:
                options = (
                    {"process_id": step["process_id"]} if "process_id" in step else {}
                )
                accepted = await mark_attempt_running(
                    db, UUID(execution_id), UUID(arguments["attempt_token"]), **options
                )
                await db.commit()
            return "accepted" if accepted else "rejected", []
        if step["kind"] == "cancel":
            check(self.principal is not None, "fixture principal unavailable")
            async with self.sessions() as db:
                _, error = await ExecutionRepository(db).cancel_execution(
                    UUID(execution_id), self.principal
                )
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
                execution_context=deepcopy(
                    step.get("execution_context", {"parity_context": "supplied"})
                ),
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
                source = await sources.get_by_topic(
                    topic, organization_id=self.ids["org"]
                )
                if source is None:
                    continue
                eligible = await subscriptions.get_active_for_event(
                    source_id=source.id,
                    event_type=topic,
                    organization_id=self.ids["org"],
                )
                check(
                    not any(
                        _subscription_matches_event_org(item, self.ids["org"])
                        for item in eligible
                    ),
                    "fixture isolation: existing topic subscriber could launch extra execution",
                )

    async def events(self) -> list[dict[str, Any]]:
        await self.redis.publish(self.barrier, "barrier")
        result = []
        deadline = time.monotonic() + 5
        identities = {str(self.ids["execution"]), str(self.ids["foreign"])}
        while time.monotonic() < deadline:
            message = await self.pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1
            )
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
                    await db.execute(
                        delete(ExecutionLog).where(ExecutionLog.execution_id.in_(ids))
                    )
                    await db.execute(delete(Execution).where(Execution.id.in_(ids)))
                    # Event.organization_id is SET NULL on org deletion; remove
                    # only genuine events stamped with this fresh fixture org.
                    await db.execute(
                        delete(Event).where(Event.organization_id == self.ids["org"])
                    )
                    await db.execute(delete(User).where(User.id == self.ids["user"]))
                    await db.execute(
                        delete(Organization).where(Organization.id == self.ids["org"])
                    )
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
        (
            observations / (self.case["case_id"] + "-" + str(self.ids["org"]) + ".json")
        ).write_text(
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


def clock(
    directive: str, before: Any, after: Any, observation: StepObservation, field: str
) -> None:
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


def unchanged(
    before: dict[str, Any], after: dict[str, Any], except_fields: set[str]
) -> None:
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
            check(
                directive is None or directive in mapping, "unknown static attempt enum"
            )
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
            expected = (
                "json"
                if isinstance(supplied, dict)
                else "html"
                if supplied.strip().startswith("<")
                else "text"
            )
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
    attempt_id = (
        request_rows["attempt"]["id"] if request_rows["attempt"] is not None else None
    )
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
        changed_execution = projection_fields(
            logical, execution_before, execution_after, observation
        )
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
    same(
        observation.before["history"], observation.after["history"], "recorded history"
    )
