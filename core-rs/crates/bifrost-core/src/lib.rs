//! W0 service foundation; no device routes or background business work.
pub mod config;
pub mod telemetry;

use axum::{
    Json, Router,
    error_handling::HandleErrorLayer,
    extract::{MatchedPath, Request, State},
    http::{HeaderValue, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
};
use bifrost_contracts::HealthResponse;
use bifrost_db::Database;
use opentelemetry::{KeyValue, metrics::Histogram};
use std::{
    future::Future,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use thiserror::Error;
use tokio::{net::TcpListener, sync::oneshot};
use tower::ServiceBuilder;
use tracing::Instrument;

#[derive(Clone)]
pub struct AppState {
    pub database: Database,
    readiness_timeout: Duration,
    draining: Arc<AtomicBool>,
    request_duration: Histogram<f64>,
}

impl AppState {
    pub fn new(database: Database, readiness_timeout: Duration) -> Self {
        Self {
            database,
            readiness_timeout,
            draining: Arc::new(AtomicBool::new(false)),
            request_duration: opentelemetry::global::meter("bifrost-core")
                .f64_histogram("bifrost.http.server.duration")
                .with_unit("s")
                .build(),
        }
    }
    pub fn begin_shutdown(&self) {
        self.draining.store(true, Ordering::Release);
    }
    pub fn is_draining(&self) -> bool {
        self.draining.load(Ordering::Acquire)
    }
}

pub fn application(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/ready", get(ready))
        .layer(middleware::from_fn_with_state(state.clone(), trace_request))
        .layer(
            ServiceBuilder::new()
                .layer(HandleErrorLayer::new(|_: axum::BoxError| async {
                    StatusCode::SERVICE_UNAVAILABLE
                }))
                .load_shed()
                .concurrency_limit(64),
        )
        .with_state(state)
}

async fn health() -> Json<HealthResponse> {
    Json(HealthResponse { status: "ok" })
}

async fn ready(State(state): State<AppState>) -> Response {
    if state.is_draining() {
        return unavailable();
    }
    match tokio::time::timeout(state.readiness_timeout, state.database.readiness()).await {
        Ok(Ok(())) if !state.is_draining() => {
            Json(HealthResponse { status: "ready" }).into_response()
        }
        _ => {
            // Never format driver errors, SQL, endpoint URLs, or credentials.
            tracing::warn!(
                reason = "database_or_schema_unavailable",
                "readiness rejected"
            );
            unavailable()
        }
    }
}

fn unavailable() -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(HealthResponse {
            status: "unavailable",
        }),
    )
        .into_response()
}

async fn trace_request(State(state): State<AppState>, request: Request, next: Next) -> Response {
    // Paths, queries, bodies, headers and caller-provided request IDs may contain secrets.
    // Log the bounded router template; create a server-owned request ID.
    static REQUEST_ID: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    let request_id = REQUEST_ID.fetch_add(1, Ordering::Relaxed);
    let route = request
        .extensions()
        .get::<MatchedPath>()
        .map_or("unmatched", MatchedPath::as_str)
        .to_owned();
    let span = tracing::info_span!("http.request", request_id, route = %route);
    async move {
        let start = Instant::now();
        let mut response = next.run(request).await;
        let status = response.status().as_u16();
        state.request_duration.record(
            start.elapsed().as_secs_f64(),
            &[
                KeyValue::new("http.route", route),
                KeyValue::new("http.response.status_code", i64::from(status)),
            ],
        );
        tracing::info!(
            status,
            duration_ms = start.elapsed().as_millis() as u64,
            "request completed"
        );
        if let Ok(value) = HeaderValue::from_str(&request_id.to_string()) {
            response.headers_mut().insert("x-request-id", value);
        }
        response
    }
    .instrument(span)
    .await
}

#[derive(Debug, Error)]
pub enum ServeError {
    #[error("HTTP server failed")]
    Server,
    #[error("graceful shutdown deadline exceeded")]
    ShutdownTimeout,
}

/// Stop intake, fail readiness, drain HTTP, then close the pool. No detached tasks.
pub async fn serve(
    listener: TcpListener,
    state: AppState,
    shutdown: impl Future<Output = ()> + Send + 'static,
    shutdown_timeout: Duration,
) -> Result<(), ServeError> {
    let (started_tx, started_rx) = oneshot::channel();
    let shutdown_state = state.clone();
    let server =
        axum::serve(listener, application(state.clone())).with_graceful_shutdown(async move {
            shutdown.await;
            shutdown_state.begin_shutdown();
            tracing::info!("graceful shutdown started");
            let _ = started_tx.send(());
        });
    let server = std::future::IntoFuture::into_future(server);
    tokio::pin!(server);
    let result = tokio::select! {
        result = &mut server => result.map_err(|_| ServeError::Server),
        _ = started_rx => match tokio::time::timeout(shutdown_timeout, &mut server).await {
            Ok(result) => result.map_err(|_| ServeError::Server),
            Err(_) => Err(ServeError::ShutdownTimeout),
        },
    };
    state.begin_shutdown();
    tokio::time::timeout(shutdown_timeout, state.database.close())
        .await
        .map_err(|_| ServeError::ShutdownTimeout)?;
    tracing::info!("HTTP server and database pool stopped");
    result
}
