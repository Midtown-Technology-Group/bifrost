"""Managed-identity-only bootstrap for the fixed external worker app."""
import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field
from shared.models import ExternalWorkerIdentityRequest

from src.services.operation_catalog import operation_route
from src.config import get_settings
from src.core.auth import CurrentSuperuser
from src.core.db_deps import DbSession
from src.services.external_worker_scaling import (
    Azure, controller_status, enroll_replica, validate_settings, verify_identity,
)

router = APIRouter(prefix="/api/platform/external-workers", tags=["External Workers"])


class EnrollmentRequest(BaseModel):
    replica: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9-]+$")
    boot: UUID


@router.post("/enroll", **operation_route("externalworkers.enroll"))
async def enroll(request: EnrollmentRequest, response: Response, db: DbSession,
                 authorization: Annotated[str | None, Header()] = None) -> dict[str, str]:
    settings = get_settings()
    response.headers["Cache-Control"] = "no-store"
    if not settings.external_worker_scaling_enabled:
        raise HTTPException(404, "External worker enrollment is disabled")
    try:
        validate_settings(settings)
        if not authorization or not authorization.startswith("Bearer "):
            raise ValueError("Missing identity")
        await verify_identity(authorization[7:], settings)
    except Exception:
        raise HTTPException(403, "External worker identity rejected") from None
    azure = Azure(settings)
    try:
        return await enroll_replica(db, azure, request.replica, request.boot)
    except ValueError:
        raise HTTPException(403, "External worker replica rejected") from None
    except Exception:
        raise HTTPException(503, "External worker enrollment is unavailable") from None
    finally:
        await azure.close()


@router.get("", **operation_route("externalworkers.status"))
async def status(_admin: CurrentSuperuser, db: DbSession) -> dict:
    try:
        return await controller_status(db)
    except RuntimeError:
        raise HTTPException(503, "External worker state is unavailable") from None


@router.post("/identity", **operation_route("externalworkers.identity"))
async def identity_operation(request: ExternalWorkerIdentityRequest, response: Response, db: DbSession,
                             authorization: Annotated[str | None, Header()] = None) -> dict:
    """Global controller bookkeeping, authorized only by the fixed worker UAMI."""
    try:
        # Leave headroom inside the local client's ten-second request ceiling.
        # Cancellation never rolls back an already committed attempt marker.
        async with asyncio.timeout(8):
            return await _identity_operation(request, response, db, authorization)
    except TimeoutError:
        raise HTTPException(503, "External worker identity is unavailable") from None


async def _identity_operation(request: ExternalWorkerIdentityRequest, response: Response, db: DbSession,
                              authorization: str | None) -> dict:
    from src.services.external_worker_ownership import acquire, grant, mutate

    settings = get_settings()
    response.headers["Cache-Control"] = "no-store"
    if not settings.external_worker_scaling_enabled or not settings.external_worker_identity_enabled:
        raise HTTPException(404, "External worker identity is disabled")
    try:
        validate_settings(settings)
        if not authorization or not authorization.startswith("Bearer "):
            raise ValueError("Missing identity")
        await verify_identity(authorization[7:], settings)
    except Exception:
        raise HTTPException(403, "External worker identity rejected") from None
    azure = Azure(settings)
    try:
        allowed = {
            "acquire": {"attempt"}, "begin": {"token"}, "grant": {"token"},
            "bind": {"token", "host_id", "network_id"},
            "checkpoint": {"token", "state"}, "renew": {"token"}, "release": {"token"},
        }[request.operation] | {"operation", "replica"}
        if request.model_fields_set - allowed:
            raise ValueError("Unexpected identity operation fields")
        if request.operation == "acquire":
            return await acquire(db, azure, request.replica, request.attempt or "")
        token = request.token.get_secret_value() if request.token else ""
        if request.operation == "grant":
            result = await grant(db, azure, request.replica, token)
            return {"ok": True, "token": token, "code": result["code"],
                    "hostID": result["hostId"], "networkID": settings.external_worker_defined_network_id}
        return await mutate(db, azure, request.replica, token, request.operation,
                            host_id=request.host_id, network_id=request.network_id,
                            checkpoint=request.state or b"")
    except ValueError:
        raise HTTPException(403, "External worker ownership rejected") from None
    except Exception:
        raise HTTPException(503, "External worker identity is unavailable") from None
    finally:
        await azure.close()
