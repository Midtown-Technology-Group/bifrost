from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.models.contracts.platform import (
    AppServiceMetricSeries,
    AppServiceMetricsResponse,
)
from src.services import app_service_diagnostics as diagnostics

RESOURCE_ID = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Web/serverfarms/plan"


def _available_response() -> AppServiceMetricsResponse:
    latest = datetime.now(timezone.utc) - timedelta(seconds=10)
    return AppServiceMetricsResponse(
        resource_id=RESOURCE_ID,
        range="1h",
        sample_grain="PT1M",
        fetched_at=latest,
        latest_sample_at=latest,
        status="available",
        metrics=[
            AppServiceMetricSeries(
                name="CpuPercentage",
                unit="Percent",
                points=[{"timestamp": latest, "value": 20}],
            ),
        ],
    )


def test_resource_id_validation_rejects_arbitrary_urls():
    assert diagnostics._validate_resource_id(RESOURCE_ID) == RESOURCE_ID
    assert diagnostics._validate_resource_id("https://example.test/metrics") is None
    assert diagnostics._validate_resource_id(RESOURCE_ID + "/") is None
    assert (
        diagnostics._validate_resource_id(
            RESOURCE_ID.replace("/resourceGroups/rg/", "/resourceGroups/rg?x=/")
        )
        is None
    )
    assert (
        diagnostics._validate_resource_id(
            RESOURCE_ID.replace(
                "00000000-0000-0000-0000-000000000000",
                "00000000-0000-0000-0000-00000000000g",
            )
        )
        is None
    )


def test_settings_accepts_the_unprefixed_api_binding(monkeypatch):
    monkeypatch.setenv("BIFROST_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("APP_SERVICE_PLAN_RESOURCE_ID", RESOURCE_ID)
    from src.config import Settings

    settings = Settings(_env_file=None)
    assert settings.app_service_plan_resource_id == RESOURCE_ID
    monkeypatch.setenv("APP_SERVICE_PLAN_RESOURCE_ID", "https://example.test")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_metric_mapping_preserves_null_gaps_and_missing_metrics():
    payload = {
        "value": [
            {
                "name": {"value": "CpuPercentage"},
                "timeseries": [
                    {
                        "data": [
                            {"timeStamp": "2026-09-21T13:00:00Z", "average": 42},
                            {"timeStamp": "2026-09-21T13:01:00Z", "average": None},
                        ]
                    }
                ],
            }
        ]
    }
    cpu = diagnostics._series_from_payload("CpuPercentage", payload)
    memory = diagnostics._series_from_payload("MemoryPercentage", payload)
    assert cpu.available is True
    assert [point.value for point in cpu.points] == [42.0, None]
    assert memory.available is False
    assert memory.unavailable_reason == "no_data"
    assert [point.value for point in cpu.points] == [42.0, None]


def test_availability_status_keeps_partial_metrics_visible():
    metrics = [
        AppServiceMetricSeries(name="CpuPercentage", unit="Percent"),
        AppServiceMetricSeries(
            name="MemoryPercentage",
            unit="Percent",
            available=False,
            unavailable_reason="no_data",
        ),
        AppServiceMetricSeries(
            name="HttpQueueLength",
            unit="Count",
            available=False,
            unavailable_reason="no_data",
        ),
    ]
    assert diagnostics._availability_status(metrics) == (
        "available",
        "missing:MemoryPercentage,HttpQueueLength",
    )
    assert diagnostics._availability_status(
        [metric.model_copy(update={"available": False}) for metric in metrics]
    ) == ("unavailable", "no_data")


def test_metric_mapping_turns_nonfinite_values_into_null_gaps():
    payload = {
        "value": [
            {
                "name": {"value": "CpuPercentage"},
                "timeseries": [
                    {
                        "data": [
                            {
                                "timeStamp": "2026-09-21T13:00:00Z",
                                "average": float("nan"),
                            },
                            {
                                "timeStamp": "2026-09-21T13:01:00Z",
                                "average": float("inf"),
                            },
                        ],
                    }
                ],
            },
        ],
    }
    series = diagnostics._series_from_payload("CpuPercentage", payload)
    assert series.available is False
    assert [point.value for point in series.points] == [None, None]


def test_freshness_is_per_metric_and_ignores_trailing_nulls():
    checked_at = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
    response = AppServiceMetricsResponse(
        resource_id=RESOURCE_ID,
        range="1h",
        sample_grain="PT1M",
        fetched_at=checked_at,
        status="unavailable",
        metrics=[
            AppServiceMetricSeries(
                name="CpuPercentage",
                unit="Percent",
                points=[
                    {"timestamp": checked_at - timedelta(seconds=30), "value": 44},
                    {"timestamp": checked_at, "value": None},
                ],
            ),
            AppServiceMetricSeries(
                name="MemoryPercentage",
                unit="Percent",
                points=[
                    {"timestamp": checked_at - timedelta(minutes=5), "value": 71},
                    {"timestamp": checked_at, "value": None},
                ],
            ),
            AppServiceMetricSeries(
                name="HttpQueueLength",
                unit="Count",
                available=False,
                unavailable_reason="no_data",
                points=[],
            ),
        ],
    )
    checked = diagnostics._apply_freshness(response, now=checked_at)
    by_name = {metric.name: metric for metric in checked.metrics}
    assert checked.status == "unavailable"
    assert checked.stale is True
    assert by_name["CpuPercentage"].stale is False
    assert by_name["CpuPercentage"].latest_sample_at == checked_at - timedelta(
        seconds=30
    )
    assert by_name["MemoryPercentage"].stale is True
    assert by_name["MemoryPercentage"].stale_reason == "data_delayed"
    assert by_name["HttpQueueLength"].stale is False
    assert checked.latest_sample_at == checked_at - timedelta(seconds=30)


def test_staleness_uses_latest_sample_age():
    response = _available_response()
    checked_at = response.latest_sample_at + timedelta(minutes=3)  # type: ignore[operator]
    stale = diagnostics._apply_freshness(response, now=checked_at)
    assert stale.stale is True
    assert stale.stale_reason == "data_delayed"


@pytest.mark.asyncio
async def test_unconfigured_returns_explicit_unavailable(monkeypatch):
    response = await diagnostics.get_app_service_metrics(
        "1h", settings=SimpleNamespace(app_service_plan_resource_id=None)
    )
    assert response.status == "unavailable"
    assert response.unavailable_reason == "not_configured"
    assert all(not metric.available for metric in response.metrics)


@pytest.mark.asyncio
async def test_fetch_error_returns_stale_cached_success(monkeypatch):
    cached = _available_response().model_copy(
        update={"fetched_at": datetime.now(timezone.utc) - timedelta(minutes=2)}
    )
    monkeypatch.setattr(diagnostics, "_read_cached", lambda _key: _async_return(cached))
    monkeypatch.setattr(
        diagnostics,
        "_query_azure",
        lambda *args, **kwargs: _async_raise(RuntimeError("down")),
    )
    response = await diagnostics.get_app_service_metrics(
        "1h", settings=SimpleNamespace(app_service_plan_resource_id=RESOURCE_ID)
    )
    assert response.status == "available"
    assert response.stale is True
    assert response.stale_reason == "azure_monitor_error"
    assert response.fetched_at == cached.fetched_at


@pytest.mark.asyncio
async def test_fresh_cache_avoids_azure_fetch(monkeypatch):
    cached = _available_response()
    query = AsyncMock(side_effect=AssertionError("fresh cache must avoid Azure"))
    monkeypatch.setattr(diagnostics, "_read_cached", lambda _key: _async_return(cached))
    monkeypatch.setattr(diagnostics, "_query_azure", query)
    response = await diagnostics.get_app_service_metrics(
        "1h", settings=SimpleNamespace(app_service_plan_resource_id=RESOURCE_ID)
    )
    assert response.fetched_at == cached.fetched_at
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_bounded_timeout_without_cache_is_unavailable(monkeypatch):
    monkeypatch.setattr(diagnostics, "_read_cached", lambda _key: _async_return(None))
    monkeypatch.setattr(
        diagnostics,
        "_query_azure",
        lambda *args, **kwargs: _async_raise(TimeoutError("bounded")),
    )
    response = await diagnostics.get_app_service_metrics(
        "1h", settings=SimpleNamespace(app_service_plan_resource_id=RESOURCE_ID)
    )
    assert response.status == "unavailable"
    assert response.unavailable_reason == "azure_monitor_unavailable"


@pytest.mark.asyncio
async def test_fetch_error_is_cached_briefly_without_masking_healthy_data(monkeypatch):
    monkeypatch.setattr(diagnostics, "_read_cached", lambda _key: _async_return(None))
    monkeypatch.setattr(
        diagnostics,
        "_query_azure",
        lambda *args, **kwargs: _async_raise(TimeoutError("bounded")),
    )
    write_cached = AsyncMock()
    monkeypatch.setattr(diagnostics, "_write_cached", write_cached)
    response = await diagnostics.get_app_service_metrics(
        "1h", settings=SimpleNamespace(app_service_plan_resource_id=RESOURCE_ID)
    )
    assert response.status == "unavailable"
    write_cached.assert_awaited_once()
    assert (
        write_cached.await_args.kwargs["ttl_seconds"]
        == diagnostics.CACHE_UNAVAILABLE_TTL_SECONDS
    )


@pytest.mark.asyncio
async def test_fresh_unavailable_cache_avoids_immediate_arm_retry(monkeypatch):
    cached = diagnostics._unavailable(
        "1h", resource_id=RESOURCE_ID, reason="azure_monitor_unavailable"
    )
    monkeypatch.setattr(diagnostics, "_read_cached", lambda _key: _async_return(cached))
    query = AsyncMock(
        side_effect=AssertionError("short unavailable cache must avoid Azure")
    )
    monkeypatch.setattr(diagnostics, "_query_azure", query)
    response = await diagnostics.get_app_service_metrics(
        "1h", settings=SimpleNamespace(app_service_plan_resource_id=RESOURCE_ID)
    )
    assert response.status == "unavailable"
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_response_is_cached_as_available(monkeypatch):
    partial = AppServiceMetricsResponse(
        resource_id=RESOURCE_ID,
        range="1h",
        sample_grain="PT1M",
        fetched_at=datetime.now(timezone.utc),
        status="available",
        unavailable_reason="missing:HttpQueueLength",
        metrics=[
            AppServiceMetricSeries(name="CpuPercentage", unit="Percent", points=[]),
            AppServiceMetricSeries(
                name="MemoryPercentage", unit="Percent", available=False, unavailable_reason="no_data"
            ),
            AppServiceMetricSeries(
                name="HttpQueueLength", unit="Count", available=False, unavailable_reason="no_data"
            ),
        ],
    )
    monkeypatch.setattr(diagnostics, "_read_cached", lambda _key: _async_return(None))
    monkeypatch.setattr(diagnostics, "_query_azure", lambda *args, **kwargs: _async_return(partial))
    write_cached = AsyncMock()
    monkeypatch.setattr(diagnostics, "_write_cached", write_cached)
    response = await diagnostics.get_app_service_metrics(
        "1h", settings=SimpleNamespace(app_service_plan_resource_id=RESOURCE_ID)
    )
    assert response.status == "available"
    write_cached.assert_awaited_once_with(
        diagnostics._cache_key(RESOURCE_ID, "1h"), response
    )


@pytest.mark.asyncio
async def test_arm_request_uses_managed_identity_and_never_returns_token(monkeypatch):
    azure_identity = pytest.importorskip("azure.identity.aio")
    captured: dict[str, object] = {}
    monkeypatch.setenv("AZURE_CLIENT_ID", "uami-client-id")

    class FakeCredential:
        def __init__(self, *, client_id):
            captured["client_id"] = client_id

        async def get_token(self, scope):
            captured["scope"] = scope
            return SimpleNamespace(token="test-token")

        async def close(self):
            captured["closed"] = True

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "value": [
                    {
                        "name": {"value": metric},
                        "timeseries": [
                            {
                                "data": [
                                    {
                                        "timeStamp": "2026-09-21T13:59:00Z",
                                        "average": value,
                                    }
                                ],
                            }
                        ],
                    }
                    for metric, value in (
                        ("CpuPercentage", 42),
                        ("MemoryPercentage", 55),
                        ("HttpQueueLength", 0.25),
                    )
                ]
            }

    class FakeClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, *, params, headers):
            captured.update(url=url, params=params, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(azure_identity, "ManagedIdentityCredential", FakeCredential)
    monkeypatch.setattr(diagnostics.httpx, "AsyncClient", FakeClient)
    response = await diagnostics._query_azure(
        RESOURCE_ID, "1h", now=datetime(2026, 9, 21, 14, tzinfo=timezone.utc)
    )
    assert captured["client_id"] == "uami-client-id"
    assert captured["scope"] == diagnostics.ARM_SCOPE
    assert str(captured["url"]).startswith(
        "https://management.azure.com/subscriptions/"
    )
    assert captured["params"]["api-version"] == "2023-10-01"  # type: ignore[index]
    assert captured["params"]["aggregation"] == "Average"  # type: ignore[index]
    assert captured["headers"] == {"Authorization": "Bearer test-token"}
    assert captured["closed"] is True
    assert response.metrics[-1].points[0].value == 0.25
    assert "test-token" not in response.model_dump_json()


async def _async_return(value):
    return value


async def _async_raise(error):
    raise error
