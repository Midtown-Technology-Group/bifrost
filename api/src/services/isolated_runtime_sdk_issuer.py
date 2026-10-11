"""One private finite issuer connection from the original trusted Rust owner.

This service is never mounted into the platform/SDK app. The parent pins caller,
source, policy and one committed-grant loader independently of incoming bytes.
No lifecycle writes, renewal, ordinary credential, replay or vendor operation.
"""

import asyncio
import hashlib
import json
import os
import socket
import stat
import struct
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from src.core.runtime_sdk_credentials import (
    AcceptedManifestIdentity,
    AuthorizedCallerSnapshot,
    GrantReference,
    GrantSnapshot,
    RuntimeSDKDenied,
    SelectedSDKPolicy,
)
from src.services.isolated_runtime_sdk_bridge import _start_ticks
from src.services.isolated_runtime_sdk_tokens import sign_finite_runtime_sdk_access


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _closed(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate issuer field")
        result[key] = value
    return result


class FiniteIssuerServer:
    def __init__(
        self,
        *,
        directory: Path,
        owner_pid: int,
        owner_uid: int,
        owner_start_ticks: str,
        grant_id: UUID,
        load_snapshot: Callable[[], Awaitable[GrantSnapshot]],
        caller: AuthorizedCallerSnapshot,
        source: AcceptedManifestIdentity,
        policy: SelectedSDKPolicy,
        ca_pem: str,
    ) -> None:
        self.directory = directory
        self.path = directory / "issuer.sock"
        self.owner_pid, self.owner_uid, self.owner_ticks = (
            owner_pid,
            owner_uid,
            owner_start_ticks,
        )
        self.grant_id, self.load_snapshot = grant_id, load_snapshot
        self.caller = AuthorizedCallerSnapshot.model_validate(caller.model_dump())
        self.source = AcceptedManifestIdentity.model_validate(source.model_dump())
        self.policy = SelectedSDKPolicy.model_validate(policy.model_dump())
        self.ca_pem = ca_pem
        self._socket: socket.socket | None = None
        self._inode: tuple[int, int] | None = None
        self._task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._started = False
        self._response_attempted = False

    def _original_peer(self, connected: socket.socket) -> None:
        pid, uid, _ = struct.unpack(
            "=3i", connected.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if (
            pid != self.owner_pid
            or uid != self.owner_uid
            or _start_ticks(pid) != self.owner_ticks
        ):
            raise RuntimeSDKDenied("finite issuer peer denied")

    async def start(self) -> None:
        if self._started:
            raise RuntimeSDKDenied("finite issuer cannot restart")
        self._started = True
        parent = self.directory.lstat()
        if (
            os.geteuid() <= 0
            or type(self.owner_pid) is not int
            or self.owner_pid <= 0
            or type(self.owner_uid) is not int
            or self.owner_uid <= 0
            or not self.owner_ticks.isascii()
            or not self.owner_ticks.isdigit()
            or _start_ticks(self.owner_pid) != self.owner_ticks
            or not self.directory.is_absolute()
            or self.directory.resolve(strict=True) != self.directory
            or len(os.fsencode(self.path)) >= 108
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
            or any(self.directory.iterdir())
            or not self.ca_pem
            or len(self.ca_pem.encode()) > 8192
        ):
            raise RuntimeSDKDenied("finite issuer custody denied")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket = sock
        try:
            sock.bind(str(self.path))
            original = self.path.lstat()
            self._inode = original.st_dev, original.st_ino
            os.chmod(self.path, 0o600)
            sock.listen(1)
            sock.setblocking(False)
            self._task = asyncio.create_task(self._serve_once(sock))
        except BaseException:
            await self.stop()
            raise

    async def _serve_once(self, sock: socket.socket) -> None:
        connected: socket.socket | None = None
        try:
            connected, _ = await asyncio.get_running_loop().sock_accept(sock)
            # One accepted connection consumes this issuer, even if denied.
            sock.close()
            async with asyncio.timeout(3):
                self._original_peer(connected)
                reader, writer = await asyncio.open_connection(sock=connected)
                self._writer = writer
                size = int.from_bytes(await reader.readexactly(4), "big")
                if not 0 < size <= 8192:
                    raise RuntimeSDKDenied("finite issuer request denied")
                raw = await reader.readexactly(size)
                if await reader.read(1) != b"":
                    raise RuntimeSDKDenied("finite issuer request denied")
                peer = writer.get_extra_info("socket")
                if peer is None:
                    raise RuntimeSDKDenied("finite issuer peer denied")
                self._original_peer(peer)
                response = await self._signed_response(raw)
                self._original_peer(peer)
                if not 0 < len(response) <= 16384:
                    raise RuntimeSDKDenied("finite issuer response denied")
                writer.write(len(response).to_bytes(4, "big") + response)
                await writer.drain()
        except (
            OSError,
            ValueError,
            TypeError,
            IndexError,
            KeyError,
            struct.error,
            asyncio.IncompleteReadError,
            TimeoutError,
            RuntimeSDKDenied,
        ):
            pass  # Denial/uncertainty closes this sole attempt; never retransmit.
        finally:
            if self._writer is not None:
                self._writer.close()
                try:
                    await asyncio.wait_for(self._writer.wait_closed(), timeout=1)
                except (OSError, TimeoutError):
                    pass
                self._writer = None
            elif connected is not None:
                connected.close()

    async def _signed_response(self, raw: bytes) -> bytes:
        if self._response_attempted:
            raise RuntimeSDKDenied("finite issuer attempt already consumed")
        self._response_attempted = True
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_closed)
        if (
            not isinstance(request, dict)
            or set(request)
            != {"snapshot", "caller", "grant_id", "grant_digest", "expires_at"}
            or _canonical(request) != raw
            or request["grant_id"] != str(self.grant_id)
        ):
            raise RuntimeSDKDenied("finite issuer request denied")
        supplied = GrantSnapshot.model_validate(request["snapshot"])
        caller = AuthorizedCallerSnapshot.model_validate(request["caller"])
        # This loader chooses the fixed original identity, never incoming IDs.
        snapshot = GrantSnapshot.model_validate(await self.load_snapshot())
        expiry = (
            snapshot.initial_access_expires_at.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        if (
            snapshot.id != self.grant_id
            or supplied != snapshot
            or caller != self.caller
            or request["expires_at"] != expiry
        ):
            raise RuntimeSDKDenied("finite issuer preimages denied")
        reference = GrantReference(
            grant_id=self.grant_id, grant_digest=request["grant_digest"]
        )
        credential = sign_finite_runtime_sdk_access(
            snapshot,
            self.caller,
            self.source,
            self.policy,
            reference,
            now=datetime.now(UTC),
        )
        return _canonical(
            {
                "request_sha256": hashlib.sha256(raw).hexdigest(),
                "expires_at": expiry,
                "sdk_configuration": {
                    "endpoint": "https://127.0.0.1:8443",
                    "bearer": credential.access_token,
                    "organization_id": str(snapshot.effective_organization_id),
                    "solution_id": str(snapshot.solution_install_id),
                    "test_ca_pem": self.ca_pem,
                },
            }
        )

    async def stop(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._writer is not None:
            self._writer.close()
        if self._task is not None:
            self._task.cancel()
            done, _ = await asyncio.wait({self._task}, timeout=1)
            if not done:
                raise RuntimeError("finite issuer request drain unproven")
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._inode is not None:
            original = self.path.lstat()
            if (
                (original.st_dev, original.st_ino) != self._inode
                or original.st_uid != os.geteuid()
                or not stat.S_ISSOCK(original.st_mode)
                or stat.S_IMODE(original.st_mode) != 0o600
            ):
                raise RuntimeError("finite issuer cleanup custody changed")
            self.path.unlink()
            self._inode = None
