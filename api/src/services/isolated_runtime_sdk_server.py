"""Explicit TLS Unix-socket host for the isolated restricted SDK app only.

The parent owns this lifetime, certificates and database access. The runtime's
read-only SDK mount contains only ingress.sock, never the key or owner gate.
"""

import asyncio
import os
import socket
import stat
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from src.services.execution.worker_sdk_http import _no_signal_capture


class IsolatedSDKServer:
    def __init__(
        self,
        app: FastAPI,
        *,
        directory: Path,
        socket_uid: int,
        certificate: Path,
        private_key: Path,
    ) -> None:
        self.app = app
        self.directory = directory
        self.uid = socket_uid
        self.certificate = certificate
        self.private_key = private_key
        self.path = directory / "ingress.sock"
        self._socket: socket.socket | None = None
        self._inode: tuple[int, int] | None = None
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._used = False

    def _validate(self) -> None:
        directory = self.directory.lstat()
        key = self.private_key.lstat()
        certificate = self.certificate.lstat()
        if (
            type(self.uid) is not int
            or self.uid <= 0
            or os.geteuid() not in (0, self.uid)
            or not self.directory.is_absolute()
            or self.directory.resolve(strict=True) != self.directory
            or len(os.fsencode(self.path)) >= 108
            or not stat.S_ISDIR(directory.st_mode)
            or stat.S_IMODE(directory.st_mode) != 0o700
            or directory.st_uid != self.uid
            or any(self.directory.iterdir())
            or not self.private_key.is_absolute()
            or self.private_key.resolve(strict=True) != self.private_key
            or not stat.S_ISREG(key.st_mode)
            or key.st_uid != os.geteuid()
            or stat.S_IMODE(key.st_mode) != 0o600
            or self.private_key.is_relative_to(self.directory)
            or self.certificate.is_relative_to(self.directory)
            or not self.certificate.is_absolute()
            or self.certificate.resolve(strict=True) != self.certificate
            or not stat.S_ISREG(certificate.st_mode)
            or len(self.app.routes) != 1
            or getattr(self.app.routes[0], "path", None) != "/api/sdk/integrations/get"
            or getattr(self.app.routes[0], "methods", None) != {"POST"}
        ):
            raise RuntimeError("isolated SDK server custody denied")

    async def start(self) -> None:
        if self._used:
            raise RuntimeError("isolated SDK server cannot restart")
        self._used = True
        self._validate()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self.path))
            original = self.path.lstat()
            self._inode = original.st_dev, original.st_ino
            self._socket = sock
            os.chown(self.path, self.uid, -1)
            os.chmod(self.path, 0o600)
            sock.listen(4)
            sock.setblocking(False)
            config = uvicorn.Config(
                self.app,
                lifespan="off",
                log_level="critical",
                log_config=None,
                access_log=False,
                ssl_certfile=str(self.certificate),
                ssl_keyfile=str(self.private_key),
                timeout_graceful_shutdown=1,
            )
            server = uvicorn.Server(config)
            server.capture_signals = _no_signal_capture  # type: ignore[method-assign]
            self._server = server
            self._task = asyncio.create_task(server.serve(sockets=[sock]))
            async with asyncio.timeout(2):
                while not server.started:
                    if self._task.done():
                        await self._task
                        raise RuntimeError("isolated SDK server did not start")
                    await asyncio.sleep(0.01)
        except BaseException:
            sock.close()
            await self.stop()
            raise

    async def stop(self) -> None:
        """Join serving/request tasks, then unlink only the original socket.

        A replaced path or unfinished request is a cleanup failure. The caller
        retains responsibility; this method never finalizes lifecycle/source.
        """
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=3)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                raise RuntimeError("isolated SDK server drain unproven") from None
            if self._server is not None and any(
                not task.done() for task in self._server.server_state.tasks
            ):
                raise RuntimeError("isolated SDK request drain unproven")
            self._task = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._inode is not None:
            original = self.path.lstat()
            if (
                (original.st_dev, original.st_ino) != self._inode
                or original.st_uid != self.uid
                or not stat.S_ISSOCK(original.st_mode)
                or stat.S_IMODE(original.st_mode) != 0o600
            ):
                raise RuntimeError("isolated SDK socket cleanup custody changed")
            self.path.unlink()
            self._inode = None
