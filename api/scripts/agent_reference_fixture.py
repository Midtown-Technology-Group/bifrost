"""Bounded test-only reference collector; no receipt certifies runtime authority.

ACK emission proves a successful bounded server write, never peer receipt.
Actual observer ACK flags/status and committed joins remain independent gates.
"""

from __future__ import annotations

import asyncio
import hmac
import importlib.metadata
import json
import os
import re
import signal
import socket
import socketserver
import ssl
import stat
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from scripts import agent_reference_contract as wire


@dataclass(frozen=True)
class Emission:
    """Prepared response; lifecycle publication happens only after full write."""

    status: int
    body: bytes
    identity: tuple[str, str, int] | None = None


class Collector:
    """One lane/nominal case, immutable admitted ledger and sticky failures."""

    def __init__(self, lane_id: str, clock: Callable[[], int] = time.monotonic_ns):
        self.lane_id = lane_id
        self._clock = clock
        self._lock = threading.RLock()
        self._roles: dict[str, dict[str, Any]] = {}
        self._ledger: list[tuple[str, str | None, str, bytes]] = []
        self._pending: dict[str, Emission] = {}
        self._case: dict[str, Any] | None = None
        self._disposition: str | None = None
        self.first_failure: str | None = None
        self.oracle: Oracle | None = None
        # The shared family validates lane identity; there is no local schema.
        self.finish_request()

    def fail(self, code: str, *, case: bool = False) -> None:
        with self._lock:
            if code not in wire.FAILURE_CODES:
                raise wire.ContractError("invalid_schema")
            if self.first_failure is None:
                self.first_failure = code
            if case and self._case is not None and self._case["first_failure"] is None:
                self._case["first_failure"] = code

    def _reject(self, error: str, failure: str) -> None:
        self.fail(failure, case=self._case is not None)
        raise wire.ContractError(error)

    def _decode(self, family: str, body: bytes) -> dict[str, Any]:
        try:
            return wire.decode_private(family, body)
        except wire.ContractError:
            self.fail(
                "control_invalid" if family != "receipt" else "observer_exception"
            )
            raise

    def _role_names(self, flag: str) -> list[str]:
        return [role for role in wire.ROLES if self._roles.get(role, {}).get(flag)]

    def _lane(self) -> dict[str, Any]:
        return {
            "case_count": int(self._case is not None),
            "active_case_id": self._case["case_id"] if self._case else None,
            "roles_ready": self._role_names("ready"),
            "roles_closed": self._role_names("closed"),
            "first_failure": self.first_failure,
        }

    def _case_id(self, case_id: str) -> dict[str, Any]:
        if self._case is None or self._case["case_id"] != case_id:
            raise wire.ContractError("unknown_case")
        return self._case

    def _request_attempt(self, *, sdk: bool) -> None:
        assert self._case is not None
        case = self._case
        case["request_count"] = min(
            case["request_count"] + 1, wire.MAX_REQUESTS_PER_CASE + 1
        )
        if sdk:
            case["sdk_receipt_count"] = min(
                case["sdk_receipt_count"] + 1, wire.MAX_SDK_RECEIPTS_PER_CASE + 1
            )
        if (
            case["request_count"] > wire.MAX_REQUESTS_PER_CASE
            or case["sdk_receipt_count"] > wire.MAX_SDK_RECEIPTS_PER_CASE
        ):
            self._reject("capacity_exhausted", "collector_capacity")

    def ingest(self, body: bytes) -> Emission:
        with self._lock:
            receipt = self._decode("receipt", body)
            if receipt["lane_id"] != self.lane_id:
                self._reject("identity_mismatch", "observer_exception")
            role, nonce, seq = receipt["role"], receipt["nonce"], receipt["seq"]
            previous = self._roles.get(role)
            if previous is None:
                if receipt["kind"] != "ready" or seq != 1:
                    self._reject("sequence_mismatch", "observer_exception")
            elif nonce != previous["nonce"]:
                self._reject("identity_mismatch", "observer_exception")
            elif seq != previous["seq"] + 1:
                self._reject("sequence_mismatch", "observer_exception")
            elif (
                receipt["kind"] == "ready"
                or previous["closed"]
                or not previous["ready"]
                or role in self._pending
            ):
                self._reject("invalid_state", "observer_exception")
            if len(self._ledger) >= len(wire.ROLES) * wire.MAX_ROLE_RECEIPTS_PER_LANE:
                self._reject("capacity_exhausted", "collector_capacity")
            case_id, association = None, "lifecycle"
            if receipt["kind"] in ("sdk_request", "unexpected_sdk_path"):
                payload = receipt["payload"]
                claims = payload["verification"]["claims"]
                associated = (
                    self._case is not None
                    and payload["verification"]["outcome"] == "verified"
                    and claims["engine"] is True
                    and claims["engine_execution_id"] is not None
                    and claims["engine_solution_id"] == self._case["solution_id"]
                )
                if associated:
                    case_id = self._case["case_id"]
                    self._request_attempt(sdk=True)
                    if self._case["state"] in ("closing", "closed"):
                        association = "late"
                        self.fail("late_witness", case=True)
                    else:
                        association = "active-arm-pending-db"
                    if receipt["kind"] == "sdk_request":
                        value = payload["request"]["value"]
                        if (
                            value is not None
                            and value["solution"] != self._case["solution_id"]
                        ):
                            self.fail("cross_case_sdk", case=True)
                else:
                    association = "unbound"
                    self.fail("unbound_sdk", case=self._case is not None)
                for failure in payload["failures"]:
                    self.fail(failure, case=associated)
            elif receipt["kind"] == "failure":
                self.fail(receipt["payload"]["code"], case=self._case is not None)
            elif receipt["kind"] == "closed":
                if self.oracle is not None and self.oracle.pending:
                    self._reject("invalid_state", "shutdown_incomplete")
                if self._case is None or self._case["state"] not in (
                    "closing",
                    "closed",
                ):
                    self._reject("invalid_state", "shutdown_incomplete")
                if receipt["payload"]["first_failure"] is not None:
                    self.fail(receipt["payload"]["first_failure"], case=True)
                else:
                    actual_sdk = sum(
                        wire.decode_private("receipt", item[3])["role"] == role
                        and wire.decode_private("receipt", item[3])["kind"]
                        in ("sdk_request", "unexpected_sdk_path")
                        for item in self._ledger
                    )
                    prior = receipt["payload"]["prior"]
                    if (
                        prior["requests_started"] != actual_sdk
                        or prior["requests_finished"] != actual_sdk
                        or prior["receipts_seen"] != actual_sdk + 1
                    ):
                        self._reject("invalid_state", "shutdown_incomplete")
            emitted_ns = str(self._clock())
            # Admission retains canonical bytes, not caller-mutable objects.
            self._ledger.append((emitted_ns, case_id, association, bytes(body)))
            self._roles[role] = {
                "nonce": nonce,
                "seq": seq,
                "ready": previous["ready"] if previous else False,
                "closed": previous["closed"] if previous else False,
            }
            ack = {
                "schema": "bifrost.agent-reference.observer-ack/v1",
                "lane_id": self.lane_id,
                "role": role,
                "nonce": nonce,
                "seq": seq,
                "accepted": True,
                "case_id": case_id,
            }
            response = Emission(
                201, wire.encode_private("ack", ack), (role, nonce, seq)
            )
            self._pending[role] = response
            return response

    def emitted(self, response: Emission) -> None:
        """Call only after complete socket emission; never assert peer receipt."""
        with self._lock:
            if response.identity is None:
                return
            role, nonce, seq = response.identity
            if self._pending.get(role) is not response:
                self._reject("identity_mismatch", "ack_invalid")
            del self._pending[role]
            # Look up this immutable identity, not arrival order across roles.
            kind = None
            for entry in reversed(self._ledger):
                receipt = wire.decode_private("receipt", entry[3])
                if (receipt["role"], receipt["nonce"], receipt["seq"]) == (
                    role,
                    nonce,
                    seq,
                ):
                    kind = receipt["kind"]
                    break
            if kind == "ready":
                self._roles[role]["ready"] = True
            elif kind == "closed":
                self._roles[role]["closed"] = True
                if self._role_names("closed") == list(wire.ROLES) and self._case:
                    self._case["state"] = "closed"

    def emission_failed(self, response: Emission) -> None:
        with self._lock:
            if (
                response.identity
                and self._pending.get(response.identity[0]) is not response
            ):
                self._reject("identity_mismatch", "ack_invalid")
            self.fail("transport_failure", case=self._case is not None)
            # Admission remains immutable; failed/lost ACK is never retried.
            if response.identity:
                self._pending.pop(response.identity[0], None)

    def control(self, family: str, case_id: str, body: bytes) -> Emission:
        with self._lock:
            if family not in ("arm", "bind", "close"):
                raise wire.ContractError("invalid_schema")
            value = self._decode(family, body)
            if value["lane_id"] != self.lane_id or value["case_id"] != case_id:
                self._reject("identity_mismatch", "control_invalid")
            if family == "arm":
                if self.oracle is not None and case_id != self.oracle.case_id:
                    self._reject("identity_mismatch", "control_invalid")
                if self._case is not None or self._role_names("ready") != list(
                    wire.ROLES
                ):
                    self._reject("invalid_state", "control_invalid")
                if self.first_failure is not None:
                    self._reject("invalid_state", "control_invalid")
                self._case = {
                    key: value[key]
                    for key in (
                        "case_id",
                        "mode",
                        "solution_id",
                        "deployment_id",
                        "agent_id",
                    )
                }
                self._case.update(
                    state="armed",
                    run_id=None,
                    finish_id=None,
                    request_count=0,
                    sdk_receipt_count=0,
                    first_failure=None,
                )
            else:
                case = self._case_id(case_id)
                if family == "bind":
                    if case["state"] != "armed":
                        self._reject("invalid_state", "control_invalid")
                    case.update(state="bound", run_id=value["run_id"])
                else:
                    if case["state"] not in ("armed", "bound"):
                        self._reject("invalid_state", "control_invalid")
                    if value["run_id"] != case["run_id"]:
                        self._reject("identity_mismatch", "control_invalid")
                    if value["disposition"] == "finish" and case["state"] != "bound":
                        self._reject("invalid_state", "control_invalid")
                    if (
                        value["disposition"] == "finish"
                        and self.oracle is not None
                        and (self.oracle.pending or self.oracle.phase != "done")
                    ):
                        self._reject("invalid_state", "product_not_settled")
                    case.update(state="closing", finish_id=value["finish_id"])
                    self._disposition = value["disposition"]
                    if value["disposition"] == "abort":
                        self.fail("case_aborted", case=True)
            assert self._case is not None
            ack = {
                "schema": "bifrost.agent-reference.case-control-ack/v1",
                "lane_id": self.lane_id,
                "case_id": case_id,
                "operation": family,
                "state": self._case["state"],
                "run_id": self._case["run_id"],
                "finish_id": self._case["finish_id"],
                "first_failure": self._case["first_failure"],
            }
            return Emission(
                201 if family == "arm" else 200, wire.encode_private("control_ack", ack)
            )

    def readback(self, cursor: int, case_id: str | None = None) -> bytes:
        with self._lock:
            case = self._case_id(case_id) if case_id is not None else None
            entries = [
                (index, item)
                for index, item in enumerate(self._ledger)
                if case_id is None or item[1] == case_id
            ]
            if type(cursor) is not int or not 0 <= cursor <= len(entries):
                raise wire.ContractError("invalid_state")
            records = [
                {
                    "index": index,
                    "received_ns": item[0],
                    "case_id": item[1],
                    "association": item[2],
                    "receipt": wire.decode_private("receipt", item[3]),
                }
                for index, item in entries[cursor : cursor + wire.READBACK_PAGE_SIZE]
            ]
            end = cursor + len(records)
            value = {
                "schema": "bifrost.agent-reference.observer-readback/v1",
                "lane_id": self.lane_id,
                "scope": "case" if case_id is not None else "lane",
                "case_id": case_id,
                "offset": cursor,
                "next_cursor": end if end < len(entries) else None,
                "total": len(entries),
                "records": records,
                "case": case,
                "lane": self._lane(),
            }
            return wire.encode_private("readback", value)

    def finish_request(self) -> bytes:
        with self._lock:
            case = self._case
            return wire.encode_private(
                "finish_request",
                {
                    "schema": "bifrost.agent-reference.finish-request/v1",
                    "lane_id": self.lane_id,
                    "case_id": case["case_id"] if case else None,
                    "run_id": case["run_id"] if case else None,
                    "finish_id": case["finish_id"] if case else None,
                    "disposition": self._disposition,
                    "state": case["state"] if case else None,
                    "roles": [
                        {"role": role, "nonce": self._roles[role]["nonce"]}
                        for role in self._role_names("ready")
                    ],
                },
            )


def private_error(error: wire.ContractError) -> Emission:
    status = {
        "unauthorized": 401,
        "invalid_json": 400,
        "invalid_schema": 400,
        "unknown_case": 404,
        "capacity_exhausted": 503,
        "internal_failure": 500,
    }.get(error.code, 409)
    return Emission(
        status,
        wire.encode_private(
            "error",
            {
                "schema": "bifrost.agent-reference.private-error/v1",
                "error": error.code,
            },
        ),
    )


def load_private_file(
    path: Path, *, capability: bool, maximum: int = wire.MAX_FIXTURE_INPUT_BYTES
) -> bytes:
    """Read fixed owned regular0600 material; never disclose its value in errors."""
    fd = None
    result = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if (
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.geteuid()
            and info.st_gid == os.getegid()
            and info.st_nlink == 1
            and (info.st_size == 64 if capability else 0 < info.st_size <= maximum)
        ):
            result = os.read(fd, info.st_size + 1)
            if len(result) != info.st_size or (
                capability and re.fullmatch(b"[0-9a-f]{64}", result) is None
            ):
                result = None
    except OSError:
        pass
    finally:
        if fd is not None:
            os.close(fd)
    if result is None:
        raise wire.ContractError("internal_failure")
    return result


def load_public_certificate(path: Path) -> bytes:
    """Bound fixed public PEM material; no symlink or ambient trust selector."""
    fd = None
    material = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if (
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and 0 < info.st_size <= wire.MAX_FIXTURE_INPUT_BYTES
        ):
            material = os.read(fd, info.st_size + 1)
            if len(material) != info.st_size:
                material = None
    except OSError:
        pass
    finally:
        if fd is not None:
            os.close(fd)
    if material is None:
        raise wire.ContractError("internal_failure")
    return material


def client_tls(ca_file: Path) -> ssl.SSLContext:
    material = load_public_certificate(ca_file)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_verify_locations(cadata=material.decode("ascii"))
    except (ValueError, UnicodeError, ssl.SSLError):
        raise wire.ContractError("internal_failure") from None
    return context


def server_tls(certificate: Path, private_key: Path) -> ssl.SSLContext:
    # Fixed RO bind/inode custody is independently checked by the lane owner.
    load_public_certificate(certificate)
    load_private_file(private_key, capability=False)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2

    def reject_password() -> str:
        # OpenSSL must never fall back to an interactive password prompt.
        raise ValueError("private TLS key must be unencrypted")

    try:
        context.load_cert_chain(
            str(certificate), str(private_key), password=reject_password
        )
    except (OSError, ValueError, ssl.SSLError):
        raise wire.ContractError("internal_failure") from None
    return context


async def host_disposition(lane_id: str) -> bytes:
    """Fixed loopback read only; process identity/mount custody belongs the host."""
    # Validate the immutable metadata before any credentialed request.
    Collector(lane_id)
    context = client_tls(Path("/run/agent-reference/observer-ca.pem"))
    key = load_private_file(
        Path("/run/agent-reference/observer-control-key"), capability=True
    )
    failure = False
    result = None
    try:
        async with asyncio.timeout(wire.MAX_PRIVATE_HOST_HTTP_SECONDS):
            async with (
                httpx.AsyncClient(
                    verify=context,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS,
                ) as client,
                client.stream(
                    "GET",
                    "https://127.0.0.1:8443/__agent-reference/observer/host-disposition",
                    headers={"X-Agent-Reference-Observer-Key": key.decode("ascii")},
                ) as response,
            ):
                if (
                    response.status_code != 200
                    or response.headers.get("content-type") != "application/json"
                ):
                    raise wire.ContractError("invalid_schema")
                body = bytearray()
                async for chunk in response.aiter_raw(
                    chunk_size=wire.FAMILY_BYTE_CAPS["finish_request"] + 1
                ):
                    if len(body) + len(chunk) > wire.FAMILY_BYTE_CAPS["finish_request"]:
                        raise wire.ContractError("capacity_exhausted")
                    body.extend(chunk)
                value = wire.decode_private("finish_request", bytes(body))
                if value["lane_id"] != lane_id:
                    raise wire.ContractError("identity_mismatch")
                result = bytes(body)
    except Exception:  # noqa: BLE001 - no HTTP/parser/secret-bearing error escapes
        failure = True
    if failure or result is None:
        raise wire.ContractError("internal_failure")
    return result


@dataclass(frozen=True)
class PrivateRoute:
    family: str
    case_id: str | None = None
    cursor: int = 0


def private_route(method: str, target: str) -> PrivateRoute:
    if method == "POST" and target == "/__agent-reference/observer/sdk-receipts":
        return PrivateRoute("receipt")
    match = re.fullmatch(r"/__agent-reference/([0-9a-f]{32})/(arm|bind|close)", target)
    if method == "POST" and match:
        return PrivateRoute(match[2], match[1])
    if method == "GET" and target == "/__agent-reference/observer/host-disposition":
        return PrivateRoute("finish_request")
    match = re.fullmatch(
        r"/__agent-reference/([0-9a-f]{32})/oracle\?offset=(0|[1-9][0-9]?)", target
    )
    if method == "GET" and match and int(match[2]) <= wire.MAX_REQUESTS_PER_CASE:
        return PrivateRoute("model_readback", match[1], int(match[2]))
    match = re.fullmatch(
        r"/__agent-reference/(observer|[0-9a-f]{32}/observer)/sdk-receipts(?:\?cursor=(0|[1-9][0-9]{0,2}))?",
        target,
    )
    if method == "GET" and match:
        cursor = int(match[2] or "0")
        if cursor <= len(wire.ROLES) * wire.MAX_ROLE_RECEIPTS_PER_LANE:
            return PrivateRoute(
                "readback",
                None if match[1] == "observer" else match[1].split("/")[0],
                cursor,
            )
    raise wire.ContractError("invalid_schema")


def private_length(
    method: str,
    headers: list[tuple[str, str]],
    route: PrivateRoute,
    ingestion_key: bytes,
    control_key: bytes,
) -> int:
    values: dict[str, list[str]] = {}
    for name, value in headers:
        folded = name.lower()
        if folded in values:
            raise wire.ContractError("invalid_json")
        values[folded] = [value]
    if "transfer-encoding" in values or "content-encoding" in values:
        raise wire.ContractError("invalid_json")
    cap_values = values.get("x-agent-reference-observer-key", [])
    if len(cap_values) != 1 or re.fullmatch(r"[0-9a-f]{64}", cap_values[0]) is None:
        raise wire.ContractError("unauthorized")
    key = ingestion_key if route.family == "receipt" else control_key
    if not hmac.compare_digest(cap_values[0].encode("ascii"), key):
        raise wire.ContractError("unauthorized")
    length_values = values.get("content-length", [])
    types = values.get("content-type", [])
    if method == "GET":
        if types or length_values not in ([], ["0"]):
            raise wire.ContractError("invalid_json")
        return 0
    if types != ["application/json"] or len(length_values) != 1:
        raise wire.ContractError("invalid_json")
    if (
        re.fullmatch(r"0|[1-9][0-9]*", length_values[0]) is None
        or len(length_values[0]) > 5
    ):
        raise wire.ContractError("invalid_json")
    size = int(length_values[0])
    if size > wire.FAMILY_BYTE_CAPS[route.family]:
        raise wire.ContractError("capacity_exhausted")
    return size


def dispatch_private(
    collector: Collector, route: PrivateRoute, body: bytes
) -> Emission:
    if route.family == "receipt":
        return collector.ingest(body)
    if route.family in ("arm", "bind", "close"):
        assert route.case_id is not None
        return collector.control(route.family, route.case_id, body)
    if body:
        raise wire.ContractError("invalid_json")
    if route.family == "finish_request":
        return Emission(200, collector.finish_request())
    if route.family == "model_readback":
        if collector.oracle is None:
            raise wire.ContractError("invalid_state")
        return Emission(200, collector.oracle.readback(route.case_id, route.cursor))
    return Emission(200, collector.readback(route.cursor, route.case_id))


class PrivateHandler(socketserver.BaseRequestHandler):
    """One declared frame; no claim of TCP EOF or future-byte absence."""

    def handle(self) -> None:
        response = None
        collector = self.server.collector
        try:
            deadline = time.monotonic() + wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise wire.ContractError("invalid_json")
                self.request.settimeout(remaining)
                chunk = self.request.recv(1)
                if not chunk or len(header) >= wire.MAX_AUTH_HEADER_BYTES:
                    raise wire.ContractError("invalid_json")
                header.extend(chunk)
            lines = bytes(header).decode("ascii").split("\r\n")
            first = lines[0].split(" ")
            if len(first) != 3 or first[2] != "HTTP/1.1":
                raise wire.ContractError("invalid_json")
            method, target = first[:2]
            headers = []
            for line in lines[1:-2]:
                name, separator, value = line.partition(": ")
                if (
                    not separator
                    or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None
                ):
                    raise wire.ContractError("invalid_json")
                if any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise wire.ContractError("invalid_json")
                headers.append((name, value))
            route = private_route(method, target)
            size = private_length(
                method,
                headers,
                route,
                self.server.ingestion_key,
                self.server.control_key,
            )
            body = bytearray()
            deadline = time.monotonic() + wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS
            while len(body) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise wire.ContractError("invalid_json")
                self.request.settimeout(remaining)
                # One available extra byte is a detected pipelined frame, not an EOF wait.
                chunk = self.request.recv(size - len(body) + 1)
                if not chunk or len(chunk) > size - len(body):
                    raise wire.ContractError("invalid_json")
                body.extend(chunk)
            response = dispatch_private(collector, route, bytes(body))
        except wire.ContractError as error:
            collector.fail("control_invalid", case=True)
            response = private_error(error)
        except (OSError, UnicodeError, ValueError):
            collector.fail("control_invalid", case=True)
            response = private_error(wire.ContractError("invalid_json"))
        except Exception:  # noqa: BLE001 - private boundary never exports exception data
            collector.fail("observer_exception", case=True)
            response = private_error(wire.ContractError("internal_failure"))
        try:
            head = (
                f"HTTP/1.1 {response.status} Response\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(response.body)}\r\nConnection: close\r\n\r\n"
            ).encode("ascii")
            self.request.settimeout(wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS)
            self.request.sendall(head + response.body)
        except OSError:
            collector.emission_failed(response)
        else:
            collector.emitted(response)


class PrivateServer(socketserver.TCPServer):
    """Serial private TLS listener; certificate/keys never become selectors."""

    allow_reuse_address = True
    request_queue_size = wire.MAX_ROLE_QUEUE

    def __init__(
        self,
        address: tuple[str, int],
        collector: Collector,
        context: ssl.SSLContext,
        ingestion_key: bytes,
        control_key: bytes,
    ):
        if (
            type(ingestion_key) is not bytes
            or type(control_key) is not bytes
            or re.fullmatch(b"[0-9a-f]{64}", ingestion_key) is None
            or re.fullmatch(b"[0-9a-f]{64}", control_key) is None
            or hmac.compare_digest(ingestion_key, control_key)
        ):
            raise wire.ContractError("internal_failure")
        self.collector = collector
        self.context = context
        self.ingestion_key = ingestion_key
        self.control_key = control_key
        super().__init__(address, PrivateHandler)

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        try:
            connection.settimeout(wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS)
            connection = self.context.wrap_socket(
                connection, server_side=True, do_handshake_on_connect=False
            )
            connection.do_handshake()
            return connection, address
        except (OSError, ValueError):
            connection.close()
            self.collector.fail("transport_failure", case=True)
            raise OSError("private TLS handshake failed") from None

    def handle_error(self, request: Any, client_address: Any) -> None:
        self.collector.fail("observer_exception", case=True)


# Complete pinned source literals: declaration b79b5064, helpers suffix, summary v4.
AUTHORED_PROMPT = "You are the MTG Cove Recovery Steward escalation analyst. Independently verify ambiguous or contradictory recovery-test evidence with read-only tools. Always inspect capacity first. Call preview only when the delegated task supplies an authoritative device ID, recovery-agent ID, and VM name and specifically requests an independent preview; otherwise resolve mapping or capacity ambiguity from capacity evidence alone. Never invent IDs or call a mutation.\n\nReturn exactly one verdict as the first line:\n- SAFE_TO_REPREVIEW when the requested mapping is correct, live evidence is consistent, no active restore blocks it, and a bounded preview may safely be repeated.\n- DO_NOT_PROCEED when the request contains a wrong target ID or mapping, an active restore blocks it, compatibility is false, or the requested action is unsafe as stated. Give the authoritative correction when known.\n- HUMAN_REVIEW_REQUIRED when authoritative evidence remains missing, ambiguous, conflicting, unavailable, or outside the runbook after investigation.\n\nThen include authoritative IDs, the resolved or unresolved contradiction, evidence, confidence, and the smallest next action. Never expose credentials, tokens, passwords, passphrases, or raw sensitive payloads.\n"
AUTONOMOUS_SUFFIX = '\n\n---\nEXECUTION MODE: You are running autonomously — there is no human in this conversation.\n- Your final response MUST be conclusive: a summary, report, action result, or structured output.\n- Do NOT ask questions, request clarification, or use phrases like "let me know if you need anything else."\n- If you lack information, state what you could not determine and why, then provide the best result possible with available data.\n- If a tool call fails, attempt reasonable alternatives before reporting failure.'
SUMMARY_PROMPT = "You summarize what an AI agent did on a single run.\n\nYou receive the agent's name, the system prompt that defines its job, the\nrun's input, and the run's output. Produce a JSON object with:\n\n  - asked: one short sentence (<100 chars) describing what the user or event\n    asked for, in the user's voice. Extract the specific ask, not a generic\n    restatement of the agent's purpose.\n\n  - did: a short prose explanation of how the agent worked through the\n    request — the way someone would describe their work to a coworker. 1-4\n    sentences (<800 chars total). Walk through the meaningful decisions\n    and the reason behind each one (\"I needed X, so I called Y; that gave\n    me Z, which told me to...\"). Skip filler tool calls (a couple of\n    look-ups aren't worth narrating). DO NOT restate the agent's role or\n    purpose — describe THIS run.\n\n    *** TOOL MARKERS — STRICT RULE ***\n    If the run made any tool calls (visible in the input as\n    tool_call/tool_response entries, or implied by the agent's output),\n    EVERY tool name you mention in `did` MUST be wrapped in square\n    brackets. Use the exact machine-readable tool name from the run's\n    tool_call steps (e.g. `ai_ticketing_get_ticket_details`,\n    `delegate_to_security_subagent`), NOT a friendly paraphrase. The\n    brackets are required syntax — the UI renders them as clickable chips.\n\n    GOOD example (markers present, tool names exact):\n      \"I pulled the ticket via [ai_ticketing_get_ticket_details], saw the\n      EOL alert came from a Windows 2012 R2 host, then delegated to\n      [delegate_to_security_subagent] which recommended an in-place\n      upgrade. Wrote the categorization back with\n      [ai_ticketing_update_ticket].\"\n\n    BAD example (markers MISSING — do not do this):\n      \"I pulled the ticket details, saw the alert came from a 2012 R2\n      host, then asked the security sub-agent for guidance and updated\n      the ticket.\"\n\n    If no tools were called on this run, write `did` as plain prose with\n    no brackets. Brackets are required ONLY when tools were actually used.\n\n  - answered: one short sentence (<100 chars) capturing the agent's final\n    answer or outcome — the user-facing result of the run. Different from\n    `did`: `did` is the work, `answered` is the result.\n\n  - confidence: float 0.0-1.0 — how confident the agent's output appears to\n    be.\n  - confidence_reason: one sentence explaining the confidence assessment.\n  - metadata: object of k/v pairs (string -> string) extracting notable\n    entities from the run — the specific decisions, IDs, customer names,\n    categories, severity levels, billing status, and so on. Max 8 entries.\n\nReturn a single JSON object and nothing else. Do not wrap it in markdown code\nfences. Do not add a preamble, trailing prose, or explanation. The first\ncharacter of your response must be `{` and the last must be `}`."
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "cove_data_protection_recovery_steward_inspect_capacity",
            "description": "Read-only inventory of recovery locations and active Cove restores.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cove_data_protection_recovery_steward_preview_restore",
            "description": "Read-only compatibility and restore-point preview with a stable approval fingerprint.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "integer", "title": "Device Id"},
                    "recovery_agent_id": {
                        "type": "integer",
                        "title": "Recovery Agent Id",
                    },
                    "vm_name": {"type": "string", "title": "Vm Name"},
                    "halo_ticket_id": {"type": "integer", "title": "Halo Ticket Id"},
                    "cpu_count": {
                        "type": "integer",
                        "title": "Cpu Count",
                        "default": 2,
                    },
                    "ram_size_mb": {
                        "type": "integer",
                        "title": "Ram Size Mb",
                        "default": 4096,
                    },
                    "vhd_path": {
                        "type": "string",
                        "title": "Vhd Path",
                        "default": "E:\\",
                    },
                },
                "additionalProperties": False,
                "required": [
                    "device_id",
                    "recovery_agent_id",
                    "vm_name",
                    "halo_ticket_id",
                ],
            },
        },
    },
]

MODEL = "gpt-5.4-mini"
AGENT_NAME = "Cove Recovery Steward Escalation Analyst"
TOOL_NAME = "cove_data_protection_recovery_steward_inspect_capacity"
CALL_ID = "call_c1r_capacity"
CREATED = 1780272000
TASK = {
    "task": "Independently inspect capacity evidence only. No authoritative device or recovery-agent mapping is supplied; do not preview a restore."
}
FINAL_TEXT = "HUMAN_REVIEW_REQUIRED\nCapacity inventory returned no recovery agents or restore rows. No authoritative target was supplied. Confidence: low. Next action: obtain authoritative device and recovery-agent mapping."
# Receive-side HTTP policy bounds, not a new private schema or product limit.
MAX_PUBLIC_HEADERS = 20
MAX_PUBLIC_HEADER_VALUE = 256
MAX_SYNTHETIC_BEARER = 128


def exact(value: Any, expected: Any) -> bool:
    """Closed semantic equality: bool/int and absent/null never alias."""
    if type(value) is not type(expected):
        return False
    if type(value) is dict:
        return value.keys() == expected.keys() and all(
            exact(value[k], expected[k]) for k in expected
        )
    if type(value) is list:
        return len(value) == len(expected) and all(
            exact(a, b) for a, b in zip(value, expected, strict=True)
        )
    return value == expected


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid public JSON")
        result[key] = value
    return result


def reject_number(value: str) -> Any:
    raise ValueError("invalid public JSON number")


def public_json(body: bytes) -> Any:
    return json.loads(
        body.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_float=reject_number,
        parse_constant=reject_number,
    )


def compact(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def derive_header_facts() -> dict[str, str]:
    """Passive installed source metadata only; no async-context guess or probes."""
    from httpx import _client as httpx_client
    from httpx2 import _client as httpx2_client
    from openai import _base_client as sdk

    for package, version in (
        ("openai", "3.3.0"),
        ("pydantic-ai-slim", "2.35.3"),
        ("httpx", "0.28.1"),
        ("httpx2", "2.12.0"),
    ):
        if importlib.metadata.version(package) != version:
            raise wire.ContractError("internal_failure")
    facts = {
        "os": str(sdk.get_platform()),
        "arch": str(sdk.get_architecture()),
        "runtime": sdk.get_python_runtime(),
        "version": sdk.get_python_version(),
        "model_encoding": httpx2_client.ACCEPT_ENCODING,
        "cove_encoding": httpx_client.ACCEPT_ENCODING,
    }
    allowed = (
        "gzip, deflate",
        "gzip, deflate, br",
        "gzip, deflate, zstd",
        "gzip, deflate, br, zstd",
    )
    if (
        facts["os"] != "Linux"
        or facts["arch"] not in ("x64", "arm64")
        or facts["runtime"] != "CPython"
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", facts["version"]) is None
        or any(facts[k] not in allowed for k in ("model_encoding", "cove_encoding"))
    ):
        raise wire.ContractError("internal_failure")
    return facts


def header_expectation(
    kind: str, size: int, config: dict[str, Any], facts: dict[str, str]
) -> dict[str, str]:
    cove = kind.startswith("cove_")
    result = {
        "host": "scheduler-fixtures:8080",
        "content-type": "application/json",
        "accept": "application/json, text/plain, */*" if cove else "application/json",
        "accept-encoding": facts["cove_encoding" if cove else "model_encoding"],
        "connection": "keep-alive",
        "user-agent": "python-httpx/0.28.1"
        if cove
        else (
            "AsyncOpenAI/Python 3.3.0"
            if kind in ("responses_probe", "chat_probe")
            else "pydantic-ai/2.35.3"
        ),
    }
    if kind not in ("cove_agents", "cove_dashboard"):
        result["content-length"] = str(size)
    if cove:
        result.update(
            origin="https://backup.management", referer="https://backup.management/"
        )
        if kind != "cove_login":
            result["authorization"] = "Bearer " + config["visa"]
    else:
        result.update(
            {
                "authorization": "Bearer " + config["model_key"],
                "x-stainless-lang": "python",
                "x-stainless-package-version": "3.3.0",
                "x-stainless-os": facts["os"],
                "x-stainless-arch": facts["arch"],
                "x-stainless-runtime": facts["runtime"],
                "x-stainless-runtime-version": facts["version"],
                "x-stainless-async": "async:asyncio",
                "x-stainless-retry-count": "0",
                "x-stainless-read-timeout": "600",
            }
        )
    return result


def checked_transport_headers(headers: list[tuple[str, str]]) -> dict[str, str]:
    result = {}
    total = 0
    for name, value in headers:
        total += len(name.encode("ascii")) + len(value.encode("ascii")) + 4
        if name.lower() in result or any(
            ord(char) < 32 or ord(char) == 127 for char in value
        ):
            raise wire.ContractError("invalid_schema")
        result[name.lower()] = value
    if total > wire.MAX_AUTH_HEADER_BYTES:
        raise wire.ContractError("invalid_schema")
    return result


def checked_public_headers(headers: list[tuple[str, str]]) -> dict[str, str]:
    result = checked_transport_headers(headers)
    if (
        len(headers) > MAX_PUBLIC_HEADERS
        or any(len(value) > MAX_PUBLIC_HEADER_VALUE for _, value in headers)
        or len(result.get("authorization", "")) > MAX_SYNTHETIC_BEARER
    ):
        raise wire.ContractError("invalid_schema")
    return result


@dataclass(frozen=True)
class ProgramResponse:
    status: int
    body: bytes
    content_type: str = "application/json"
    matched: bool = False
    tool_content: str | None = None
    kind: str = "unexpected"


def completion(
    case_id: str,
    label: str,
    content: str | None,
    finish: str,
    prompt: int,
    reply: int,
    *,
    tool: bool = False,
) -> bytes:
    message = {"role": "assistant", "content": content, "refusal": None}
    if tool:
        message["tool_calls"] = [
            {
                "id": CALL_ID,
                "type": "function",
                "function": {"name": TOOL_NAME, "arguments": "{}"},
            }
        ]
    return compact(
        {
            "id": (
                f"chatcmpl-c1r-probe-{case_id}-{label.split('-', 1)[1]}"
                if label.startswith("probe-")
                else f"chatcmpl-c1r-{label}-{case_id}"
            ),
            "object": "chat.completion",
            "created": CREATED,
            "model": MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "logprobs": None,
                    "finish_reason": finish,
                }
            ],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": reply,
                "total_tokens": prompt + reply,
            },
        }
    )


def summary_sse(case_id: str) -> bytes:
    summary = {
        "asked": "Inspect capacity evidence.",
        "did": "Inspected capacity; no recovery agents or restore rows were configured.",
        "answered": "HUMAN_REVIEW_REQUIRED",
        "confidence": 0.4,
        "confidence_reason": "Authoritative target mapping was not supplied.",
        "metadata": {"fixture_case": case_id},
    }
    base = {
        "id": f"chatcmpl-c1r-summary-{case_id}",
        "object": "chat.completion.chunk",
        "created": CREATED,
        "model": MODEL,
    }
    frames = []
    for delta, finish in (
        ({"role": "assistant", "content": ""}, None),
        ({"content": compact(summary).decode("utf-8")}, None),
        ({}, "stop"),
    ):
        frames.append(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": finish,
                        "logprobs": None,
                    }
                ],
            }
        )
    frames.append(
        {
            **base,
            "choices": [],
            "usage": {"prompt_tokens": 37, "completion_tokens": 13, "total_tokens": 50},
        }
    )
    return (
        b"".join(b"data: " + compact(frame) + b"\n\n" for frame in frames)
        + b"data: [DONE]\n\n"
    )


class Oracle:
    """Finite model/Cove reservations; same case budget, no remote-consumption claim."""

    def __init__(
        self, collector: Collector, config: dict[str, Any], facts: dict[str, str]
    ):
        wire.validate_private("model_input", config)
        if config["lane_id"] != collector.lane_id or collector.oracle is not None:
            raise wire.ContractError("identity_mismatch")
        self.collector = collector
        # The validated private object is copied, never caller-mutable state.
        self.config = wire.decode_private(
            "model_input", wire.encode_private("model_input", config)
        )
        self.case_id = config["case_id"]
        self.facts = facts.copy()
        self.records: list[dict[str, Any]] = []
        self.phase = "responses_probe"
        self.pairs = 1
        collector.oracle = self

    @property
    def pending(self) -> int:
        return sum(not record["settled"] for record in self.records)

    def reserve(self) -> int:
        with self.collector._lock:
            case = self.collector._case_id(self.case_id)
            if case["state"] in ("closing", "closed"):
                self.collector.fail("late_witness", case=True)
            self.collector._request_attempt(sdk=False)
            index = len(self.records)
            self.records.append(
                {
                    "index": index,
                    "kind": "unexpected",
                    "settled": False,
                    "matched": None,
                    "status": None,
                    "write_complete": False,
                    "tool_content": None,
                }
            )
            return index

    def _value(self, offset: int) -> dict[str, Any]:
        case = self.collector._case_id(self.case_id)
        total = len(self.records)
        if type(offset) is not int or not 0 <= offset <= total:
            raise wire.ContractError("invalid_state")
        end = min(offset + wire.READBACK_PAGE_SIZE, total)
        return {
            "schema": "bifrost.agent-reference.model-oracle-readback/v1",
            "lane_id": self.collector.lane_id,
            "case_id": self.case_id,
            "run_id": case["run_id"],
            "finish_id": case["finish_id"],
            "state": case["state"],
            "first_failure": case["first_failure"],
            "attempt_count": case["request_count"],
            "offset": offset,
            "total": total,
            "next_offset": end if end < total else None,
            "records": [record.copy() for record in self.records[offset:end]],
        }

    def readback(self, case_id: str | None, offset: int) -> bytes:
        with self.collector._lock:
            if case_id != self.case_id:
                raise wire.ContractError("unknown_case")
            return wire.encode_private("model_readback", self._value(offset))

    def _kind(self, method: str, target: str) -> str:
        base = f"/__agent-reference/{self.case_id}"
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return "unexpected"
        if (
            method == "POST"
            and parsed.path == base + "/model/v1/responses"
            and not parsed.query
        ):
            return "responses_probe"
        if (
            method == "POST"
            and parsed.path == base + "/model/v1/chat/completions"
            and not parsed.query
        ):
            return (
                self.phase
                if self.phase in ("chat_probe", "agent_first", "agent_final", "summary")
                else "unexpected"
            )
        if (
            method == "POST"
            and parsed.path == base + "/cove/jsonapi"
            and not parsed.query
        ):
            return "cove_login"
        if (
            method == "GET"
            and parsed.path
            == base + "/cove/draas/actual-statistics/v1/dashboard/recovery-agents/"
        ):
            return "cove_agents"
        if (
            method == "GET"
            and parsed.path == base + "/cove/draas/actual-statistics/v1/dashboard/"
        ):
            return "cove_dashboard"
        return "unexpected"

    def _tool_content(self, index: int, content: Any) -> None:
        # Local validation candidate only; these flags are NOT published/emission proof.
        value = self._value(index)
        value["records"][0] = {
            "index": index,
            "kind": "agent_final",
            "settled": True,
            "matched": True,
            "status": 200,
            "write_complete": True,
            "tool_content": content,
        }
        wire.validate_private("model_readback", value)

    def prepare(
        self,
        index: int,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
        body: bytes,
    ) -> ProgramResponse:
        with self.collector._lock:
            if type(index) is not int or not 0 <= index < len(self.records):
                raise wire.ContractError("invalid_state")
            if self.records[index]["settled"]:
                raise wire.ContractError("invalid_state")
            kind = self._kind(method, target)
            try:
                case = self.collector._case_id(self.case_id)
                if (
                    case["state"] in ("closing", "closed")
                    or self.collector.first_failure is not None
                ):
                    raise wire.ContractError("invalid_state")
                if len(body) > wire.MAX_FIXTURE_INPUT_BYTES:
                    raise wire.ContractError("capacity_exhausted")
                observed = checked_public_headers(headers)
                if not exact(
                    observed,
                    header_expectation(kind, len(body), self.config, self.facts),
                ):
                    raise wire.ContractError("invalid_schema")
                # Optional second detector pair ONLY after the final agent completion.
                if (
                    self.phase == "summary"
                    and kind == "responses_probe"
                    and self.pairs == 1
                ):
                    self.pairs = 2
                elif kind != self.phase:
                    raise wire.ContractError("invalid_state")
                response = self._program(index, kind, target, body)
            except (
                wire.ContractError,
                ValueError,
                UnicodeError,
                RecursionError,
                KeyError,
                TypeError,
            ):
                self.collector.fail("correlation_failed", case=True)
                return ProgramResponse(
                    400, compact({"error": "correlation_failed"}), kind=kind
                )
            return ProgramResponse(
                response.status,
                response.body,
                response.content_type,
                response.matched,
                response.tool_content,
                kind,
            )

    def _program(
        self, index: int, kind: str, target: str, body: bytes
    ) -> ProgramResponse:
        if kind == "responses_probe":
            expected = {
                "model": MODEL,
                "input": "Reply with OK.",
                "max_output_tokens": 64,
                "store": False,
            }
            if not exact(public_json(body), expected):
                raise wire.ContractError("invalid_schema")
            self.phase = "chat_probe"
            return ProgramResponse(
                404,
                compact(
                    {
                        "error": {
                            "message": "Responses API is not supported",
                            "type": "invalid_request_error",
                            "code": "unsupported_endpoint",
                        }
                    }
                ),
                matched=True,
            )
        if kind == "chat_probe":
            expected = {
                "model": MODEL,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_completion_tokens": 64,
            }
            if not exact(public_json(body), expected):
                raise wire.ContractError("invalid_schema")
            self.phase = "agent_first" if self.pairs == 1 else "summary"
            return ProgramResponse(
                200,
                completion(self.case_id, f"probe-{self.pairs}", "OK", "stop", 1, 1),
                matched=True,
            )
        if kind in ("agent_first", "agent_final"):
            messages = [
                {"role": "system", "content": AUTHORED_PROMPT + AUTONOMOUS_SUFFIX},
                {"role": "user", "content": json.dumps(TASK)},
            ]
            content = None
            value = public_json(body)
            if kind == "agent_final":
                received = value["messages"]
                if (
                    type(received) is not list
                    or len(received) != 4
                    or type(received[3]) is not dict
                ):
                    raise wire.ContractError("invalid_schema")
                content = received[3].get("content")
                self._tool_content(index, content)
                messages += [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": CALL_ID,
                                "type": "function",
                                "function": {"name": TOOL_NAME, "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": CALL_ID, "content": content},
                ]
            expected = {
                "model": MODEL,
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "stream": False,
                "max_completion_tokens": 8192,
                "store": False,
            }
            if not exact(value, expected):
                raise wire.ContractError("invalid_schema")
            if kind == "agent_first":
                self.phase = "cove_login"
                return ProgramResponse(
                    200,
                    completion(
                        self.case_id, "first", None, "tool_calls", 17, 5, tool=True
                    ),
                    matched=True,
                )
            self.phase = "summary"
            return ProgramResponse(
                200,
                completion(self.case_id, "final", FINAL_TEXT, "stop", 29, 11),
                matched=True,
                tool_content=content,
            )
        if kind == "cove_login":
            value = public_json(body)
            identity = value.get("id") if type(value) is dict else None
            if (
                type(identity) is not str
                or re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    identity,
                )
                is None
                or not exact(
                    value,
                    {
                        "jsonrpc": "2.0",
                        "method": "Login",
                        "params": {
                            "partner": self.config["partner_name"],
                            "username": self.config["username"],
                            "password": self.config["password"],
                        },
                        "id": identity,
                    },
                )
            ):
                raise wire.ContractError("invalid_schema")
            self.phase = "cove_agents"
            return ProgramResponse(
                200,
                compact(
                    {
                        "jsonrpc": "2.0",
                        "id": identity,
                        "visa": self.config["visa"],
                        "result": {"result": {"PartnerId": 7}},
                    }
                ),
                matched=True,
            )
        if kind in ("cove_agents", "cove_dashboard"):
            query = parse_qsl(
                urlsplit(target).query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=MAX_PUBLIC_HEADERS,
            )
            expected_query = {"offset": "0", "limit": "100"}
            if kind == "cove_agents":
                expected_query.update(
                    {
                        "filter[agent_state.in]": "ONLINE,OFFLINE,STORAGE_NOT_CONFIGURED",
                        "filter[materialized_path.contains]": "/7/",
                        "sort": "name",
                    }
                )
            else:
                expected_query.update(
                    {
                        "fields": "",
                        "sort": "-start_restore_timestamp",
                        "filter[type.in]": "AZURE,ESXI_ON_DEMAND,SELF_HOSTED_ON_DEMAND",
                        "filter[partner_materialized_path.contains]": "/7/",
                    }
                )
            if (
                body
                or len(query) != len(dict(query))
                or not exact(dict(query), expected_query)
            ):
                raise wire.ContractError("invalid_schema")
            self.phase = "cove_dashboard" if kind == "cove_agents" else "agent_final"
            return ProgramResponse(200, compact({"data": []}), matched=True)
        if kind == "summary":
            payload = {
                "agent_name": AGENT_NAME,
                "agent_system_prompt": AUTHORED_PROMPT[:2000],
                "input": TASK,
                "output": {"text": FINAL_TEXT},
            }
            expected = {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": json.dumps(payload, default=str)},
                ],
                "stream": True,
                "stream_options": {"include_usage": True},
                "store": False,
            }
            if not exact(public_json(body), expected):
                raise wire.ContractError("invalid_schema")
            self.phase = "done"
            return ProgramResponse(
                200, summary_sse(self.case_id), "text/event-stream", True
            )
        raise wire.ContractError("invalid_schema")

    def settle(
        self, index: int, response: ProgramResponse, *, write_complete: bool
    ) -> None:
        with self.collector._lock:
            if type(index) is not int or not 0 <= index < len(self.records):
                raise wire.ContractError("invalid_state")
            record = self.records[index]
            if record["settled"]:
                raise wire.ContractError("invalid_state")
            if not write_complete:
                self.collector.fail("transport_failure", case=True)
            if not response.matched:
                self.collector.fail("correlation_failed", case=True)
            record.update(
                kind=response.kind,
                settled=True,
                matched=response.matched,
                status=response.status if write_complete else None,
                write_complete=write_complete,
                tool_content=response.tool_content
                if response.matched and response.kind == "agent_final"
                else None,
            )


class PublicHandler(socketserver.BaseRequestHandler):
    """Bound first-line classification; model reservation precedes body/response I/O."""

    def handle(self) -> None:
        oracle = self.server.oracle
        index = None
        health = False
        admission_attempted = False
        response = ProgramResponse(400, compact({"error": "correlation_failed"}))
        try:
            deadline = time.monotonic() + wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS
            header = bytearray()

            def read_until(marker: bytes) -> None:
                while not header.endswith(marker):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or len(header) >= wire.MAX_AUTH_HEADER_BYTES:
                        raise wire.ContractError("invalid_json")
                    self.request.settimeout(remaining)
                    chunk = self.request.recv(1)
                    if not chunk:
                        raise wire.ContractError("invalid_json")
                    header.extend(chunk)

            read_until(b"\r\n")
            first = bytes(header[:-2]).decode("ascii").split(" ")
            if len(first) != 3 or first[2] != "HTTP/1.1":
                raise wire.ContractError("invalid_json")
            method, target = first[:2]
            # Exact existing infrastructure path/query handling, never model authority.
            health = method == "GET" and urlsplit(target).path == "/health"
            if not health:
                admission_attempted = True
                index = oracle.reserve()
            read_until(b"\r\n\r\n")
            headers = []
            for line in bytes(header).decode("ascii").split("\r\n")[1:-2]:
                name, separator, value = line.partition(": ")
                if (
                    not separator
                    or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None
                ):
                    raise wire.ContractError("invalid_json")
                headers.append((name, value))
            values = (
                checked_transport_headers(headers)
                if health
                else checked_public_headers(headers)
            )
            if "transfer-encoding" in values or "content-encoding" in values:
                raise wire.ContractError("invalid_json")
            length = values.get("content-length")
            if method == "GET":
                if length is not None and not (health and length == "0"):
                    raise wire.ContractError("invalid_json")
                size = 0
            elif (
                method == "POST"
                and length is not None
                and re.fullmatch(r"[1-9][0-9]{0,4}", length)
            ):
                size = int(length)
                if size > wire.MAX_FIXTURE_INPUT_BYTES:
                    raise wire.ContractError("capacity_exhausted")
            else:
                raise wire.ContractError("invalid_json")
            if health:
                response = ProgramResponse(200, json.dumps({"status": "ok"}).encode())
            else:
                body = bytearray()
                deadline = time.monotonic() + wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS
                while len(body) < size:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise wire.ContractError("invalid_json")
                    self.request.settimeout(remaining)
                    chunk = self.request.recv(size - len(body) + 1)
                    if not chunk or len(chunk) > size - len(body):
                        raise wire.ContractError("invalid_json")
                    body.extend(chunk)
                response = oracle.prepare(index, method, target, headers, bytes(body))
        except Exception as error:  # noqa: BLE001 - original input/exception values never escape
            if not health:
                if not admission_attempted:
                    admission_attempted = True
                    try:
                        index = oracle.reserve()
                    except wire.ContractError as admission:
                        status = 429 if admission.code == "capacity_exhausted" else 409
                        response = ProgramResponse(
                            status, compact({"error": admission.code})
                        )
                oracle.collector.fail("correlation_failed", case=True)
            if (
                isinstance(error, wire.ContractError)
                and error.code == "capacity_exhausted"
            ):
                response = ProgramResponse(
                    429, compact({"error": "capacity_exhausted"})
                )
        except BaseException:
            if not health:
                if not admission_attempted:
                    admission_attempted = True
                    try:
                        index = oracle.reserve()
                    except wire.ContractError:
                        pass
                oracle.collector.fail("transport_failure", case=True)
                if index is not None:
                    oracle.settle(index, response, write_complete=False)
            raise
        complete = False
        try:
            head = (
                f"HTTP/1.1 {response.status} Response\r\nContent-Type: {response.content_type}\r\nContent-Length: {len(response.body)}\r\nConnection: close\r\n\r\n"
            ).encode("ascii")
            self.request.settimeout(wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS)
            self.request.sendall(head + response.body)
            complete = True
        except OSError:
            if not health:
                oracle.collector.fail("transport_failure", case=True)
        finally:
            if index is not None:
                oracle.settle(index, response, write_complete=complete)


class PublicServer(socketserver.TCPServer):
    allow_reuse_address = True
    request_queue_size = wire.MAX_ROLE_QUEUE

    def __init__(self, address: tuple[str, int], oracle: Oracle):
        self.oracle = oracle
        super().__init__(address, PublicHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        self.oracle.collector.fail("correlation_failed", case=True)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["--host-disposition"]):
        print("agent-reference fixture failed", file=sys.stderr)
        return 1
    private = None
    public = None
    thread = None
    private_started = False
    try:
        lane_id = os.environ.get("BIFROST_AGENT_REFERENCE_LANE_ID")
        if args == ["--host-disposition"]:
            raw = asyncio.run(host_disposition(lane_id))
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()
            return 0
        collector = Collector(lane_id)
        ingestion = load_private_file(
            Path("/run/agent-reference/observer-ingest-key"), capability=True
        )
        control = load_private_file(
            Path("/run/agent-reference/observer-control-key"), capability=True
        )
        raw = load_private_file(
            Path("/app/reference-model-oracle/input.json"),
            capability=False,
            maximum=wire.MAX_MODEL_INPUT_BYTES,
        )
        config = wire.decode_private("model_input", raw)
        if any(
            value.encode("ascii") in (ingestion, control)
            for value in (config["model_key"], config["password"], config["visa"])
        ):
            raise wire.ContractError("internal_failure")
        oracle = Oracle(collector, config, derive_header_facts())
        tls = server_tls(
            Path("/run/agent-reference/observer-server.pem"),
            Path("/run/agent-reference/observer-server-key.pem"),
        )
        private = PrivateServer(("0.0.0.0", 8443), collector, tls, ingestion, control)
        public = PublicServer(("0.0.0.0", 8080), oracle)
        thread = threading.Thread(
            target=private.serve_forever, name="agent-reference-private", daemon=False
        )

        def stop_fixture(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop_fixture)
        thread.start()
        private_started = True
        public.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001 - fixed CLI never exports config, headers or raw exceptions
        print("agent-reference fixture failed", file=sys.stderr)
        return 1
    finally:
        if private is not None:
            if private_started:
                private.shutdown()
                thread.join(wire.MAX_PRIVATE_TRANSPORT_STAGE_SECONDS)
            private.server_close()
        if public is not None:
            public.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
