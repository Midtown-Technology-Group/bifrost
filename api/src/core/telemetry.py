"""OpenTelemetry configuration helpers."""

import logging
import os
from hashlib import sha256
from pathlib import Path

logger = logging.getLogger(__name__)

_configured_services: set[str] = set()
_trace_provider = None
_meter_provider = None
_CGROUP_CPU_STAT = Path("/sys/fs/cgroup/cpu.stat")
_CGROUP_V1_CPU_USAGE = Path("/sys/fs/cgroup/cpuacct/cpuacct.usage")
_CGROUP_V1_MEMORY_STAT = Path("/sys/fs/cgroup/memory/memory.stat")


def _stat_value(path: Path, key: str) -> int | None:
    try:
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == key:
                return int(parts[1])
    except (OSError, ValueError):
        return None
    return None


def _cgroup_cpu_seconds() -> float | None:
    usage_usec = _stat_value(_CGROUP_CPU_STAT, "usage_usec")
    if usage_usec is not None:
        return usage_usec / 1_000_000
    try:
        return int(_CGROUP_V1_CPU_USAGE.read_text().strip()) / 1_000_000_000
    except (OSError, ValueError):
        return None


def _cgroup_working_set() -> int | None:
    from src.services.execution.memory_monitor import get_cgroup_memory

    working_set, _limit = get_cgroup_memory()
    if working_set >= 0:
        return working_set
    rss = _stat_value(_CGROUP_V1_MEMORY_STAT, "total_rss")
    active_file = _stat_value(_CGROUP_V1_MEMORY_STAT, "total_active_file")
    return rss + active_file if rss is not None and active_file is not None else None


def _register_container_metrics(meter) -> None:
    from opentelemetry.metrics import Observation

    def cpu_observations(_options):
        seconds = _cgroup_cpu_seconds()
        return [Observation(seconds)] if seconds is not None else []

    def memory_observations(_options):
        working_set = _cgroup_working_set()
        return [Observation(working_set)] if working_set is not None else []

    meter.create_observable_counter(
        "bifrost.runtime.cpu.time",
        callbacks=[cpu_observations],
        unit="s",
        description="Cumulative CPU time for this container, including child processes.",
    )
    meter.create_observable_gauge(
        "bifrost.runtime.memory.working_set",
        callbacks=[memory_observations],
        unit="By",
        description="Cgroup resident memory plus active file cache for this container.",
    )


def _resource_attributes(service_name: str, container_resources: bool) -> dict[str, str]:
    attributes = {"service.name": service_name}
    if container_resources:
        instance = os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("HOSTNAME")
        if instance:
            attributes["service.instance.id"] = sha256(instance.encode()).hexdigest()[:16]
    return attributes


def _event_loop_lag_view():
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View

    return View(
        instrument_name="bifrost.event_loop.lag",
        aggregation=ExplicitBucketHistogramAggregation(
            boundaries=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0)
        ),
    )


def configure_opentelemetry(
    service_name: str, *, span_processor: str = "batch", container_resources: bool = False
) -> None:
    """Configure OTLP trace and metric export when an endpoint is provided."""
    global _meter_provider, _trace_provider

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    if service_name in _configured_services:
        return

    try:
        from opentelemetry import metrics
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError as exc:
        logger.warning("OpenTelemetry export unavailable for %s: %s", service_name, exc)
        return

    resource = Resource.create(_resource_attributes(service_name, container_resources))
    trace_provider = TracerProvider(resource=resource)
    trace_exporter = OTLPSpanExporter(endpoint=endpoint)

    if span_processor == "simple":
        trace_provider.add_span_processor(SimpleSpanProcessor(trace_exporter))
    else:
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))

    trace.set_tracer_provider(trace_provider)
    _trace_provider = trace_provider

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint),
        export_interval_millis=15_000,
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
        views=[_event_loop_lag_view()],
    )
    metrics.set_meter_provider(meter_provider)
    _meter_provider = meter_provider
    if container_resources:
        _register_container_metrics(meter_provider.get_meter("bifrost.runtime"))

    _configured_services.add(service_name)
    logger.info("OpenTelemetry trace and metric export configured for %s", service_name)


def flush_opentelemetry(*, timeout_millis: int = 5_000) -> None:
    """Force any configured OpenTelemetry providers to export buffered data."""
    for provider_name, provider in (
        ("metric", _meter_provider),
        ("trace", _trace_provider),
    ):
        if provider is None:
            continue

        force_flush = getattr(provider, "force_flush", None)
        if force_flush is None:
            continue

        try:
            try:
                force_flush(timeout_millis=timeout_millis)
            except TypeError:
                force_flush()
        except Exception as exc:
            logger.warning("OpenTelemetry %s flush failed: %s", provider_name, exc)
