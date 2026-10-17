use crate::config::TelemetryConfig;
use opentelemetry::{KeyValue, trace::TracerProvider};
use opentelemetry_otlp::{WithExportConfig, WithTonicConfig};
use opentelemetry_sdk::{
    Resource,
    metrics::SdkMeterProvider,
    trace::{BatchConfigBuilder, BatchSpanProcessor, SdkTracerProvider},
};
use std::time::Duration;
use tracing_subscriber::{Layer, layer::SubscriberExt, util::SubscriberInitExt};

pub struct Telemetry {
    traces: Option<SdkTracerProvider>,
    metrics: Option<SdkMeterProvider>,
}

#[derive(Debug)]
pub struct TelemetryError;

impl Telemetry {
    pub fn init(config: &TelemetryConfig) -> Result<Self, TelemetryError> {
        let resource = Resource::builder_empty()
            .with_attributes([
                KeyValue::new("service.name", "bifrost-core"),
                KeyValue::new("service.version", env!("CARGO_PKG_VERSION")),
                KeyValue::new(
                    "bifrost.build.sha",
                    option_env!("BIFROST_BUILD_SHA").unwrap_or("development"),
                ),
            ])
            .build();
        let (traces, metrics) = if let Some(endpoint) = &config.endpoint {
            let exporter = opentelemetry_otlp::SpanExporter::builder()
                .with_tonic()
                .with_tls_config(tonic::transport::ClientTlsConfig::new().with_native_roots())
                .with_endpoint(endpoint)
                .with_timeout(Duration::from_secs(2))
                .build()
                .map_err(|_| TelemetryError)?;
            let processor = BatchSpanProcessor::builder(exporter)
                .with_batch_config(
                    BatchConfigBuilder::default()
                        .with_max_queue_size(512)
                        .with_max_export_batch_size(128)
                        .build(),
                )
                .build();
            let traces = SdkTracerProvider::builder()
                .with_span_processor(processor)
                .with_resource(resource.clone())
                .build();
            let exporter = opentelemetry_otlp::MetricExporter::builder()
                .with_tonic()
                .with_tls_config(tonic::transport::ClientTlsConfig::new().with_native_roots())
                .with_endpoint(endpoint)
                .with_timeout(Duration::from_secs(2))
                .build()
                .map_err(|_| TelemetryError)?;
            let metrics = SdkMeterProvider::builder()
                .with_periodic_exporter(exporter)
                .with_resource(resource)
                .build();
            opentelemetry::global::set_meter_provider(metrics.clone());
            (Some(traces), Some(metrics))
        } else {
            (None, None)
        };
        // Only authored, sanitized events are emitted. Driver/exporter internals can
        // format credentials or SQL. Do not enable those with RUST_LOG.
        let filter = || {
            tracing_subscriber::filter::filter_fn(|metadata| {
                matches!(
                    metadata.target(),
                    "bifrost_core" | "bifrost_core::telemetry" | "bifrost_db"
                )
            })
        };
        let fmt = tracing_subscriber::fmt::layer()
            .json()
            .with_current_span(true)
            .with_span_list(true)
            .with_filter(filter());
        let otel = traces.as_ref().map(|provider| {
            tracing_opentelemetry::layer()
                .with_tracer(provider.tracer("bifrost-core"))
                .with_filter(filter())
        });
        tracing_subscriber::registry()
            .with(fmt)
            .with(otel)
            .try_init()
            .map_err(|_| TelemetryError)?;
        Ok(Self { traces, metrics })
    }

    pub async fn shutdown(self) -> Result<(), TelemetryError> {
        // Exporter I/O is bounded and never controls domain correctness.
        // SDK0.33 metrics shutdown has a fixed5s deadline (its supplied
        // timeout argument is ignored); trace shutdown is capped at2s.
        tokio::time::timeout(
            Duration::from_secs(8),
            tokio::task::spawn_blocking(move || {
                let mut flushed = true;
                if let Some(provider) = self.traces {
                    let success = provider
                        .shutdown_with_timeout(Duration::from_secs(2))
                        .is_ok();
                    tracing::info!(signal = "traces", success, "OTLP exporter shutdown");
                    flushed &= success;
                }
                if let Some(provider) = self.metrics {
                    let success = provider
                        .shutdown_with_timeout(Duration::from_secs(2))
                        .is_ok();
                    tracing::info!(signal = "metrics", success, "OTLP exporter shutdown");
                    flushed &= success;
                }
                if flushed { Ok(()) } else { Err(TelemetryError) }
            }),
        )
        .await
        .map_err(|_| TelemetryError)?
        .map_err(|_| TelemetryError)?
    }
}
