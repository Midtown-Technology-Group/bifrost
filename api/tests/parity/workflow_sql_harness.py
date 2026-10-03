"""Selected Result SQL differential; actual consumer remains the reference.

This integer-JSON fixture profile does not own arbitrary sanitizer inputs,
worker recovery, Rust events, transaction faults or runtime authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import signal
import stat
import struct
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.sql import visitors
from src.config import get_settings
from src.core import database, redis_client
from src.core.cache.keys import active_execution_key, execution_logs_stream_key, pending_changes_key
from src.core.execution_variable_safety import sanitize_execution_variables
from src.jobs.consumers import workflow_execution as consumer_module
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.repositories.executions import _make_json_safe
from src.services.execution import process_pool as pool_module

from tests.parity.workflow_domain_harness import (
    MISSING_FENCE,
    REFERENCE_HASHES,
    SEED_TIME,
    WorkflowCohort,
    dormant,
    unstarted_consumer,
)

FIXTURE = Path(__file__).parent / "fixtures/workflow-result-v1.json"
API_ROOT = Path(__file__).resolve().parents[2]
ROOT = API_ROOT.parent
EVIDENCE = Path("/tmp/bifrost/workflow-result-parity")
DRIVER = EVIDENCE / "driver"
RECEIPT = EVIDENCE / "receipt.json"
SUCCESS = ("status", "result", "error", "error_type", "duration_ms", "variables", "execution_context", "metrics", "roi")
FAILURE = ("error", "error_type", "duration_ms", "execution_context", "metrics")
METRICS = ("peak_memory_bytes", "process_rss_bytes", "cpu_user_seconds", "cpu_system_seconds", "cpu_total_seconds")
ROI = ("time_saved", "value")
PREPARATION = (
    "api/src/core/execution_variable_safety.py",
    "api/src/repositories/executions.py",
    "api/tests/parity/workflow_sql_harness.py",
)
ALLOW_CODES = {"23514", "23505", "23503", "22003", "57014", "55P03"}
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
SQL_STAGES = {
    "Advisory",
    "ReadHistory",
    "ReadExecution",
    "ReadAttempt",
    "ReadContext",
    "Decode",
    "Clock",
    "WriteAttempt",
    "WriteExecution",
}
SETTLEMENT = {"Setup", "Acquire", "Begin", "Commit", "Rollback", "Close"}
CLASSES = {"Database", "InvalidRow", "ClockRange", "Cardinality", "Resource"}
EXEC_FIELDS = {
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
ATTEMPT_FIELDS = {
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
_HELD_CUSTODY = False
_CLOCK_ACTOR = ContextVar("result_clock_actor", default=None)
_CLOCK_REPOSITORY = ContextVar("result_clock_repository", default=None)
_CLOCK_LIMIT = 9007199254740991


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def closed(value: Any, fields: set[str]) -> None:
    check(type(value) is dict and set(value) == fields, "closed Result schema")


def decode(raw: bytes) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            check(key not in value, "duplicate Result key")
            value[key] = item
        return value

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise AssertionError("invalid Result JSON") from None


def read_file(path: Path, limit: int, *, executable: bool = False) -> bytes:
    fd = -1
    original = None
    data = bytearray()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        check(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, "Result file admission"
        )
        if executable:
            check(before.st_mode & 0o111 != 0, "Result executable admission")
        while block := os.read(fd, min(65536, limit + 1 - len(data))):
            data.extend(block)
            check(len(data) <= limit, "Result file bound")
        after = os.fstat(fd)
        check(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode),
            "Result file changed",
        )
    except BaseException as error:
        original = error
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    return bytes(data)


def load_fixture() -> dict:
    value = decode(read_file(FIXTURE, 1024 * 1024))
    closed(value, {"schema", "synthetic", "cases", "held_protocols", "controls"})
    check(
        value["schema"] == "bifrost.test.workflow-result-fixtures/v1" and value["synthetic"] is True,
        "Result fixture identity",
    )
    check(len(value["cases"]) == 233 and len(value["held_protocols"]) == 8, "Result protocol count")
    ids = []
    for case in value["cases"]:
        closed(case, {"case_id", "seed", "lane", "raw_fields_json"})
        check(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", case["case_id"]) is not None, "Result case label")
        check(case["lane"] in {"success", "failure"}, "Result lane")
        closed(case["seed"], {"status", "attempt", "phase", "token", "context", "tracking"})
        check(
            type(case["raw_fields_json"]) is str and len(case["raw_fields_json"].encode()) <= 65536,
            "Result raw fixture bound",
        )
        ids.append(case["case_id"])
    ids += [item["case_id"] for item in value["held_protocols"]]
    check(len(ids) == 241 and len(set(ids)) == 241, "Result complete identity roster")
    check([len(v) for v in value["controls"].values()] == [27, 9, 9, 20, 8], "Result control roster")
    return value


def materialize(fields: dict, lane: str) -> dict:
    closed(fields, set(SUCCESS if lane == "success" else FAILURE))

    def tag(value, members=None):
        check(type(value) is dict and value.get("kind") in {"absent", "null", "value"}, "Result presence")
        closed(value, {"kind", "value"} if value["kind"] == "value" else {"kind"})
        if value["kind"] == "absent":
            return False, None
        if value["kind"] == "null":
            return True, None
        check(value["value"] is not None, "Result value-null")
        if members is None:
            return True, deepcopy(value["value"])
        closed(value["value"], set(members))
        result = {}
        for name in members:
            supplied, item = tag(value["value"][name])
            if supplied:
                result[name] = item
        return True, result

    result = {}
    for name, value in fields.items():
        supplied, item = tag(value, METRICS if name == "metrics" else ROI if name == "roi" else None)
        if supplied:
            result[name] = item
    return result


def prepare_json_inputs(raw_fields: dict, lane: str) -> dict:
    """Only incumbent pure preparation; no row, numeric rounding or outcome."""
    original = materialize(raw_fields, lane)
    result = {}
    for source, target in (
        ("result", "prepared_result"),
        ("variables", "prepared_variables"),
        ("execution_context", "prepared_context"),
    ):
        if lane == "failure" and source != "execution_context":
            result[target] = {"kind": "absent"}
            continue
        tag = raw_fields[source]
        if tag["kind"] != "value":
            result[target] = {"kind": tag["kind"]}
        else:
            value = original[source]
            if source == "variables":
                value = sanitize_execution_variables(value)
            result[target] = {"kind": "value", "value": _make_json_safe(value)}
    lines = [
        path + " " + hashlib.sha256(read_file(ROOT / path, 2 * 1024 * 1024)).hexdigest() + "\n" for path in PREPARATION
    ]
    result["preparation_source_sha256"] = hashlib.sha256("".join(lines).encode("ascii")).hexdigest()
    return result


def source_admission() -> dict:
    """Read-only external receipt contract; producer is a separately held gate."""
    value = decode(read_file(RECEIPT, 1024 * 1024))
    closed(value, {"schema", "candidate", "sources", "binary", "graphs"})
    check(value["schema"] == "bifrost.test.workflow-result-source/v1", "Result source receipt")
    closed(value["candidate"], {"head", "tree"})
    check(
        all(re.fullmatch(r"[0-9a-f]{40}", value["candidate"][k]) for k in ("head", "tree")), "Result candidate metadata"
    )
    check(
        type(value["sources"]) is dict
        and all(type(k) is str and re.fullmatch(r"[0-9a-f]{64}", v) for k, v in value["sources"].items()),
        "Result source map",
    )
    required = (
        set(REFERENCE_HASHES)
        | set(PREPARATION)
        | {
            "api/src/core/database.py",
            "api/src/config.py",
            "api/tests/parity/test_workflow_sql.py",
            "api/tests/parity/fixtures/workflow-result-v1.json",
            "core-rs/crates/bifrost-db/src/workflow_parity.rs",
            "core-rs/crates/bifrost-db/src/workflow_numeric.rs",
            "core-rs/crates/bifrost-db/examples/workflow_sql_vectors.rs",
            "core-rs/crates/bifrost-db/Cargo.toml",
            "core-rs/Cargo.lock",
        }
    )
    check(required <= set(value["sources"]), "Result source receipt incomplete")
    for path in required:
        check(
            hashlib.sha256(read_file(ROOT / path, 4 * 1024 * 1024)).hexdigest() == value["sources"][path],
            "Result source readback",
        )
    for path, digest in REFERENCE_HASHES.items():
        check(value["sources"][path] == digest, "Result reference drift")
    closed(value["binary"], {"sha256", "source_paths", "build_head", "build_tree"})
    check(
        value["binary"]["build_head"] == value["candidate"]["head"]
        and value["binary"]["build_tree"] == value["candidate"]["tree"],
        "Result binary source binding",
    )
    check(
        set(value["binary"]["source_paths"]) <= set(value["sources"])
        and required - {x for x in required if x.startswith("api/")} <= set(value["binary"]["source_paths"]),
        "Result binary source roster",
    )
    check(
        hashlib.sha256(read_file(DRIVER, 64 * 1024 * 1024, executable=True)).hexdigest() == value["binary"]["sha256"],
        "Result binary readback",
    )
    check(
        type(value["graphs"]) is dict and set(value["graphs"]) == {"default", "selected", "all_features"},
        "Result feature evidence absent",
    )
    for graph in value["graphs"].values():
        closed(graph, {"sha256", "packages", "features"})
        check(
            re.fullmatch(r"[0-9a-f]{64}", graph["sha256"]) is not None
            and type(graph["packages"]) is list
            and type(graph["features"]) is list,
            "Result graph metadata",
        )
    return value


def driver_dsn(engine) -> str:
    """Fail closed where constructor/TLS equivalence cannot be reconstructed."""
    original = make_url(get_settings().database_url)
    cleaned, options = database._prepare_asyncpg_url(str(original.render_as_string(hide_password=False)))
    check(make_url(cleaned) == engine.url == database.get_engine().url, "Result constructor endpoint mismatch")
    factory = database.get_session_factory()
    check(
        factory.kw["bind"] is database.get_engine()
        and factory.kw.get("autoflush") is False
        and factory.kw.get("expire_on_commit") is False,
        "Result production session settings",
    )
    # Authenticated SSLContext cannot be reproduced from a cleaned URL alone.
    check(
        not options and set(original.query) <= {"sslmode"} and original.query.get("sslmode") in {None, "disable"},
        "Result TLS association unsupported",
    )
    return original.set(drivername="postgresql").render_as_string(hide_password=False)


class CaseLifetime:
    def __init__(self, request):
        check(not _HELD_CUSTODY, "Result prior custody retained")
        self.request = request
        self.start = asyncio.get_running_loop().time()
        self.work_end = self.start + 75
        self.end = self.start + 90
        self.tasks = set()
        self.processes = []
        self.cohorts = []
        self.retained = False
        self.retain_dependencies = False

    def task(self, coroutine):
        try:
            task = asyncio.create_task(coroutine)
        except BaseException:
            with suppress(BaseException):
                coroutine.close()
            raise
        self.tasks.add(task)
        return task

    async def cleanup(self):
        global _HELD_CUSTODY
        process_error = None
        for process in self.processes:
            try:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
            except BaseException as error:
                if process_error is None:
                    process_error = error
            try:
                async with asyncio.timeout_at(self.end):
                    await process.wait()
            except BaseException as error:
                if process_error is None:
                    process_error = error
        for task in self.tasks:
            try:
                if not task.done():
                    task.cancel()
            except BaseException as error:
                if process_error is None:
                    process_error = error
        pending = [task for task in self.tasks if not task.done()]
        if pending:
            try:
                left = self.end - asyncio.get_running_loop().time()
                if left > 0:
                    await asyncio.wait(pending, timeout=left)
            except BaseException as error:
                if process_error is None:
                    process_error = error
        if (
            self.retain_dependencies
            or process_error is not None
            or any(process.returncode is None for process in self.processes)
            or any(not task.done() for task in self.tasks)
        ):
            self.retained = _HELD_CUSTODY = True
            if not self.request.session.shouldstop:
                self.request.session.shouldstop = "Result owned task custody retained"
            if process_error is not None:
                raise process_error
            raise AssertionError("Result owned task remains pending")
        original = None
        for cohort in reversed(self.cohorts):
            try:
                async with asyncio.timeout_at(self.end):
                    await cohort.close(self.end)
            except BaseException as error:
                if original is None:
                    original = error
        if original is not None:
            self.retained = _HELD_CUSTODY = True
            if not self.request.session.shouldstop:
                self.request.session.shouldstop = "Result resource custody retained"
            raise original


@asynccontextmanager
async def lifetime(request):
    case = CaseLifetime(request)
    original = None
    try:
        async with asyncio.timeout_at(case.work_end):
            yield case
    except BaseException as error:
        original = error
    finally:
        try:
            await case.cleanup()
        except BaseException as error:
            if original is None:
                original = error
    if original is not None:
        raise original


async def invoke(case: CaseLifetime, payload: bytes, mode: str, *, database_url: str | None = None):
    source_admission()
    check(mode in {"apply-result", "decode-number"} and len(payload) <= 65537, "Result invocation admission")
    env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL") if key in os.environ}
    if database_url is not None:
        env["BIFROST_RUST_TEST_DATABASE_URL"] = database_url
    proc = None
    original = None
    tasks = []
    stdout = stderr = b""
    end = min(case.work_end, asyncio.get_running_loop().time() + 30)

    async def read(stream):
        value = bytearray()
        while chunk := await stream.read(4097 - len(value)):
            value.extend(chunk)
            check(len(value) <= 4096, "Result child output bound")
        return bytes(value)

    try:
        async with asyncio.timeout_at(end):
            proc = await asyncio.create_subprocess_exec(
                str(DRIVER),
                mode,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
                limit=4096,
            )
            case.processes.append(proc)
            check(proc.stdin is not None and proc.stdout is not None and proc.stderr is not None, "Result child pipes")
            tasks = [case.task(read(proc.stdout)), case.task(read(proc.stderr))]
            proc.stdin.write(payload)
            await proc.stdin.drain()
            proc.stdin.close()
            await proc.stdin.wait_closed()
            stdout, stderr = await asyncio.gather(*tasks)
            await proc.wait()
    except BaseException as error:
        original = error
    finally:
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except BaseException as error:
                if original is None:
                    original = error
            try:
                if proc.returncode is None:
                    os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as error:
                if original is None:
                    original = error
            try:
                async with asyncio.timeout_at(min(case.work_end, end + 2)):
                    await proc.wait()
            except BaseException as error:
                case.retained = True
                if original is None:
                    original = error
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            try:
                async with asyncio.timeout_at(min(case.work_end, end + 2)):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    check(proc is not None and proc.returncode is not None and not stderr, "Result child completion")
    source_admission()
    return proc.returncode, stdout


class ResultCohort(WorkflowCohort):
    def __init__(self, engine, fixture):
        seed = fixture["seed"]
        super().__init__(
            engine,
            {
                "case_id": fixture["case_id"],
                "arrangement": {
                    "status": seed["status"],
                    "attempt": seed["attempt"],
                    "tracking": seed["tracking"],
                },
            },
        )
        self.fixture = fixture
        self.sessions = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        self.sql_labels = []

    async def seed(self):
        await super().seed()
        async with self.sessions() as db:
            for role in ("execution", "foreign"):
                row = await db.get(Execution, self.ids[role])
                check(row is not None, "Result seed row")
                row.created_at = SEED_TIME
                row.started_at = SEED_TIME
                row.completed_at = SEED_TIME
                row.result = {"old": 1}
                row.result_type = "json"
                row.error_message = "old-error"
                row.variables = {"old": 1}
                row.duration_ms = 19
                row.time_saved = 19
                row.value = 12.34
                row.peak_memory_bytes = 41
                row.process_rss_bytes = 43
                row.cpu_user_seconds = 1.25
                row.cpu_system_seconds = 2.25
                row.cpu_total_seconds = 3.25
            attempts = (
                await db.scalars(
                    select(WorkflowExecutionAttempt).where(
                        WorkflowExecutionAttempt.execution_id.in_([self.ids["execution"], self.ids["foreign"]])
                    )
                )
            ).all()
            for attempt in attempts:
                attempt.created_at = SEED_TIME
                attempt.published_at = SEED_TIME if attempt.published_at is not None else None
                attempt.claimed_at = SEED_TIME if attempt.claimed_at is not None else None
                attempt.started_at = SEED_TIME if attempt.started_at is not None else None
                attempt.completed_at = SEED_TIME if attempt.completed_at is not None else None
                attempt.heartbeat_at = SEED_TIME
                attempt.worker_incarnation_id = self.ids["org"]
                attempt.duration_ms = 23
                attempt.peak_memory_bytes = 31
                attempt.cpu_total_seconds = 3.25
                if attempt.execution_id == self.ids["execution"]:
                    attempt.phase = self.fixture["seed"]["phase"]
            await db.commit()
        context = self.fixture["seed"]["context"]
        values = {
            "old": '{"old":1}',
            "sql-null": None,
            "json-null": "null",
            "empty": "{}",
            "server-present": '{"old":1,"teams_action_completion":{"server":1}}',
            "server-null": '{"old":1,"teams_action_completion":null}',
        }
        check(context in values, "Result context seed directive")
        async with self.sessions() as db:
            await db.execute(
                text("UPDATE executions SET execution_context = CAST(:value AS jsonb) WHERE id = :id"),
                {"value": values[context], "id": self.ids["execution"]},
            )
            await db.commit()
        await self.topic_isolation()
        await self.empty_buffers()

    async def empty_buffers(self):
        for role in ("execution", "foreign"):
            identity = str(self.ids[role])
            check(await self.redis.hlen(pending_changes_key(identity)) == 0, "Result sync buffer not empty")
            check(await self.redis.xlen(execution_logs_stream_key(identity)) == 0, "Result log buffer not empty")

    async def snapshot(self):
        from src.models.orm.executions import ExecutionLog

        snapshot = {}
        async with self.sessions() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            for model, name in (
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
                snapshot[name] = [
                    {column.name: getattr(row, column.name) for column in model.__table__.columns} for row in rows
                ]
            rows = (
                (
                    await db.execute(
                        text(
                            "SELECT id, execution_context IS NULL AS sql_null, execution_context::text AS json_text "
                            "FROM executions WHERE id = :owned OR id = :foreign ORDER BY id"
                        ),
                        {"owned": self.ids["execution"], "foreign": self.ids["foreign"]},
                    )
                )
                .mappings()
                .all()
            )
            snapshot["context_storage"] = {
                row["id"]: {"sql_null": row["sql_null"], "json_text": row["json_text"]} for row in rows
            }
            await db.rollback()
        return snapshot

    async def close(self, end):
        from src.models.orm.events import Event
        from src.models.orm.executions import ExecutionLog
        from src.models.orm.organizations import Organization
        from src.models.orm.users import User

        original = None
        db = None
        try:
            async with asyncio.timeout_at(end):
                for role in ("execution", "foreign"):
                    await self.redis.delete(active_execution_key(str(self.ids[role])))
                db = self.sessions()
                ids = [self.ids["execution"], self.ids["foreign"]]
                await db.execute(delete(ExecutionLog).where(ExecutionLog.execution_id.in_(ids)))
                await db.execute(delete(Execution).where(Execution.id.in_(ids)))
                await db.execute(delete(Event).where(Event.organization_id == self.ids["org"]))
                await db.execute(delete(User).where(User.id == self.ids["user"]))
                await db.execute(delete(Organization).where(Organization.id == self.ids["org"]))
                await db.commit()
        except BaseException as error:
            original = error
        # Each owned close is independently attempted under the SAME deadline;
        # a suppressed cancellation cannot turn expired cleanup into new time.
        for handle in (db, self.pubsub, self.redis):
            if handle is None:
                continue
            try:
                async with asyncio.timeout_at(end):
                    if handle is db:
                        await handle.close()
                    else:
                        await handle.aclose()
            except BaseException as error:
                if original is None:
                    original = error
        if original is not None:
            raise original

    def token(self):
        selector = self.fixture["seed"]["token"]
        return None if selector == "missing" else self.ids[selector + "_token"]


@contextmanager
def repository_observer(identity: str):
    original = consumer_module.update_execution
    calls = []

    async def forward(*args, **kwargs):
        if kwargs.get("execution_id") == identity:
            calls.append({name: deepcopy(value) for name, value in kwargs.items() if name != "session"})
        actor = _CLOCK_ACTOR.get()
        if kwargs.get("execution_id") != identity or actor is None or not actor.collect_roles:
            return await original(*args, **kwargs)
        actor.logical_selected = kwargs.get("duration_ms") is not None
        with clock_context(_CLOCK_REPOSITORY, actor):
            return await original(*args, **kwargs)

    consumer_module.update_execution = forward
    pending = None
    try:
        yield calls
    except BaseException as error:
        pending = error
    finally:
        try:
            check(consumer_module.update_execution is forward, "Result repository observer replaced")
            consumer_module.update_execution = original
        except BaseException as error:
            if pending is None:
                pending = error
    if pending is not None:
        raise pending


@asynccontextmanager
async def actual_consumer():
    """No start/stop/loader override; retain exact incumbent constructor."""
    previous_pool = pool_module._pool
    previous_redis = redis_client._redis_client
    pool = None
    consumer = None
    previous_callback = None
    original = None
    try:
        pool = pool_module.get_process_pool()
        previous_callback = pool.on_result
        dormant(pool)
        consumer = consumer_module.WorkflowExecutionConsumer()
        unstarted_consumer(consumer, pool)
        yield consumer
    except BaseException as error:
        original = error
    finally:
        actions = []
        if pool is not None:

            def restore_pool():
                dormant(pool)
                if consumer is not None:
                    unstarted_consumer(consumer, pool)
                    check(pool.on_result == consumer._handle_result, "Result callback replaced")
                check(pool_module._pool is pool, "Result pool replaced")
                pool.on_result = previous_callback
                if previous_pool is None:
                    pool_module._pool = None

            actions.append(restore_pool)
        for action in actions:
            try:
                action()
            except BaseException as error:
                if original is None:
                    original = error
        try:
            if previous_redis is None and redis_client._redis_client is not None:
                await redis_client.close_redis_client()
            elif previous_redis is not None:
                check(redis_client._redis_client is previous_redis, "Result Redis singleton replaced")
        except BaseException as error:
            if original is None:
                original = error
    if original is not None:
        raise original


@contextmanager
def clock_context(variable, value):
    check(variable is _CLOCK_ACTOR or variable is _CLOCK_REPOSITORY, "Result clock context role")
    token = variable.set(value)
    pending = None
    try:
        yield
    except BaseException as error:
        pending = error
    finally:
        try:
            variable.reset(token)
        except BaseException as error:
            if pending is None:
                pending = error
    if pending is not None:
        raise pending


def utc_microseconds(value):
    check(isinstance(value, datetime) and value.tzinfo is not None, "Result clock datetime")
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    result = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    check(0 <= result <= _CLOCK_LIMIT, "Result clock UTC bound")
    return result


class SourceClockObserver:
    """Private actual actor/connection association; no SQL text or binds retained."""

    def __init__(self):
        self.collect_roles = True
        self.connection = None
        self.transaction = None
        self.attempt = {}
        self.logical = {}
        self.logical_selected = None
        self.commit_dispatch = None
        self.commit_returned = False

    def mark(self, slot, name):
        check(name not in slot, "Result duplicate source clock role")
        slot[name] = utc_microseconds(datetime.now(UTC))

    def observe(self, connection, context, before):
        if not self.collect_roles or _CLOCK_ACTOR.get() is not self:
            return
        compiled = context.compiled
        if compiled is None:
            return
        statement = compiled.statement
        repository = _CLOCK_REPOSITORY.get() is self
        is_select = getattr(statement, "is_select", False)
        is_update = getattr(statement, "is_update", False)
        table = getattr(getattr(statement, "table", None), "name", None)
        columns = set()
        where_columns = set()
        locking = False
        if is_select:
            columns = {
                (getattr(getattr(column, "table", None), "name", None), getattr(column, "name", None))
                for column in statement.selected_columns
            }
            for criterion in statement._where_criteria:
                where_columns.update(
                    (getattr(getattr(node, "table", None), "name", None), getattr(node, "name", None))
                    for node in visitors.iterate(criterion)
                    if getattr(node, "table", None) is not None
                )
            locking = statement._for_update_arg is not None
        if not repository and is_select and locking and ("executions", "id") in columns:
            check(where_columns == {("executions", "id")}, "Result owned execution query association")
            if not before:
                check(self.connection is None, "Result duplicate owned connection")
                self.connection = connection
                self.transaction = connection.get_transaction()
                check(self.transaction is not None, "Result owned transaction absent")
            return
        attempt_read = (
            not repository
            and is_select
            and locking
            and ("workflow_execution_attempts", "id") in columns
            and where_columns
            == {
                ("workflow_execution_attempts", "claim_token"),
                ("workflow_execution_attempts", "execution_id"),
                ("workflow_execution_attempts", "completed_at"),
            }
        )
        attempt_write = is_update and table == "workflow_execution_attempts" and not repository
        status_read = repository and is_select and columns == {("executions", "status")}
        context_read = repository and is_select and columns == {("executions", "execution_context")}
        logical_write = repository and is_update and table == "executions"
        if not any((attempt_read, attempt_write, status_read, context_read, logical_write)):
            return
        check(
            connection is self.connection
            and connection.get_transaction() is self.transaction
            and self.transaction is not None,
            "Result source transaction association",
        )
        if status_read or context_read:
            check(not locking and where_columns == {("executions", "id")}, "Result repository query association")
        if attempt_read and not before:
            self.mark(self.attempt, "read_ack")
        elif attempt_write:
            self.mark(self.attempt, "upper" if before else "write_ack")
        elif status_read and not before:
            self.mark(self.logical, "read_ack")
        elif context_read and before:
            self.mark(self.logical, "upper")
            self.logical["upper_kind"] = "context_read_dispatch"
        elif logical_write:
            if before and "upper" not in self.logical:
                self.mark(self.logical, "upper")
                self.logical["upper_kind"] = "write_execution_dispatch"
            elif not before:
                self.mark(self.logical, "write_ack")


@contextmanager
def sql_observer(engine, labels, clock):
    def before(connection, _cursor, _sql, _parameters, context, _many):
        clock.observe(connection, context, True)

    def after(connection, _cursor, _sql, _parameters, context, _many):
        clock.observe(connection, context, False)
        if _CLOCK_ACTOR.get() is not clock or context.compiled is None:
            return
        statement = context.compiled.statement
        if getattr(statement, "is_update", False):
            table = getattr(getattr(statement, "table", None), "name", None)
            if table in {"executions", "workflow_execution_attempts"}:
                check(len(labels) < 8, "Result SQL label bound")
                labels.append(table)

    def commit(connection):
        if _CLOCK_ACTOR.get() is clock and connection is clock.connection:
            check(connection.get_transaction() is clock.transaction, "Result source commit transaction")
            check(clock.commit_dispatch is None, "Result duplicate source commit")
            clock.commit_dispatch = utc_microseconds(datetime.now(UTC))

    owned = []
    pending = None
    try:
        for name, callback in (("before_cursor_execute", before), ("after_cursor_execute", after), ("commit", commit)):
            owned.append((name, callback))
            event.listen(engine.sync_engine, name, callback)
        yield
    except BaseException as error:
        pending = error
    finally:
        for name, callback in reversed(owned):
            try:
                check(event.contains(engine.sync_engine, name, callback), "Result SQL observer replaced")
                event.remove(engine.sync_engine, name, callback)
            except BaseException as error:
                if pending is None:
                    pending = error
    if pending is not None:
        raise pending


def sql_error(error: DBAPIError):
    actual = error.orig
    code = getattr(actual, "sqlstate", None)
    cause = getattr(actual, "__cause__", None)
    if code is None and cause is not None:
        code = getattr(cause, "sqlstate", None)
    constraint = getattr(actual, "constraint_name", None)
    if constraint is None and cause is not None:
        constraint = getattr(cause, "constraint_name", None)
    return code if code in ALLOW_CODES else None, constraint


def clock_roles_selected(payload):
    # Exact incumbent success/failure branch: result.get("attempt_token") truthiness.
    # Legacy forwarding/SQL labels stay genuine, without fenced-role admission.
    return bool(payload.get("attempt_token"))


async def python_result(cohort, fields, case, *, source_width=False):
    payload = materialize(fields, cohort.fixture["lane"])
    payload.update(sync=False, execution_id=str(cohort.ids["execution"]))
    token = cohort.token()
    if token is not None:
        payload["attempt_token"] = str(token)
    reference = "returned"
    source_code = None
    calls = []
    clock = SourceClockObserver()
    clock.collect_roles = clock_roles_selected(payload)
    async with asyncio.timeout_at(min(case.work_end, asyncio.get_running_loop().time() + 30)):
        async with actual_consumer() as consumer:
            with repository_observer(str(cohort.ids["execution"])) as calls:
                with sql_observer(database.get_engine(), cohort.sql_labels, clock):
                    try:
                        method = (
                            consumer._process_success
                            if cohort.fixture["lane"] == "success"
                            else consumer._process_failure
                        )
                        with clock_context(_CLOCK_ACTOR, clock):
                            await method(str(cohort.ids["execution"]), payload)
                            clock.commit_returned = clock.commit_dispatch is not None
                    except RuntimeError as error:
                        if str(error) != MISSING_FENCE:
                            raise
                        reference = "missing_fence"
                    except DBAPIError as error:
                        code, _ = sql_error(error)
                        source_code = code
                        check(source_width or code == "22003", "Result unexpected reference SQL failure")
                        reference = "source_width_failure" if source_width else "numeric_range"
    await cohort.empty_buffers()
    events = await cohort.events()
    case.request.node.user_properties.append(("result_source_sqlstate", source_code or "none"))
    return reference, calls, events, clock


def request_bytes(cohort, fields_json):
    fields = decode(fields_json.encode())
    projection = prepare_json_inputs(fields, cohort.fixture["lane"])
    # Preserve literal raw_fields bytes; only the freshly owned envelope is encoded.
    prefix = {
        "schema": "bifrost.test.workflow-sql/v1",
        "case_id": cohort.fixture["case_id"],
        "cohort": {
            "execution_id": str(cohort.ids["execution"]),
            "submitted_token": str(cohort.token()) if cohort.token() is not None else None,
        },
        "projection": projection,
    }
    encoded = json.dumps(prefix, allow_nan=False, ensure_ascii=True, separators=(",", ":")).encode()
    operation = (
        b',"operation":{"kind":"result","lane":'
        + json.dumps(cohort.fixture["lane"]).encode()
        + b',"raw_fields":'
        + fields_json.encode()
        + b"}}"
    )
    result = encoded[:-1] + operation
    check(len(result) <= 65536, "Result request bound")
    return result


def response(raw, case_id):
    value = decode(raw)
    closed(value, {"schema", "case_id", "decision", "transaction", "clock_witness"})
    check(
        value["schema"] == "bifrost.test.workflow-sql-result/v2" and value["case_id"] == case_id,
        "Result response identity",
    )
    decision = value["decision"]
    check(
        type(decision) is dict and decision.get("kind") in {"applied", "rejected", "infrastructure_failure"},
        "Result decision",
    )
    if decision["kind"] == "applied":
        closed(decision, {"kind", "plan"})
        closed(decision["plan"], {"execution", "attempt"})
        closed(decision["plan"]["execution"], EXEC_FIELDS)
        closed(decision["plan"]["attempt"], ATTEMPT_FIELDS)
    elif decision["kind"] == "rejected":
        closed(decision, {"kind", "reason"})
        check(decision["reason"] in REASONS, "Result domain rejection")
    else:
        closed(decision, {"kind", "stage", "class", "sqlstate"})
        check(
            decision["stage"] in SQL_STAGES | SETTLEMENT and decision["class"] in CLASSES, "Result failure vocabulary"
        )
        check(not (decision["stage"] in SQL_STAGES and decision["class"] == "Resource"), "Result invalid failure pair")
        check(decision["sqlstate"] is None or decision["sqlstate"] in ALLOW_CODES, "Result SQLSTATE allowlist")
    tx = value["transaction"]
    closed(tx, {"status", "affected_execution_rows", "affected_attempt_rows"})
    check(tx["status"] in {"committed", "rolled_back", "unknown"}, "Result transaction status")
    for key in ("affected_execution_rows", "affected_attempt_rows"):
        check(tx[key] is None or (type(tx[key]) is int and 0 <= tx[key] < 2**64), "Result affected row count")
    check(decision["kind"] != "applied" or tx["status"] == "committed", "Result applied commit admission")
    check(
        tx["status"] != "committed" or decision["kind"] in {"applied", "infrastructure_failure"},
        "Result committed disposition",
    )
    validate_clock_witness(value["clock_witness"], applied=decision["kind"] == "applied")
    if decision["kind"] == "rejected":
        check(
            value["clock_witness"] == {"complete": True, "attempt": None, "logical": None, "commit": None},
            "Result rejected clock roles",
        )
    return value


def validate_clock_witness(value, *, applied=False):
    closed(value, {"complete", "attempt", "logical", "commit"})
    check(type(value["complete"]) is bool, "Result clock complete type")
    events = []

    def role(name, fields, first):
        slot = value[name]
        if slot is None:
            return
        closed(slot, set(fields))
        for offset, key in enumerate(fields):
            if key == "upper_kind":
                continue
            item = slot[key]
            if item is None:
                continue
            closed(item, {"ordinal", "utc_us", "elapsed_ns"})
            expected = first + offset
            if name == "logical" and key == "write_ack":
                expected -= 1
            check(type(item["ordinal"]) is int and item["ordinal"] == expected, "Result clock role ordinal")
            for field in ("utc_us", "elapsed_ns"):
                check(type(item[field]) is int and 0 <= item[field] <= _CLOCK_LIMIT, "Result clock event bound")
            events.append(item)

    role("attempt", ("read_ack", "sample", "write_dispatch", "write_ack"), 1)
    role("logical", ("status_read_ack", "sample", "upper", "upper_kind", "write_ack"), 5)
    role("commit", ("dispatch", "ack"), 9 if value["logical"] is not None else 5)
    if value["logical"] is not None:
        check(
            type(value["logical"]["upper_kind"]) is str
            and value["logical"]["upper_kind"] in {"context_read_dispatch", "write_execution_dispatch"},
            "Result clock upper kind",
        )
    for previous, current in pairwise(events):
        check(
            previous["ordinal"] < current["ordinal"]
            and previous["elapsed_ns"] <= current["elapsed_ns"]
            and previous["utc_us"] <= current["utc_us"],
            "Result clock event order",
        )
    if applied:
        check(
            value["complete"] and value["attempt"] is not None and value["commit"] is not None,
            "Result clock incomplete",
        )
        for name in ("attempt", "logical", "commit"):
            slot = value[name]
            if slot is not None:
                check(all(item is not None for item in slot.values()), "Result clock incomplete")
    return value


def source_clock_rows(clock, attempt, execution, before):
    check(clock.commit_returned and clock.commit_dispatch is not None, "Result source commit acknowledgment")
    check(set(clock.attempt) == {"read_ack", "upper", "write_ack"}, "Result source attempt roles")
    sample = utc_microseconds(attempt["completed_at"])
    check(attempt["heartbeat_at"] == attempt["completed_at"], "Result attempt clock equality")
    check(
        clock.attempt["read_ack"] <= sample <= clock.attempt["upper"] <= clock.attempt["write_ack"],
        "Result source attempt interval",
    )
    check(type(clock.logical_selected) is bool, "Result source logical selection")
    check(set(clock.logical) == {"read_ack", "upper", "upper_kind", "write_ack"}, "Result source logical roles")
    check(
        clock.attempt["write_ack"]
        <= clock.logical["read_ack"]
        <= clock.logical["upper"]
        <= clock.logical["write_ack"]
        <= clock.commit_dispatch,
        "Result source role order",
    )
    if clock.logical_selected:
        logical = utc_microseconds(execution["completed_at"])
        check(clock.logical["read_ack"] <= logical <= clock.logical["upper"], "Result source logical interval")
    else:
        check(execution["completed_at"] == before["completed_at"], "Result logical clock Keep")


def native_clock_rows(witness, attempt, execution, before, logical_selected):
    validate_clock_witness(witness, applied=True)
    selected = witness["logical"] is not None
    check(type(logical_selected) is bool and selected == logical_selected, "Result actual logical role selection")
    sample = witness["attempt"]["sample"]["utc_us"]
    check(
        utc_microseconds(attempt["completed_at"]) == sample and utc_microseconds(attempt["heartbeat_at"]) == sample,
        "Result native attempt sample association",
    )
    if selected:
        check(
            utc_microseconds(execution["completed_at"]) == witness["logical"]["sample"]["utc_us"],
            "Result native logical sample association",
        )
    else:
        check(execution["completed_at"] == before["completed_at"], "Result logical clock Keep")


def clock_comparison_controls():
    # Synthetic bounded role/row views exercise the SAME actual admission helpers.
    base = 1700000000000000

    def event_value(ordinal):
        return {"ordinal": ordinal, "utc_us": base + ordinal, "elapsed_ns": ordinal}

    witness = {
        "complete": True,
        "attempt": {
            key: event_value(i) for i, key in enumerate(("read_ack", "sample", "write_dispatch", "write_ack"), 1)
        },
        "logical": {
            **{key: event_value(i) for i, key in enumerate(("status_read_ack", "sample", "upper", "write_ack"), 5)},
            "upper_kind": "context_read_dispatch",
        },
        "commit": {"dispatch": event_value(9), "ack": event_value(10)},
    }

    def stamp(offset):
        from datetime import timedelta

        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=base + offset)

    attempt = {"completed_at": stamp(2), "heartbeat_at": stamp(2)}
    execution = {"completed_at": stamp(6)}
    before = {"completed_at": stamp(0)}
    source = SourceClockObserver()
    source.attempt = {"read_ack": base + 1, "upper": base + 3, "write_ack": base + 4}
    source.logical = {
        "read_ack": base + 5,
        "upper": base + 7,
        "write_ack": base + 8,
        "upper_kind": "context_read_dispatch",
    }
    source.logical_selected = source.commit_returned = True
    source.commit_dispatch = base + 9
    source_clock_rows(source, attempt, execution, before)
    native_clock_rows(witness, attempt, execution, before, True)
    keep = deepcopy(witness)
    keep["logical"] = None
    keep["commit"] = {"dispatch": event_value(5), "ack": event_value(6)}
    native_clock_rows(keep, attempt, before, before, False)
    source.logical_selected = False
    source_clock_rows(source, attempt, before, before)
    source.logical_selected = True

    def rejects(callback, label):
        try:
            callback()
        except AssertionError as error:
            check(str(error) == label, "Result clock control wrong rejection")
        else:
            raise AssertionError("Result clock drift admitted")

    # Same scoping helper uses actual payload truthiness, not case/expected outcome.
    check(clock_roles_selected({"attempt_token": "owned-fence"}), "Result fenced clock scope")
    for payload in ({}, {"attempt_token": None}, {"attempt_token": ""}):
        check(not clock_roles_selected(payload), "Result unfenced clock scope")
    inactive = SourceClockObserver()
    inactive.collect_roles = clock_roles_selected({})
    with clock_context(_CLOCK_ACTOR, inactive):
        inactive.observe(None, None, True)
    check(
        inactive.connection is None and not inactive.attempt and not inactive.logical,
        "Result unfenced roles collected",
    )

    # Confined before/after snapshots exercise actual foreign preservation, not
    # ledger ordinal errors. Empty attempts prevent irrelevant sample admission.
    class SnapshotCohort:
        def __init__(self):
            self.ids = {
                "execution": UUID("00000000-0000-0000-0000-000000000001"),
                "foreign": UUID("00000000-0000-0000-0000-000000000002"),
                "attempt": UUID("00000000-0000-0000-0000-000000000003"),
            }

    cohort = SnapshotCohort()
    snapshot = {
        "executions": [
            {"id": cohort.ids[role], "completed_at": stamp(0), "result": {"sentinel": 1}}
            for role in ("execution", "foreign")
        ],
        "attempts": [],
        "logs": [],
        "context_storage": {},
    }
    compare_rows(snapshot, snapshot, snapshot, snapshot, cohort, cohort, source, witness)
    foreign_clock = deepcopy(snapshot)
    foreign_clock["executions"][1]["completed_at"] = stamp(1)
    rejects(
        lambda: compare_rows(foreign_clock, foreign_clock, snapshot, snapshot, cohort, cohort, source, witness),
        "Result foreign clock changed",
    )
    foreign_sentinel = deepcopy(snapshot)
    foreign_sentinel["executions"][1]["result"] = {"sentinel": 2}
    rejects(
        lambda: compare_rows(foreign_sentinel, foreign_sentinel, snapshot, snapshot, cohort, cohort, source, witness),
        "Result collateral or unlisted field changed",
    )

    swapped = deepcopy(witness)
    # Equal UTC values cannot rescue exchanged semantic ordinal roles.
    for slot in (swapped["attempt"], swapped["logical"], swapped["commit"]):
        for item in slot.values():
            if type(item) is dict:
                item["utc_us"] = base
    swapped["attempt"]["sample"], swapped["logical"]["sample"] = (
        swapped["logical"]["sample"],
        swapped["attempt"]["sample"],
    )
    rejects(lambda: validate_clock_witness(swapped, applied=True), "Result clock role ordinal")
    role_objects = {**witness, "attempt": witness["logical"], "logical": witness["attempt"]}
    rejects(lambda: validate_clock_witness(role_objects, applied=True), "closed Result schema")
    early = deepcopy(witness)
    early["logical"]["sample"]["ordinal"] = 4
    rejects(lambda: validate_clock_witness(early, applied=True), "Result clock role ordinal")
    rejects(
        lambda: native_clock_rows(
            witness, {**attempt, "completed_at": stamp(6), "heartbeat_at": stamp(6)}, execution, before, True
        ),
        "Result native attempt sample association",
    )
    rejects(
        lambda: native_clock_rows(witness, {**attempt, "heartbeat_at": stamp(3)}, execution, before, True),
        "Result native attempt sample association",
    )
    rejects(lambda: native_clock_rows(keep, attempt, execution, before, False), "Result logical clock Keep")
    rejects(
        lambda: native_clock_rows(witness, attempt, {"completed_at": stamp(2)}, before, True),
        "Result native logical sample association",
    )
    rejects(
        lambda: source_clock_rows(source, {**attempt, "heartbeat_at": stamp(3)}, execution, before),
        "Result attempt clock equality",
    )
    foreign = deepcopy(witness)
    foreign["commit"]["ack"]["ordinal"] = 11
    rejects(lambda: validate_clock_witness(foreign, applied=True), "Result clock role ordinal")
    for bad, label in (
        ({**witness, "extra": None}, "closed Result schema"),
        ({key: value for key, value in witness.items() if key != "attempt"}, "closed Result schema"),
        ({**witness, "attempt": None}, "Result clock incomplete"),
        ({**witness, "complete": False}, "Result clock incomplete"),
    ):
        rejects(lambda bad=bad: validate_clock_witness(bad, applied=True), label)
    rejects(
        lambda: source_clock_rows(source, attempt, {"completed_at": stamp(4)}, before), "Result source logical interval"
    )
    pending = KeyboardInterrupt()
    try:
        with clock_context(_CLOCK_ACTOR, source):
            raise pending
    except BaseException as caught:
        check(caught is pending, "Result clock control original identity")
    else:
        raise AssertionError("Result clock control swallowed original")
    bad_bound = deepcopy(witness)
    bad_bound["attempt"]["sample"]["utc_us"] = _CLOCK_LIMIT + 1
    rejects(lambda: validate_clock_witness(bad_bound, applied=True), "Result clock event bound")
    bad_type = deepcopy(witness)
    bad_type["attempt"]["sample"]["ordinal"] = True
    rejects(lambda: validate_clock_witness(bad_type, applied=True), "Result clock role ordinal")
    bad_upper = deepcopy(witness)
    bad_upper["logical"]["upper_kind"] = "unknown"
    rejects(lambda: validate_clock_witness(bad_upper, applied=True), "Result clock upper kind")
    bad_null = deepcopy(witness)
    bad_null["attempt"]["sample"] = None
    rejects(lambda: validate_clock_witness(bad_null, applied=True), "Result clock incomplete")
    bad_elapsed = deepcopy(witness)
    bad_elapsed["attempt"]["sample"]["elapsed_ns"] = 0
    rejects(lambda: validate_clock_witness(bad_elapsed, applied=True), "Result clock event order")
    rejects(lambda: decode(b'{"complete":true,"complete":false}'), "duplicate Result key")
    rejects(
        lambda: response(
            json.dumps(
                {
                    "schema": "bifrost.test.workflow-sql-result/v1",
                    "case_id": "synthetic",
                    "decision": {},
                    "transaction": {},
                    "clock_witness": witness,
                }
            ).encode(),
            "synthetic",
        ),
        "Result response identity",
    )


def mapped(value, cohort):
    if isinstance(value, UUID):
        names = [name for name, identity in cohort.ids.items() if value == identity]
        check(len(names) == 1, "Result undeclared UUID normalization")
        return names[0]
    if isinstance(value, ExecutionStatus):
        return value.value
    if isinstance(value, float):
        return ("float_bits", struct.pack(">d", value).hex())
    if isinstance(value, list):
        return [mapped(v, cohort) for v in value]
    if isinstance(value, dict):
        return {mapped(k, cohort): mapped(v, cohort) for k, v in value.items()}
    return value


def compare_rows(left, right, left_before, right_before, left_cohort, right_cohort, left_clock, right_clock):
    clocks = {"executions": {"completed_at"}, "attempts": {"completed_at", "heartbeat_at"}}
    for table in ("executions", "attempts", "logs"):
        check(len(left[table]) == len(right[table]), "Result committed row cardinality")

        def keyed(snapshot, cohort, selected_table=table):
            return {mapped(row["id"], cohort): row for row in snapshot[selected_table]}

        la, ra = keyed(left, left_cohort), keyed(right, right_cohort)
        lb, rb = keyed(left_before, left_cohort), keyed(right_before, right_cohort)
        check(set(la) == set(ra) == set(lb) == set(rb), "Result committed identities")
        for identity in la:
            check(set(la[identity]) == set(ra[identity]), "Result column roster")
            for key in la[identity]:
                x, y = la[identity][key], ra[identity][key]
                xold, yold = lb[identity][key], rb[identity][key]
                if key in clocks.get(table, set()) and (x != xold or y != yold):
                    check(x != xold and y != yold, "Result clock Keep mismatch")
                    check(identity not in {"foreign", "foreign_attempt"}, "Result foreign clock changed")
                else:
                    check(mapped(x, left_cohort) == mapped(y, right_cohort), "Result committed field mismatch")
                allowed = (
                    {
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
                        *METRICS,
                    }
                    if table == "executions"
                    else ATTEMPT_FIELDS - {"started_at"}
                    if table == "attempts"
                    else set()
                )
                if identity in {"foreign", "foreign_attempt"} or table == "logs" or key not in allowed:
                    check(x == xold and y == yold, "Result collateral or unlisted field changed")
    left_execution = next(row for row in left["executions"] if row["id"] == left_cohort.ids["execution"])
    right_execution = next(row for row in right["executions"] if row["id"] == right_cohort.ids["execution"])
    left_attempt = next((row for row in left["attempts"] if row["id"] == left_cohort.ids["attempt"]), None)
    right_attempt = next((row for row in right["attempts"] if row["id"] == right_cohort.ids["attempt"]), None)
    if (
        left_attempt is not None
        and right_attempt is not None
        and left_attempt["completed_at"]
        != next(row["completed_at"] for row in left_before["attempts"] if row["id"] == left_cohort.ids["attempt"])
    ):
        left_old = next(row for row in left_before["executions"] if row["id"] == left_cohort.ids["execution"])
        right_old = next(row for row in right_before["executions"] if row["id"] == right_cohort.ids["execution"])
        source_clock_rows(left_clock, left_attempt, left_execution, left_old)
        native_clock_rows(right_clock, right_attempt, right_execution, right_old, left_clock.logical_selected)
        if left_clock.logical_selected:
            check(
                right_clock["logical"]["upper_kind"] == left_clock.logical["upper_kind"],
                "Result actual logical upper branch",
            )
    check(
        mapped(left["context_storage"], left_cohort) == mapped(right["context_storage"], right_cohort),
        "Result SQLNULL JSONnull distinction",
    )
    for snapshot, cohort in ((left, left_cohort), (right, right_cohort)):
        for row in snapshot["attempts"]:
            if (
                row["id"] == cohort.ids["attempt"]
                and row["completed_at"] != SEED_TIME
                and row["completed_at"] is not None
            ):
                check(row["heartbeat_at"] == row["completed_at"], "Result attempt clock equality")
                owned = next(r for r in snapshot["executions"] if r["id"] == cohort.ids["execution"])
                if owned["completed_at"] != SEED_TIME:
                    check(row["completed_at"] <= owned["completed_at"], "Result source clock ordering")


async def paired_result(engine, fixture, case):
    dsn = driver_dsn(engine)
    source_admission()
    cohorts = []
    for _ in range(2):
        cohort = ResultCohort(engine, fixture)
        case.cohorts.append(cohort)
        cohorts.append(cohort)
        await cohort.seed()
    python, rust = cohorts
    before_python, before_rust = await python.snapshot(), await rust.snapshot()
    check(
        {mapped(r["id"], python): mapped(r, python) for r in before_python["executions"]}
        == {mapped(r["id"], rust): mapped(r, rust) for r in before_rust["executions"]},
        "Result seed execution mismatch",
    )
    check(
        {mapped(r["id"], python): mapped(r, python) for r in before_python["attempts"]}
        == {mapped(r["id"], rust): mapped(r, rust) for r in before_rust["attempts"]},
        "Result seed attempt mismatch",
    )
    fields = decode(fixture["raw_fields_json"].encode())
    first_error = None
    reference = None
    calls = []
    observed_events = []
    clock_python = None
    result = None
    try:
        reference, calls, observed_events, clock_python = await python_result(python, fields, case)
    except BaseException as error:
        first_error = error
    if first_error is None:
        try:
            code, raw = await invoke(
                case, request_bytes(rust, fixture["raw_fields_json"]), "apply-result", database_url=dsn
            )
            check(code in {0, 1}, "Result native apply exit")
            result = response(raw, fixture["case_id"])
            check(
                (code == 0) == (result["decision"]["kind"] != "infrastructure_failure"),
                "Result native exit disposition",
            )
        except BaseException as error:
            first_error = error
    # A failed pipe/unknown reply never establishes rollback. Read actual rows
    # independently after native actor settlement even on malformed output.
    if any(process.returncode is None for process in case.processes):
        case.retain_dependencies = True
        if first_error is not None:
            raise first_error
        raise AssertionError("Result actor settlement unavailable")
    after_python = after_rust = None
    try:
        async with asyncio.timeout_at(case.work_end):
            after_python, after_rust = await python.snapshot(), await rust.snapshot()
        case.request.node.user_properties.append(("result_independent_readback", "complete"))
        if result is not None:
            case.request.node.user_properties.append(("result_measured_transaction", result["transaction"]["status"]))
    except BaseException as error:
        case.retain_dependencies = True
        if first_error is None:
            first_error = error
    if first_error is not None:
        raise first_error
    check(result is not None and result["transaction"]["status"] != "unknown", "Result commit outcome unknown")
    check(
        after_python is not None and after_rust is not None and clock_python is not None,
        "Result readback admission",
    )
    await rust.empty_buffers()
    compare_rows(
        after_python, after_rust, before_python, before_rust, python, rust, clock_python, result["clock_witness"]
    )
    changed = (
        after_python["executions"] != before_python["executions"]
        or after_python["attempts"] != before_python["attempts"]
    )
    if result["decision"]["kind"] == "rejected":
        check(result["transaction"]["status"] == "rolled_back", "Result rejected rollback acknowledgment")
    if reference == "numeric_range":
        check(result["transaction"]["status"] == "rolled_back", "Result numeric rollback acknowledgment")
        check(
            result["decision"]["kind"] == "infrastructure_failure"
            and result["decision"]["class"] == "Database"
            and result["decision"]["sqlstate"] == "22003",
            "Result actual numeric error parity",
        )
        check(
            after_python["executions"] == before_python["executions"]
            and after_python["attempts"] == before_python["attempts"],
            "Result reference overflow rollback",
        )
        check(
            after_rust["executions"] == before_rust["executions"] and after_rust["attempts"] == before_rust["attempts"],
            "Result native overflow rollback",
        )
    elif reference == "missing_fence":
        check(result["decision"] == {"kind": "rejected", "reason": "MissingFence"}, "Result missing fence precedence")
    else:
        check((result["decision"]["kind"] == "applied") == changed, "Result actual write eligibility")
    if changed:
        check(
            result["transaction"]["affected_execution_rows"] == 1
            and result["transaction"]["affected_attempt_rows"] == 1,
            "Result actual affected rows",
        )
        check(python.sql_labels[:2] == ["workflow_execution_attempts", "executions"], "Result emitted update order")
    return {
        "case_id": fixture["case_id"],
        "reference": reference,
        "native_kind": result["decision"]["kind"],
        "python_projection_calls": len(calls),
        "python_event_count": len(observed_events),
        "rust_events": "absent-held",
        "rust_logs": "absent-empty-buffer-profile",
    }


def number_vector(case_id):
    midpoint = "0.500000000000000055511151231257827021181583404541015625"
    values = {
        "p-005": "0.005",
        "p-015": "0.015",
        "p-neg-int-zero": "-0",
        "p-neg-float-zero": "-0.0",
        "p-exp-zero": "0e0",
        "p-exp-one": "1e0",
        "p-int-above53": "9007199254740993",
        "p-u64-max": "18446744073709551615",
        "p-long-midpoint-exact": midpoint,
        "p-long-midpoint-below": "0.500000000000000055511151231257827021181583404541015624",
        "p-long-midpoint-above": "0.500000000000000055511151231257827021181583404541015626",
        "p-minsubnormal": "5e-324",
        "p-overflow": "1e309",
        "p-underflow": "1e-9999",
        "p-cpu-int-i64-min": "-9223372036854775808",
        "p-cpu-int-i64-max": "9223372036854775807",
        "p-cpu-int-u64-max": "18446744073709551615",
        "p-cpu-int-below53": "9007199254740991",
        "p-cpu-int-above53": "9007199254740993",
        "p-cpu-int-neg-above53": "-9007199254740993",
    }
    check(case_id in values, "Result number vector")
    return values[case_id], "cpu" if case_id.startswith("p-cpu-") else "roi"


async def decode_control(case, case_id):
    if case_id == "p-005":
        clock_comparison_controls()
        case.request.node.user_properties.append(("result_clock_controls", 6))
    lexeme, role = number_vector(case_id)
    request = {"schema": "bifrost.test.workflow-sql-number/v1", "case_id": case_id, "role": role, "lexeme": lexeme}
    code, raw = await invoke(case, json.dumps(request).encode(), "decode-number")
    check(code == 0, "Result number decoder exit")
    value = decode(raw)
    closed(value, {"schema", "case_id", "kind", "integer", "float_bits", "admitted"})
    check(
        value["schema"] == "bifrost.test.workflow-sql-number-result/v1" and value["case_id"] == case_id,
        "Result number identity",
    )
    check(type(value["admitted"]) is bool, "Result number admitted type")
    # Genuine Python decoder and float(int), never a prepared Decimal/bits oracle.
    python = json.loads(lexeme)
    if isinstance(python, float) and not math.isfinite(python):
        check(
            value["kind"] == "invalid"
            and value["integer"] is None
            and value["float_bits"] is None
            and value["admitted"] is False,
            "Result nonfinite exact invalid disposition",
        )
        return
    kind = "float" if isinstance(python, float) else "signed" if lexeme.startswith("-") else "unsigned"
    check(value["kind"] == kind, "Result number lexical kind")
    check(value["integer"] == (str(python) if isinstance(python, int) else None), "Result number integer width")
    floating = float(python) if role == "cpu" else python if isinstance(python, float) else None
    if floating is None:
        check(value["float_bits"] is None, "Result integer unsolicited float")
    elif math.isfinite(floating):
        check(value["float_bits"] == struct.pack(">d", floating).hex(), "Result decoder actual float bits")
    else:
        check(value["admitted"] is False, "Result finite profile boundary")
    check(
        value["admitted"] == (not isinstance(python, float) or math.isfinite(python)), "Result number profile admission"
    )


def mutate_codec(payload: bytes, case_id: str) -> bytes:
    value = decode(payload)
    fields = value["operation"]["raw_fields"]

    def scalar(name, item):
        fields[name] = {"kind": "value", "value": item}

    if case_id == "c-duplicate-envelope":
        return b'{"schema":"bifrost.test.workflow-sql/v1",' + payload[1:]
    if case_id == "c-duplicate-nested":
        return payload.replace(b'"status":{"kind":"absent"}', b'"status":{"kind":"absent","kind":"absent"}', 1)
    if case_id == "c-unknown-field":
        value["unknown"] = 1
    elif case_id == "c-missing-required":
        del value["cohort"]
    elif case_id == "c-invalid-tag":
        fields["duration_ms"] = {"kind": "unknown"}
    elif case_id == "c-tag-extra-value":
        fields["duration_ms"] = {"kind": "absent", "value": 0}
    elif case_id == "c-value-null-loophole":
        scalar("duration_ms", None)
    elif case_id == "c-numeric-bool":
        scalar("duration_ms", True)
    elif case_id == "c-numeric-string":
        scalar("duration_ms", "7")
    elif case_id == "c-duration-over-i32":
        scalar("duration_ms", 2147483648)
    elif case_id in {"c-metric-over-i64", "c-cpu-over-u64", "c-cpu-below-i64"}:
        name = "peak_memory_bytes" if case_id == "c-metric-over-i64" else "cpu_total_seconds"
        number = (
            9223372036854775808
            if name == "peak_memory_bytes"
            else 18446744073709551616
            if case_id.endswith("over-u64")
            else -9223372036854775809
        )
        fields["metrics"] = {
            "kind": "value",
            "value": {k: {"kind": "value", "value": number} if k == name else {"kind": "absent"} for k in METRICS},
        }
    elif case_id in {"c-roi-over-u64", "c-float-overflow", "c-raw-roi-nan"}:
        fields["roi"] = {
            "kind": "value",
            "value": {
                "time_saved": {"kind": "null"},
                "value": {
                    "kind": "value",
                    "value": 18446744073709551616 if case_id == "c-roi-over-u64" else "RAW_NUMBER",
                },
            },
        }
    elif case_id == "c-surrogate":
        scalar("result", "\ud800")
        value["projection"]["prepared_result"] = fields["result"]
    elif case_id == "c-depth65":
        nested = 1
        for _ in range(65):
            nested = [nested]
        scalar("result", nested)
        value["projection"]["prepared_result"] = fields["result"]
    elif case_id == "c-request65537":
        return payload + b" " * (65537 - len(payload))
    elif case_id == "c-trailing-json":
        return payload + b"{}"
    elif case_id == "c-wrong-branch-field":
        value["operation"]["lane"] = "failure"
    elif case_id == "c-projection-class-mismatch":
        scalar("result", {"x": 1})
        value["projection"]["prepared_result"] = {"kind": "value", "value": []}
    elif case_id in {
        "c-json-data-float",
        "c-json-data-over-u64",
        "c-json-data-below-i64",
        "c-json-string-nul",
        "c-json-key-nul",
    }:
        item = {
            "c-json-data-float": 1.0,
            "c-json-data-over-u64": 18446744073709551616,
            "c-json-data-below-i64": -9223372036854775809,
            "c-json-string-nul": "x\x00y",
            "c-json-key-nul": {"x\x00y": 1},
        }[case_id]
        scalar("result", item)
        value["projection"]["prepared_result"] = fields["result"]
    else:
        check(case_id == "c-duplicate-nested", "Result unknown codec control")
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if case_id in {"c-float-overflow", "c-raw-roi-nan"}:
        raw = raw.replace(b'"RAW_NUMBER"', b"1e309" if case_id == "c-float-overflow" else b"NaN")
    return raw


async def codec_control(engine, case, case_id, fixture):
    cohort = ResultCohort(engine, fixture)
    case.cohorts.append(cohort)
    await cohort.seed()
    before = await cohort.snapshot()
    good = request_bytes(cohort, fixture["raw_fields_json"])
    bad = mutate_codec(good, case_id)
    code, raw = await invoke(case, bad, "apply-result", database_url=driver_dsn(engine))
    check(code == 2 and raw == b"", "Result malformed input pre-admission")
    after = await cohort.snapshot()
    check(before == after, "Result malformed input touched owned rows")
    await cohort.empty_buffers()


SCHEMA_ERRORS = {
    "q-duplicate-token": ("23505", "uq_workflow_execution_attempt_claim_token"),
    "q-two-active": ("23505", "uq_workflow_execution_attempt_active"),
    "q-duplicate-attempt-number": ("23505", "uq_workflow_execution_attempt_number"),
    "q-running-with-completion": ("23514", "ck_workflow_execution_attempt_terminal_time"),
    "q-running-without-start": ("23514", "ck_workflow_execution_attempt_state_shape"),
    "q-claimed-without-token": ("23514", "ck_workflow_execution_attempt_state_shape"),
    "q-dispatching-with-token": ("23514", "ck_workflow_execution_attempt_state_shape"),
    "q-unknown-phase": ("23514", "ck_workflow_execution_attempt_phase"),
    "q-foreign-execution-fk": ("23503", "workflow_execution_attempts_execution_id_fkey"),
}


async def schema_control(engine, case, case_id, fixture):
    from uuid import uuid4

    cohort = ResultCohort(engine, fixture)
    case.cohorts.append(cohort)
    await cohort.seed()
    # Each offending transaction uses otherwise legal shape and isolated conflict.
    before = await cohort.snapshot()
    attempt = dict(before["attempts"][0])
    attempt.update(
        id=uuid4(),
        execution_id=cohort.ids["execution"],
        attempt_number=2,
        claim_token=uuid4(),
        status="succeeded",
        phase="terminal",
        completed_at=SEED_TIME,
        started_at=SEED_TIME,
        published_at=SEED_TIME,
        claimed_at=SEED_TIME,
    )
    if case_id == "q-duplicate-token":
        attempt["execution_id"] = cohort.ids["foreign"]
        attempt["claim_token"] = cohort.ids["current_token"]
    elif case_id == "q-two-active":
        attempt.update(status="running", phase="execution", completed_at=None)
    elif case_id == "q-duplicate-attempt-number":
        attempt["attempt_number"] = 1
    elif case_id == "q-running-with-completion":
        attempt.update(status="running", phase="execution", completed_at=SEED_TIME)
    elif case_id == "q-running-without-start":
        attempt.update(status="running", phase="execution", completed_at=None, started_at=None)
    elif case_id == "q-claimed-without-token":
        attempt.update(status="claimed", phase="claim", completed_at=None, claim_token=None)
    elif case_id == "q-dispatching-with-token":
        attempt.update(
            status="dispatching",
            phase="dispatch",
            completed_at=None,
            published_at=None,
            claimed_at=None,
            started_at=None,
        )
    elif case_id == "q-unknown-phase":
        attempt["phase"] = "synthetic_unknown"
    elif case_id == "q-foreign-execution-fk":
        attempt["execution_id"] = cohort.ids["missing"]
    expected = SCHEMA_ERRORS[case_id]
    observed = None
    async with cohort.sessions() as db:
        try:
            db.add(WorkflowExecutionAttempt(**attempt))
            await db.flush()
        except DBAPIError as error:
            observed = sql_error(error)
        finally:
            await db.rollback()
    check(observed == expected, "Result actual named schema diagnostic")
    check(await cohort.snapshot() == before, "Result schema rollback changed rows")
    # Genuine valid continuation after rollback, independent session/row admission.
    fields = decode(fixture["raw_fields_json"].encode())
    reference, calls, _, _ = await python_result(cohort, fields, case)
    check(reference == "returned" and bool(calls), "Result schema genuine admission after rollback")
    continued = await cohort.snapshot()
    check(continued["attempts"] != before["attempts"], "Result schema valid continuation did not write")


async def source_control(engine, case, case_id, fixture):
    selected = deepcopy(fixture)
    selected["lane"] = (
        "failure" if case_id in {"x-OrphanedExecution", "x-ProcessCrashError", "x-WorkerShutdownError"} else "success"
    )
    selected["seed"].update(
        tracking=case_id != "x-legacy-unfenced",
        attempt="none" if case_id == "x-legacy-unfenced" else "running",
        token="missing" if case_id == "x-legacy-unfenced" else "current",
    )
    cohort = ResultCohort(engine, selected)
    case.cohorts.append(cohort)
    await cohort.seed()
    fields = {name: {"kind": "absent"} for name in (FAILURE if selected["lane"] == "failure" else SUCCESS)}
    if selected["lane"] == "failure":
        fields["error_type"] = {"kind": "value", "value": case_id[2:]}
    if case_id == "x-duration-outside-i32-source":
        fields["duration_ms"] = {"kind": "value", "value": 2147483648}
    if case_id == "x-metric-outside-i64-source":
        fields["metrics"] = {
            "kind": "value",
            "value": {
                k: {"kind": "value", "value": 9223372036854775808} if k == "peak_memory_bytes" else {"kind": "absent"}
                for k in METRICS
            },
        }
    if case_id in {"x-missing-logical-active-metadata", "x-missing-logical-no-metadata"}:
        async with cohort.sessions() as db:
            await db.execute(delete(Execution).where(Execution.id == cohort.ids["execution"]))
            await db.commit()
        if case_id.endswith("no-metadata"):
            await cohort.redis.delete(active_execution_key(str(cohort.ids["execution"])))
    if case_id == "x-unsupported-buffer":
        key = pending_changes_key(str(cohort.ids["execution"]))
        await cohort.redis.hset(key, "synthetic", "{}")
        rejected = False
        try:
            await cohort.empty_buffers()
        except AssertionError:
            rejected = True
        finally:
            await cohort.redis.delete(key)
        check(rejected, "Result unexpected buffer not rejected")
        await cohort.empty_buffers()
        return {"reference": "profile-excluded", "native": "not-invoked"}
    before = await cohort.snapshot()
    reference, calls, observed_events, _ = await python_result(
        cohort, fields, case, source_width=case_id in {"x-duration-outside-i32-source", "x-metric-outside-i64-source"}
    )
    after = await cohort.snapshot()
    if case_id in {"x-duration-outside-i32-source", "x-metric-outside-i64-source"}:
        check(
            reference == "source_width_failure"
            and before["executions"] == after["executions"]
            and before["attempts"] == after["attempts"],
            "Result source width/rollback characterization",
        )
    elif case_id.startswith("x-missing-logical"):
        check(
            before["executions"] == after["executions"] and not calls, "Result missing logical source characterization"
        )
    elif case_id == "x-legacy-unfenced":
        check(bool(calls) and before["executions"] != after["executions"], "Result actual legacy branch")
    else:
        check(bool(calls) and before["attempts"] != after["attempts"], "Result actual worker-loss source branch")
    return {"reference": reference, "native": "outside-private-profile", "event_count": len(observed_events)}
