use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bifrost_core::{AppState, application, config::Config, serve};
use bifrost_db::{Database, DatabaseConfig, ReadinessError};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::{net::TcpListener, sync::oneshot};
use tower::ServiceExt;

type TestResult = Result<(), Box<dyn std::error::Error>>;
const SYNTHETIC_URL: &str = "postgresql+asyncpg://synthetic:never-log-this@127.0.0.1:1/disposable";

fn config(extra: &[(&str, &str)]) -> Result<Config, bifrost_core::config::ConfigError> {
    Config::from_pairs(
        std::iter::once(("BIFROST_DATABASE_URL", SYNTHETIC_URL))
            .chain(extra.iter().copied())
            .map(|(key, value)| (key.to_owned(), value.to_owned())),
    )
}

#[test]
fn configuration_preserves_python_database_conventions() -> TestResult {
    let value = config(&[])?;
    assert_eq!(value.bind.port(), 8001);
    assert_eq!(value.database.max_connections(), 15);
    assert_eq!(value.readiness_timeout, Duration::from_secs(3));
    assert_eq!(
        config(&[
            ("BIFROST_DATABASE_POOL_SIZE", "1"),
            ("BIFROST_DATABASE_MAX_OVERFLOW", "0")
        ])?
        .database
        .max_connections(),
        1
    );
    assert!(
        DatabaseConfig::new(
            "postgresql://synthetic:never-log-this@127.0.0.1/disposable",
            1,
            Duration::from_secs(1)
        )
        .is_ok()
    );
    Ok(())
}

#[test]
fn invalid_configuration_fails_without_values() {
    for (key, value) in [
        (
            "BIFROST_DATABASE_URL",
            "mysql://synthetic:never-log-this@localhost/disposable",
        ),
        ("BIFROST_DATABASE_URL", "postgresql://"),
        ("BIFROST_DATABASE_URL", "postgresql://synthetic@localhost"),
        ("BIFROST_RUST_BIND", "never-log-this"),
        ("BIFROST_DATABASE_POOL_SIZE", "0"),
        ("BIFROST_DATABASE_POOL_SIZE", "33"),
        ("BIFROST_DATABASE_MAX_OVERFLOW", "32"),
        ("BIFROST_DATABASE_POOL_SIZE", "4294967295"),
        ("BIFROST_RUST_READINESS_TIMEOUT_MS", "0"),
        ("BIFROST_RUST_SHUTDOWN_TIMEOUT_MS", "30001"),
        ("BIFROST_RUST_DATABASE_ACQUIRE_TIMEOUT_MS", "never-log-this"),
        (
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "https://user:never-log-this@localhost:4317",
        ),
        (
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://localhost:4317?never-log-this",
        ),
        (
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://localhost:4318/v1/traces",
        ),
        ("OTEL_EXPORTER_OTLP_HEADERS", "authorization=never-log-this"),
        (
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
            "api-key=never-log-this",
        ),
        ("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
    ] {
        let error = match config(&[(key, value)]) {
            Ok(_) => panic!("invalid configuration accepted for {key}"),
            Err(error) => error,
        };
        assert_eq!(error.0, key);
        assert!(!error.to_string().contains("never-log-this"));
    }
    assert!(Config::from_pairs([]).is_err());
    assert!(
        config(&[
            ("BIFROST_DATABASE_POOL_SIZE", "32"),
            ("BIFROST_DATABASE_MAX_OVERFLOW", "1")
        ])
        .is_err()
    );
}

#[tokio::test]
async fn liveness_and_database_readiness_are_distinct() -> TestResult {
    let config = config(&[
        ("BIFROST_RUST_DATABASE_ACQUIRE_TIMEOUT_MS", "50"),
        ("BIFROST_RUST_READINESS_TIMEOUT_MS", "100"),
    ])?;
    let database = Database::new(&config.database)?;
    assert_eq!(database.readiness().await, Err(ReadinessError::Unavailable));
    let state = AppState::new(database, config.readiness_timeout);
    for (path, expected) in [
        ("/health", StatusCode::OK),
        ("/ready", StatusCode::SERVICE_UNAVAILABLE),
        ("/api/device/claim", StatusCode::NOT_FOUND),
    ] {
        let response = application(state.clone())
            .oneshot(Request::builder().uri(path).body(Body::empty())?)
            .await?;
        assert_eq!(response.status(), expected);
        let bytes = to_bytes(response.into_body(), 1024).await?;
        assert!(!String::from_utf8_lossy(&bytes).contains("never-log-this"));
    }
    state.begin_shutdown();
    assert_eq!(
        application(state.clone())
            .oneshot(Request::builder().uri("/ready").body(Body::empty())?)
            .await?
            .status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
    state.database.close().await;
    Ok(())
}

#[tokio::test]
async fn graceful_shutdown_stops_http_and_closes_database_pool() -> TestResult {
    let config = config(&[])?;
    let state = AppState::new(Database::new(&config.database)?, config.readiness_timeout);
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let (tx, rx) = oneshot::channel();
    let server = tokio::spawn(serve(
        listener,
        state.clone(),
        async move {
            let _ = rx.await;
        },
        Duration::from_millis(500),
    ));
    tx.send(()).map_err(|_| "shutdown receiver missing")?;
    tokio::time::timeout(Duration::from_secs(1), server).await???;
    assert!(state.is_draining());
    assert!(state.database.pool().is_closed());
    assert!(tokio::net::TcpStream::connect(address).await.is_err());
    Ok(())
}

#[tokio::test]
async fn graceful_shutdown_drains_an_inflight_readiness_request() -> TestResult {
    let blackhole = TcpListener::bind("127.0.0.1:0").await?;
    let url = format!(
        "postgresql://synthetic:never-log-this@{}/disposable",
        blackhole.local_addr()?
    );
    let config = config(&[
        ("BIFROST_DATABASE_URL", &url),
        ("BIFROST_RUST_READINESS_TIMEOUT_MS", "100"),
        ("BIFROST_RUST_DATABASE_ACQUIRE_TIMEOUT_MS", "100"),
    ])?;
    let state = AppState::new(Database::new(&config.database)?, config.readiness_timeout);
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let (tx, rx) = oneshot::channel();
    let server = tokio::spawn(serve(
        listener,
        state.clone(),
        async move {
            let _ = rx.await;
        },
        Duration::from_millis(500),
    ));
    let mut client = tokio::net::TcpStream::connect(address).await?;
    client
        .write_all(b"GET /ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .await?;
    // A DB handshake proves readiness is in flight before shutdown begins.
    let (_database_connection, _) =
        tokio::time::timeout(Duration::from_secs(1), blackhole.accept()).await??;
    tx.send(()).map_err(|_| "shutdown receiver missing")?;
    let mut response = String::new();
    tokio::time::timeout(Duration::from_secs(1), client.read_to_string(&mut response)).await??;
    assert!(response.starts_with("HTTP/1.1 503"));
    assert!(!response.contains("never-log-this"));
    tokio::time::timeout(Duration::from_secs(1), server).await???;
    assert!(state.is_draining());
    assert!(state.database.pool().is_closed());
    Ok(())
}

#[tokio::test]
async fn request_spans_do_not_record_caller_secrets() -> TestResult {
    #[derive(Clone)]
    struct Buffer(std::sync::Arc<std::sync::Mutex<Vec<u8>>>);
    impl std::io::Write for Buffer {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0
                .lock()
                .map_err(|_| std::io::Error::other("log lock failed"))?
                .extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let bytes = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let writer = Buffer(bytes.clone());
    let subscriber = tracing_subscriber::fmt()
        .json()
        .with_writer(move || writer.clone())
        .finish();
    let _guard = tracing::subscriber::set_default(subscriber);
    let config = config(&[])?;
    let state = AppState::new(Database::new(&config.database)?, config.readiness_timeout);
    let response = application(state.clone())
        .oneshot(
            Request::builder()
                .uri("/never-log-this?token=never-log-this")
                .header("authorization", "Bearer never-log-this")
                .header("x-request-id", "never-log-this")
                .body(Body::from("never-log-this"))?,
        )
        .await?;
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert_ne!(
        response
            .headers()
            .get("x-request-id")
            .and_then(|header| header.to_str().ok()),
        Some("never-log-this")
    );
    let logs = String::from_utf8(bytes.lock().map_err(|_| "log lock failed")?.clone())?;
    assert!(logs.contains("request completed"));
    assert!(!logs.contains("never-log-this"));
    state.database.close().await;
    Ok(())
}
