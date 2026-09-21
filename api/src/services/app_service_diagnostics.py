"""Bounded Azure Monitor reads for the platform diagnostics App Service panel."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx

from src.config import (
    APP_SERVICE_PLAN_RESOURCE_ID_PATTERN,
    Settings,
    get_settings,
)
from src.core.cache import get_shared_redis
from src.models.contracts.platform import (
    AppServiceMetricPoint,
    AppServiceMetricSeries,
    AppServiceMetricsResponse,
)

logger = logging.getLogger(__name__)

Range = Literal["1h", "6h", "24h", "7d"]
RANGES: dict[Range, tuple[timedelta, str]] = {
    "1h": (timedelta(hours=1), "PT1M"),
    "6h": (timedelta(hours=6), "PT5M"),
    "24h": (timedelta(hours=24), "PT1H"),
    "7d": (timedelta(days=7), "PT30M"),
}
METRIC_UNITS: dict[str, Literal["Percent", "Count"]] = {
    "CpuPercentage": "Percent",
    "MemoryPercentage": "Percent",
    "HttpQueueLength": "Count",
}
ARM_METRICS_API_VERSION = "2023-10-01"
ARM_SCOPE = "https://management.azure.com/.default"
CACHE_FRESH_SECONDS = 60
CACHE_HARD_TTL_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 12


def _validate_resource_id(resource_id: str | None) -> str | None:
    if resource_id and APP_SERVICE_PLAN_RESOURCE_ID_PATTERN.fullmatch(resource_id.strip()):
        return resource_id.strip()
    return None


def _cache_key(resource_id: str, range: Range) -> str:
    return f"bifrost:diagnostics:app-service:{resource_id}:{range}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _series_from_payload(metric_name: str, payload: dict[str, Any]) -> AppServiceMetricSeries:
    metric = next(
        (
            item
            for item in payload.get("value", [])
            if isinstance(item, dict)
            and str((item.get("name") or {}).get("value", "")).lower()
            == metric_name.lower()
        ),
        None,
    )
    points: list[AppServiceMetricPoint] = []
    if metric:
        timeseries = metric.get("timeseries") or []
        first_series = timeseries[0] if timeseries else {}
        for datum in first_series.get("data", []) if isinstance(first_series, dict) else []:
            if not isinstance(datum, dict):
                continue
            timestamp = _parse_timestamp(datum.get("timeStamp"))
            if timestamp is None:
                continue
            value = datum.get("average")
            points.append(
                AppServiceMetricPoint(
                    timestamp=timestamp,
                    value=float(value) if isinstance(value, (int, float)) else None,
                )
            )

    available = any(point.value is not None for point in points)
    return AppServiceMetricSeries(
        name=metric_name,  # type: ignore[arg-type]
        unit=METRIC_UNITS[metric_name],
        available=available,
        unavailable_reason=None if available else "no_data",
        points=points,
    )


def _latest_sample(metrics: list[AppServiceMetricSeries]) -> datetime | None:
    timestamps = [
        point.timestamp
        for metric in metrics
        for point in metric.points
        if point.value is not None
    ]
    return max(timestamps) if timestamps else None


def _apply_freshness(
    response: AppServiceMetricsResponse,
    *,
    now: datetime | None = None,
    stale_reason: str | None = None,
) -> AppServiceMetricsResponse:
    now = now or _utc_now()
    grain_seconds = {
        "PT1M": 60,
        "PT5M": 300,
        "PT1H": 3600,
        "PT30M": 1800,
    }.get(response.sample_grain, CACHE_FRESH_SECONDS)
    delay_threshold = max(CACHE_FRESH_SECONDS, grain_seconds * 2)
    updated_metrics: list[AppServiceMetricSeries] = []
    delayed_metrics = False
    for metric in response.metrics:
        latest = _latest_sample([metric])
        delayed = (
            latest is not None
            and (now - latest).total_seconds() > delay_threshold
        )
        metric_stale = metric.available and (delayed or stale_reason is not None)
        metric_reason = stale_reason or ("data_delayed" if delayed else None)
        delayed_metrics = delayed_metrics or metric_stale
        updated_metrics.append(
            metric.model_copy(
                update={
                    "latest_sample_at": latest,
                    "stale": metric_stale,
                    "stale_reason": metric_reason,
                }
            )
        )
    latest = _latest_sample(updated_metrics)
    return response.model_copy(
        update={
            "latest_sample_at": latest,
            "metrics": updated_metrics,
            "stale": delayed_metrics,
            "stale_reason": stale_reason or ("data_delayed" if delayed_metrics else None),
        }
    )


def _unavailable(
    range: Range,
    *,
    resource_id: str | None,
    reason: str,
    fetched_at: datetime | None = None,
) -> AppServiceMetricsResponse:
    _, grain = RANGES[range]
    metrics = [
        AppServiceMetricSeries(
            name=name,  # type: ignore[arg-type]
            unit=unit,
            available=False,
            unavailable_reason=reason,
        )
        for name, unit in METRIC_UNITS.items()
    ]
    return AppServiceMetricsResponse(
        resource_id=resource_id,
        range=range,
        sample_grain=grain,
        fetched_at=fetched_at or _utc_now(),
        status="unavailable",
        unavailable_reason=reason,
        metrics=metrics,
    )


async def _read_cached(key: str) -> AppServiceMetricsResponse | None:
    try:
        redis = await get_shared_redis()
        raw = await redis.get(key)
        return AppServiceMetricsResponse.model_validate_json(raw) if raw else None
    except Exception:  # cache is an optimization; Azure remains authoritative
        logger.debug("App Service diagnostics cache read failed", exc_info=True)
        return None


async def _write_cached(key: str, response: AppServiceMetricsResponse) -> None:
    try:
        redis = await get_shared_redis()
        await redis.setex(
            key,
            CACHE_HARD_TTL_SECONDS,
            response.model_dump_json(),
        )
    except Exception:
        logger.debug("App Service diagnostics cache write failed", exc_info=True)


async def _query_azure(
    resource_id: str,
    range: Range,
    *,
    now: datetime,
) -> AppServiceMetricsResponse:
    delta, grain = RANGES[range]
    start = now - delta
    params = {
        "api-version": ARM_METRICS_API_VERSION,
        "metricnames": ",".join(METRIC_UNITS),
        "timespan": f"{start.isoformat().replace('+00:00', 'Z')}/{now.isoformat().replace('+00:00', 'Z')}",
        "interval": grain,
        "aggregation": "Average",
    }
    url = (
        f"https://management.azure.com{resource_id}"
        "/providers/microsoft.insights/metrics"
    )
    from azure.identity.aio import ManagedIdentityCredential  # pyright: ignore[reportMissingImports]

    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip() or None
    credential = ManagedIdentityCredential(client_id=client_id)
    try:
        token = await credential.get_token(ARM_SCOPE)
        timeout = httpx.Timeout(8.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token.token}"},
            )
            response.raise_for_status()
            payload = response.json()
    finally:
        await credential.close()

    metrics = [_series_from_payload(name, payload) for name in METRIC_UNITS]
    missing = [metric.name for metric in metrics if not metric.available]
    return AppServiceMetricsResponse(
        resource_id=resource_id,
        range=range,
        sample_grain=grain,
        fetched_at=now,
        latest_sample_at=_latest_sample(metrics),
        status="available" if not missing else "unavailable",
        unavailable_reason=None if not missing else "missing:" + ",".join(missing),
        metrics=metrics,
    )


async def get_app_service_metrics(
    range: Range = "1h",
    *,
    settings: Settings | None = None,
) -> AppServiceMetricsResponse:
    """Read bounded plan metrics using only the server-configured resource ID."""
    resolved_settings = settings if settings is not None else get_settings()
    resource_id = _validate_resource_id(resolved_settings.app_service_plan_resource_id)
    if not resource_id:
        return _unavailable(range, resource_id=None, reason="not_configured")

    now = _utc_now()
    key = _cache_key(resource_id, range)
    cached = await _read_cached(key)
    cache_age = (now - cached.fetched_at).total_seconds() if cached else None
    if cached and cache_age is not None and 0 <= cache_age < CACHE_FRESH_SECONDS:
        return _apply_freshness(cached, now=now)

    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            response = await _query_azure(resource_id, range, now=now)
    except Exception:
        logger.warning("Azure Monitor App Service diagnostics request failed", exc_info=True)
        if (
            cached
            and cached.status == "available"
            and cache_age is not None
            and 0 <= cache_age <= CACHE_HARD_TTL_SECONDS
        ):
            return _apply_freshness(cached, now=now, stale_reason="azure_monitor_error")
        return _unavailable(
            range,
            resource_id=resource_id,
            reason="azure_monitor_unavailable",
        )

    response = _apply_freshness(response, now=now)
    if response.status == "available":
        await _write_cached(key, response)
    return response
