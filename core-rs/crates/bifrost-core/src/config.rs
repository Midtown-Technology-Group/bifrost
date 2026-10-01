use std::{collections::HashMap, net::SocketAddr, time::Duration};

use bifrost_db::DatabaseConfig;
use thiserror::Error;
use url::Url;

/// Intentionally no Debug: configuration contains connection credentials.
pub struct Config {
    pub bind: SocketAddr,
    pub database: DatabaseConfig,
    pub readiness_timeout: Duration,
    pub shutdown_timeout: Duration,
    pub telemetry: TelemetryConfig,
}

pub struct TelemetryConfig {
    pub endpoint: Option<String>,
}

#[derive(Debug, Error, PartialEq, Eq)]
#[error("invalid or missing configuration: {0}")]
pub struct ConfigError(pub &'static str);

impl Config {
    pub fn from_env() -> Result<Self, ConfigError> {
        let keys = [
            "BIFROST_RUST_BIND",
            "BIFROST_DATABASE_URL",
            "BIFROST_DATABASE_POOL_SIZE",
            "BIFROST_DATABASE_MAX_OVERFLOW",
            "BIFROST_RUST_DATABASE_ACQUIRE_TIMEOUT_MS",
            "BIFROST_RUST_READINESS_TIMEOUT_MS",
            "BIFROST_RUST_SHUTDOWN_TIMEOUT_MS",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
            "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
            "OTEL_EXPORTER_OTLP_PROTOCOL",
        ];
        let mut pairs = Vec::new();
        for key in keys {
            match std::env::var(key) {
                Ok(value) => pairs.push((key.to_owned(), value)),
                Err(std::env::VarError::NotPresent) => {}
                Err(std::env::VarError::NotUnicode(_)) => return Err(ConfigError(key)),
            }
        }
        Self::from_pairs(pairs)
    }

    pub fn from_pairs(
        pairs: impl IntoIterator<Item = (String, String)>,
    ) -> Result<Self, ConfigError> {
        let env: HashMap<String, String> = pairs.into_iter().collect();
        let get = |key: &'static str, default: &str| {
            env.get(key)
                .map_or_else(|| default.to_owned(), Clone::clone)
        };
        let number = |key: &'static str, default: &str, min: u32, max: u32| {
            let value = get(key, default)
                .parse::<u32>()
                .map_err(|_| ConfigError(key))?;
            if !(min..=max).contains(&value) {
                return Err(ConfigError(key));
            }
            Ok(value)
        };
        let bind = get("BIFROST_RUST_BIND", "0.0.0.0:8001")
            .parse::<SocketAddr>()
            .map_err(|_| ConfigError("BIFROST_RUST_BIND"))?;
        let url_key = "BIFROST_DATABASE_URL";
        let raw = env.get(url_key).ok_or(ConfigError(url_key))?;
        let normalized = raw
            .strip_prefix("postgresql+asyncpg://")
            .map_or_else(|| raw.clone(), |tail| format!("postgresql://{tail}"));
        let url = Url::parse(&normalized).map_err(|_| ConfigError(url_key))?;
        if !matches!(url.scheme(), "postgres" | "postgresql")
            || url.host_str().is_none()
            || url.username().is_empty()
            || url.path().len() <= 1
            || url.fragment().is_some()
        {
            return Err(ConfigError(url_key));
        }
        let pool_size = number("BIFROST_DATABASE_POOL_SIZE", "5", 1, 32)?;
        let overflow = number("BIFROST_DATABASE_MAX_OVERFLOW", "10", 0, 31)?;
        let max = pool_size
            .checked_add(overflow)
            .filter(|v| *v <= 32)
            .ok_or(ConfigError("BIFROST_DATABASE_MAX_OVERFLOW"))?;
        let acquire = Duration::from_millis(u64::from(number(
            "BIFROST_RUST_DATABASE_ACQUIRE_TIMEOUT_MS",
            "2000",
            50,
            10000,
        )?));
        let database =
            DatabaseConfig::new(&normalized, max, acquire).map_err(|_| ConfigError(url_key))?;
        let readiness_timeout = Duration::from_millis(u64::from(number(
            "BIFROST_RUST_READINESS_TIMEOUT_MS",
            "3000",
            50,
            10000,
        )?));
        let shutdown_timeout = Duration::from_millis(u64::from(number(
            "BIFROST_RUST_SHUTDOWN_TIMEOUT_MS",
            "10000",
            100,
            30000,
        )?));
        let endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT").cloned();
        if let Some(endpoint) = &endpoint {
            let url =
                Url::parse(endpoint).map_err(|_| ConfigError("OTEL_EXPORTER_OTLP_ENDPOINT"))?;
            if !matches!(url.scheme(), "http" | "https")
                || url.host_str().is_none()
                || !url.username().is_empty()
                || url.password().is_some()
                || url.query().is_some()
                || url.fragment().is_some()
                || !matches!(url.path(), "" | "/")
            {
                return Err(ConfigError("OTEL_EXPORTER_OTLP_ENDPOINT"));
            }
        }
        for key in [
            "OTEL_EXPORTER_OTLP_HEADERS",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
            "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        ] {
            if env.get(key).is_some_and(|value| !value.is_empty()) {
                return Err(ConfigError(key));
            }
        }
        if env
            .get("OTEL_EXPORTER_OTLP_PROTOCOL")
            .is_some_and(|value| value != "grpc")
        {
            return Err(ConfigError("OTEL_EXPORTER_OTLP_PROTOCOL"));
        }
        Ok(Self {
            bind,
            database,
            readiness_timeout,
            shutdown_timeout,
            telemetry: TelemetryConfig { endpoint },
        })
    }
}
