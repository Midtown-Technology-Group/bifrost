"""Private finite SDK ingress-to-owner bridge; no route or lifecycle writer.

Only the trusted ingress calls this, after dedicated JWT/preimage verification.
Its original actor PID/UID/start-time and private socket come from authenticated
parent custody, never an HTTP request. No bearer or signing material crosses it.
The runtime sees only ordinary HTTPS, not this coordinator implementation seam.
"""

import asyncio
import hashlib
import json
import os
import socket
import stat
import struct
import sys
from pathlib import Path

from src.core.runtime_sdk_credentials import RuntimeSDKDenied
from src.services.isolated_runtime_sdk_tokens import FiniteIntegrationGetIntent


def _request_bytes(intent: FiniteIntegrationGetIntent) -> bytes:
    raw = json.dumps(
        {
            "grant_id": str(intent.grant_id),
            "grant_digest": intent.grant_digest,
            "integration_name": intent.integration_name,
            "organization_id": str(intent.organization_id),
            "solution_id": str(intent.solution_id),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if not 0 < len(raw) <= 4096:
        raise RuntimeSDKDenied("live runtime SDK admission denied")
    return raw


def _accepted_response(raw: bytes, request: bytes) -> bool:
    def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("duplicate private response field")
            result[name] = value
        return result

    response = json.loads(raw.decode("utf-8"), object_pairs_hook=closed_object)
    return (
        isinstance(response, dict)
        and set(response) == {"admitted", "request_sha256"}
        and response["admitted"] is True
        and response["request_sha256"] == hashlib.sha256(request).hexdigest()
    )


def _start_ticks(pid: int) -> str:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    return fields[19]


def _private_socket(path: Path, uid: int) -> tuple[int, int]:
    if (
        not path.is_absolute()
        or path.resolve(strict=True) != path
        or len(os.fsencode(path)) >= 108
    ):
        raise RuntimeSDKDenied("live runtime SDK admission denied")
    parent, target = path.parent.lstat(), path.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != uid
        or stat.S_IMODE(parent.st_mode) != 0o700
        or not stat.S_ISSOCK(target.st_mode)
        or target.st_uid != uid
        or stat.S_IMODE(target.st_mode) != 0o600
    ):
        raise RuntimeSDKDenied("live runtime SDK admission denied")
    return target.st_dev, target.st_ino


async def admit_live_integration_get(
    intent: FiniteIntegrationGetIntent,
    *,
    gate_path: Path,
    owner_pid: int,
    owner_uid: int,
    owner_start_ticks: str,
) -> None:
    """One bounded request to the original live owner; never replay/reconnect.

    The owner supplies its retained SessionFence internally, matches the pinned
    neutral artifact binding, checks actual guardian custody and commits the
    common Rust SQL admission. An uncertain/denied response prevents fetching.
    A successful reply belongs only to this request/connection; it cannot start,
    cancel, renew, reopen, reassign or finalize an execution.
    """
    writer: asyncio.StreamWriter | None = None
    try:
        if (
            sys.platform != "linux"
            or type(owner_pid) is not int
            or owner_pid <= 0
            or type(owner_uid) is not int
            or owner_uid <= 0
            or type(owner_start_ticks) is not str
            or not owner_start_ticks.isascii()
            or not owner_start_ticks.isdigit()
            or _start_ticks(owner_pid) != owner_start_ticks
        ):
            raise ValueError("invalid original owner")
        original = _private_socket(gate_path, owner_uid)
        request = _request_bytes(intent)

        async def exchange() -> None:
            nonlocal writer
            reader, writer = await asyncio.open_unix_connection(path=gate_path)
            peer = writer.get_extra_info("socket")
            if peer is None:
                raise ValueError("missing peer")
            peer_pid, peer_uid, _ = struct.unpack(
                "=3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if (
                peer_pid != owner_pid
                or peer_uid != owner_uid
                or _start_ticks(peer_pid) != owner_start_ticks
                or _private_socket(gate_path, owner_uid) != original
            ):
                raise ValueError("owner custody changed")
            writer.write(len(request).to_bytes(4, "big") + request)
            await writer.drain()
            writer.write_eof()
            size = int.from_bytes(await reader.readexactly(4), "big")
            if not 0 < size <= 1024:
                raise ValueError("invalid private response size")
            response = await reader.readexactly(size)
            if await reader.read(1) != b"" or not _accepted_response(response, request):
                raise ValueError("no observed admission")

        await asyncio.wait_for(exchange(), timeout=8)
    except (
        OSError,
        ValueError,
        TypeError,
        IndexError,
        KeyError,
        struct.error,
        asyncio.IncompleteReadError,
        TimeoutError,
        RecursionError,
    ):
        raise RuntimeSDKDenied("live runtime SDK admission denied") from None
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            except (OSError, TimeoutError):
                pass  # connection is closed; this never enables fetch or replay
