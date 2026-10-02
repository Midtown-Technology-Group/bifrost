"""Private C1-R instrumentation; neither a lossless tee nor fresh-lease authority."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import ssl
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from scripts import agent_reference_contract as wire


class ObservationClosureError(RuntimeError):
    """Value-free, harness-only failure; never substitutes for an app exception."""

    def __init__(self) -> None:
        super().__init__("agent-reference observation failed")


def _regular(fd: int, mode: int, cap: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != mode
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or info.st_nlink != 1
        or info.st_size > cap
    ):
        raise ObservationClosureError()


def _read_ingest_key() -> str:
    fd = os.open(
        "/run/agent-reference/observer-ingest-key",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    try:
        _regular(fd, 0o600, 64)
        raw = os.read(fd, 65)
    finally:
        os.close(fd)
    if len(raw) != 64 or any(byte not in b"0123456789abcdef" for byte in raw):
        raise ObservationClosureError()
    return raw.decode("ascii")


class PrivateSender:
    """One request at a time, fixed TLS origin, no redirects/retries/ambient trust."""

    def __init__(self, key: str, context: ssl.SSLContext) -> None:
        self._key = key
        self._context = context

    @classmethod
    def from_mounts(cls) -> PrivateSender:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_verify_locations("/run/agent-reference/observer-ca.pem")
            if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                raise ObservationClosureError()
            return cls(_read_ingest_key(), context)
        except (OSError, ssl.SSLError, ValueError):
            raise ObservationClosureError() from None

    async def send(self, raw: bytes, deadline: float | None) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        now = loop.time()
        expires = now + wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS
        if deadline is not None:
            expires = min(expires, deadline)
        budget = expires - now
        if budget <= 0:
            raise ObservationClosureError()
        # A fresh client cannot carry a prior Set-Cookie into another receipt.
        async with (
            asyncio.timeout_at(expires),
            httpx.AsyncClient(
                verify=self._context,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(budget),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            ) as client,
            client.stream(
                "POST",
                "https://scheduler-fixtures:8443/__agent-reference/observer/sdk-receipts",
                headers={
                    "X-Agent-Reference-Observer-Key": self._key,
                    "Content-Type": "application/json",
                    "Content-Length": str(len(raw)),
                },
                content=raw,
            ) as response,
        ):
            if response.status_code != 201:
                raise ObservationClosureError()
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > wire.MAX_ACK_BYTES:
                    raise ObservationClosureError()
                body.extend(chunk)
            return wire.decode_private("ack", bytes(body))


class StatusStore:
    """Single fixed role directory, atomic 0600 replacement and exact readback."""

    def __init__(self, role: str) -> None:
        if role not in wire.ROLES:
            raise ObservationClosureError()
        directory = Path("/app/reference-observer-status") / role
        for parent in (*reversed(directory.parents), directory):
            if stat.S_ISLNK(parent.lstat().st_mode):
                raise ObservationClosureError()
        self._fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(self._fd)
        if (
            stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
        ):
            os.close(self._fd)
            raise ObservationClosureError()
        for name in ("status.json", "status.json.tmp"):
            try:
                os.stat(name, dir_fd=self._fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            os.close(self._fd)
            raise ObservationClosureError()
        self._generation = -1

    def _check_existing(self, name: str) -> None:
        try:
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fd
            )
        except FileNotFoundError:
            return
        try:
            _regular(fd, 0o600, wire.MAX_STATUS_BYTES)
        finally:
            os.close(fd)

    async def write(self, raw: bytes) -> None:
        snapshot = wire.decode_private("status", raw)
        if snapshot["generation"] <= self._generation:
            raise ObservationClosureError()
        self._check_existing("status.json")
        fd = os.open(
            "status.json.tmp",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self._fd,
        )
        try:
            _regular(fd, 0o600, wire.MAX_STATUS_BYTES)
            view = memoryview(raw)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise ObservationClosureError()
                view = view[written:]
        finally:
            os.close(fd)
        os.replace(
            "status.json.tmp", "status.json", src_dir_fd=self._fd, dst_dir_fd=self._fd
        )
        self._generation = snapshot["generation"]

    async def read(self) -> bytes:
        fd = os.open(
            "status.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=self._fd,
        )
        try:
            _regular(fd, 0o600, wire.MAX_STATUS_BYTES)
            return os.read(fd, wire.MAX_STATUS_BYTES + 1)
        finally:
            os.close(fd)

    def close(self) -> None:
        os.close(self._fd)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # Observed product JSON is not required to use the private canonical encoding.
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("body_invalid")
        result[name] = value
    return result


class Capture:
    """Only an admitted slot retains a bounded body prefix and safe projection."""

    def __init__(self, owner: Observer, scope: dict[str, Any]) -> None:
        self.owner = owner
        self.started_ns = str(time.monotonic_ns())
        self.kind = (
            "sdk_request" if scope["path"] == wire.SDK_PATH else "unexpected_sdk_path"
        )
        method = scope.get("method")
        self.method = method if method in wire.METHODS else "OTHER"
        self.query_present = bool(scope.get("query_string"))
        self.prefix = bytearray()
        self.request_bytes = 0
        self.request_complete = False
        self.response = {
            "status": None,
            "bytes": 0,
            "complete": False,
            "disconnect_seen": False,
            "app_exception": None,
        }
        self.verification = self._verify(scope.get("headers", ()))
        for code, detected in (
            (self.verification["reason"], self.verification["reason"] is not None),
            ("method_unexpected", self.method != "POST"),
            ("query_present", self.query_present),
            ("unexpected_sdk_path", self.kind == "unexpected_sdk_path"),
        ):
            if detected:
                owner.latch(code)

    def _verify(self, headers: Any) -> dict[str, Any]:
        count = 0
        selected = None
        for name, value in headers:
            if name.lower() == b"authorization":
                count += 1
                if count == 1:
                    selected = value
        reason = None
        claims = None
        if count == 0:
            reason = "auth_missing"
        elif count != 1:
            reason = "auth_duplicate"
        elif len(selected) > wire.MAX_AUTH_HEADER_BYTES:
            reason = "auth_oversized"
        else:
            try:
                scheme, token = selected.decode("ascii").split(" ", 1)
                if (
                    scheme.lower() != "bearer"
                    or not token
                    or any(char.isspace() or char == "," for char in token)
                ):
                    raise ValueError("auth_malformed")
            except (UnicodeError, ValueError):
                reason = "auth_malformed"
            else:
                payload = self.owner.verifier(token)
                if payload is None:
                    reason = "jwt_rejected"
                elif type(payload) is not dict:
                    reason = "claims_schema"
                else:
                    claims = {
                        name: payload.get(name)
                        for name in (
                            "sub",
                            "engine_execution_id",
                            "engine_solution_id",
                            "org_id",
                            "delegated_user_id",
                            "engine",
                            "is_superuser",
                            "engine_global_repo_access",
                            "delegated_is_superuser",
                            "delegated_is_provider_org",
                            "delegated_is_external",
                        )
                    }
                    self.verification = {
                        "outcome": "verified",
                        "reason": None,
                        "claims": claims,
                    }
                    try:
                        # Null is an absent-claim projection, never a typed JWT value.
                        if any(
                            name in payload and payload[name] is None for name in claims
                        ):
                            raise wire.ContractError("invalid_schema")
                        wire.validate_private("receipt", self.receipt())
                    except wire.ContractError:
                        reason, claims = "claims_schema", None
        return {
            "outcome": "verified" if reason is None else "unverified",
            "reason": reason,
            "claims": claims,
        }

    def received(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.disconnect":
            self.response["disconnect_seen"] = True
            self.owner.latch("disconnect_seen")
        elif message["type"] == "http.request" and self.kind == "sdk_request":
            body = message.get("body", b"")
            self.request_bytes = min(
                wire.MAX_SDK_BODY_BYTES + 1, self.request_bytes + len(body)
            )
            room = wire.MAX_SDK_BODY_BYTES - len(self.prefix)
            self.prefix.extend(body[:room])
            if self.request_bytes > wire.MAX_SDK_BODY_BYTES:
                self.owner.latch("body_oversized")
            if not message.get("more_body", False):
                self.request_complete = True

    def sent(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.response["status"] = message["status"]
        elif message["type"] == "http.response.body":
            self.response["bytes"] = min(
                wire.MAX_SELECTED_RESPONSE_BYTES + 1,
                self.response["bytes"] + len(message.get("body", b"")),
            )
            if self.response["bytes"] > wire.MAX_SELECTED_RESPONSE_BYTES:
                self.owner.latch("response_oversized")
            if not message.get("more_body", False):
                self.response["complete"] = True

    def receipt(self) -> dict[str, Any]:
        failures = set()
        if self.verification["reason"] is not None:
            failures.add(self.verification["reason"])
        payload = {
            "started_ns": self.started_ns,
            "method": self.method,
            "query_present": self.query_present,
            "verification": self.verification,
            "response": self.response.copy(),
        }
        if self.kind == "sdk_request":
            reason, value = None, None
            if not self.request_complete:
                reason = "body_incomplete"
            elif self.request_bytes > wire.MAX_SDK_BODY_BYTES:
                reason = "body_oversized"
            else:
                try:
                    value = json.loads(
                        self.prefix.decode("utf-8"), object_pairs_hook=_strict_object
                    )
                except (UnicodeError, ValueError, RecursionError):
                    reason = "body_invalid"
            payload["path"] = wire.SDK_PATH
            payload["request"] = {
                "complete": self.request_complete,
                "bytes": self.request_bytes,
                "reason": reason,
                "value": value,
            }
            if reason is not None:
                failures.add(reason)
        else:
            failures.add("unexpected_sdk_path")
        for code, present in (
            ("method_unexpected", self.method != "POST"),
            ("query_present", self.query_present),
            ("app_exception", self.response["app_exception"] is not None),
            ("disconnect_seen", self.response["disconnect_seen"]),
            ("response_incomplete", not self.response["complete"]),
            (
                "response_oversized",
                self.response["bytes"] > wire.MAX_SELECTED_RESPONSE_BYTES,
            ),
        ):
            if present:
                failures.add(code)
        payload["failures"] = [code for code in wire.FAILURE_CODES if code in failures]
        result = self.owner.receipt(self.kind, payload, 1)
        if self.kind == "sdk_request" and payload["request"]["reason"] is None:
            try:
                wire.validate_private("receipt", result)
            except wire.ContractError:
                payload["request"].update(reason="body_invalid", value=None)
                failures.add("body_invalid")
                payload["failures"] = [
                    code for code in wire.FAILURE_CODES if code in failures
                ]
        return result


class Observer:
    """Single-loop producer/sender/status owner; original app objects pass through."""

    def __init__(
        self,
        app: Any,
        *,
        lane_id: str,
        role: str,
        verifier: Callable[[str], Any],
        sender: Any,
        store: Any,
        nonce: str | None = None,
    ) -> None:
        self.app, self.lane_id, self.role = app, lane_id, role
        self.nonce = nonce if nonce is not None else secrets.token_hex(16)
        self.verifier, self.sender, self.store = verifier, sender, store
        self.counters = dict.fromkeys(
            (
                "receipts_seen",
                "queued",
                "enqueue_rejected",
                "acknowledged",
                "send_failed",
                "queue_pending",
                "sending",
                "requests_started",
                "requests_finished",
                "requests_in_flight",
                "seq_allocated",
                "last_ack_seq",
                "capacity_rejected",
            ),
            0,
        )
        self.phase = "starting"
        self.upstream = {"startup_forwarded": False, "shutdown_forwarded": False}
        self.ready_acked = self.closed_acked = False
        self.first_failure: str | None = None
        self.generation = 0
        self.active_captures = 0
        self.lifespan_task: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.deadline: float | None = None
        self.closed = False
        self._grant = asyncio.Event()
        self._progress = asyncio.Event()
        self._status_changed = asyncio.Event()
        self._queue: asyncio.Queue = asyncio.Queue(wire.MAX_ROLE_QUEUE)
        self._sender_task: asyncio.Task | None = None
        self._writer_task: asyncio.Task | None = None
        self._latest: tuple[int, bytes] | None = None
        self._written_generation: int | None = None
        self._status_failed = False
        self._case_id: str | None = None
        wire.validate_private("status", self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "bifrost.agent-reference.observer-status/v1",
            "lane_id": self.lane_id,
            "role": self.role,
            "nonce": self.nonce,
            "generation": self.generation,
            "mono_ns": str(time.monotonic_ns()),
            "phase": self.phase,
            "counters": self.counters.copy(),
            "upstream": self.upstream.copy(),
            "ready_acked": self.ready_acked,
            "closed_acked": self.closed_acked,
            "first_failure": self.first_failure,
        }

    def latch(self, code: str) -> None:
        if self.first_failure is None:
            self.first_failure = code

    def change(self, name: str, delta: int) -> None:
        value = self.counters[name]
        if value == wire.MAX_COUNTER:
            self.latch("counter_exhausted")
            return
        result = value + delta
        if result >= wire.MAX_COUNTER:
            result = wire.MAX_COUNTER
            self.latch("counter_exhausted")
        self.counters[name] = result

    def publish(self) -> None:
        if self.generation < wire.MAX_COUNTER:
            self.generation += 1
        if self.generation == wire.MAX_COUNTER:
            self.latch("counter_exhausted")
        try:
            self._latest = (
                self.generation,
                wire.encode_private("status", self.snapshot()),
            )
            self._status_changed.set()
        except wire.ContractError:
            self._status_failed = True
            self.latch("observer_exception")
        self._progress.set()

    def receipt(self, kind: str, payload: dict[str, Any], seq: int) -> dict[str, Any]:
        return {
            "schema": "bifrost.agent-reference.sdk-observation/v1",
            "lane_id": self.lane_id,
            "role": self.role,
            "nonce": self.nonce,
            "seq": seq,
            "mono_ns": str(time.monotonic_ns()),
            "kind": kind,
            "payload": payload,
        }

    def reject_capture(self, *, capacity: bool = False) -> None:
        self.change("receipts_seen", 1)
        if self.counters["seq_allocated"] < wire.MAX_ROLE_RECEIPTS_PER_LANE:
            self.change("seq_allocated", 1)
        else:
            capacity = True
        self.change("enqueue_rejected", 1)
        if capacity:
            self.change("capacity_rejected", 1)
        self.latch("observer_exception")
        self.publish()

    def enqueue(self, kind: str, payload: dict[str, Any]) -> None:
        self.change("receipts_seen", 1)
        code = None
        if self.counters["seq_allocated"] >= wire.MAX_ROLE_RECEIPTS_PER_LANE:
            code = "receipt_exhausted"
        else:
            self.change("seq_allocated", 1)
            try:
                value = self.receipt(kind, payload, self.counters["seq_allocated"])
                raw = wire.encode_private("receipt", value)
                self._queue.put_nowait((value["seq"], kind, raw))
            except asyncio.QueueFull:
                code = "queue_exhausted"
            except wire.ContractError:
                code = "observer_exception"
        if code is None:
            self.change("queued", 1)
            self.change("queue_pending", 1)
        else:
            self.change("enqueue_rejected", 1)
            if code in {"queue_exhausted", "receipt_exhausted"}:
                self.change("capacity_rejected", 1)
            self.latch(code)
        self.publish()

    async def _write_status(self) -> None:
        try:
            while True:
                await self._status_changed.wait()
                self._status_changed.clear()
                snapshot = self._latest
                if snapshot is None:
                    continue
                await self.store.write(snapshot[1])
                self._written_generation = snapshot[0]
                self._progress.set()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- private I/O fault cannot become a stale success.
            self._status_failed = True
            self.latch("status_io_failure")
            self._progress.set()

    async def _send_receipts(self) -> None:
        while True:
            seq, kind, raw = await self._queue.get()
            self.change("queue_pending", -1)
            self.change("sending", 1)
            self.publish()
            try:
                ack = await self.sender.send(raw, self.deadline)
                wire.validate_private("ack", ack)
                if any(
                    ack[key] != value
                    for key, value in (
                        ("lane_id", self.lane_id),
                        ("role", self.role),
                        ("nonce", self.nonce),
                        ("seq", seq),
                    )
                ) or (
                    kind not in {"sdk_request", "unexpected_sdk_path"}
                    and ack["case_id"] is not None
                ):
                    raise wire.ContractError("identity_mismatch")
            except asyncio.CancelledError:
                self.change("sending", -1)
                self.change("send_failed", 1)
                self.latch("transport_failure")
                self.publish()
                raise
            except Exception as error:  # noqa: BLE001 -- private transport faults retain only a static category.
                self.change("sending", -1)
                self.change("send_failed", 1)
                self.latch(
                    "ack_invalid"
                    if isinstance(error, wire.ContractError)
                    else "transport_failure"
                )
            else:
                self.change("sending", -1)
                self.change("acknowledged", 1)
                self.counters["last_ack_seq"] = seq
                if kind == "ready":
                    self.ready_acked = True
                elif kind == "closed":
                    self.closed_acked = True
                elif ack["case_id"] is None:
                    self.latch("unbound_sdk")
                elif self._case_id is not None and self._case_id != ack["case_id"]:
                    self.latch("cross_case_sdk")
                else:
                    self._case_id = ack["case_id"]
            self.publish()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(scope, receive, send)
        elif scope["type"] == "http" and scope.get("path", "").startswith("/api/sdk/"):
            await self._http(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    async def _http(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.change("requests_started", 1)
        self.change("requests_in_flight", 1)
        capture = None
        admitted = self.active_captures < wire.MAX_ROLE_ACTIVE_CAPTURES
        if self.phase != "active":
            self.latch("shutdown_incomplete")
        if admitted:
            self.active_captures += 1
            try:
                capture = Capture(self, scope)
            except Exception:  # noqa: BLE001 -- failed instrumentation must not replace the actual app.
                self.latch("observer_exception")
        else:
            self.latch("observer_exception")
        self.publish()

        async def observed_receive():
            message = await receive()
            if capture is not None:
                try:
                    capture.received(message)
                except Exception:  # noqa: BLE001 -- observation follows the original receive unchanged.
                    self.latch("observer_exception")
            return message

        async def observed_send(message):
            await send(message)
            if capture is not None:
                try:
                    capture.sent(message)
                except Exception:  # noqa: BLE001 -- observation follows the original send unchanged.
                    self.latch("observer_exception")

        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            if capture is not None:
                capture.response["app_exception"] = "cancelled"
                self.latch("app_exception")
            raise
        except Exception:
            if capture is not None:
                capture.response["app_exception"] = "exception"
                self.latch("app_exception")
            raise
        finally:
            self.change("requests_finished", 1)
            self.change("requests_in_flight", -1)
            if admitted:
                self.active_captures -= 1
            if capture is None:
                self.reject_capture(capacity=not admitted)
            else:
                try:
                    result = capture.receipt()
                    for code in result["payload"]["failures"]:
                        self.latch(code)
                    self.enqueue(capture.kind, result["payload"])
                except Exception:  # noqa: BLE001 -- finalization must preserve the app's original exception.
                    self.reject_capture()
                finally:
                    capture.prefix.clear()
            self.publish()

    async def _lifespan(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        task = asyncio.current_task()
        if self.lifespan_task is not None or task is None:
            self.latch("shutdown_incomplete")
            self.publish()
            raise ObservationClosureError()
        self.lifespan_task, self.loop = task, asyncio.get_running_loop()
        self._latest = (0, wire.encode_private("status", self.snapshot()))
        self._status_changed.set()
        self._sender_task = asyncio.create_task(self._send_receipts())
        self._writer_task = asyncio.create_task(self._write_status())

        async def observed_send(message):
            await send(message)
            kind = message["type"]
            if kind == "lifespan.startup.complete":
                self.upstream["startup_forwarded"] = True
                self.phase = "active"
                self.enqueue("ready", {"startup_forwarded": True})
            elif kind == "lifespan.shutdown.complete":
                self.upstream["shutdown_forwarded"] = True
                self.phase = "stopping"
                self.publish()
            elif kind in {"lifespan.startup.failed", "lifespan.shutdown.failed"}:
                self.latch("shutdown_incomplete")
                self.publish()

        try:
            await self.app(scope, receive, observed_send)
            if not all(self.upstream.values()):
                raise ObservationClosureError()
            await self._grant.wait()
            await self._close()
        except BaseException:
            self.latch("shutdown_incomplete")
            self.publish()
            self._cancel_background()
            raise

    def grant_closure(self, deadline: float) -> None:
        if (
            self._grant.is_set()
            or self.loop is not asyncio.get_running_loop()
            or self.lifespan_task is None
            or not all(self.upstream.values())
        ):
            self.latch("shutdown_incomplete")
            self.publish()
            raise ObservationClosureError()
        self.deadline = deadline
        self._grant.set()

    async def _wait_until(self, predicate: Callable[[], bool]) -> None:
        if self.deadline is None:
            raise ObservationClosureError()
        async with asyncio.timeout_at(self.deadline):
            while not predicate():
                if self.first_failure is not None or self._status_failed:
                    raise ObservationClosureError()
                self._progress.clear()
                await self._progress.wait()

    def _cancel_background(self) -> None:
        for task in (self._sender_task, self._writer_task):
            if task is not None and not task.done():
                task.cancel()

    async def _close(self) -> None:
        await self._wait_until(
            lambda: (
                self.counters["queue_pending"] == 0 and self.counters["sending"] == 0
            )
        )
        prior = self.counters.copy()
        self.enqueue(
            "closed",
            {
                "shutdown_forwarded": self.upstream["shutdown_forwarded"],
                "prior": prior,
                "first_failure": self.first_failure,
            },
        )
        await self._wait_until(lambda: self.closed_acked)
        if self.first_failure is not None:
            raise ObservationClosureError()
        self.phase = "closed"
        self.publish()
        final = self._latest
        await self._wait_until(
            lambda: final is not None and self._written_generation == final[0]
        )
        if self.deadline is None:
            raise ObservationClosureError()
        async with asyncio.timeout_at(self.deadline):
            raw = await self.store.read()
            if final is None or raw != final[1]:
                raise ObservationClosureError()
            wire.decode_private("status", raw)
            self._cancel_background()
            tasks = {self._sender_task, self._writer_task}
            _, pending = await asyncio.wait(
                tasks, timeout=max(0, self.deadline - asyncio.get_running_loop().time())
            )
            if pending:
                raise ObservationClosureError()
            for task in tasks:
                if not task.cancelled() and task.exception() is not None:
                    raise ObservationClosureError()
            self.store.close()
            self.closed = True


def create_app() -> Observer:
    """Actual unchanged product factory, only after private construction validates."""
    lane_id = os.environ.get("BIFROST_AGENT_REFERENCE_LANE_ID")
    role = os.environ.get("BIFROST_AGENT_REFERENCE_ROLE")
    try:
        # Constructor/codec validates nonsecret lane/role before any product import.
        prototype = Observer(
            None,
            lane_id=lane_id,
            role=role,
            verifier=lambda _: None,
            sender=None,
            store=None,
        )
        sender = PrivateSender.from_mounts()
        store = StatusStore(role)
    except (wire.ContractError, OSError, ValueError):
        raise ObservationClosureError() from None
    from src.core.security import decode_token
    from src.main import create_app as product_create_app

    prototype.app = product_create_app()
    prototype.verifier = lambda token: decode_token(token, expected_type="access")
    prototype.sender, prototype.store = sender, store
    return prototype
