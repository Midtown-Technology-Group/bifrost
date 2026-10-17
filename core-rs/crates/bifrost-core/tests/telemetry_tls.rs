//! Mandatory, isolated native-root TLS proof; run through scripts/telemetry-tls.sh.
use axum::{body::Body, http::Request};
use bifrost_core::{AppState, application, config::TelemetryConfig, telemetry::Telemetry};
use bifrost_db::{Database, DatabaseConfig};
use opentelemetry_proto::tonic::collector::{
    metrics::v1::{
        ExportMetricsServiceRequest, ExportMetricsServiceResponse,
        metrics_service_server::{MetricsService, MetricsServiceServer},
    },
    trace::v1::{
        ExportTraceServiceRequest, ExportTraceServiceResponse,
        trace_service_server::{TraceService, TraceServiceServer},
    },
};
use std::{sync::Arc, time::Duration};
use tokio::{
    net::TcpListener,
    sync::{Mutex, oneshot},
};
use tokio_stream::wrappers::TcpListenerStream;
use tonic::{
    Response, Status,
    transport::{Identity, Server, ServerTlsConfig},
};
use tower::ServiceExt;

type TestResult = Result<(), Box<dyn std::error::Error>>;

#[derive(Clone, Default)]
struct Collector {
    traces: Arc<Mutex<Vec<ExportTraceServiceRequest>>>,
    metrics: Arc<Mutex<Vec<ExportMetricsServiceRequest>>>,
}

#[tonic::async_trait]
impl TraceService for Collector {
    async fn export(
        &self,
        request: tonic::Request<ExportTraceServiceRequest>,
    ) -> Result<Response<ExportTraceServiceResponse>, Status> {
        self.traces.lock().await.push(request.into_inner());
        Ok(Response::new(ExportTraceServiceResponse::default()))
    }
}

#[tonic::async_trait]
impl MetricsService for Collector {
    async fn export(
        &self,
        request: tonic::Request<ExportMetricsServiceRequest>,
    ) -> Result<Response<ExportMetricsServiceResponse>, Status> {
        self.metrics.lock().await.push(request.into_inner());
        Ok(Response::new(ExportMetricsServiceResponse::default()))
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn actual_exporters_validate_native_trust_and_flush() -> TestResult {
    // No fallback/skip: the CI script supplies ephemeral certificate paths and trust.
    let cert = std::fs::read(std::env::var("BIFROST_RUST_TLS_CERT")?)?;
    let key = std::fs::read(std::env::var("BIFROST_RUST_TLS_KEY")?)?;
    let trust = std::env::var("SSL_CERT_FILE")?;
    assert!(!std::fs::read(trust)?.is_empty());
    let trusted = match std::env::var("BIFROST_RUST_TLS_EXPECT")?.as_str() {
        "trusted" => true,
        "untrusted" => false,
        _ => return Err("BIFROST_RUST_TLS_EXPECT must be trusted or untrusted".into()),
    };
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let collector = Collector::default();
    let (stop_tx, stop_rx) = oneshot::channel();
    let server = Server::builder()
        .tls_config(ServerTlsConfig::new().identity(Identity::from_pem(cert, key)))?
        .add_service(TraceServiceServer::new(collector.clone()))
        .add_service(MetricsServiceServer::new(collector.clone()));
    let server_task = tokio::spawn(server.serve_with_incoming_shutdown(
        TcpListenerStream::new(listener),
        async {
            let _ = stop_rx.await;
        },
    ));
    let telemetry = Telemetry::init(&TelemetryConfig {
        endpoint: Some(format!("https://localhost:{}", address.port())),
    })
    .map_err(|_| "telemetry initialization failed")?;
    let database = Database::new(&DatabaseConfig::new(
        "postgresql://synthetic:tls-test-only@127.0.0.1:1/disposable",
        1,
        Duration::from_millis(100),
    )?)?;
    let response = application(AppState::new(database.clone(), Duration::from_millis(100)))
        .oneshot(
            Request::builder()
                .uri("/health?synthetic-secret")
                .header("authorization", "synthetic-secret")
                .body(Body::empty())?,
        )
        .await?;
    assert_eq!(response.status(), 200);
    drop(response);
    database.close().await;
    let flush = tokio::time::timeout(Duration::from_secs(10), telemetry.shutdown()).await?;
    let traces = collector.traces.lock().await;
    let metrics = collector.metrics.lock().await;
    if trusted {
        assert!(flush.is_ok(), "trusted TLS exporter shutdown must succeed");
        assert!(
            traces
                .iter()
                .flat_map(|request| &request.resource_spans)
                .flat_map(|resource| &resource.scope_spans)
                .flat_map(|scope| &scope.spans)
                .any(|span| span.name == "http.request")
        );
        assert!(
            metrics
                .iter()
                .flat_map(|request| &request.resource_metrics)
                .flat_map(|resource| &resource.scope_metrics)
                .flat_map(|scope| &scope.metrics)
                .any(|metric| {
                    metric.name == "bifrost.http.server.duration"
                        && matches!(
                            &metric.data,
                            Some(opentelemetry_proto::tonic::metrics::v1::metric::Data::Histogram(histogram))
                                if histogram.data_points.iter().any(|point| point.count == 1)
                        )
                })
        );
        let exported = format!("{traces:?}{metrics:?}");
        assert!(!exported.contains("synthetic-secret"));
        assert!(!exported.contains("tls-test-only"));
    } else {
        assert!(flush.is_err(), "untrusted TLS exporter flush must fail");
        assert!(
            traces.is_empty() && metrics.is_empty(),
            "untrusted chain must deliver neither signal"
        );
    }
    drop(traces);
    drop(metrics);
    let _ = stop_tx.send(());
    tokio::time::timeout(Duration::from_secs(3), server_task).await???;
    Ok(())
}
