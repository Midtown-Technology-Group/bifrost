"""First-party, hosted-only one-shot COMMIT response-loss fixture.

Transparent bytes go only to the already selected synthetic control-plane pool.
No credentials, SQL text or payloads are logged/retained. This is neither an SDK
service nor a database writer and is never registered in production dispatch.
"""

import asyncio
import json
import ipaddress
import os
import signal
import stat
import struct
import sys
from pathlib import Path
from uuid import UUID

MAX_PACKET = 17 * 1024 * 1024


def publish(path: Path, value: dict) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class CommitAckLossProxy:
    def __init__(self, host: str, port: int, directory: Path, session: str) -> None:
        if str(UUID(session)) != session:
            raise ValueError("noncanonical isolated session")
        self.host, self.port, self.directory, self.session = (
            host,
            port,
            directory,
            session,
        )
        self.dropped = False
        self.failed = False
        self.forwarded_commits = 0
        self.tasks: set[asyncio.Task] = set()
        self.server: asyncio.Server | None = None

    def armed(self) -> bool:
        path = self.directory / "commit-ack-arm.json"
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return False
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ValueError("isolated arm custody")
            raw = stream.read(65537)
        if len(raw) > 65536 or json.loads(raw) != {
            "event": "arm_commit_ack_loss",
            "session_id": self.session,
        }:
            raise ValueError("isolated arm identity")
        return True

    async def start(self) -> int:
        if self.server is not None:
            raise ValueError("isolated proxy cannot restart")
        self.server = await asyncio.start_server(self.connection, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        publish(
            self.directory / "commit-ack-proxy-ready.json",
            {
                "event": "commit_ack_proxy_ready",
                "session_id": self.session,
                "host": "127.0.0.1",
                "port": port,
            },
        )
        return port

    async def connection(
        self, client: asyncio.StreamReader, output: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        upstream_output = None
        copy = None
        try:
            upstream, connected = await asyncio.open_connection(self.host, self.port)
            upstream_output = connected

            async def forward_client() -> None:
                while raw := await client.read(65536):
                    connected.write(raw)
                    await connected.drain()

            copy = asyncio.create_task(forward_client())
            while True:
                # Fault-case client explicitly uses sslmode=disable on this
                # existing isolated plain-TCP pool; production TLS is untouched.
                header = await upstream.readexactly(5)
                length = struct.unpack("!I", header[1:])[0]
                if not 4 <= length <= MAX_PACKET:
                    raise ValueError("isolated packet bound")
                body = await upstream.readexactly(length - 4)
                if header[:1] == b"C" and body == b"COMMIT\0":
                    if not self.dropped and self.armed():
                        self.dropped = True
                        publish(
                            self.directory / "commit-ack-dropped.json",
                            {
                                "event": "commit_ack_dropped",
                                "session_id": self.session,
                                "server_commit_observed": True,
                                "client_commit_ack_forwarded": False,
                                "forwarded_commits": self.forwarded_commits,
                            },
                        )
                        break
                    self.forwarded_commits += 1
                output.write(header + body)
                await output.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except (ValueError, OSError):
            self.failed = True
        finally:
            for writer in (output, upstream_output):
                if writer is not None:
                    writer.close()
            try:
                if copy is not None:
                    copy.cancel()
                    await asyncio.gather(copy, return_exceptions=True)
                for writer in (output, upstream_output):
                    if writer is not None:
                        try:
                            await writer.wait_closed()
                        except ConnectionError:
                            pass
            except asyncio.CancelledError:
                pass
            finally:
                if task is not None:
                    self.tasks.discard(task)

    async def stop(self) -> None:
        if self.server is None:
            raise ValueError("isolated proxy not started")
        self.server.close()
        await self.server.wait_closed()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.tasks or self.failed:
            raise ValueError("isolated proxy still owns connections")


async def main() -> None:
    if os.environ.get("BIFROST_ISOLATED_COMMIT_ACK_TEST") != "1" or len(sys.argv) != 4:
        raise ValueError("isolated fixture guard")
    host = ipaddress.ip_address(sys.argv[1])
    if host.version != 4 or not host.is_private or host.is_loopback:
        raise ValueError("isolated pool address")
    directory = Path(sys.argv[2])
    metadata = directory.lstat()
    if (
        not directory.is_absolute()
        or directory.resolve() != directory
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("isolated fixture directory")
    proxy = CommitAckLossProxy(sys.argv[1], 5432, directory, sys.argv[3])
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for item in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(item, stopped.set)
    await proxy.start()
    await stopped.wait()
    await proxy.stop()
    publish(
        directory / "commit-ack-proxy-stopped.json",
        {
            "event": "commit_ack_proxy_stopped",
            "session_id": proxy.session,
            "connections_remaining": 0,
            "dropped": proxy.dropped,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
