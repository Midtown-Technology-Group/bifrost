"""First-party disposable issuer fixture; no core writer credential enters it."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import UUID

# This trusted fixture runs only in the disposable API test container. Drop
# privilege before application imports or loading the isolated signing key.
if os.geteuid() == 0:
    os.setgroups([])
    os.setgid(1001)
    os.setuid(1001)

from sqlalchemy import URL  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from src.core.runtime_sdk_credentials import (  # noqa: E402
    AcceptedManifestIdentity,
    AuthorizedCallerSnapshot,
    SelectedSDKPolicy,
)
from src.services.isolated_runtime_sdk_bridge import _start_ticks  # noqa: E402
from src.services.isolated_runtime_sdk_issuer import FiniteIssuerServer  # noqa: E402
from src.services.isolated_runtime_sdk_snapshot import load_committed_finite_snapshot  # noqa: E402


class LostReplyIssuer(FiniteIssuerServer):
    signed_before_loss = False

    async def _signed_response(self, raw: bytes) -> bytes:
        await super()._signed_response(raw)
        self.signed_before_loss = True
        raise OSError("synthetic lost reply after finite signing")


async def run():
    raw = sys.stdin.buffer.readline(16385)
    if not raw.endswith(b"\n") or len(raw) > 16384:
        raise ValueError("invalid trusted issuer fixture input")
    data = json.loads(raw)
    # Fixed readonly fixture principal. The core password is deliberately absent.
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            username="wex_incumbent",
            password="synthetic_primer_incumbent",
            host="writer-guard-pool",
            database="bifrost_test",
        ),
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0, "command_timeout": 5},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = {key: UUID(value) for key, value in data["identity"].items()}

    async def committed():
        return await load_committed_finite_snapshot(factory, **ids)

    with tempfile.TemporaryDirectory(prefix="bifrost-issuer-", dir="/tmp") as temporary:
        root = Path(temporary)
        issuer_type = (
            LostReplyIssuer if data["mode"] == "lost_reply" else FiniteIssuerServer
        )
        server = issuer_type(
            directory=root,
            owner_pid=data["owner_pid"],
            owner_uid=data["owner_uid"],
            owner_start_ticks=data["owner_ticks"],
            grant_id=ids["grant_id"],
            load_snapshot=committed,
            caller=AuthorizedCallerSnapshot.model_validate(data["caller"]),
            source=AcceptedManifestIdentity.model_validate(data["source"]),
            policy=SelectedSDKPolicy.model_validate(data["policy"]),
            ca_pem=data["ca"],
        )
        try:
            await server.start()
            print(
                json.dumps(
                    {
                        "path": str(server.path),
                        "pid": os.getpid(),
                        "uid": os.geteuid(),
                        "ticks": _start_ticks(os.getpid()),
                        "ca": data["ca"],
                    }
                ),
                flush=True,
            )
            # Parent completion signal is separate from the original owner IPC.
            command = await asyncio.to_thread(sys.stdin.buffer.readline, 16)
            if command != b"finish\n":
                raise ValueError("invalid fixture completion")
        finally:
            await server.stop()
            await engine.dispose()
        assert list(root.iterdir()) == []
    if isinstance(server, LostReplyIssuer):
        assert server.signed_before_loss
        print("issuer_signed_reply_lost", flush=True)
    print("issuer_drained", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
