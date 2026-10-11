"""Private single-capability ingress app; never mounted in the platform app.

The isolated trusted supervisor serves this app over its TLS Unix socket. Keys,
DB sessions and original-owner custody stay in the parent. No ordinary engine
auth, refresh, application routes or lifecycle commands are installed.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import ClientDisconnect

from shared.isolated_runtime_sdk import fetch_synthetic_integration
from src.core.runtime_sdk_credentials import (
    AcceptedManifestIdentity,
    AuthorizedCallerSnapshot,
    GrantSnapshot,
    RuntimeSDKDenied,
    SelectedSDKPolicy,
    decode_runtime_sdk_access,
)
from src.models.contracts.cli import (
    SDKIntegrationsGetRequest,
    SDKIntegrationsGetResponse,
)
from src.services.isolated_runtime_sdk_bridge import admit_live_integration_get
from src.services.isolated_runtime_sdk_tokens import verify_finite_integration_get


def build_isolated_sdk_app(
    *,
    grant_id: UUID,
    load_snapshot: Callable[[], Awaitable[GrantSnapshot]],
    caller: AuthorizedCallerSnapshot,
    source: AcceptedManifestIdentity,
    policy: SelectedSDKPolicy,
    gate_path: Path,
    owner_pid: int,
    owner_uid: int,
    owner_start_ticks: str,
    session_factory: async_sessionmaker[AsyncSession],
    fixture_integration_id: UUID,
) -> FastAPI:
    """Reserve one grant and original actor before the dormant runtime starts.

    The parent loader reads this fixed grant from committed owner/provision records and
    actual original process custody. Supplying consistent fixture models alone
    does not establish issuance or runtime acceptance. All requests still need
    fresh Rust custody/SQL admission, and no denied or uncertain admission fetches.
    """
    app = FastAPI(
        openapi_url=None, docs_url=None, redoc_url=None, redirect_slashes=False
    )
    # Copy validated preimages so caller mutation cannot rebind this ingress.
    caller = AuthorizedCallerSnapshot.model_validate(caller.model_dump())
    source = AcceptedManifestIdentity.model_validate(source.model_dump())
    policy = SelectedSDKPolicy.model_validate(policy.model_dump())
    busy = False
    requests = 0
    snapshot: GrantSnapshot | None = None
    snapshot_attempted = False

    @app.post("/api/sdk/integrations/get", response_model=SDKIntegrationsGetResponse)
    async def integration_get(request: Request) -> SDKIntegrationsGetResponse:
        nonlocal busy, requests, snapshot, snapshot_attempted
        # Bound concurrent work and total connections to the guardian gate's
        # fixed session budget. Failed requests consume budget; never replay.
        if busy or requests >= 16:
            raise HTTPException(403, "runtime SDK request denied")
        busy = True
        requests += 1
        try:
            async with asyncio.timeout(8):
                if request.url.query:
                    raise RuntimeSDKDenied("runtime SDK request denied")
                authorization = request.headers.getlist("authorization")
                if (
                    len(authorization) != 1
                    or not authorization[0].startswith("Bearer ")
                    or not 0 < len(authorization[0][7:]) <= 4096
                ):
                    raise RuntimeSDKDenied("runtime SDK request denied")
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 8192:
                        raise RuntimeSDKDenied("runtime SDK request denied")
                    body.extend(chunk)
                # Authenticate purpose/signature and this parent-reserved grant
                # before consulting storage. HTTP never selects a grant/session.
                claims = decode_runtime_sdk_access(authorization[0][7:])
                if claims.sub != str(grant_id):
                    raise RuntimeSDKDenied("runtime SDK request denied")
                if snapshot is None:
                    if snapshot_attempted:
                        raise RuntimeSDKDenied("runtime SDK request denied")
                    snapshot_attempted = True
                    loaded = await load_snapshot()
                    frozen = GrantSnapshot.model_validate(loaded.model_dump())
                    if frozen.id != grant_id:
                        raise RuntimeSDKDenied("runtime SDK request denied")
                    snapshot = frozen
                intent = verify_finite_integration_get(
                    authorization[0][7:],
                    bytes(body),
                    snapshot,
                    caller,
                    source,
                    policy,
                    now=datetime.now(UTC),
                )
                # Use the existing public DTO only after exact raw-body policy
                # verification; its permissive normalization is not admission.
                SDKIntegrationsGetRequest.model_validate_json(body)
                await admit_live_integration_get(
                    intent,
                    gate_path=gate_path,
                    owner_pid=owner_pid,
                    owner_uid=owner_uid,
                    owner_start_ticks=owner_start_ticks,
                )
                async with session_factory() as session:
                    return await fetch_synthetic_integration(
                        session,
                        intent,
                        fixture_integration_id=fixture_integration_id,
                        external=caller.caller_external,
                    )
        except (
            RuntimeSDKDenied,
            ValueError,
            SQLAlchemyError,
            TimeoutError,
            OSError,
            ClientDisconnect,
        ):
            raise HTTPException(403, "runtime SDK request denied") from None
        finally:
            busy = False

    return app
