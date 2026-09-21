"""Platform-admin App Service capacity diagnostics."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from src.core.auth import CurrentSuperuser, get_current_superuser
from src.models.contracts.platform import AppServiceMetricsResponse
from src.services.app_service_diagnostics import get_app_service_metrics

router = APIRouter(
    prefix="/api/platform/app-service",
    tags=["Platform Admin - App Service"],
    dependencies=[Depends(get_current_superuser)],
)


@router.get(
    "/metrics",
    response_model=AppServiceMetricsResponse,
    summary="Get Azure App Service plan metrics",
)
async def app_service_metrics(
    _admin: CurrentSuperuser,
    range: Annotated[
        Literal["1h", "6h", "24h", "7d"],
        Query(description="Time range: 1h, 6h, 24h, 7d"),
    ] = "1h",
) -> AppServiceMetricsResponse:
    return await get_app_service_metrics(range)
