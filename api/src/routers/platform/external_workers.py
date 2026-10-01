"""Managed-identity-only bootstrap for the fixed external worker app."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.config import get_settings
from src.core.auth import CurrentSuperuser
from src.core.db_deps import DbSession
from src.models.orm.config import SystemConfig
from src.services.external_worker_scaling import (
    Azure, CATEGORY, enroll_replica, validate_settings, verify_identity,
)

router = APIRouter(prefix="/api/platform/external-workers", tags=["External Workers"])


class EnrollmentRequest(BaseModel):
    replica: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9-]+$")
    boot: UUID


@router.post("/enroll")
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


@router.get("")
async def status(_admin: CurrentSuperuser, db: DbSession) -> dict:
    rows = list((await db.scalars(select(SystemConfig).where(
        SystemConfig.category == CATEGORY, SystemConfig.organization_id.is_(None)))).all())
    demand = [entry.value_json for entry in rows if entry.key == "demand"]
    if len(demand) > 1:
        raise HTTPException(503, "External worker state is ambiguous")
    from datetime import UTC, datetime
    value = demand[0] or {} if demand else {}
    observed = value.get("observed_at")
    age = datetime.now(UTC).timestamp() - observed if observed else None
    return {"enabled": get_settings().external_worker_scaling_enabled,
            "healthy": age is not None and 0 <= age <= 30,
            "observation_age_seconds": age, "demand": value,
            "tracked_hosts": sum(entry.key.startswith("host-") for entry in rows)}
