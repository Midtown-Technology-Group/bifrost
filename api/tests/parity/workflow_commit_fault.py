"""Private F4 framing and observation primitives; never a coordinator authority."""

from __future__ import annotations

import asyncio
import json
import math
import re
import struct
from dataclasses import dataclass
from uuid import UUID

CONTROL_LIMIT = 4096
ROW_LIMIT = 65536
IPC_SCHEMA = "bifrost.test.workflow-commit-ipc/v1"
OBSERVER_SCHEMA = "bifrost.test.workflow-commit-observer/v1"


class FaultAdmissionError(Exception):
    """Closed local failure; deliberately contains no raw input or driver error."""

    def __init__(self, label: str):
        super().__init__(label)
        self.label = label


def require(condition: bool, label: str) -> None:
    if not condition:
        raise FaultAdmissionError(label)


def closed(value, keys):
    require(type(value) is dict and set(value) == set(keys), "closed_object")
    return value


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, "integer")
    return value


def canonical_uuid(value):
    require(
        type(value) is str
        and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value) is not None,
        "uuid",
    )
    require(str(UUID(value)) == value, "uuid")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate")
        result[key] = value
    return result


def _finite(value, depth=0):
    require(depth <= 64, "depth")
    if type(value) is dict:
        for key, child in value.items():
            require(type(key) is str and "\0" not in key, "key")
            _finite(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _finite(child, depth + 1)
    elif type(value) is float:
        require(math.isfinite(value), "nonfinite")
    else:
        require(value is None or type(value) in (str, int, bool), "json_type")
        if type(value) is str:
            require("\0" not in value, "nul")


def decode(raw: bytes):
    require(type(raw) is bytes and 0 < len(raw) <= ROW_LIMIT, "json_bound")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(FaultAdmissionError("nonfinite")),
        )
    except (ValueError, UnicodeError, RecursionError) as error:
        raise FaultAdmissionError("invalid_json") from error
    _finite(value)
    return value


def encode(value, limit):
    _finite(value)
    try:
        raw = json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    except (ValueError, UnicodeError, RecursionError) as error:
        raise FaultAdmissionError("invalid_json") from error
    require(0 < len(raw) <= limit, "frame_bound")
    return raw


def cycle(body, expected=None):
    integer(body["cycle"], 1, 2)
    require((body["cycle"], body["actor"]) in ((1, "python"), (2, "rust")), "cycle")
    if expected is not None:
        require((body["cycle"], body["actor"]) == expected, "cycle_order")


def finish_permission(body):
    closed(body, ("cycle", "actor"))
    cycle(body, (2, "rust"))


def query_witness(value):
    closed(value, ("xid", "backend_pid", "database_name", "server_address", "server_port", "server_version_num"))
    require(type(value["xid"]) is str and re.fullmatch(r"[1-9][0-9]{0,19}", value["xid"]) is not None, "xid")
    require(int(value["xid"]) <= 2**64 - 1, "xid")
    integer(value["backend_pid"], 1, 2**31 - 1)
    require(type(value["database_name"]) is str and 0 < len(value["database_name"].encode("utf-8")) <= 63, "database")
    import ipaddress

    require(
        type(value["server_address"]) is str
        and str(ipaddress.IPv4Address(value["server_address"])) == value["server_address"],
        "ipv4",
    )
    integer(value["server_port"], 1, 65535)
    require(
        type(value["server_version_num"]) is str
        and re.fullmatch(r"[1-9][0-9]{0,9}", value["server_version_num"]) is not None,
        "server_version",
    )
    require(160000 <= int(value["server_version_num"]) < 170000, "server_version")
    return value


def source_witness(value):
    closed(
        value,
        (
            "schema",
            "sdk_function_source",
            "sdk_call_count",
            "sdk_arguments_match",
            "sdk_result_zero",
            "sdk_call_error",
            "session_connection_join",
            "shared_export_source",
        ),
    )
    require(value["schema"] == "bifrost.test.f4-sdk-source/v1", "source_schema")
    integer(value["sdk_call_count"], 1, 1)
    for name in (
        "sdk_function_source",
        "sdk_arguments_match",
        "sdk_result_zero",
        "sdk_call_error",
        "session_connection_join",
        "shared_export_source",
    ):
        require(type(value[name]) is bool, "source_type")
        require(value[name] is (name != "sdk_call_error"), "source_incomplete")
    return value


@dataclass
class Deadline:
    """Local upper bound only; the supervising parent retains absolute authority."""

    work_end: float
    total_end: float

    @classmethod
    def admitted(cls, remaining_ms: int):
        integer(remaining_ms, 1, 75000)
        now = asyncio.get_running_loop().time()
        return cls(now + remaining_ms / 1000, now + remaining_ms / 1000 + 15)

    def remaining(self, cleanup=False):
        end = self.total_end if cleanup else self.work_end
        remaining = end - asyncio.get_running_loop().time()
        require(remaining > 0, "deadline")
        return remaining


class Channel:
    """One existing peer, incremental complete frames and monotonically retained sequences."""

    def __init__(self, reader, writer, invocation, outgoing_lane, incoming_lane, schema, deadline):
        self.reader = reader
        self.writer = writer
        self.invocation = canonical_uuid(invocation)
        self.outgoing_lane = outgoing_lane
        self.incoming_lane = incoming_lane
        self.schema = schema
        self.deadline = deadline
        self.sent = 0
        self.received = 0
        self.closed = False

    async def send(self, kind, body, *, data=False, cleanup=False):
        require(not self.closed, "channel_closed")
        require(self.sent < 2**64 - 1, "sequence_overflow")
        self.sent += 1
        raw = encode(
            {
                "schema": self.schema,
                "lane": self.outgoing_lane,
                "invocation": self.invocation,
                "seq": self.sent,
                "kind": kind,
                "body": body,
            },
            ROW_LIMIT if data else CONTROL_LIMIT,
        )
        async with asyncio.timeout(self.deadline.remaining(cleanup)):
            self.writer.write(struct.pack("!I", len(raw)) + raw)
            await self.writer.drain()

    async def receive(self, kind, keys, *, data=False, cleanup=False):
        require(not self.closed, "channel_closed")
        async with asyncio.timeout(self.deadline.remaining(cleanup)):
            try:
                prefix = await self.reader.readexactly(4)
                length = struct.unpack("!I", prefix)[0]
                require(0 < length <= (ROW_LIMIT if data else CONTROL_LIMIT), "frame_bound")
                value = decode(await self.reader.readexactly(length))
            except asyncio.IncompleteReadError as error:
                raise FaultAdmissionError("partial_frame") from error
        closed(value, ("schema", "lane", "invocation", "seq", "kind", "body"))
        require(
            value["schema"] == self.schema
            and value["lane"] == self.incoming_lane
            and value["invocation"] == self.invocation
            and value["kind"] == kind,
            "frame_identity",
        )
        require(self.received < 2**64 - 1, "sequence_overflow")
        integer(value["seq"], self.received + 1, self.received + 1)
        closed(value["body"], keys)
        self.received += 1
        return value["body"]

    async def close(self):
        if not self.closed:
            self.closed = True
            self.writer.close()
            async with asyncio.timeout(self.deadline.remaining(True)):
                await self.writer.wait_closed()


class SdkTrace:
    """Observe the natural SDK call; no import, callable replacement or application error injection."""

    def __init__(self, consumer_code, execution_id, source_check):
        self.consumer_code = consumer_code
        self.execution_id = execution_id
        self.source_check = source_check
        self.consumer_frame = None
        self.sdk_frame = None
        self.sdk_code = None
        self.session = None
        self.call_count = 0
        self.arguments_match = False
        self.result_zero = False
        self.function_source = False
        self.shared_source = False
        self.failed = False
        self.installed = False
        # Bound-method access makes new objects: retain ONE actual installed callback.
        self.callback = self._observe

    def _observe(self, frame, event, _argument):
        try:
            self.observe(frame, event)
        except BaseException:
            # Observation never injects a failure into genuine import/call/commit.
            self.failed = True
        if frame is self.consumer_frame or frame is self.sdk_frame:
            return self.callback
        return None

    def observe(self, frame, event):
        if frame.f_code is self.consumer_code:
            if self.consumer_frame is None:
                self.consumer_frame = frame
            require(frame is self.consumer_frame, "consumer_duplicate")
            if event == "line" and frame.f_lineno == 520:
                import sys

                function = frame.f_locals.get("flush_pending_changes")
                module = sys.modules.get("bifrost._sync")
                require(module is not None and function is vars(module).get("flush_pending_changes"), "sdk_function")
                require(callable(function), "sdk_function")
                code = getattr(function, "__code__", None)
                require(code is not None and self.source_check(module, code), "sdk_source")
                self.sdk_code = code
                self.function_source = True
                session = frame.f_locals.get("session")
                require(session is not None and frame.f_locals.get("execution_id") == self.execution_id, "sdk_session")
                if self.session is not None:
                    require(self.session is session, "sdk_session_changed")
                self.session = session
                package = sys.modules.get("bifrost")
                shared = sys.modules.get("shared.workspace_effects")
                require(package is not None and shared is not None, "shared_not_loaded")
                require(self.source_check(shared, None), "shared_source")
                for name in ("WorkflowBounds", "WorkflowEffect"):
                    require(
                        vars(package).get(name) is vars(shared).get(name) and vars(shared).get(name) is not None,
                        "shared_export",
                    )
                self.shared_source = True
            elif event == "line" and frame.f_lineno == 521:
                value = frame.f_locals.get("changes_count")
                require(
                    self.sdk_frame is not None and self.arguments_match and type(value) is int and value == 0,
                    "sdk_result",
                )
                self.result_zero = True
            elif event == "line" and frame.f_lineno in (523, 524):
                self.failed = True
        elif self.sdk_code is not None and frame.f_code is self.sdk_code:
            if self.sdk_frame is None:
                self.sdk_frame = frame
                self.call_count += 1
            require(frame is self.sdk_frame, "sdk_duplicate")
            require(
                frame.f_locals.get("execution_id") == self.execution_id
                and frame.f_locals.get("session") is self.session,
                "sdk_arguments",
            )
            self.arguments_match = True
        # Coroutine return/exception events never establish completed success.

    def install(self):
        import sys

        require(sys.gettrace() is None, "prior_trace")
        try:
            sys.settrace(self.callback)
        finally:
            self.installed = sys.gettrace() is self.callback
        require(self.installed, "trace_install")

    def restore(self):
        import sys

        if self.installed:
            require(sys.gettrace() is self.callback, "foreign_trace")
            sys.settrace(None)
            self.installed = False
            require(sys.gettrace() is None, "trace_restore")

    def witness(self, joined):
        import sys

        require(self.installed and sys.gettrace() is self.callback, "trace_lost")
        value = {
            "schema": "bifrost.test.f4-sdk-source/v1",
            "sdk_function_source": self.function_source,
            "sdk_call_count": self.call_count,
            "sdk_arguments_match": self.arguments_match,
            "sdk_result_zero": self.result_zero,
            "sdk_call_error": self.failed,
            "session_connection_join": joined,
            "shared_export_source": self.shared_source,
        }
        return source_witness(value)

    def release_references(self):
        self.consumer_frame = None
        self.sdk_frame = None
        self.session = None


class SessionJoins:
    """Public class event captures; SessionTransaction is NOT Connection RootTransaction."""

    def __init__(self, engine, event_api, session_class):
        self.engine = engine
        self.event_api = event_api
        self.session_class = session_class
        self.records = []
        self.failed = False
        self.installed = False
        self.callback = self._capture

    def _capture(self, session, transaction, connection):
        try:
            if connection.engine is self.engine:
                require(len(self.records) < 16, "session_join_bound")
                self.records.append((session, transaction, connection))
        except BaseException:
            self.failed = True

    def install(self):
        require(not self.event_api.contains(self.session_class, "after_begin", self.callback), "prior_listener")
        try:
            self.event_api.listen(self.session_class, "after_begin", self.callback)
        finally:
            self.installed = self.event_api.contains(self.session_class, "after_begin", self.callback)
        require(self.installed, "listener_install")

    def join(self, async_session, selected_connection):
        require(not self.failed and async_session is not None, "session_join")
        matches = [
            entry
            for entry in self.records
            if entry[0] is async_session.sync_session and entry[2] is selected_connection
        ]
        require(len(matches) == 1, "session_join_ambiguous")
        return True

    def restore(self):
        if self.installed:
            require(self.event_api.contains(self.session_class, "after_begin", self.callback), "listener_lost")
            self.event_api.remove(self.session_class, "after_begin", self.callback)
            self.installed = False
            require(not self.event_api.contains(self.session_class, "after_begin", self.callback), "listener_restore")

    def release_references(self):
        self.records.clear()


def cleanup_failure(captured, original, error):
    """Latch custody failure separately from the exact first exception."""
    captured["failed"] = True
    return original if original is not None else error


def acquire_task(coroutine, custody, constructor=None):
    """Protect the already-created coroutine until its actual Task is returned."""
    if constructor is None:
        constructor = asyncio.create_task
    try:
        return constructor(coroutine)
    except BaseException as original:
        try:
            coroutine.close()
        except BaseException as error:
            cleanup_failure(custody, original, error)
        raise


def close_unadmitted_writer(writer, custody, original=None):
    """No invented prebootstrap wait clock; parent retains stream/process custody."""
    try:
        writer.close()
    except BaseException as error:
        return cleanup_failure(custody, original, error)
    return original


async def readback_connection(engine, read, deadline, custody):
    """Own direct connection settlement without AsyncConnection's shielded exit."""
    connection = transaction = None
    original = result = None
    record = {"connection": None, "closed": False}
    custody.readbacks.append(record)
    try:
        connection = engine.connect()
        record["connection"] = connection
        require(await connection is connection, "readback_connection_identity")
        transaction = await connection.begin()
        await connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        result = await read(connection)
    except BaseException as error:
        original = error
    finally:
        for operation, is_close in (
            (transaction.rollback if transaction is not None else None, False),
            (connection.close if connection is not None else None, True),
        ):
            if operation is not None:
                try:
                    async with asyncio.timeout(deadline.remaining(True)):
                        await operation()
                    if is_close:
                        require(connection.closed is True, "readback_close_unknown")
                        record["closed"] = True
                except BaseException as error:
                    custody.failed = True
                    if original is None:
                        original = error
        if connection is None:
            # No returned connection to close; acquisition failure itself remains RED.
            record["closed"] = True
    if original is not None:
        raise original
    return result


def independent_cleanup(actions, original=None):
    """All independent attempts; retain exact original exception/control identity."""
    failures = []
    for action in actions:
        try:
            action()
        except BaseException as error:
            failures.append(error)
    if original is not None:
        raise original
    if failures:
        raise failures[0]


class RelayPair:
    """One opaque socket pair; all counters are actual reads/sends, never SQL inference."""

    def __init__(self, selector, frontend, upstream):
        self.selector = selector
        self.frontend = frontend
        self.upstream = upstream
        self.to_upstream = bytearray()
        self.to_frontend = bytearray()
        self.armed = False
        self.closed = False
        self.close_failed = False
        self.upstream_bytes = 0
        self.suppressed_bytes = 0
        self.frontend_bytes = 0
        self.received_upstream_bytes = 0
        self.sent_frontend_bytes = 0
        self.registered = set()
        for endpoint in (frontend, upstream):
            endpoint.setblocking(False)
        self.interests()

    @staticmethod
    def count(old, addition):
        require(type(addition) is int and addition >= 0 and old + addition <= 2**64 - 1, "counter_overflow")
        return old + addition

    def _interest(self, endpoint, mask, role):
        if not mask:
            if endpoint in self.registered:
                self.selector.unregister(endpoint)
                self.registered.remove(endpoint)
        elif endpoint in self.registered:
            self.selector.modify(endpoint, mask, role)
        else:
            self.selector.register(endpoint, mask, role)
            self.registered.add(endpoint)

    def interests(self):
        import selectors

        if self.closed:
            return
        frontend_mask = selectors.EVENT_READ if len(self.to_upstream) < ROW_LIMIT else 0
        if not self.armed and self.to_frontend:
            frontend_mask |= selectors.EVENT_WRITE
        upstream_mask = selectors.EVENT_READ if self.armed or len(self.to_frontend) < ROW_LIMIT else 0
        if self.to_upstream:
            upstream_mask |= selectors.EVENT_WRITE
        self._interest(self.frontend, frontend_mask, "frontend")
        self._interest(self.upstream, upstream_mask, "upstream")

    def arm(self):
        require(not self.closed and not self.armed, "arm_state")
        self.armed = True
        self.suppressed_bytes = self.count(self.suppressed_bytes, len(self.to_frontend))
        self.to_frontend.clear()
        # This synchronous update re-enables upstream READ even when its old
        # full downstream queue disabled it. ACK is emitted only after return.
        self.interests()

    def dispatch(self, role, mask):
        import selectors

        require(not self.closed and role in ("frontend", "upstream"), "pair_state")
        endpoint = self.frontend if role == "frontend" else self.upstream
        outgoing = self.to_frontend if role == "frontend" else self.to_upstream
        incoming = self.to_upstream if role == "frontend" else self.to_frontend
        if mask & selectors.EVENT_READ:
            capacity = 16384 if role == "upstream" and self.armed else min(16384, ROW_LIMIT - len(incoming))
            if capacity:
                try:
                    data = endpoint.recv(capacity)
                except BlockingIOError:
                    data = None
                if data is not None:
                    require(bool(data), "unselected_eof")
                    if role == "frontend":
                        self.frontend_bytes = self.count(self.frontend_bytes, len(data))
                        incoming.extend(data)
                    else:
                        self.received_upstream_bytes = self.count(self.received_upstream_bytes, len(data))
                        if self.armed:
                            self.suppressed_bytes = self.count(self.suppressed_bytes, len(data))
                            self.close()
                            return
                        incoming.extend(data)
        # A stale queued WRITE dispatch cannot send after arm or socket close.
        if mask & selectors.EVENT_WRITE and outgoing and not self.closed and not (role == "frontend" and self.armed):
            try:
                sent = endpoint.send(outgoing)
            except BlockingIOError:
                sent = 0
            require(type(sent) is int and 0 <= sent <= len(outgoing), "send_count")
            if role == "upstream":
                self.upstream_bytes = self.count(self.upstream_bytes, sent)
            else:
                self.sent_frontend_bytes = self.count(self.sent_frontend_bytes, sent)
            del outgoing[:sent]
        require(len(self.to_upstream) <= ROW_LIMIT and len(self.to_frontend) <= ROW_LIMIT, "queue_bound")
        self.interests()

    def close(self):
        original = None
        self.closed = True
        for endpoint in (self.frontend, self.upstream):
            if endpoint in self.registered:
                try:
                    self.selector.unregister(endpoint)
                    self.registered.remove(endpoint)
                except BaseException as error:
                    self.close_failed = True
                    if original is None:
                        original = error
            try:
                endpoint.close()
            except BaseException as error:
                self.close_failed = True
                if original is None:
                    original = error
        self.to_upstream.clear()
        self.to_frontend.clear()
        if original is not None:
            raise original

    def settled(self):
        require(
            self.closed
            and not self.close_failed
            and not self.registered
            and self.frontend.fileno() == -1
            and self.upstream.fileno() == -1,
            "pair_not_closed",
        )
        return {
            "upstream_bytes": self.upstream_bytes,
            "downstream_suppressed_bytes": self.suppressed_bytes,
            "transport_closed": True,
        }


# Exact source-selected SQL/cells; no arbitrary query or selector input.
FIXED_SQL = {
    "executions": "SELECT id::text AS row_id,row_bytes,CASE WHEN row_bytes<=65536 THEN row_data::text ELSE NULL END AS row_json FROM (SELECT id,row_data,pg_catalog.octet_length(row_data::text) AS row_bytes FROM (SELECT id,pg_catalog.jsonb_build_object('id',pg_catalog.jsonb_build_object('sql_null',\"id\" IS NULL,'value',\"id\"::text),'workflow_name',pg_catalog.jsonb_build_object('sql_null',\"workflow_name\" IS NULL,'value',\"workflow_name\"::text),'workflow_version',pg_catalog.jsonb_build_object('sql_null',\"workflow_version\" IS NULL,'value',\"workflow_version\"::text),'status',pg_catalog.jsonb_build_object('sql_null',\"status\" IS NULL,'value',\"status\"::text),'parameters',pg_catalog.jsonb_build_object('sql_null',\"parameters\" IS NULL,'value',\"parameters\"::text),'result',pg_catalog.jsonb_build_object('sql_null',\"result\" IS NULL,'value',\"result\"::text),'result_type',pg_catalog.jsonb_build_object('sql_null',\"result_type\" IS NULL,'value',\"result_type\"::text),'variables',pg_catalog.jsonb_build_object('sql_null',\"variables\" IS NULL,'value',\"variables\"::text),'execution_context',pg_catalog.jsonb_build_object('sql_null',\"execution_context\" IS NULL,'value',\"execution_context\"::text),'error_message',pg_catalog.jsonb_build_object('sql_null',\"error_message\" IS NULL,'value',\"error_message\"::text),'started_at',pg_catalog.jsonb_build_object('sql_null',\"started_at\" IS NULL,'value',(extract(epoch FROM \"started_at\") * 1000000)::bigint),'completed_at',pg_catalog.jsonb_build_object('sql_null',\"completed_at\" IS NULL,'value',(extract(epoch FROM \"completed_at\") * 1000000)::bigint),'duration_ms',pg_catalog.jsonb_build_object('sql_null',\"duration_ms\" IS NULL,'value',\"duration_ms\"),'peak_memory_bytes',pg_catalog.jsonb_build_object('sql_null',\"peak_memory_bytes\" IS NULL,'value',\"peak_memory_bytes\"),'process_rss_bytes',pg_catalog.jsonb_build_object('sql_null',\"process_rss_bytes\" IS NULL,'value',\"process_rss_bytes\"),'cpu_user_seconds',pg_catalog.jsonb_build_object('sql_null',\"cpu_user_seconds\" IS NULL,'value',pg_catalog.encode(pg_catalog.float8send(\"cpu_user_seconds\"),'hex')),'cpu_system_seconds',pg_catalog.jsonb_build_object('sql_null',\"cpu_system_seconds\" IS NULL,'value',pg_catalog.encode(pg_catalog.float8send(\"cpu_system_seconds\"),'hex')),'cpu_total_seconds',pg_catalog.jsonb_build_object('sql_null',\"cpu_total_seconds\" IS NULL,'value',pg_catalog.encode(pg_catalog.float8send(\"cpu_total_seconds\"),'hex')),'time_saved',pg_catalog.jsonb_build_object('sql_null',\"time_saved\" IS NULL,'value',\"time_saved\"),'value',pg_catalog.jsonb_build_object('sql_null',\"value\" IS NULL,'value',\"value\"::text),'executed_by',pg_catalog.jsonb_build_object('sql_null',\"executed_by\" IS NULL,'value',\"executed_by\"::text),'executed_by_name',pg_catalog.jsonb_build_object('sql_null',\"executed_by_name\" IS NULL,'value',\"executed_by_name\"::text),'organization_id',pg_catalog.jsonb_build_object('sql_null',\"organization_id\" IS NULL,'value',\"organization_id\"::text),'form_id',pg_catalog.jsonb_build_object('sql_null',\"form_id\" IS NULL,'value',\"form_id\"::text),'workflow_id',pg_catalog.jsonb_build_object('sql_null',\"workflow_id\" IS NULL,'value',\"workflow_id\"::text),'solution_deployment_id',pg_catalog.jsonb_build_object('sql_null',\"solution_deployment_id\" IS NULL,'value',\"solution_deployment_id\"::text),'runtime_mode',pg_catalog.jsonb_build_object('sql_null',\"runtime_mode\" IS NULL,'value',\"runtime_mode\"::text),'runtime_evidence',pg_catalog.jsonb_build_object('sql_null',\"runtime_evidence\" IS NULL,'value',\"runtime_evidence\"::text),'runtime_evidence_hash',pg_catalog.jsonb_build_object('sql_null',\"runtime_evidence_hash\" IS NULL,'value',\"runtime_evidence_hash\"::text),'dispatch_evidence',pg_catalog.jsonb_build_object('sql_null',\"dispatch_evidence\" IS NULL,'value',\"dispatch_evidence\"::text),'dispatch_evidence_hash',pg_catalog.jsonb_build_object('sql_null',\"dispatch_evidence_hash\" IS NULL,'value',\"dispatch_evidence_hash\"::text),'retry_policy',pg_catalog.jsonb_build_object('sql_null',\"retry_policy\" IS NULL,'value',\"retry_policy\"::text),'attempt_tracking_version',pg_catalog.jsonb_build_object('sql_null',\"attempt_tracking_version\" IS NULL,'value',\"attempt_tracking_version\"::text),'api_key_id',pg_catalog.jsonb_build_object('sql_null',\"api_key_id\" IS NULL,'value',\"api_key_id\"::text),'is_local_execution',pg_catalog.jsonb_build_object('sql_null',\"is_local_execution\" IS NULL,'value',\"is_local_execution\"),'execution_model',pg_catalog.jsonb_build_object('sql_null',\"execution_model\" IS NULL,'value',\"execution_model\"::text),'session_id',pg_catalog.jsonb_build_object('sql_null',\"session_id\" IS NULL,'value',\"session_id\"::text),'created_at',pg_catalog.jsonb_build_object('sql_null',\"created_at\" IS NULL,'value',(extract(epoch FROM \"created_at\") * 1000000)::bigint),'scheduled_at',pg_catalog.jsonb_build_object('sql_null',\"scheduled_at\" IS NULL,'value',(extract(epoch FROM \"scheduled_at\") * 1000000)::bigint)) AS row_data FROM public.executions WHERE id IN ($1::uuid,$2::uuid) ORDER BY id LIMIT 3) AS cells) AS sized ORDER BY id",
    "workflow_execution_attempts": "SELECT id::text AS row_id,row_bytes,CASE WHEN row_bytes<=65536 THEN row_data::text ELSE NULL END AS row_json FROM (SELECT id,row_data,pg_catalog.octet_length(row_data::text) AS row_bytes FROM (SELECT id,pg_catalog.jsonb_build_object('id',pg_catalog.jsonb_build_object('sql_null',\"id\" IS NULL,'value',\"id\"::text),'execution_id',pg_catalog.jsonb_build_object('sql_null',\"execution_id\" IS NULL,'value',\"execution_id\"::text),'attempt_number',pg_catalog.jsonb_build_object('sql_null',\"attempt_number\" IS NULL,'value',\"attempt_number\"),'claim_token',pg_catalog.jsonb_build_object('sql_null',\"claim_token\" IS NULL,'value',\"claim_token\"::text),'status',pg_catalog.jsonb_build_object('sql_null',\"status\" IS NULL,'value',\"status\"::text),'phase',pg_catalog.jsonb_build_object('sql_null',\"phase\" IS NULL,'value',\"phase\"::text),'failure_phase',pg_catalog.jsonb_build_object('sql_null',\"failure_phase\" IS NULL,'value',\"failure_phase\"::text),'failure_code',pg_catalog.jsonb_build_object('sql_null',\"failure_code\" IS NULL,'value',\"failure_code\"::text),'worker_id',pg_catalog.jsonb_build_object('sql_null',\"worker_id\" IS NULL,'value',\"worker_id\"::text),'worker_incarnation_id',pg_catalog.jsonb_build_object('sql_null',\"worker_incarnation_id\" IS NULL,'value',\"worker_incarnation_id\"::text),'process_id',pg_catalog.jsonb_build_object('sql_null',\"process_id\" IS NULL,'value',\"process_id\"::text),'runtime_mode',pg_catalog.jsonb_build_object('sql_null',\"runtime_mode\" IS NULL,'value',\"runtime_mode\"::text),'runtime_evidence_hash',pg_catalog.jsonb_build_object('sql_null',\"runtime_evidence_hash\" IS NULL,'value',\"runtime_evidence_hash\"::text),'dispatch_evidence_hash',pg_catalog.jsonb_build_object('sql_null',\"dispatch_evidence_hash\" IS NULL,'value',\"dispatch_evidence_hash\"::text),'policy_digest',pg_catalog.jsonb_build_object('sql_null',\"policy_digest\" IS NULL,'value',\"policy_digest\"::text),'policy_version',pg_catalog.jsonb_build_object('sql_null',\"policy_version\" IS NULL,'value',\"policy_version\"::text),'published_at',pg_catalog.jsonb_build_object('sql_null',\"published_at\" IS NULL,'value',(extract(epoch FROM \"published_at\") * 1000000)::bigint),'claimed_at',pg_catalog.jsonb_build_object('sql_null',\"claimed_at\" IS NULL,'value',(extract(epoch FROM \"claimed_at\") * 1000000)::bigint),'started_at',pg_catalog.jsonb_build_object('sql_null',\"started_at\" IS NULL,'value',(extract(epoch FROM \"started_at\") * 1000000)::bigint),'heartbeat_at',pg_catalog.jsonb_build_object('sql_null',\"heartbeat_at\" IS NULL,'value',(extract(epoch FROM \"heartbeat_at\") * 1000000)::bigint),'completed_at',pg_catalog.jsonb_build_object('sql_null',\"completed_at\" IS NULL,'value',(extract(epoch FROM \"completed_at\") * 1000000)::bigint),'duration_ms',pg_catalog.jsonb_build_object('sql_null',\"duration_ms\" IS NULL,'value',\"duration_ms\"),'peak_memory_bytes',pg_catalog.jsonb_build_object('sql_null',\"peak_memory_bytes\" IS NULL,'value',\"peak_memory_bytes\"),'cpu_total_seconds',pg_catalog.jsonb_build_object('sql_null',\"cpu_total_seconds\" IS NULL,'value',pg_catalog.encode(pg_catalog.float8send(\"cpu_total_seconds\"),'hex')),'created_at',pg_catalog.jsonb_build_object('sql_null',\"created_at\" IS NULL,'value',(extract(epoch FROM \"created_at\") * 1000000)::bigint)) AS row_data FROM public.workflow_execution_attempts WHERE execution_id IN ($1::uuid,$2::uuid) ORDER BY id LIMIT 3) AS cells) AS sized ORDER BY id",
    "execution_logs": "SELECT id::text AS row_id,row_bytes,CASE WHEN row_bytes<=65536 THEN row_data::text ELSE NULL END AS row_json FROM (SELECT id,row_data,pg_catalog.octet_length(row_data::text) AS row_bytes FROM (SELECT id,pg_catalog.jsonb_build_object('id',pg_catalog.jsonb_build_object('sql_null',\"id\" IS NULL,'value',\"id\"),'execution_id',pg_catalog.jsonb_build_object('sql_null',\"execution_id\" IS NULL,'value',\"execution_id\"::text),'level',pg_catalog.jsonb_build_object('sql_null',\"level\" IS NULL,'value',\"level\"::text),'message',pg_catalog.jsonb_build_object('sql_null',\"message\" IS NULL,'value',\"message\"::text),'log_metadata',pg_catalog.jsonb_build_object('sql_null',\"log_metadata\" IS NULL,'value',\"log_metadata\"::text),'timestamp',pg_catalog.jsonb_build_object('sql_null',\"timestamp\" IS NULL,'value',(extract(epoch FROM \"timestamp\") * 1000000)::bigint),'sequence',pg_catalog.jsonb_build_object('sql_null',\"sequence\" IS NULL,'value',\"sequence\")) AS row_data FROM public.execution_logs WHERE execution_id IN ($1::uuid,$2::uuid) ORDER BY id LIMIT 1025) AS cells) AS sized ORDER BY id",
    "assigned_transaction": "SELECT pg_backend_pid() AS backend_pid,pg_current_xact_id_if_assigned()::text AS xid,current_database() AS database_name,inet_server_addr()::text AS server_address,inet_server_port() AS server_port,current_setting('server_version_num') AS server_version_num",
    "fate": "SELECT pg_xact_status($1::xid8)::text AS fate,current_database() AS database_name,inet_server_addr()::text AS server_address,inet_server_port() AS server_port,current_setting('server_version_num') AS server_version_num",
}
ROW_COLUMNS = {
    "executions": [
        ("id", "uuid", False),
        ("workflow_name", "text", False),
        ("workflow_version", "text", True),
        ("status", "text", False),
        ("parameters", "jsonb", False),
        ("result", "jsonb", True),
        ("result_type", "text", True),
        ("variables", "jsonb", True),
        ("execution_context", "jsonb", True),
        ("error_message", "text", True),
        ("started_at", "utc_us", True),
        ("completed_at", "utc_us", True),
        ("duration_ms", "integer", True),
        ("peak_memory_bytes", "integer", True),
        ("process_rss_bytes", "integer", True),
        ("cpu_user_seconds", "float64_bits", True),
        ("cpu_system_seconds", "float64_bits", True),
        ("cpu_total_seconds", "float64_bits", True),
        ("time_saved", "integer", False),
        ("value", "decimal", False),
        ("executed_by", "uuid", True),
        ("executed_by_name", "text", False),
        ("organization_id", "uuid", True),
        ("form_id", "uuid", True),
        ("workflow_id", "uuid", True),
        ("solution_deployment_id", "uuid", True),
        ("runtime_mode", "text", False),
        ("runtime_evidence", "jsonb", True),
        ("runtime_evidence_hash", "text", True),
        ("dispatch_evidence", "jsonb", True),
        ("dispatch_evidence_hash", "text", True),
        ("retry_policy", "jsonb", False),
        ("attempt_tracking_version", "text", True),
        ("api_key_id", "uuid", True),
        ("is_local_execution", "bool", False),
        ("execution_model", "text", True),
        ("session_id", "uuid", True),
        ("created_at", "utc_us", False),
        ("scheduled_at", "utc_us", True),
    ],
    "attempts": [
        ("id", "uuid", False),
        ("execution_id", "uuid", False),
        ("attempt_number", "integer", False),
        ("claim_token", "uuid", True),
        ("status", "text", False),
        ("phase", "text", False),
        ("failure_phase", "text", True),
        ("failure_code", "text", True),
        ("worker_id", "text", True),
        ("worker_incarnation_id", "uuid", True),
        ("process_id", "text", True),
        ("runtime_mode", "text", True),
        ("runtime_evidence_hash", "text", True),
        ("dispatch_evidence_hash", "text", True),
        ("policy_digest", "text", True),
        ("policy_version", "text", False),
        ("published_at", "utc_us", True),
        ("claimed_at", "utc_us", True),
        ("started_at", "utc_us", True),
        ("heartbeat_at", "utc_us", True),
        ("completed_at", "utc_us", True),
        ("duration_ms", "integer", True),
        ("peak_memory_bytes", "integer", True),
        ("cpu_total_seconds", "float64_bits", True),
        ("created_at", "utc_us", False),
    ],
    "logs": [
        ("id", "integer", False),
        ("execution_id", "uuid", False),
        ("level", "text", False),
        ("message", "text", False),
        ("log_metadata", "jsonb", True),
        ("timestamp", "utc_us", False),
        ("sequence", "integer", False),
    ],
}


def validate_rows(rows):
    closed(rows, ROW_COLUMNS)
    for table, columns in ROW_COLUMNS.items():
        records = rows[table]
        require(type(records) is list and len(records) <= (1024 if table == "logs" else 2), "row_count")
        identities = []
        for record in records:
            closed(record, [name for name, _kind, _nullable in columns])
            for name, kind, nullable in columns:
                cell = closed(record[name], ("sql_null", "value"))
                require(type(cell["sql_null"]) is bool, "null_flag")
                value = cell["value"]
                if cell["sql_null"]:
                    require(nullable and value is None, "sql_null")
                    continue
                require(value is not None, "cell_value")
                if kind == "uuid":
                    canonical_uuid(value)
                elif kind in ("text", "jsonb", "decimal"):
                    require(type(value) is str and "\0" not in value, "cell_text")
                elif kind == "bool":
                    require(type(value) is bool, "cell_bool")
                elif kind in ("integer", "utc_us"):
                    width = 64 if kind == "utc_us" or name in ("peak_memory_bytes", "process_rss_bytes") else 32
                    integer(value, -(2 ** (width - 1)), 2 ** (width - 1) - 1)
                elif kind == "float64_bits":
                    require(type(value) is str and re.fullmatch(r"[0-9a-f]{16}", value) is not None, "float_bits")
                    require(math.isfinite(struct.unpack("!d", bytes.fromhex(value))[0]), "nonfinite_cell")
                else:
                    raise FaultAdmissionError("unselected_cell_kind")
            identities.append(record["id"]["value"])
        require(identities == sorted(identities) and len(set(identities)) == len(identities), "row_order")
    encode(rows, ROW_LIMIT)
    return rows


def bootstrap_value(value, *, relay):
    closed(value, ("schema", "lane", "invocation", "seq", "kind", "body"))
    require(value["schema"] == (IPC_SCHEMA if relay else OBSERVER_SCHEMA), "bootstrap_schema")
    require(value["lane"] == ("relay" if relay else "supervisor"), "bootstrap_lane")
    require(value["kind"] == "bootstrap", "bootstrap_kind")
    integer(value["seq"], 1, 1)
    canonical_uuid(value["invocation"])
    body = closed(value["body"], ("hostname", "port", "remaining_ms") if relay else ("remaining_ms",))
    integer(body["remaining_ms"], 1, 75000)
    if relay:
        hostname = body["hostname"]
        require(
            type(hostname) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", hostname) is not None,
            "bootstrap_hostname",
        )
        require(re.fullmatch(r"[0-9.]+", hostname) is None, "bootstrap_ip_literal")
        integer(body["port"], 1024, 65535)
    return value


async def bootstrap_channel(reader, writer, *, relay):
    # No invented prebootstrap timer. The actual parent owns startup deadline,
    # socket closure and process settlement; this read cannot claim that proof.
    try:
        prefix = await reader.readexactly(4)
        length = struct.unpack("!I", prefix)[0]
        require(0 < length <= CONTROL_LIMIT, "bootstrap_bound")
        value = bootstrap_value(decode(await reader.readexactly(length)), relay=relay)
    except asyncio.IncompleteReadError as error:
        raise FaultAdmissionError("bootstrap_partial") from error
    deadline = Deadline.admitted(value["body"]["remaining_ms"])
    channel = Channel(
        reader,
        writer,
        value["invocation"],
        "relay" if relay else "observer",
        "relay" if relay else "supervisor",
        IPC_SCHEMA if relay else OBSERVER_SCHEMA,
        deadline,
    )
    channel.received = 1
    return channel, value["body"]


def shorten(deadline, remaining_ms, now):
    integer(remaining_ms, 1, 75000)
    deadline.work_end = min(deadline.work_end, now + remaining_ms / 1000)
    deadline.total_end = min(deadline.total_end, now + remaining_ms / 1000 + 15)


def fault_input(value, actor):
    closed(value, ("schema", "invocation", "cycle", "actor", "scope", "request"))
    require(value["schema"] == "bifrost.test.workflow-result-fault-input/v1", "fault_schema")
    canonical_uuid(value["invocation"])
    cycle(value, (1, "python") if actor == "python" else (2, "rust"))
    scope = closed(value["scope"], ("foreign_execution_id", "attempt_id", "foreign_attempt_id"))
    for identity in scope.values():
        canonical_uuid(identity)
    request = closed(value["request"], ("schema", "case_id", "cohort", "projection", "operation"))
    require(
        request["schema"] == "bifrost.test.workflow-sql/v1" and request["case_id"] == "r-attempt-claimed-unstarted",
        "request_identity",
    )
    cohort = closed(request["cohort"], ("execution_id", "submitted_token"))
    canonical_uuid(cohort["execution_id"])
    canonical_uuid(cohort["submitted_token"])
    require(
        scope["foreign_execution_id"] != cohort["execution_id"] and scope["attempt_id"] != scope["foreign_attempt_id"],
        "scope_distinct",
    )
    operation = closed(request["operation"], ("kind", "lane", "raw_fields"))
    require(
        operation["kind"] == "result" and operation["lane"] == "success" and type(operation["raw_fields"]) is dict,
        "source_raw_fields",
    )
    fields = closed(
        operation["raw_fields"],
        ("status", "result", "error", "error_type", "duration_ms", "variables", "execution_context", "metrics", "roi"),
    )
    for presence in fields.values():
        closed(presence, ("kind",))
        require(presence["kind"] == "absent", "source_absent_field")
    # Projection is genuine source preparation, independently qualified by the
    # original observer/parent. It is not an expected database-state bag.
    encode(value, ROW_LIMIT)
    return value


async def driver_snapshot(driver, owned, foreign):
    rows = {}
    for table, query, limit in (
        ("executions", "executions", 2),
        ("attempts", "workflow_execution_attempts", 2),
        ("logs", "execution_logs", 1024),
    ):
        records = await driver.fetch(FIXED_SQL[query], UUID(owned), UUID(foreign))
        require(len(records) <= limit, "snapshot_overflow")
        values = []
        for record in records:
            integer(record["row_bytes"], 1, ROW_LIMIT)
            require(type(record["row_json"]) is str, "snapshot_oversize")
            value = decode(record["row_json"].encode("utf-8"))
            require(str(value["id"]["value"]) == record["row_id"], "snapshot_row_identity")
            values.append(value)
        rows[table] = values
    return validate_rows(rows)


async def assigned(driver):
    records = await driver.fetch(FIXED_SQL["assigned_transaction"])
    require(len(records) == 1, "assigned_count")
    return query_witness(dict(records[0]))


def classify_python(error, captured_original, captured_wrapper):
    # Exact installed classes, not exception-name/repr matching or cause traversal.
    from asyncpg.exceptions import ConnectionDoesNotExistError
    from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
    from sqlalchemy.exc import DBAPIError

    if not isinstance(error, DBAPIError) or error is not captured_wrapper:
        return {"family": "other", "code": None}
    adapted = error.orig
    if adapted is not captured_original or type(adapted) is not AsyncAdapt_asyncpg_dbapi.Error:
        return {"family": "other", "code": None}
    leaf = adapted.__cause__
    if (
        error.__cause__ is not adapted
        or type(leaf) is not ConnectionDoesNotExistError
        or getattr(adapted, "sqlstate", None) != "08003"
        or getattr(adapted, "pgcode", None) != "08003"
        or leaf.sqlstate != "08003"
    ):
        return {"family": "other", "code": None}
    return {"family": "python_connection_lost", "code": "08003"}


SOURCE_FILES = {
    "/app/src/core/database.py": "bd938b9918d613e107ccfe97f83f138b12ea874a20fb673228d2f9a232a1de4b",
    "/app/src/config.py": "cdcbf0fa364b48f2d93b16b95e5476bdd9482a595603de59f88500faf3578fbd",
    "/app/src/jobs/consumers/workflow_execution.py": "c285f2d315b36bcdfc81dc0b8e23705f22a7dbb6f740311233612068e10cf218",
    "/app/bifrost/__init__.py": "ee6376146eb1028721a0b63de66e154cf6e9d460641e6d2c10e62ec64f7c3212",
    "/app/bifrost/_sync.py": "47e1fe99c142591b88031c314cc373e7274031fa96f1cd9f32570b6ddd21763c",
    "/app/shared/workspace_effects.py": "00a6d0b822f40719651fe202c70e372eea2ef9ba8677b1551f584559f0af609f",
}


def source_file(path, expected):
    import hashlib
    import os
    import stat

    before = os.stat(path, follow_symlinks=False)
    require(
        stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 4 * 1024 * 1024,
        "source_profile",
    )

    def identity(value):
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    descriptor = None
    original = None
    result = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        require(identity(os.fstat(descriptor)) == identity(before), "source_open_identity")
        data = bytearray()
        while len(data) <= before.st_size:
            chunk = os.read(descriptor, min(65536, before.st_size + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        require(len(data) == before.st_size and hashlib.sha256(data).hexdigest() == expected, "source_hash")
        require(
            identity(os.fstat(descriptor)) == identity(before)
            and identity(os.stat(path, follow_symlinks=False)) == identity(before),
            "source_after_identity",
        )
        result = True
    except BaseException as error:
        original = error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    return result


def loaded_sdk_source(module, code):
    path = vars(module).get("__file__")
    require(path in SOURCE_FILES and (code is None or code.co_filename == path), "loaded_source_origin")
    return source_file(path, SOURCE_FILES[path])


class Restoration:
    """Retain actual installed identities; never overwrite a foreign replacement."""

    def __init__(self):
        self.owned = []

    def install(self, module, name, observer):
        previous = getattr(module, name)
        self.owned.append((module, name, previous, observer))
        setattr(module, name, observer)
        require(getattr(module, name) is observer, "observer_install")
        return previous

    def restore(self):
        original = None
        for module, name, previous, observer in reversed(self.owned):
            try:
                require(getattr(module, name) is observer, "observer_replaced")
                setattr(module, name, previous)
                self.owned.remove((module, name, previous, observer))
            except BaseException as error:
                if original is None:
                    original = error
        if original is not None:
            raise original
