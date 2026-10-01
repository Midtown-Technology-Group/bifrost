use bifrost_core::{AppState, config::Config, serve, telemetry::Telemetry};
use bifrost_db::Database;
use std::process::ExitCode;
use tokio::net::TcpListener;

#[tokio::main(worker_threads = 2)]
async fn main() -> ExitCode {
    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("{message}");
            ExitCode::FAILURE
        }
    }
}

async fn run() -> Result<(), String> {
    let config = Config::from_env().map_err(|error| error.to_string())?;
    let telemetry = Telemetry::init(&config.telemetry)
        .map_err(|_| "telemetry initialization failed".to_owned())?;
    let database =
        Database::new(&config.database).map_err(|_| "database initialization failed".to_owned())?;
    let listener = TcpListener::bind(config.bind)
        .await
        .map_err(|_| "HTTP listener bind failed".to_owned())?;
    // Install signal handlers before accepting traffic; failure is a startup error.
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .map_err(|_| "SIGTERM handler installation failed".to_owned())?;
    let mut interrupt = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())
        .map_err(|_| "SIGINT handler installation failed".to_owned())?;
    let shutdown = async move {
        tokio::select! { _ = terminate.recv() => {}, _ = interrupt.recv() => {} }
    };
    tracing::info!(service = "bifrost-core", version = env!("CARGO_PKG_VERSION"), build_sha = option_env!("BIFROST_BUILD_SHA").unwrap_or("development"), bind = %config.bind, "service started");
    let result = serve(
        listener,
        AppState::new(database, config.readiness_timeout),
        shutdown,
        config.shutdown_timeout,
    )
    .await;
    telemetry
        .shutdown()
        .await
        .map_err(|_| "telemetry shutdown deadline exceeded".to_owned())?;
    result.map_err(|error| error.to_string())
}
