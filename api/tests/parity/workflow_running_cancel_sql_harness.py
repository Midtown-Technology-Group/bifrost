"""Actual Python/Rust Running and Cancel SQL effects on owned legal cohorts.

This is SQL-projection evidence. Python cancellation publishes to real Redis;
Rust has no publisher. No runtime, principal, source or writer-owner admission
is implemented here. Requests, fences, DSNs and full rows stay private.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import signal
import sys
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import redis.asyncio as redis
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.core.database import get_session_factory
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.models.orm.events import Event
from src.models.orm.executions import Execution, ExecutionLog, WorkflowExecutionAttempt
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.repositories.executions import ExecutionRepository
from src.services.execution.attempts import mark_attempt_running

EVIDENCE = Path("/tmp/bifrost/workflow-running-cancel-sql")
DRIVER = EVIDENCE / "driver"
RECEIPT = EVIDENCE / "receipt.json"
API_ROOT = Path(__file__).resolve().parents[2]
INPUT_SCHEMA = "bifrost.test.workflow-running-cancel/v1"
OUTPUT_SCHEMA = "bifrost.test.workflow-running-cancel-result/v1"
RECEIPT_SCHEMA = "bifrost.test.workflow-running-cancel-receipt/v1"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
INVOCATION = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}\Z")
T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
TP, TC, TS, TT = (T0 + timedelta(seconds=i) for i in range(1, 5))
PROCESS = {
    "none": None,
    "empty": "",
    "supplied": "synthetic-supplied-process",
    "first": "synthetic-first-process",
    "raceA": "synthetic-race-A",
    "raceB": "synthetic-race-B",
    "rollback": "synthetic-rollback-process",
    "255chars": "\u00e9" * 255,
    "256chars": "\u00e9" * 256,
}
COUNTS = {
    "scenarios": 39,
    "paired_scenarios": 38,
    "rust_only_scenarios": 1,
    "python_actor_invocations": 44,
    "rust_actor_invocations": 45,
    "total_actor_invocations": 89,
}
SOURCE_PATHS = {
    "core-rs/crates/bifrost-db/examples/workflow_running_cancel_sql.rs",
    "core-rs/crates/bifrost-db/Cargo.toml",
    "core-rs/Cargo.lock",
    "api/tests/parity/workflow_running_cancel_sql_harness.py",
    "api/tests/parity/test_workflow_running_cancel_sql.py",
    "scripts/ci/workflow-running-cancel-sql.sh",
    ".github/workflows/workflow-running-cancel-sql.yml",
    "core-rs/crates/bifrost-db/src/workflow_parity.rs",
    "core-rs/crates/bifrost-db/src/lib.rs",
    "core-rs/crates/bifrost-domain/src/lib.rs",
    "core-rs/crates/bifrost-domain/src/workflow/mod.rs",
    "core-rs/crates/bifrost-domain/src/workflow/tests.rs",
    "api/src/services/execution/attempts.py",
    "api/src/repositories/executions.py",
    "api/src/models/orm/executions.py",
    "api/src/core/database.py",
    "api/src/core/pubsub.py",
    "api/tests/parity/workflow_domain_harness.py",
    "core-rs/Dockerfile",
}
FROZEN_SOURCE = {
    "core-rs/crates/bifrost-db/examples/workflow_running_cancel_sql.rs": "1856974e452237a659038729211e98d8d9f074bca2155e7b2373737bc2ee3953",
    "core-rs/crates/bifrost-db/Cargo.toml": "8e130b9bcd793e0ae4005a21fe95df2e398945888b6afe99fe1066b88770bd2a",
    "core-rs/Cargo.lock": "08a36a258d8ebc8b47e322adbae3fd541d66920ccf6dfc5a2cc0ab8a840ed021",
    "core-rs/crates/bifrost-db/src/workflow_parity.rs": "41362641ee0bd2928ea9fa23882c93273cddf05089936f339571984f13608569",
    "core-rs/crates/bifrost-db/src/lib.rs": "91d4f4691b7f3c93e2ef48b17cdf2fad1112ecce84d33f01fc11b3fa192f7599",
    "core-rs/crates/bifrost-domain/src/lib.rs": "aeea8c98c1da4fdb7ad790bc56648a6f0165b51257f7780e90704f425ac481a5",
    "core-rs/crates/bifrost-domain/src/workflow/mod.rs": "d7cbe9acce22278f9282408dda57bb542abec8592ecc74373ce9b70da750d2bb",
    "core-rs/crates/bifrost-domain/src/workflow/tests.rs": "625144d61ce9a7c0561c74bb6f48e0d890942ed6b7ce49a2178b91b5d1c2a772",
    "api/src/services/execution/attempts.py": "82ed8abc28a1a51d17ddcdaeeabf9473468ef8474c172bb01e7bc5b66b067afa",
    "api/src/repositories/executions.py": "6767c4462d07e5286f6a5245dc8942272792e81588b13af8a663ef018e165007",
    "api/src/models/orm/executions.py": "9d398b075384ff402631b98d53a1df0cadd962baa3bccb20f3144770b6c2ce7f",
    "api/src/core/database.py": "bd938b9918d613e107ccfe97f83f138b12ea874a20fb673228d2f9a232a1de4b",
    "api/src/core/pubsub.py": "14435ac03fcc684194ed2ce4ab2deff757a34d2efd3f82da4ac18d654c92296d",
    "api/tests/parity/workflow_domain_harness.py": "b8de5b523577b3231f3f393197546146613e377d05206ba34cee0b3123b60055",
}
OBSERVER_SQL = """
SELECT pid, application_name, state, wait_event_type, wait_event,
       pg_blocking_pids(pid) AS pg_blocking_pids
FROM pg_catalog.pg_stat_activity
WHERE datname = current_database()
  AND application_name = ANY(:labels)
ORDER BY application_name, pid
"""
RUN_FIELDS = {"status", "phase", "started_at", "heartbeat_at", "process_id"}
CANCEL_LOGICAL = {"status", "completed_at"}
CANCEL_ATTEMPT = {
    "status",
    "phase",
    "failure_phase",
    "failure_code",
    "completed_at",
    "heartbeat_at",
}
TIME_FIELDS = {"started_at", "heartbeat_at", "completed_at"}


class RedactedFailure(AssertionError):
    """Only static diagnostics can cross the private observation boundary."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RedactedFailure(message)


def closed(value: Any, keys: set[str]) -> None:
    check(isinstance(value, dict) and set(value) == keys, "closed schema mismatch")


def unique_json(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            check(key not in result, "duplicate JSON field")
            result[key] = value
        return result

    def nonfinite(_value: str) -> Any:
        raise RedactedFailure("nonfinite JSON denied")

    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite
        )
    except (ValueError, UnicodeError, RecursionError):
        raise RedactedFailure("invalid bounded JSON") from None


def encoded(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, repr=False)
class Step:
    kind: str
    process: str = "none"
    token: str = "current_token"
    disposition: str = "commit"


@dataclass(frozen=True, repr=False)
class Case:
    case_id: str
    status: str
    attempt: str
    steps: tuple[Step, ...]
    schedule: str = "ordered"
    paired: bool = True
    deleted: bool = False


CASES = (
    Case("r-claimed-unstarted-none", "Pending", "C0", (Step("running"),)),
    Case("r-claimed-started-empty", "Pending", "C1", (Step("running", "empty"),)),
    Case("r-running-started-supplied", "Running", "R", (Step("running", "supplied"),)),
    Case(
        "r-running-repeat", "Pending", "C0", (Step("running", "first"), Step("running"))
    ),
    Case("r-logical-cancelling-claimed", "Cancelling", "C0", (Step("running"),)),
    Case("r-missing-execution", "Pending", "C0", (Step("running"),), deleted=True),
    Case("r-missing-attempt", "Pending", "N", (Step("running", token="wrong_token"),)),
    Case(
        "r-foreign-token-wrong-execution",
        "Pending",
        "C0",
        (Step("running", token="foreign_token"),),
    ),
    Case("r-wrong-token", "Pending", "C0", (Step("running", token="wrong_token"),)),
    Case(
        "r-completed-attempt",
        "Success",
        "succeeded",
        (Step("running", token="terminal_token"),),
    ),
    Case(
        "r-published-tokenless-no-match",
        "Pending",
        "P",
        (Step("running", token="wrong_token"),),
    ),
    Case("r-process-255-chars", "Pending", "C0", (Step("running", "255chars"),)),
    Case("r-process-256-chars", "Pending", "C0", (Step("running", "256chars"),)),
    Case("c-scheduled-no-active", "Scheduled", "N", (Step("cancel"),)),
    Case("c-pending-no-active", "Pending", "N", (Step("cancel"),)),
    Case("c-scheduled-dispatching", "Scheduled", "D", (Step("cancel"),)),
    Case("c-pending-published", "Pending", "P", (Step("cancel"),)),
    Case("c-pending-claimed", "Pending", "C0", (Step("cancel"),)),
    Case("c-pending-running", "Pending", "R", (Step("cancel"),)),
    Case("c-pending-completed-history", "Pending", "history", (Step("cancel"),)),
    Case("c-running-active", "Running", "R", (Step("cancel"),)),
    Case("c-running-no-active", "Running", "N", (Step("cancel"),)),
    Case("c-missing", "Pending", "C0", (Step("cancel"),), deleted=True),
    Case("c-prior-cancelling", "Cancelling", "R", (Step("cancel"),)),
    Case("c-terminal-success", "Success", "succeeded", (Step("cancel"),)),
    Case("c-terminal-failed", "Failed", "failed", (Step("cancel"),)),
    Case("c-terminal-timeout", "Timeout", "timed_out", (Step("cancel"),)),
    Case("c-terminal-stuck", "Stuck", "worker_lost", (Step("cancel"),)),
    Case(
        "c-terminal-completed-errors",
        "CompletedWithErrors",
        "succeeded",
        (Step("cancel"),),
    ),
    Case("c-terminal-cancelled", "Cancelled", "cancelled", (Step("cancel"),)),
    Case(
        "x-running-then-queued-cancel",
        "Pending",
        "C0",
        (Step("running", "raceA"), Step("cancel")),
    ),
    Case(
        "x-queued-cancel-then-running",
        "Pending",
        "C0",
        (Step("cancel"), Step("running")),
    ),
    Case(
        "x-two-running",
        "Pending",
        "C0",
        (Step("running", "raceA"), Step("running", "raceB")),
        "race",
    ),
    Case(
        "x-queued-cancel-running",
        "Pending",
        "C0",
        (Step("cancel"), Step("running", "raceB")),
        "race",
    ),
    Case(
        "x-two-queued-cancel", "Pending", "C0", (Step("cancel"), Step("cancel")), "race"
    ),
    Case(
        "t-running-rollback",
        "Pending",
        "C0",
        (Step("running", "rollback", disposition="rollback"),),
    ),
    Case(
        "t-queued-cancel-rollback-rust-only",
        "Pending",
        "C0",
        (Step("cancel", disposition="rollback"),),
        paired=False,
    ),
    Case("w-running-after-attempt-lock", "Pending", "C0", (Step("running"),), "wait"),
    Case(
        "w-queued-cancel-before-attempt-lock",
        "Pending",
        "C0",
        (Step("cancel"),),
        "wait",
    ),
)
SEED_MATRIX = {
    "schema": "bifrost.test.workflow-running-cancel-seeds/v1",
    "times": [stamp.isoformat() for stamp in (T0, TP, TC, TS, TT)],
    "attempts": {
        "D": ["dispatching", "dispatch", None, None, None, None, None],
        "P": ["published", "queue", None, "TP", None, None, None],
        "C0": ["claimed", "claim", "current", "TP", "TC", None, None],
        "C1": ["claimed", "claim", "current", "TP", "TC", "TS", None],
        "R": ["running", "execution", "current", "TP", "TC", "TS", None],
        "H": ["terminal", "terminal", "terminal", "TP", "TC", "TS", "TT"],
    },
    "logical_sentinels": {"duration_ms": 17, "time_saved": 7, "value": "12.50"},
    "attempt_sentinels": {
        "duration_ms": 19,
        "peak_memory_bytes": 1024,
        "cpu_total_seconds": 0.125,
    },
    "process_variants": {
        key: len(value) if value is not None else None for key, value in PROCESS.items()
    },
}


def manifest_hashes() -> tuple[str, str]:
    # These documents contain roles/constants only, never generated fences.
    return (
        hashlib.sha256(encoded([asdict(case) for case in CASES])).hexdigest(),
        hashlib.sha256(encoded(SEED_MATRIX)).hexdigest(),
    )


def synthetic_manifest() -> dict[str, Any]:
    """Pure static inputs: no settings, receipt, UUID, engine or Redis call."""
    return {
        "schema": "bifrost.test.workflow-running-cancel-manifest/v1",
        "roster": [asdict(case) for case in CASES],
        "seed_matrix": deepcopy(SEED_MATRIX),
        "expected_counts": dict(COUNTS),
    }


def verify_receipt() -> dict[str, Any]:
    with RECEIPT.open("rb") as stream:
        raw = stream.read(65537)
    check(len(raw) <= 65536, "producer receipt too large")
    value = unique_json(raw)
    closed(
        value,
        {
            "schema",
            "candidate_sha",
            "candidate_tree",
            "driver_sha256",
            "source_sha256",
            "roster_sha256",
            "seed_matrix_sha256",
            "rust_image_id",
            "api_image_id",
            "database_label",
            "migration_heads",
            "expected_counts",
        },
    )
    check(value["schema"] == RECEIPT_SCHEMA, "producer receipt version mismatch")
    check(
        all(
            isinstance(value[key], str) and HEX40.fullmatch(value[key])
            for key in ("candidate_sha", "candidate_tree")
        ),
        "candidate custody invalid",
    )
    check(
        isinstance(value["expected_counts"], dict)
        and all(type(count) is int for count in value["expected_counts"].values())
        and value["expected_counts"] == COUNTS,
        "producer count mismatch",
    )
    check(
        value["roster_sha256"] == manifest_hashes()[0]
        and value["seed_matrix_sha256"] == manifest_hashes()[1],
        "producer manifest mismatch",
    )
    sources = value["source_sha256"]
    check(
        isinstance(sources, dict) and set(sources) == SOURCE_PATHS,
        "producer source scope mismatch",
    )
    check(
        all(
            isinstance(digest, str) and HEX64.fullmatch(digest)
            for digest in [value["driver_sha256"], *sources.values()]
        ),
        "producer digest invalid",
    )
    for path, digest in FROZEN_SOURCE.items():
        check(sources[path] == digest, "frozen source drift")
    for path in SOURCE_PATHS:
        if path.startswith("api/"):
            check(
                sha_file(API_ROOT / path.removeprefix("api/")) == sources[path],
                "mounted source drift",
            )
    check(sha_file(DRIVER) == value["driver_sha256"], "driver custody mismatch")
    for key in ("rust_image_id", "api_image_id"):
        check(
            isinstance(value[key], str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", value[key]),
            "image producer identity invalid",
        )
    check(
        isinstance(value["database_label"], str)
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value["database_label"]),
        "database association invalid",
    )
    heads = value["migration_heads"]
    # Producer binds EXPECTED source head; only DB admission measures installed.
    check(
        isinstance(heads, list)
        and len(heads) == 1
        and all(
            isinstance(head, str) and re.fullmatch(r"[A-Za-z0-9_]{1,128}", head)
            for head in heads
        ),
        "migration custody invalid",
    )
    return value


@dataclass(frozen=True, repr=False)
class Stamp:
    utc: datetime
    mono: float

    @classmethod
    def now(cls) -> Stamp:
        return cls(datetime.now(timezone.utc), time.monotonic())

    def safe(self) -> dict[str, Any]:
        return {"utc": self.utc.isoformat(), "monotonic": self.mono}


async def bounded(awaitable: Any, end: float) -> Any:
    async with asyncio.timeout_at(end):
        return await awaitable


async def dispose_session(
    db: AsyncSession, end: float, original: BaseException | None
) -> None:
    first: BaseException | None = None
    for operation in (db.rollback, db.close):
        try:
            # Closing a postcommit refresh transaction is honest disposal, not
            # a rollback of the Cancel method's already committed DML.
            if operation == db.rollback and not db.in_transaction():
                continue
            await bounded(operation(), min(end, time.monotonic() + 2))
        except BaseException as error:
            if first is None:
                first = error
    if original is None and first is not None:
        if not isinstance(first, Exception):
            raise first
        raise RedactedFailure("owned session disposal failed") from None


@asynccontextmanager
async def owned_session(factory: Any, end: float) -> AsyncIterator[AsyncSession]:
    db = factory()
    original: BaseException | None = None
    try:
        # No changed global pool/config: acquire the actual fixture connection
        # under the selected bound and set only owned transaction-local limits.
        await bounded(db.connection(), min(end - 4, time.monotonic() + 5))
        for statement in (
            "SET LOCAL statement_timeout = '5000ms'",
            "SET LOCAL lock_timeout = '5000ms'",
        ):
            await bounded(
                db.execute(text(statement)), min(end - 4, time.monotonic() + 5)
            )
        yield db
    except BaseException as error:
        original = error
        raise
    finally:
        await dispose_session(db, end, original)


async def read_bounded(stream: asyncio.StreamReader, limit: int = 4096) -> bytes:
    capture = bytearray()
    while True:
        chunk = await stream.read(min(1024, limit + 1 - len(capture)))
        if not chunk:
            return bytes(capture)
        capture.extend(chunk)
        check(len(capture) <= limit, "driver capture limit exceeded")


async def discard_owned_pipe(stream: asyncio.StreamReader) -> None:
    # After kill/capture failure, drain the owned pipe without retaining data.
    # A paused reader must not prevent asyncio from finishing child disposal.
    while await stream.read(1024):
        pass


def validate_response(value: Any, label: str, returncode: int) -> None:
    closed(
        value, {"schema", "case_id", "outcome", "transaction", "stage", "error_class"}
    )
    check(
        value["schema"] == OUTPUT_SCHEMA and value["case_id"] == label,
        "driver response custody mismatch",
    )
    check(
        value["transaction"] in {"committed", "rolled_back", "unknown"},
        "driver settlement invalid",
    )
    check(
        value["stage"]
        in {
            "ready",
            "connect",
            "setup",
            "advisory",
            "read_execution",
            "read_attempt",
            "decode",
            "clock",
            "write_attempt",
            "write_execution",
            "commit",
            "rollback",
        },
        "driver stage invalid",
    )
    if value["outcome"] in {"accepted", "rejected"}:
        check(
            returncode == 0 and value["stage"] == "ready", "driver result exit mismatch"
        )
        check(
            value["error_class"]
            == (None if value["outcome"] == "accepted" else "domain_rejected"),
            "driver result class mismatch",
        )
        check(value["transaction"] != "unknown", "driver result unsettled")
        if value["outcome"] == "rejected":
            check(value["transaction"] == "rolled_back", "rejected driver committed")
    else:
        check(
            value["outcome"] == "infrastructure_failure"
            and returncode == 1
            and value["error_class"]
            in {"database", "invalid_row", "clock_range", "cardinality"},
            "driver infrastructure result invalid",
        )


async def invoke_driver(
    request: dict[str, Any], dsn: str, started: Any, end: float
) -> dict[str, Any]:
    label = request["case_id"]
    check(bool(INVOCATION.fullmatch(label)), "driver invocation label invalid")
    payload = encoded(request)
    check(len(payload) <= 65536, "driver request limit exceeded")
    verify_receipt()
    process: asyncio.subprocess.Process | None = None
    tasks: list[asyncio.Task[Any]] = []
    original: BaseException | None = None
    try:
        async with asyncio.timeout_at(end - 4):
            process = await asyncio.create_subprocess_exec(
                str(DRIVER),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "BIFROST_RUST_TEST_DATABASE_URL": dsn,
                },
                cwd=str(EVIDENCE),
                start_new_session=True,
                limit=4096,
            )
            started()
            check(
                process.stdin is not None
                and process.stdout is not None
                and process.stderr is not None,
                "driver pipes missing",
            )

            async def send() -> None:
                process.stdin.write(payload)
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()

            for factory in (
                send,
                lambda: read_bounded(process.stdout),
                lambda: read_bounded(process.stderr),
                process.wait,
            ):
                coroutine = factory()
                try:
                    task = asyncio.create_task(coroutine)
                except BaseException as error:
                    try:
                        coroutine.close()
                    finally:
                        raise error
                tasks.append(task)
            _, stdout, stderr, code = await asyncio.gather(*tasks)
            check(
                not stderr and stdout.endswith(b"\n") and stdout.count(b"\n") == 1,
                "driver framing or diagnostics invalid",
            )
            value = unique_json(stdout)
            validate_response(value, label, code)
            return value
    except BaseException as error:
        original = error
        raise
    finally:
        cleanup: BaseException | None = None
        if process is not None:
            try:
                if process.returncode is None or any(not task.done() for task in tasks):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        # The owned group can disappear between observation
                        # and kill; wait() still proves child reap below.
                        pass
            except BaseException as error:
                cleanup = error
            for task in tasks:
                try:
                    if not task.done():
                        task.cancel()
                except BaseException as error:
                    if cleanup is None:
                        cleanup = error
            # One shared two-second settlement interval, not one per handle.
            reader_end = min(end, time.monotonic() + 2)
            for task in tasks:
                try:
                    await bounded(task, reader_end)
                except BaseException as error:
                    if cleanup is None:
                        cleanup = error
            # Always attempt reap, including when kill/reader cleanup failed.
            disposal: list[asyncio.Task[Any]] = []
            reap_task: asyncio.Task[Any] | None = None
            try:
                coroutine = process.wait()
                try:
                    reap_task = asyncio.create_task(coroutine)
                except BaseException as error:
                    try:
                        coroutine.close()
                    finally:
                        raise error
                disposal.append(reap_task)
                if all(task.done() for task in tasks):
                    for stream in (process.stdout, process.stderr):
                        if stream is not None:
                            coroutine = discard_owned_pipe(stream)
                            try:
                                task = asyncio.create_task(coroutine)
                            except BaseException as error:
                                try:
                                    coroutine.close()
                                finally:
                                    raise error
                            disposal.append(task)
                await bounded(asyncio.gather(*disposal), min(end, time.monotonic() + 2))
            except BaseException as error:
                if cleanup is None:
                    cleanup = error
            finally:
                for task in disposal:
                    try:
                        # A returned reap handle is awaited even when a later
                        # drain constructor fails; drain handles are canceled.
                        if task is not reap_task and not task.done():
                            task.cancel()
                    except BaseException as error:
                        if cleanup is None:
                            cleanup = error
                reaped = False
                for task in disposal:
                    try:
                        await bounded(task, end)
                        if task is reap_task:
                            reaped = True
                    except BaseException as error:
                        if cleanup is None:
                            cleanup = error
                if not reaped:
                    try:
                        # Failed construction/canceled wait must not skip the
                        # independent genuine child reap under the same end.
                        await bounded(process.wait(), end)
                    except BaseException as error:
                        if cleanup is None:
                            cleanup = error
        if cleanup is not None and original is None:
            if not isinstance(cleanup, Exception):
                raise cleanup
            raise RedactedFailure("owned child disposal failed") from None


@dataclass(repr=False)
class Actor:
    invocation_id: str
    step: Step
    operation_index: int
    started: Stamp | None = None
    finished: Stamp | None = None
    invoked: bool = False
    completed: bool = False
    response: dict[str, Any] | None = None
    reference_error: str | None = None

    @property
    def application_name(self) -> str:
        return "bifrost-rc:" + self.invocation_id

    def safe(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "kind": self.step.kind,
            "process_variant": self.step.process,
            "requested_disposition": self.step.disposition,
            "invoked": self.invoked,
            "completed": self.completed,
            "response": self.response,
            "reference_error": self.reference_error,
            "started": self.started.safe() if self.started else None,
            "finished": self.finished.safe() if self.finished else None,
        }


@dataclass(repr=False)
class Cohort:
    engine: Any
    case: Case
    lane: str
    run_uuid: UUID
    index: int
    end: float
    ids: dict[str, UUID] = field(default_factory=dict)
    records: list[Actor] = field(default_factory=list)
    graphs: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    readbacks: list[dict[str, Any]] = field(default_factory=list)
    baseline: dict[str, Any] | None = None
    principal: UserPrincipal | None = None
    seeded: bool = False
    preservation_verified: bool = False
    redis_disposed: bool = False

    def __post_init__(self) -> None:
        self.sessions = async_sessionmaker(
            self.engine, autoflush=False, expire_on_commit=False
        )
        self.ids = {
            role: uuid4()
            for role in (
                "org",
                "user",
                "execution",
                "foreign",
                "attempt",
                "foreign_attempt",
                "history_attempt",
                "current_token",
                "foreign_token",
                "terminal_token",
                "wrong_token",
                "worker",
                "foreign_worker",
                "history_worker",
            )
        }
        self.row_roles: dict[str, dict[Any, str]] = {
            "executions": {
                self.ids["execution"]: "primary",
                self.ids["foreign"]: "foreign",
            },
            "attempts": {
                self.ids["attempt"]: "primary",
                self.ids["foreign_attempt"]: "foreign",
                self.ids["history_attempt"]: "history",
            },
            "logs": {},
        }
        settings = get_settings()
        self.redis = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        self.pubsub = self.redis.pubsub()
        self.channel = "bifrost:execution:" + str(self.ids["execution"])
        self.control_channel = (
            f"bifrost:rc-control:{self.run_uuid.hex}:{self.index:02}:{self.lane}"
        )
        check(
            self.engine.url.drivername == "postgresql+asyncpg",
            "unsupported actual engine scheme",
        )
        # Query settings/endpoint/credentials are preserved; no SYNC/default URL.
        self.dsn = self.engine.url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
        check(not self.engine.echo, "fixture SQL logging enabled")

    def actor(self, number: int, step: Step) -> Actor:
        label = f"r{self.run_uuid.hex}-{self.index:02}-{self.lane}-{number:02}"
        check(
            len(label) == 41 and bool(INVOCATION.fullmatch(label)),
            "actor label construction invalid",
        )
        item = Actor(label, step, number)
        self.records.append(item)
        return item

    async def seed(self) -> None:
        receipt = verify_receipt()
        async with owned_session(self.sessions, self.end) as db:
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            heads = sorted(
                (await db.execute(text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
            check(
                heads == sorted(receipt["migration_heads"]),
                "actual schema receipt mismatch",
            )
            actual_database = (
                await db.execute(text("SELECT current_database()"))
            ).scalar_one()
            check(
                actual_database == self.engine.url.database,
                "actual database association mismatch",
            )
            self.readbacks.append(
                {
                    "producer_expected_heads": receipt["migration_heads"],
                    "measured_installed_heads": heads,
                    "database_label": receipt["database_label"],
                    "database_matches_engine": True,
                    "measured_at": Stamp.now().safe(),
                }
            )
        async with owned_session(self.sessions, self.end) as db:
            db.add(
                Organization(
                    id=self.ids["org"],
                    name="synthetic-rc-parity",
                    created_by="synthetic-rc-parity",
                    created_at=T0,
                    updated_at=T0,
                    is_active=True,
                    is_provider=False,
                )
            )
            await db.flush()
            self.seeded = True
            db.add(
                User(
                    id=self.ids["user"],
                    email=f"{self.ids['user']}@synthetic.example.test",
                    name="synthetic-rc-parity",
                    organization_id=self.ids["org"],
                    is_active=True,
                    is_superuser=False,
                    created_at=T0,
                    updated_at=T0,
                )
            )
            await db.flush()
            for role, status in (
                ("execution", self.case.status),
                ("foreign", "Running"),
            ):
                terminal = status not in {
                    "Scheduled",
                    "Pending",
                    "Running",
                    "Cancelling",
                }
                db.add(
                    Execution(
                        id=self.ids[role],
                        status=ExecutionStatus(status),
                        workflow_name="synthetic-rc-parity",
                        executed_by=self.ids["user"],
                        executed_by_name="synthetic-rc-parity",
                        organization_id=self.ids["org"],
                        parameters={"synthetic_parameters": True},
                        result={"synthetic_result": True},
                        variables={"synthetic_variables": True},
                        execution_context={"synthetic_context": True},
                        runtime_evidence={"synthetic_runtime": True},
                        dispatch_evidence={"synthetic_dispatch": True},
                        runtime_evidence_hash="sha256:" + "a" * 64,
                        dispatch_evidence_hash="sha256:" + "b" * 64,
                        result_type="text",
                        error_message="synthetic-retained-error",
                        duration_ms=17,
                        peak_memory_bytes=4096,
                        process_rss_bytes=2048,
                        cpu_user_seconds=0.5,
                        cpu_system_seconds=0.25,
                        cpu_total_seconds=0.75,
                        time_saved=7,
                        value=Decimal("12.50"),
                        runtime_mode="legacy",
                        attempt_tracking_version="v1",
                        created_at=T0,
                        started_at=None if status in {"Scheduled", "Pending"} else TS,
                        completed_at=TT if terminal else None,
                    )
                )
            await db.flush()
            self.add_attempt(db, "foreign", "C0")
            if self.case.attempt != "N":
                self.add_attempt(db, "execution", self.case.attempt)
            for role in ("execution", "foreign"):
                if role == "execution" and self.case.deleted:
                    continue
                db.add(
                    ExecutionLog(
                        execution_id=self.ids[role],
                        level="INFO",
                        message="synthetic-retained-log",
                        log_metadata={"synthetic": "retained"},
                        timestamp=T0,
                        sequence=7,
                    )
                )
            await db.commit()
        if self.case.deleted:
            async with owned_session(self.sessions, self.end) as db:
                await db.execute(
                    delete(Execution).where(Execution.id == self.ids["execution"])
                )
                await db.commit()
        async with owned_session(self.sessions, self.end) as db:
            user = await db.get(User, self.ids["user"])
            check(
                user is not None
                and user.is_active
                and not user.is_superuser
                and user.organization_id == self.ids["org"],
                "committed fixture principal invalid",
            )
            self.principal = UserPrincipal(
                user_id=user.id,
                email=user.email,
                organization_id=user.organization_id,
                name=user.name or "",
                is_active=user.is_active,
                is_superuser=user.is_superuser,
            )
            logs = (
                await db.scalars(
                    select(ExecutionLog).where(
                        ExecutionLog.execution_id.in_(
                            [self.ids["execution"], self.ids["foreign"]]
                        )
                    )
                )
            ).all()
            for log in logs:
                role = (
                    "primary"
                    if log.execution_id == self.ids["execution"]
                    else "foreign"
                )
                check(
                    role not in self.row_roles["logs"].values(),
                    "fixture log identity ambiguous",
                )
                self.row_roles["logs"][log.id] = role
        check(
            get_session_factory().kw["bind"].url == self.engine.url,
            "reference factory endpoint mismatch",
        )
        await self.pubsub.subscribe(self.channel, self.control_channel)
        acknowledged: set[str] = set()
        while len(acknowledged) != 2:
            message = await bounded(
                self.pubsub.get_message(timeout=5), min(self.end, time.monotonic() + 5)
            )
            check(
                isinstance(message, dict)
                and message.get("type") == "subscribe"
                and message.get("channel") in {self.channel, self.control_channel},
                "actual Redis subscription ACK missing",
            )
            acknowledged.add(message["channel"])
        self.ack = Stamp.now()
        self.baseline = await self.snapshot()
        expected_attempts = 1 + int(self.case.attempt != "N" and not self.case.deleted)
        check(
            len(self.baseline["attempts"]) == expected_attempts,
            "committed legal fixture shape mismatch",
        )

    def add_attempt(self, db: Any, role: str, arrangement: str) -> None:
        terminal = arrangement in {
            "history",
            "succeeded",
            "failed",
            "timed_out",
            "worker_lost",
            "cancelled",
        }
        history = arrangement == "history"
        row_role = (
            "history_attempt"
            if history
            else "attempt"
            if role == "execution"
            else "foreign_attempt"
        )
        status = (
            "succeeded"
            if history
            else {
                "D": "dispatching",
                "P": "published",
                "C0": "claimed",
                "C1": "claimed",
                "R": "running",
            }.get(arrangement, arrangement)
        )
        token_role = (
            "terminal_token"
            if terminal
            else "current_token"
            if role == "execution"
            else "foreign_token"
        )
        token = None if arrangement in {"D", "P"} else self.ids[token_role]
        worker_role = (
            "history_worker"
            if history
            else "worker"
            if role == "execution"
            else "foreign_worker"
        )
        db.add(
            WorkflowExecutionAttempt(
                id=self.ids[row_role],
                execution_id=self.ids[role],
                attempt_number=1,
                claim_token=token,
                status=status,
                phase="terminal"
                if terminal
                else "dispatch"
                if arrangement == "D"
                else "queue"
                if arrangement == "P"
                else "execution"
                if arrangement == "R"
                else "claim",
                failure_phase="execution",
                failure_code="synthetic-retained-code",
                published_at=None if arrangement == "D" else TP,
                claimed_at=TC if token is not None else None,
                started_at=TS if arrangement in {"C1", "R"} or terminal else None,
                heartbeat_at=TT
                if terminal
                else TS
                if arrangement == "R"
                else TC
                if token is not None
                else None,
                completed_at=TT if terminal else None,
                process_id="synthetic-retained-process",
                worker_id="synthetic-worker",
                worker_incarnation_id=self.ids[worker_role],
                duration_ms=19,
                peak_memory_bytes=1024,
                cpu_total_seconds=0.125,
                runtime_mode="legacy",
                runtime_evidence_hash="sha256:" + "a" * 64,
                dispatch_evidence_hash="sha256:" + "b" * 64,
                policy_digest="sha256:" + "c" * 64,
                policy_version="workflow-attempt/v1",
                created_at=T0,
            )
        )

    async def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        async with owned_session(self.sessions, self.end) as db:
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
                rows = (await db.scalars(select(model).where(predicate))).all()
                result[label] = {}
                for row in rows:
                    role = self.row_roles[label].get(row.id)
                    check(
                        role is not None and role not in result[label],
                        "readback identity outside closed cohort",
                    )
                    result[label][role] = {
                        column.name: getattr(row, column.name)
                        for column in model.__table__.columns
                    }
        return result

    async def observe(self, labels: list[str]) -> tuple[list[dict[str, Any]], Stamp]:
        # Every poll is a new session/transaction: pg_stat_activity caches must
        # never turn an old graph into a new scheduling witness.
        async with owned_session(self.sessions, self.end) as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            await db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            rows = (
                (await db.execute(text(OBSERVER_SQL), {"labels": labels}))
                .mappings()
                .all()
            )
            result = [dict(row) for row in rows]
        return result, Stamp.now()

    async def wait_graph(
        self, actor: Actor, blocker: int, tasks: list[asyncio.Task[Any]]
    ) -> tuple[dict[str, Any], Stamp]:
        while True:
            check(
                all(not task.done() for task in tasks),
                "actor ended before required wait witness",
            )
            rows, stamp = await bounded(
                self.observe([actor.application_name]),
                min(self.end, time.monotonic() + 5),
            )
            check(len(rows) <= 1, "actor PID ambiguous")
            if rows:
                row = rows[0]
                if (
                    row["state"] == "active"
                    and row["wait_event_type"] == "Lock"
                    and row["wait_event"]
                    and blocker in row["pg_blocking_pids"]
                ):
                    self.graphs.append(
                        {
                            "invocation_id": actor.invocation_id,
                            "observation": row,
                            "at": stamp.safe(),
                        }
                    )
                    return row, stamp

    async def configure(self, db: AsyncSession, application_name: str) -> None:
        await db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        await db.execute(text("SET LOCAL lock_timeout = '5000ms'"))
        await db.execute(
            text("SELECT set_config('application_name', :label, true)"),
            {"label": application_name},
        )

    async def run_actor(self, actor: Actor) -> Actor:
        verify_receipt()
        actor.started = Stamp.now()
        end = min(self.end, actor.started.mono + 30)
        check(end - actor.started.mono > 4, "actor disposal reserve unavailable")
        try:
            if self.lane == "r":
                operation: dict[str, Any] = {
                    "kind": actor.step.kind,
                    "execution_id": str(self.ids["execution"]),
                }
                if actor.step.kind == "running":
                    operation.update(
                        claim_token=str(self.ids[actor.step.token]),
                        process_id=PROCESS[actor.step.process],
                    )
                request = {
                    "schema": INPUT_SCHEMA,
                    "case_id": actor.invocation_id,
                    "transaction_disposition": actor.step.disposition,
                    "operation": operation,
                }

                def started() -> None:
                    actor.invoked = True

                actor.response = await invoke_driver(request, self.dsn, started, end)
            else:
                async with owned_session(self.sessions, end) as db:
                    async with asyncio.timeout_at(end - 4):
                        await self.configure(db, actor.application_name)
                        actor.invoked = True
                        if actor.step.kind == "running":
                            try:
                                accepted = await mark_attempt_running(
                                    db,
                                    self.ids["execution"],
                                    self.ids[actor.step.token],
                                    process_id=PROCESS[actor.step.process],
                                )
                            except Exception as error:
                                sqlstate = getattr(
                                    getattr(error, "orig", None), "sqlstate", None
                                )
                                check(
                                    actor.step.process == "256chars"
                                    and sqlstate == "22001",
                                    "unexpected reference infrastructure failure",
                                )
                                await db.rollback()
                                actor.response = {
                                    "schema": OUTPUT_SCHEMA,
                                    "case_id": actor.invocation_id,
                                    "outcome": "infrastructure_failure",
                                    "transaction": "rolled_back",
                                    "stage": "write_attempt",
                                    "error_class": "database",
                                }
                            else:
                                if accepted and actor.step.disposition == "commit":
                                    await db.commit()
                                    settlement = "committed"
                                else:
                                    await db.rollback()
                                    settlement = "rolled_back"
                                actor.response = self.reference_result(
                                    actor, accepted, settlement
                                )
                        else:
                            check(
                                self.principal is not None
                                and actor.step.disposition == "commit",
                                "reference cancellation scope mismatch",
                            )
                            dto, error = await ExecutionRepository(db).cancel_execution(
                                self.ids["execution"], self.principal
                            )
                            check(
                                error in {None, "NotFound", "BadRequest"},
                                "reference authorization fixture failed",
                            )
                            check(
                                (dto is not None) == (error is None),
                                "reference cancellation return inconsistent",
                            )
                            actor.reference_error = error
                            if dto is None:
                                await db.rollback()
                            actor.response = self.reference_result(
                                actor,
                                dto is not None,
                                "committed" if dto is not None else "rolled_back",
                            )
            actor.finished = Stamp.now()
            actor.completed = True
            check(actor.finished.mono <= end, "actor total deadline exceeded")
            return actor
        except Exception:
            # All SQL/serde/pipe exception formatting remains private. Source
            # control exceptions are BaseException and propagate unchanged.
            raise RedactedFailure("actual actor failed") from None

    @staticmethod
    def reference_result(
        actor: Actor, accepted: bool, settlement: str
    ) -> dict[str, Any]:
        return {
            "schema": OUTPUT_SCHEMA,
            "case_id": actor.invocation_id,
            "outcome": "accepted" if accepted else "rejected",
            "transaction": settlement,
            "stage": "ready",
            "error_class": None if accepted else "domain_rejected",
        }

    async def event_window(self, actors: list[Actor]) -> None:
        marker = "rc-marker-" + uuid4().hex
        await bounded(
            self.redis.publish(self.control_channel, marker),
            min(self.end, time.monotonic() + 5),
        )
        observed: list[dict[str, Any]] = []
        while True:
            message = await bounded(
                self.pubsub.get_message(timeout=5), min(self.end, time.monotonic() + 5)
            )
            check(
                isinstance(message, dict) and message.get("type") == "message",
                "Redis event window incomplete",
            )
            if message["channel"] == self.control_channel:
                check(message["data"] == marker, "Redis marker custody mismatch")
                break
            check(
                message["channel"] == self.channel, "Redis event channel outside cohort"
            )
            payload = unique_json(message["data"].encode("utf-8"))
            closed(payload, {"type", "executionId", "status"})
            check(
                payload["type"] == "execution_update"
                and payload["executionId"] == str(self.ids["execution"]),
                "actual cancellation event invalid",
            )
            check(
                payload["status"] in {"Cancelled", "Cancelling"},
                "cancellation event status invalid",
            )
            received = Stamp.now()
            committed = await self.snapshot()
            check(
                plain(committed["executions"]["primary"]["status"])
                == payload["status"],
                "event committed readback differs",
            )
            record = {
                "type": "execution_update",
                "execution_role": "primary",
                "status": payload["status"],
                "ack": self.ack.safe(),
                "received": received.safe(),
                "committed_readback": Stamp.now().safe(),
            }
            observed.append(record)
        eligible = sum(
            actor.step.kind == "cancel"
            and actor.response is not None
            and actor.response["outcome"] == "accepted"
            and actor.response["transaction"] == "committed"
            for actor in actors
        )
        check(
            len(observed) == (eligible if self.lane == "p" else 0),
            "actual Redis reference receipt missing or unexpected",
        )
        self.events.extend(observed)

    async def close(self, end: float) -> None:
        original: BaseException | None = None
        try:
            if self.seeded:
                async with owned_session(self.sessions, end) as db:
                    execution_ids = [self.ids["execution"], self.ids["foreign"]]
                    await db.execute(
                        delete(ExecutionLog).where(
                            ExecutionLog.execution_id.in_(execution_ids)
                        )
                    )
                    await db.execute(
                        delete(Execution).where(Execution.id.in_(execution_ids))
                    )
                    await db.execute(
                        delete(Event).where(Event.organization_id == self.ids["org"])
                    )
                    await db.execute(delete(User).where(User.id == self.ids["user"]))
                    await db.execute(
                        delete(Organization).where(Organization.id == self.ids["org"])
                    )
                    await db.commit()
        except BaseException as error:
            original = error
        disposed = 0
        for resource in (self.pubsub, self.redis):
            try:
                await bounded(resource.aclose(), min(end, time.monotonic() + 2))
                disposed += 1
            except BaseException as error:
                if original is None:
                    original = error
        self.redis_disposed = disposed == 2
        if original is not None:
            if not isinstance(original, Exception):
                raise original
            raise RedactedFailure("owned cohort cleanup failed") from None

    async def rows_absent(self, end: float) -> bool:
        """Fresh committed absence check, inside the original cleanup reserve."""
        async with owned_session(self.sessions, end) as db:
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            executions = [self.ids["execution"], self.ids["foreign"]]
            predicates = (
                (Execution, Execution.id.in_(executions)),
                (
                    WorkflowExecutionAttempt,
                    WorkflowExecutionAttempt.execution_id.in_(executions),
                ),
                (ExecutionLog, ExecutionLog.execution_id.in_(executions)),
                (Event, Event.organization_id == self.ids["org"]),
                (User, User.id == self.ids["user"]),
                (Organization, Organization.id == self.ids["org"]),
            )
            for model, predicate in predicates:
                if (
                    await db.scalar(select(model.id).where(predicate).limit(1))
                    is not None
                ):
                    return False
        return True


def plain(value: Any) -> Any:
    return value.value if isinstance(value, ExecutionStatus) else value


def assert_keep(
    before: dict[str, Any], after: dict[str, Any], steps: tuple[Step, ...]
) -> None:
    """Verify the full private unselected surface before any redaction."""
    check(set(before) == set(after), "snapshot table shape drift")
    for table, rows in before.items():
        check(set(rows) == set(after[table]), "snapshot row presence drift")
        for role, old in rows.items():
            new = after[table][role]
            check(set(old) == set(new), "snapshot column shape drift")
            allowed: set[str] = set()
            if role == "primary":
                if table == "attempts":
                    if any(step.kind == "running" for step in steps):
                        allowed |= RUN_FIELDS
                    if any(step.kind == "cancel" for step in steps):
                        allowed |= CANCEL_ATTEMPT
                elif table == "executions" and any(
                    step.kind == "cancel" for step in steps
                ):
                    allowed |= CANCEL_LOGICAL
            for column in set(old) - allowed:
                check(old[column] == new[column], "private Keep field drift")


def identity(value: Any, lane: Cohort, table: str, column: str) -> Any:
    if isinstance(value, UUID):
        role = next(
            (role for role, candidate in lane.ids.items() if value == candidate), None
        )
        check(role is not None, "unknown UUID cannot be normalized")
        return ("owned-identity", role)
    if table == "logs" and column == "id":
        role = lane.row_roles["logs"].get(value)
        check(role is not None, "unknown log ID cannot be normalized")
        return ("owned-log", role)
    return plain(value)


@dataclass(frozen=True, repr=False)
class SampleObservation:
    operation_index: int
    kind: str
    invocation_id: str
    value: datetime
    fields: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, repr=False)
class SamplePair:
    python: SampleObservation
    rust: SampleObservation


@dataclass(frozen=True, repr=False)
class SampleLedger:
    # Append-only frozen observations; field bindings are replaced functionally.
    pairs: tuple[SamplePair, ...] = ()
    bindings: tuple[tuple[tuple[str, str, str], int], ...] = ()

    def add(
        self, left: SampleObservation | None, right: SampleObservation | None
    ) -> SampleLedger:
        check((left is None) == (right is None), "operation sample applicability drift")
        if left is None:
            return self
        check(right is not None, "candidate sample missing")
        check(
            left.operation_index == right.operation_index
            and left.kind == right.kind
            and left.fields == right.fields,
            "operation sample role drift",
        )
        check(
            not any(
                pair.python.operation_index == left.operation_index
                for pair in self.pairs
            ),
            "operation sample already frozen",
        )
        index = len(self.pairs)
        bindings = tuple(
            (path, old) for path, old in self.bindings if path not in left.fields
        )
        return SampleLedger(
            self.pairs + (SamplePair(left, right),),
            bindings + tuple((path, index) for path in left.fields),
        )


def observe_sample(
    actor: Actor, before: dict[str, Any], after: dict[str, Any]
) -> SampleObservation | None:
    check(actor.response is not None and actor.completed, "sample actor not settled")
    if (
        actor.response["outcome"] != "accepted"
        or actor.response["transaction"] != "committed"
    ):
        return None
    fields: list[tuple[str, str, str]] = []
    if actor.step.kind == "running":
        old = before["attempts"]["primary"]
        fields.append(("attempts", "primary", "heartbeat_at"))
        if old["started_at"] is None:
            fields.append(("attempts", "primary", "started_at"))
        sample = after["attempts"]["primary"]["heartbeat_at"]
    elif plain(before["executions"]["primary"]["status"]) in {"Scheduled", "Pending"}:
        fields.append(("executions", "primary", "completed_at"))
        old = before["attempts"].get("primary")
        if old is not None and old["completed_at"] is None:
            fields.extend(
                (
                    ("attempts", "primary", "completed_at"),
                    ("attempts", "primary", "heartbeat_at"),
                )
            )
        sample = after["executions"]["primary"]["completed_at"]
    else:
        return None
    check(
        isinstance(sample, datetime)
        and actor.started is not None
        and actor.finished is not None
        and actor.started.utc <= sample <= actor.finished.utc,
        "sample outside its actual operation window",
    )
    for table, role, column in fields:
        check(
            after[table][role][column] == sample, "same-operation sample equality drift"
        )
    return SampleObservation(
        actor.operation_index,
        actor.step.kind,
        actor.invocation_id,
        sample,
        tuple(fields),
    )


def compare_rows(
    py: Cohort,
    rs: Cohort,
    before_py: dict[str, Any],
    before_rs: dict[str, Any],
    after_py: dict[str, Any],
    after_rs: dict[str, Any],
    actors_py: list[Actor],
    actors_rs: list[Actor],
    ledger: SampleLedger,
) -> None:
    assert_keep(before_py, after_py, tuple(actor.step for actor in actors_py))
    assert_keep(before_rs, after_rs, tuple(actor.step for actor in actors_rs))
    bindings = dict(ledger.bindings)
    check(set(after_py) == set(after_rs), "differential table shape drift")
    for table, py_rows in after_py.items():
        check(set(py_rows) == set(after_rs[table]), "differential row presence drift")
        for role, py_row in py_rows.items():
            rs_row = after_rs[table][role]
            check(set(py_row) == set(rs_row), "differential column drift")
            for column, value_py in py_row.items():
                value_rs = rs_row[column]
                old_py, old_rs = (
                    before_py[table][role][column],
                    before_rs[table][role][column],
                )
                path = (table, role, column)
                if path in bindings:
                    pair = ledger.pairs[bindings[path]]
                    check(
                        value_py == pair.python.value and value_rs == pair.rust.value,
                        "operation-index sample association drift",
                    )
                else:
                    check(
                        identity(value_py, py, table, column)
                        == identity(value_rs, rs, table, column),
                        "private differential field drift",
                    )
                if value_py == old_py:
                    check(value_rs == old_rs, "reference Keep rewritten by candidate")
    py.preservation_verified = rs.preservation_verified = True


def sample_swap_control(
    py: Cohort,
    rs: Cohort,
    before_py: dict[str, Any],
    before_rs: dict[str, Any],
    after_py: dict[str, Any],
    after_rs: dict[str, Any],
    actors_py: list[Actor],
    actors_rs: list[Actor],
    ledger: SampleLedger,
) -> None:
    row = after_rs["attempts"]["primary"]
    check(
        row["started_at"] != row["heartbeat_at"],
        "actual samples inseparable for swap control",
    )
    drift = deepcopy(after_rs)
    target = drift["attempts"]["primary"]
    target["started_at"], target["heartbeat_at"] = (
        row["heartbeat_at"],
        row["started_at"],
    )
    caught = False
    try:
        compare_rows(
            py, rs, before_py, before_rs, after_py, drift, actors_py, actors_rs, ledger
        )
    except RedactedFailure as error:
        caught = error.args == ("operation-index sample association drift",)
    check(caught, "actual sample swap was not detected")
    rs.graphs.append({"detector": "operation-sample-swap", "detected": caught})


def compare_results(py: list[Actor], rs: list[Actor]) -> None:
    check(len(py) == len(rs), "paired operation count mismatch")
    for left, right in zip(py, rs, strict=True):
        check(
            left.response is not None and right.response is not None,
            "actual response missing",
        )
        for key in ("outcome", "transaction", "stage", "error_class"):
            check(left.response[key] == right.response[key], "actual response differs")
        if left.step.process == "256chars":
            check(
                left.response["outcome"] == "infrastructure_failure"
                and left.response["transaction"] == "rolled_back",
                "character overflow was not a rolled-back SQL failure",
            )
        else:
            check(
                left.response["outcome"] != "infrastructure_failure",
                "unexpected SQL infrastructure failure",
            )


def stamp_order(*stamps: Stamp) -> None:
    check(
        all(
            left.mono <= right.mono and left.utc <= right.utc
            for left, right in zip(stamps, stamps[1:])
        ),
        "causal clock reversed",
    )


def clock_witness(
    lane: Cohort, actor: Actor, waited: Stamp, release: Stamp, after: dict[str, Any]
) -> None:
    check(
        actor.started is not None and actor.finished is not None,
        "causal actor bounds missing",
    )
    stamp_order(actor.started, waited, release, actor.finished)
    check(waited.utc < release.utc, "causal clock phases inseparable")
    attempt = after["attempts"]["primary"]
    if actor.step.kind == "running":
        sample = attempt["heartbeat_at"]
        check(
            attempt["started_at"] == sample
            and release.utc <= sample <= actor.finished.utc,
            "Running sample not after blocker pre-release",
        )
    else:
        sample = after["executions"]["primary"]["completed_at"]
        check(
            sample == attempt["completed_at"] == attempt["heartbeat_at"]
            and actor.started.utc <= sample <= waited.utc
            and sample < release.utc,
            "Cancel sample not before observed wait/pre-release",
        )
    lane.graphs.append(
        {
            "invocation_id": actor.invocation_id,
            "clock": {
                "wait_query_end": waited.safe(),
                "pre_release": release.safe(),
                "sample": sample.isoformat(),
            },
        }
    )


async def settle_tasks(
    tasks: list[asyncio.Task[Any]], end: float, original: BaseException | None
) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    try:
        await bounded(
            asyncio.gather(*tasks, return_exceptions=True),
            min(end, time.monotonic() + 4),
        )
    except BaseException as error:
        if original is None:
            if not isinstance(error, Exception):
                raise
            raise RedactedFailure("owned actor tasks did not settle") from None


async def wait_resource_graph(
    lane: Cohort, label: str, predecessor: int, tasks: list[asyncio.Task[Any]]
) -> tuple[dict[str, Any], Stamp]:
    while True:
        check(
            all(not task.done() for task in tasks),
            "inspection schedule ended before witness",
        )
        rows, stamp = await lane.observe([label])
        check(len(rows) <= 1, "inspection PID ambiguous")
        if rows:
            row = rows[0]
            if (
                row["state"] == "active"
                and row["wait_event_type"] == "Lock"
                and row["wait_event"]
                and predecessor in row["pg_blocking_pids"]
            ):
                lane.graphs.append({"resource_graph": row, "at": stamp.safe()})
                return row, stamp


async def inspection_schedule(lane: Cohort) -> tuple[list[Actor], list[dict[str, Any]]]:
    """Controlled A -> inspection C -> B, established through actual PG edges."""
    actors = [
        lane.actor(number, step) for number, step in enumerate(lane.case.steps, 1)
    ]
    tasks: list[asyncio.Task[Any]] = []
    original: BaseException | None = None
    try:
        async with (
            owned_session(lane.sessions, lane.end) as blocker,
            owned_session(lane.sessions, lane.end) as inspection,
        ):
            try:
                h_label = (
                    f"bifrost-rc-block:{lane.run_uuid.hex}:{lane.index:02}:{lane.lane}"
                )
                c_label = f"bifrost-rc-inspect:{lane.run_uuid.hex}:{lane.index:02}:{lane.lane}"
                check(len(c_label) == 56, "inspection label truncation risk")
                await lane.configure(blocker, h_label)
                await lane.configure(inspection, c_label)
                h_pid = (
                    await blocker.execute(text("SELECT pg_backend_pid()"))
                ).scalar_one()
                c_pid = (
                    await inspection.execute(text("SELECT pg_backend_pid()"))
                ).scalar_one()
                query = text(
                    "SELECT id FROM public.workflow_execution_attempts WHERE id = :attempt_id FOR UPDATE"
                )
                parameters = {"attempt_id": lane.ids["attempt"]}
                check(
                    (await blocker.execute(query, parameters)).scalar_one()
                    == lane.ids["attempt"],
                    "blocker scope mismatch",
                )
                names = [actor.application_name for actor in actors] + [c_label]
                rows, _ = await lane.observe(names)
                check(
                    {row["application_name"] for row in rows} == {c_label},
                    "inspection actor label collision",
                )
                a_task = asyncio.create_task(lane.run_actor(actors[0]))
                tasks.append(a_task)
                a_row, _ = await lane.wait_graph(actors[0], h_pid, tasks)
                c_task = asyncio.create_task(inspection.execute(query, parameters))
                tasks.append(c_task)
                c_row, _ = await wait_resource_graph(lane, c_label, a_row["pid"], tasks)
                check(c_row["pid"] == c_pid, "inspection PID changed")
                b_task = asyncio.create_task(lane.run_actor(actors[1]))
                tasks.append(b_task)
                await lane.wait_graph(actors[1], c_pid, tasks)
                while True:
                    check(
                        all(not task.done() for task in tasks),
                        "inspection queue ended before joint graph",
                    )
                    rows, _ = await lane.observe(names)
                    indexed = {row["application_name"]: row for row in rows}
                    check(len(indexed) == len(rows), "joint inspection graph ambiguous")
                    if set(indexed) != set(names):
                        continue
                    a, b, c = (indexed[name] for name in names)
                    if (
                        all(
                            row["state"] == "active"
                            and row["wait_event_type"] == "Lock"
                            and row["wait_event"]
                            for row in (a, b, c)
                        )
                        and a["pid"] == a_row["pid"]
                        and c["pid"] == c_pid
                        and h_pid in a["pg_blocking_pids"]
                        and a["pid"] in c["pg_blocking_pids"]
                        and a["pid"] in b["pg_blocking_pids"]
                        and c_pid in b["pg_blocking_pids"]
                    ):
                        lane.graphs.append(
                            {"inspection_joint_graph": rows, "at": Stamp.now().safe()}
                        )
                        break
                h_release = Stamp.now()
                await blocker.commit()
                check(
                    (await bounded(c_task, lane.end)).scalar_one()
                    == lane.ids["attempt"],
                    "inspection acquired wrong row",
                )
                await bounded(a_task, lane.end)
                _, waited = await lane.wait_graph(actors[1], c_pid, [b_task])
                first = await lane.snapshot()
                # The independent MVCC checkpoint never acquires a row/advisory lock.
                _, confirmed = await lane.wait_graph(actors[1], c_pid, [b_task])
                sample_a = first["attempts"]["primary"]["heartbeat_at"]
                check(
                    actors[0].finished is not None
                    and first["attempts"]["primary"]["started_at"] == sample_a
                    and h_release.utc <= sample_a <= actors[0].finished.utc,
                    "first Running checkpoint causal association failed",
                )
                c_release = Stamp.now()
                stamp_order(waited, confirmed, c_release)
                check(sample_a < c_release.utc, "Running samples cannot be separated")
                await inspection.commit()
                await bounded(b_task, lane.end)
                final = await lane.snapshot()
                sample_b = final["attempts"]["primary"]["heartbeat_at"]
                check(
                    actors[1].finished is not None
                    and c_release.utc <= sample_b <= actors[1].finished.utc
                    and final["attempts"]["primary"]["started_at"] == sample_a
                    and sample_a < sample_b,
                    "last Running checkpoint causal association failed",
                )
                lane.graphs.append(
                    {
                        "inspection_samples": {
                            "first_invocation": actors[0].invocation_id,
                            "last_invocation": actors[1].invocation_id,
                            "h_pre_release": h_release.safe(),
                            "c_pre_release": c_release.safe(),
                            "checkpoint_association_verified": True,
                        }
                    }
                )
            except BaseException as error:
                original = error
                raise
            finally:
                await settle_tasks(tasks, lane.end, original)
        await lane.event_window(actors)
        return actors, [first, final]
    except BaseException as error:
        original = error
        raise
    finally:
        await settle_tasks(tasks, lane.end, original)


async def blocked_schedule(lane: Cohort) -> tuple[list[Actor], list[dict[str, Any]]]:
    if lane.case.case_id == "x-two-running":
        return await inspection_schedule(lane)
    tasks: list[asyncio.Task[Any]] = []
    original: BaseException | None = None
    actors = [
        lane.actor(number, step) for number, step in enumerate(lane.case.steps, 1)
    ]
    try:
        async with owned_session(lane.sessions, lane.end) as blocker:
            blocker_label = (
                f"bifrost-rc-block:{lane.run_uuid.hex}:{lane.index:02}:{lane.lane}"
            )
            await lane.configure(blocker, blocker_label)
            blocker_pid = (
                await blocker.execute(text("SELECT pg_backend_pid()"))
            ).scalar_one()
            held = (
                await blocker.execute(
                    text(
                        "SELECT id FROM public.workflow_execution_attempts WHERE id = :attempt_id FOR UPDATE"
                    ),
                    {"attempt_id": lane.ids["attempt"]},
                )
            ).scalar_one()
            check(held == lane.ids["attempt"], "blocker did not own target attempt")
            # Detect duplicate actor labels before launching any selected actor.
            rows, _ = await lane.observe([actor.application_name for actor in actors])
            check(not rows, "actor label already belongs to a backend")
            tasks.append(asyncio.create_task(lane.run_actor(actors[0])))
            row_a, waited = await lane.wait_graph(actors[0], blocker_pid, tasks)
            if lane.case.schedule == "race":
                tasks.append(asyncio.create_task(lane.run_actor(actors[1])))
                _, waited_b = await lane.wait_graph(actors[1], row_a["pid"], tasks)
                stamp_order(waited, waited_b)
                # Re-observe BOTH edges together, not an old cached A graph.
                while True:
                    check(
                        all(not task.done() for task in tasks),
                        "race actor ended before joint graph",
                    )
                    rows, _ = await lane.observe(
                        [actor.application_name for actor in actors]
                    )
                    by_name = {row["application_name"]: row for row in rows}
                    check(
                        len(by_name) == len(rows),
                        "joint graph actor identity ambiguous",
                    )
                    a = by_name.get(actors[0].application_name)
                    b = by_name.get(actors[1].application_name)
                    if (
                        a
                        and b
                        and a["pid"] == row_a["pid"]
                        and blocker_pid in a["pg_blocking_pids"]
                        and a["pid"] in b["pg_blocking_pids"]
                        and all(
                            row["state"] == "active"
                            and row["wait_event_type"] == "Lock"
                            and row["wait_event"]
                            for row in (a, b)
                        )
                    ):
                        lane.graphs.append(
                            {"joint_graph": rows, "at": Stamp.now().safe()}
                        )
                        break
            release = Stamp.now()
            await blocker.commit()
            await bounded(asyncio.gather(*tasks), lane.end)
        after = await lane.snapshot()
        if lane.case.schedule == "wait":
            clock_witness(lane, actors[0], waited, release, after)
        await lane.event_window(actors)
        return actors, [after]
    except BaseException as error:
        original = error
        raise
    finally:
        await settle_tasks(tasks, lane.end, original)


def safe_counts(lanes: list[Cohort]) -> dict[str, int]:
    return {
        "python_selected": sum(
            len(lane.case.steps) for lane in lanes if lane.lane == "p"
        ),
        "rust_selected": sum(
            len(lane.case.steps) for lane in lanes if lane.lane == "r"
        ),
        "python_invoked": sum(
            actor.invoked
            for lane in lanes
            if lane.lane == "p"
            for actor in lane.records
        ),
        "rust_invoked": sum(
            actor.invoked
            for lane in lanes
            if lane.lane == "r"
            for actor in lane.records
        ),
        "python_completed": sum(
            actor.completed
            for lane in lanes
            if lane.lane == "p"
            for actor in lane.records
        ),
        "rust_completed": sum(
            actor.completed
            for lane in lanes
            if lane.lane == "r"
            for actor in lane.records
        ),
    }


def retain(
    case: Case, run_uuid: UUID, lanes: list[Cohort], passed: bool, cleanup_ok: bool
) -> None:
    # Explicit safe construction: no full rows/requests/DSNs/fences enter JSON.
    document = {
        "schema": "bifrost.test.workflow-running-cancel-observation/v1",
        "scenario_id": case.case_id,
        "run_uuid": str(run_uuid),
        "paired": case.paired,
        "passed": passed,
        "cleanup_ok": cleanup_ok,
        "gate_scope": "sql_projection",
        "rust_publisher": "absent",
        "event_parity": "held",
        "counts": safe_counts(lanes),
        "lanes": [
            {
                "lane": lane.lane,
                "cohort_id": str(lane.ids["org"]),
                "preservation_verified": lane.preservation_verified,
                "actors": [actor.safe() for actor in lane.records],
                "graphs": lane.graphs,
                "python_reference_events": lane.events,
                "database_admission": lane.readbacks,
            }
            for lane in lanes
        ],
    }
    data = encoded(document) + b"\n"
    check(len(data) <= 65536, "redacted observation too large")
    path = EVIDENCE / "observations" / f"{case.case_id}-{run_uuid}.json"
    check(path.parent.is_dir(), "owned observations directory missing")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()


@dataclass(frozen=True, repr=False)
class CaseClock:
    started: float
    work_end: float
    case_end: float

    @classmethod
    def now(cls) -> CaseClock:
        started = time.monotonic()
        return cls(started, started + 75, started + 90)


@dataclass(repr=False)
class Disposal:
    lane: Cohort
    started: float | None = None
    finished: float | None = None
    cleanup_ok: bool = False
    rows_absent: bool = False
    error: BaseException | None = None


@dataclass(repr=False)
class LifetimeState:
    clock: CaseClock
    original: BaseException | None = None
    disposals: list[Disposal] = field(default_factory=list)


async def dispose_cohorts(
    state: LifetimeState, lanes: list[Cohort], verify_absence: bool
) -> None:
    async def dispose(item: Disposal) -> None:
        # Catch controls INSIDE each owned task, preserving the original object.
        # Both independent tasks start before either lane result is awaited.
        item.started = time.monotonic()
        try:
            await bounded(item.lane.close(state.clock.case_end), state.clock.case_end)
            if verify_absence:
                item.rows_absent = await bounded(
                    item.lane.rows_absent(state.clock.case_end), state.clock.case_end
                )
                check(item.rows_absent, "owned rows remain after expiry cleanup")
            item.cleanup_ok = True
        except BaseException as error:
            item.error = error
        finally:
            item.finished = time.monotonic()
            if item.finished > state.clock.case_end:
                item.cleanup_ok = False
                if item.error is None:
                    item.error = RedactedFailure("cohort disposal deadline exceeded")

    state.disposals = [Disposal(lane) for lane in lanes]
    tasks: list[asyncio.Task[Any]] = []
    original: BaseException | None = None
    try:
        for item in state.disposals:
            coroutine = dispose(item)
            try:
                task = asyncio.create_task(coroutine)
            except BaseException as error:
                # No returned task owns this coroutine. Close it even when
                # construction raises a control, preserving that first object.
                try:
                    coroutine.close()
                finally:
                    raise error
            tasks.append(task)
        await bounded(asyncio.gather(*tasks), state.clock.case_end)
    except BaseException as error:
        original = error
    finally:
        for task in tasks:
            try:
                if not task.done():
                    task.cancel()
            except BaseException as error:
                if original is None:
                    original = error
        # Settle each returned handle independently, including tasks acquired
        # before a later constructor failed, under the unchanged absolute end.
        for task in tasks:
            try:
                await bounded(task, state.clock.case_end)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    for item in state.disposals:
        if item.error is not None:
            raise item.error


async def run_lifetime(
    state: LifetimeState,
    lanes: list[Cohort],
    work: Any,
    *,
    verify_absence: bool = False,
) -> None:
    try:
        async with asyncio.timeout_at(state.clock.work_end):
            await work()
    except BaseException as error:
        state.original = error
    cleanup: BaseException | None = None
    try:
        await dispose_cohorts(state, lanes, verify_absence)
    except BaseException as error:
        cleanup = error
    if state.original is not None:
        raise state.original
    if cleanup is not None:
        raise cleanup


async def _run_case(case: Case, engine: Any) -> dict[str, int]:
    verify_receipt()
    check(case in CASES, "scenario outside closed roster")
    run_uuid = uuid4()
    index = CASES.index(case) + 1
    state = LifetimeState(CaseClock.now())
    lanes: list[Cohort] = []
    passed = False

    async def work() -> None:
        nonlocal passed
        for kind in ("p", "r") if case.paired else ("r",):
            lanes.append(
                Cohort(engine, case, kind, run_uuid, index, state.clock.work_end)
            )
        for lane in lanes:
            await lane.seed()
        ledger = SampleLedger()
        if case.schedule in {"wait", "race"}:
            results = [await blocked_schedule(lane) for lane in lanes]
            compare_results(results[0][0], results[1][0])
            before = [lane.baseline for lane in lanes]
            if case.case_id == "x-two-running":
                for step_index in range(2):
                    actors = [results[lane][0][step_index] for lane in range(2)]
                    after = [results[lane][1][step_index] for lane in range(2)]
                    ledger = ledger.add(
                        observe_sample(actors[0], before[0], after[0]),
                        observe_sample(actors[1], before[1], after[1]),
                    )
                    compare_rows(
                        lanes[0],
                        lanes[1],
                        before[0],
                        before[1],
                        after[0],
                        after[1],
                        [actors[0]],
                        [actors[1]],
                        ledger,
                    )
                    if step_index == 1:
                        sample_swap_control(
                            lanes[0],
                            lanes[1],
                            before[0],
                            before[1],
                            after[0],
                            after[1],
                            [actors[0]],
                            [actors[1]],
                            ledger,
                        )
                    before = after
            else:
                after = [result[1][0] for result in results]
                # Actual accepted first actor owns the final sample; later
                # actual rejections have no persisted time role.
                for actor_index in range(len(case.steps)):
                    ledger = ledger.add(
                        observe_sample(results[0][0][actor_index], before[0], after[0]),
                        observe_sample(results[1][0][actor_index], before[1], after[1]),
                    )
                compare_rows(
                    lanes[0],
                    lanes[1],
                    before[0],
                    before[1],
                    after[0],
                    after[1],
                    results[0][0],
                    results[1][0],
                    ledger,
                )
        elif case.paired:
            before = [lane.baseline for lane in lanes]
            for number, step in enumerate(case.steps, 1):
                actors, after = [], []
                for lane in lanes:
                    actor = lane.actor(number, step)
                    await lane.run_actor(actor)
                    actors.append(actor)
                    after.append(await lane.snapshot())
                    await lane.event_window([actor])
                compare_results([actors[0]], [actors[1]])
                ledger = ledger.add(
                    observe_sample(actors[0], before[0], after[0]),
                    observe_sample(actors[1], before[1], after[1]),
                )
                compare_rows(
                    lanes[0],
                    lanes[1],
                    before[0],
                    before[1],
                    after[0],
                    after[1],
                    [actors[0]],
                    [actors[1]],
                    ledger,
                )
                if step.disposition == "rollback" or step.process == "256chars":
                    check(
                        before == after, "rollback did not preserve full private rows"
                    )
                before = after
        else:
            lane = lanes[0]
            actor = lane.actor(1, case.steps[0])
            await lane.run_actor(actor)
            check(
                actor.response is not None
                and actor.response["outcome"] == "accepted"
                and actor.response["transaction"] == "rolled_back",
                "Rust-only rollback result invalid",
            )
            check(
                await lane.snapshot() == lane.baseline,
                "Rust-only rollback changed private rows",
            )
            lane.preservation_verified = True
            await lane.event_window([actor])
        check(
            all(actor.completed for lane in lanes for actor in lane.records),
            "selected actual operation incomplete",
        )
        passed = True

    original: BaseException | None = None
    try:
        await run_lifetime(state, lanes, work)
    except BaseException as error:
        original = error
    cleanup_ok = bool(state.disposals) and all(
        item.cleanup_ok for item in state.disposals
    )
    try:
        retain(case, run_uuid, lanes, passed and cleanup_ok, cleanup_ok)
    except BaseException as error:
        if original is None:
            original = error
    if original is not None:
        raise original
    counts = safe_counts(lanes)
    check(
        counts["python_completed"] == (len(case.steps) if case.paired else 0)
        and counts["rust_completed"] == len(case.steps),
        "final actual invocation count mismatch",
    )
    return counts


async def run_case(case: Case, engine: Any) -> dict[str, int]:
    """Public failure boundary has no generated fences, DSN or row locals."""
    try:
        return await _run_case(case, engine)
    except Exception:
        raise RedactedFailure("Running/Cancel scenario failed") from None


async def _run_expiry_control(engine: Any) -> None:
    verify_receipt()
    state = LifetimeState(CaseClock.now())
    run_uuid = uuid4()
    lanes: list[Cohort] = []
    waiting = False
    observed: BaseException | None = None

    async def work() -> None:
        nonlocal waiting
        for kind in ("p", "r"):
            lanes.append(
                Cohort(engine, CASES[0], kind, run_uuid, 1, state.clock.work_end)
            )
        for lane in lanes:
            await lane.seed()
        check(
            all(not lane.records for lane in lanes),
            "expiry control selected a business actor",
        )
        waiting = True
        await asyncio.Event().wait()

    try:
        await run_lifetime(state, lanes, work, verify_absence=True)
    except BaseException as error:
        observed = error
    timeout_observed = (
        waiting
        and isinstance(observed, TimeoutError)
        and time.monotonic() >= state.clock.work_end
    )
    preserved = observed is not None and observed is state.original
    zero_actors = len(lanes) == 2 and all(not lane.records for lane in lanes)
    disposals_ok = len(state.disposals) == 2 and all(
        item.cleanup_ok
        and item.rows_absent
        and item.lane.redis_disposed
        and item.started is not None
        and item.finished is not None
        and state.clock.work_end
        <= item.started
        <= item.finished
        <= state.clock.case_end
        for item in state.disposals
    )
    passed = bool(
        timeout_observed
        and preserved
        and zero_actors
        and disposals_ok
        and time.monotonic() <= state.clock.case_end
    )
    try:
        check(
            all(
                item.started is not None
                and item.finished is not None
                and math.isfinite(item.started)
                and math.isfinite(item.finished)
                for item in state.disposals
            ),
            "incomplete actual disposal timeline",
        )
        document = {
            "schema": "bifrost.test.workflow-running-cancel-lifetime-control/v1",
            "control_id": "case-work-expiry",
            "run_uuid": str(run_uuid),
            "case_seconds": 90,
            "work_seconds": 75,
            "cleanup_reserve_seconds": 15,
            "started": state.clock.started,
            "work_end": state.clock.work_end,
            "case_end": state.clock.case_end,
            "timeout_observed": bool(timeout_observed),
            "original_error_preserved": preserved,
            "passed": passed,
            "business_invocations": sum(
                actor.invoked for lane in lanes for actor in lane.records
            ),
            "lanes": [
                {
                    "lane": item.lane.lane,
                    "cleanup_started": item.started,
                    "cleanup_finished": item.finished,
                    "cleanup_ok": item.cleanup_ok,
                    "rows_absent": item.rows_absent,
                    "redis_disposed": item.lane.redis_disposed,
                }
                for item in state.disposals
            ],
        }
        data = encoded(document) + b"\n"
        check(len(data) <= 65536, "expiry observation too large")
        path = EVIDENCE / "observations" / f"control-lifetime-{run_uuid}.json"
        check(path.parent.is_dir(), "owned observations directory missing")
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
    except BaseException:
        if observed is not None and not isinstance(observed, Exception):
            raise observed
        raise
    if observed is not None and not isinstance(observed, Exception):
        raise observed
    check(passed, "actual case expiry disposal control failed")


async def run_expiry_control(engine: Any) -> None:
    try:
        await _run_expiry_control(engine)
    except Exception:
        raise RedactedFailure("actual case expiry disposal control failed") from None


def manifest_cli() -> int:
    if sys.argv[1:] != ["--synthetic-manifest"]:
        return 2
    data = encoded(synthetic_manifest()) + b"\n"
    if len(data) > 65536:
        return 1
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(manifest_cli())
