import sys
import builtins
from hashlib import sha256
from types import ModuleType

from src.core import telemetry


class _FallbackFlushRaises:
    def __init__(self):
        self.calls: list[tuple[str, int | None]] = []

    def force_flush(self, timeout_millis=None):
        self.calls.append(("force_flush", timeout_millis))
        if timeout_millis is not None:
            raise TypeError("timeout unsupported")
        raise RuntimeError("fallback failed")


class _FlushSucceeds:
    def __init__(self):
        self.calls: list[int | None] = []

    def force_flush(self, timeout_millis=None):
        self.calls.append(timeout_millis)


def test_flush_opentelemetry_logs_fallback_failure_and_continues(monkeypatch, caplog):
    metric_provider = _FallbackFlushRaises()
    trace_provider = _FlushSucceeds()
    monkeypatch.setattr(telemetry, "_meter_provider", metric_provider)
    monkeypatch.setattr(telemetry, "_trace_provider", trace_provider)

    telemetry.flush_opentelemetry(timeout_millis=123)

    assert metric_provider.calls == [("force_flush", 123), ("force_flush", None)]
    assert trace_provider.calls == [123]
    assert "OpenTelemetry metric flush failed: fallback failed" in caplog.text


def test_configure_opentelemetry_returns_when_no_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(telemetry, "_configured_services", set())

    telemetry.configure_opentelemetry("api")

    assert telemetry._configured_services == set()


def test_container_resource_observations_use_cgroup_and_pseudonymous_instance(
    monkeypatch, tmp_path
):
    from src.services.execution import memory_monitor

    cpu_stat = tmp_path / "cpu.stat"
    cpu_stat.write_text("usage_usec 12500000\nuser_usec 9000000\n")
    monkeypatch.setattr(telemetry, "_CGROUP_CPU_STAT", cpu_stat)
    v1_cpu = tmp_path / "cpuacct.usage"
    v1_memory = tmp_path / "memory.stat"
    monkeypatch.setattr(telemetry, "_CGROUP_V1_CPU_USAGE", v1_cpu)
    monkeypatch.setattr(telemetry, "_CGROUP_V1_MEMORY_STAT", v1_memory)
    monkeypatch.setattr(memory_monitor, "get_cgroup_memory", lambda: (123456, 500000))
    monkeypatch.setenv("WEBSITE_INSTANCE_ID", "private-instance-name")

    class Meter:
        def __init__(self):
            self.callbacks = {}

        def create_observable_counter(self, name, *, callbacks, **_kwargs):
            self.callbacks[name] = callbacks[0]

        def create_observable_gauge(self, name, *, callbacks, **_kwargs):
            self.callbacks[name] = callbacks[0]

    meter = Meter()
    telemetry._register_container_metrics(meter)

    assert meter.callbacks["bifrost.runtime.cpu.time"](None)[0].value == 12.5
    assert meter.callbacks["bifrost.runtime.memory.working_set"](None)[0].value == 123456
    assert telemetry._resource_attributes("bifrost-worker", True) == {
        "service.name": "bifrost-worker",
        "service.instance.id": sha256(b"private-instance-name").hexdigest()[:16],
    }

    cpu_stat.unlink()
    monkeypatch.setattr(memory_monitor, "get_cgroup_memory", lambda: (-1, -1))
    v1_cpu.write_text("2500000000\n")
    v1_memory.write_text("total_rss 100000\ntotal_active_file 24000\n")
    assert telemetry._cgroup_cpu_seconds() == 2.5
    assert telemetry._cgroup_working_set() == 124000
    v1_cpu.unlink()
    v1_memory.unlink()
    unavailable_meter = Meter()
    telemetry._register_container_metrics(unavailable_meter)
    assert unavailable_meter.callbacks["bifrost.runtime.cpu.time"](None) == []
    assert unavailable_meter.callbacks["bifrost.runtime.memory.working_set"](None) == []


def test_configure_opentelemetry_skips_duplicate_service(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setattr(telemetry, "_configured_services", {"api"})

    telemetry.configure_opentelemetry("api")

    assert telemetry._configured_services == {"api"}


def test_configure_opentelemetry_logs_import_failure(monkeypatch, caplog):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setattr(telemetry, "_configured_services", set())

    real_import = builtins.__import__

    def fail_opentelemetry_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("opentelemetry"):
            raise ImportError("not installed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_opentelemetry_import)

    telemetry.configure_opentelemetry("worker")

    assert telemetry._configured_services == set()
    assert "OpenTelemetry export unavailable for worker" in caplog.text


def test_configure_opentelemetry_registers_simple_trace_and_metric_providers(
    monkeypatch,
):
    calls = []

    class Resource:
        @staticmethod
        def create(attributes):
            calls.append(("resource", attributes))
            return {"resource": attributes}

    class TracerProvider:
        def __init__(self, resource):
            self.resource = resource
            self.processors = []

        def add_span_processor(self, processor):
            self.processors.append(processor)

    class MeterProvider:
        def __init__(self, resource, metric_readers, views):
            self.resource = resource
            self.metric_readers = metric_readers
            self.views = views

    class OTLPSpanExporter:
        def __init__(self, endpoint):
            self.endpoint = endpoint

    class OTLPMetricExporter:
        def __init__(self, endpoint):
            self.endpoint = endpoint

    class SimpleSpanProcessor:
        def __init__(self, exporter):
            self.exporter = exporter

    class BatchSpanProcessor:
        def __init__(self, exporter):
            self.exporter = exporter

    class PeriodicExportingMetricReader:
        def __init__(self, exporter, export_interval_millis):
            self.exporter = exporter
            self.export_interval_millis = export_interval_millis

    trace_module = ModuleType("opentelemetry.trace")
    trace_module.set_tracer_provider = lambda provider: calls.append(("trace", provider))
    metrics_module = ModuleType("opentelemetry.metrics")
    metrics_module.set_meter_provider = lambda provider: calls.append(("metrics", provider))

    modules = {
        "opentelemetry": ModuleType("opentelemetry"),
        "opentelemetry.trace": trace_module,
        "opentelemetry.metrics": metrics_module,
        "opentelemetry.exporter": ModuleType("opentelemetry.exporter"),
        "opentelemetry.exporter.otlp": ModuleType("opentelemetry.exporter.otlp"),
        "opentelemetry.exporter.otlp.proto": ModuleType("opentelemetry.exporter.otlp.proto"),
        "opentelemetry.exporter.otlp.proto.grpc": ModuleType("opentelemetry.exporter.otlp.proto.grpc"),
        "opentelemetry.exporter.otlp.proto.grpc.metric_exporter": ModuleType(
            "opentelemetry.exporter.otlp.proto.grpc.metric_exporter"
        ),
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": ModuleType(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
        ),
        "opentelemetry.sdk": ModuleType("opentelemetry.sdk"),
        "opentelemetry.sdk.metrics": ModuleType("opentelemetry.sdk.metrics"),
        "opentelemetry.sdk.metrics.export": ModuleType("opentelemetry.sdk.metrics.export"),
        "opentelemetry.sdk.resources": ModuleType("opentelemetry.sdk.resources"),
        "opentelemetry.sdk.trace": ModuleType("opentelemetry.sdk.trace"),
        "opentelemetry.sdk.trace.export": ModuleType("opentelemetry.sdk.trace.export"),
    }
    modules["opentelemetry"].trace = trace_module
    modules["opentelemetry"].metrics = metrics_module
    modules["opentelemetry.exporter.otlp.proto.grpc.metric_exporter"].OTLPMetricExporter = (
        OTLPMetricExporter
    )
    modules["opentelemetry.exporter.otlp.proto.grpc.trace_exporter"].OTLPSpanExporter = (
        OTLPSpanExporter
    )
    modules["opentelemetry.sdk.metrics"].MeterProvider = MeterProvider
    modules["opentelemetry.sdk.metrics.export"].PeriodicExportingMetricReader = (
        PeriodicExportingMetricReader
    )
    modules["opentelemetry.sdk.resources"].Resource = Resource
    modules["opentelemetry.sdk.trace"].TracerProvider = TracerProvider
    modules["opentelemetry.sdk.trace.export"].BatchSpanProcessor = BatchSpanProcessor
    modules["opentelemetry.sdk.trace.export"].SimpleSpanProcessor = SimpleSpanProcessor
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setattr(telemetry, "_configured_services", set())
    monkeypatch.setattr(telemetry, "_trace_provider", None)
    monkeypatch.setattr(telemetry, "_meter_provider", None)
    lag_view = object()
    monkeypatch.setattr(telemetry, "_event_loop_lag_view", lambda: lag_view)

    telemetry.configure_opentelemetry("worker", span_processor="simple")

    assert telemetry._configured_services == {"worker"}
    assert isinstance(telemetry._trace_provider.processors[0], SimpleSpanProcessor)
    assert telemetry._trace_provider.processors[0].exporter.endpoint == "http://collector:4317"
    assert telemetry._meter_provider.metric_readers[0].export_interval_millis == 15_000
    assert telemetry._meter_provider.views == [lag_view]
    assert ("resource", {"service.name": "worker"}) in calls
    assert calls[-2][0] == "trace"
    assert calls[-1][0] == "metrics"


def test_event_loop_lag_histogram_distinguishes_short_stalls():
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(
        metric_readers=[reader], views=[telemetry._event_loop_lag_view()]
    )
    provider.get_meter("test").create_histogram(
        "bifrost.event_loop.lag", unit="s"
    ).record(0.012)

    metrics_data = reader.get_metrics_data()
    assert metrics_data is not None
    point = (
        metrics_data.resource_metrics[0]
        .scope_metrics[0]
        .metrics[0]
        .data.data_points[0]
    )
    assert tuple(point.explicit_bounds[:4]) == (0.001, 0.005, 0.01, 0.025)
    assert point.bucket_counts[3] == 1
    provider.shutdown()
