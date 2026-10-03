//! PostgreSQL primitives. Alembic owns schema creation and migration history.

#[cfg(feature = "workflow-sql-parity")]
pub mod workflow_numeric;

#[cfg(feature = "workflow-sql-parity")]
pub mod workflow_parity;

use std::{
    str::FromStr,
    time::{Duration, Instant},
};

use sqlx::{
    ConnectOptions, PgPool,
    postgres::{PgConnectOptions, PgPoolOptions},
};
use thiserror::Error;

/// Secrets are parsed once and never implement Debug or Display.
#[derive(Clone)]
pub struct DatabaseConfig {
    options: PgConnectOptions,
    max_connections: u32,
    acquire_timeout: Duration,
}

impl DatabaseConfig {
    pub fn new(
        url: &str,
        max_connections: u32,
        acquire_timeout: Duration,
    ) -> Result<Self, DatabaseError> {
        if !(1..=32).contains(&max_connections)
            || acquire_timeout.is_zero()
            || acquire_timeout > Duration::from_secs(10)
        {
            return Err(DatabaseError);
        }
        let options = PgConnectOptions::from_str(url)
            .map_err(|_| DatabaseError)?
            .statement_cache_capacity(0)
            .application_name("bifrost-core")
            .disable_statement_logging();
        Ok(Self {
            options,
            max_connections,
            acquire_timeout,
        })
    }

    pub fn max_connections(&self) -> u32 {
        self.max_connections
    }
}

#[derive(Debug, Error)]
#[error("invalid database configuration")]
pub struct DatabaseError;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ReadinessError {
    #[error("database unavailable")]
    Unavailable,
    #[error("device schema incompatible")]
    Schema,
}

#[derive(Clone)]
pub struct Database {
    pool: PgPool,
}

#[cfg(all(test, feature = "live-db"))]
mod live_tests {
    use super::*;

    #[tokio::test]
    async fn migrated_schema_readiness_and_drift() -> Result<(), Box<dyn std::error::Error>> {
        let url = std::env::var("BIFROST_RUST_TEST_DATABASE_URL")
            .map_err(|_| "live-db requires BIFROST_RUST_TEST_DATABASE_URL for an isolated Alembic-migrated database")?;
        let config = DatabaseConfig::new(&url, 1, Duration::from_secs(2))?;
        let database = Database::new(&config)?;
        database.readiness().await?;
        for mutation in [
            "ALTER TABLE public.device_job_logs RENAME COLUMN seq TO unexpected_seq",
            "ALTER TABLE public.device_jobs DROP CONSTRAINT ck_device_jobs_timeout_seconds",
            "DROP INDEX public.uq_device_jobs_one_active",
            "ALTER TABLE public.devices ALTER COLUMN api_key_enabled DROP NOT NULL",
        ] {
            // DDL is confined to this transaction and rolled back even on failure.
            let mut tx = database.pool().begin().await?;
            sqlx::query("SET LOCAL lock_timeout = '1000ms'")
                .persistent(false)
                .execute(&mut *tx)
                .await?;
            sqlx::query("SET LOCAL statement_timeout = '2000ms'")
                .persistent(false)
                .execute(&mut *tx)
                .await?;
            sqlx::query(mutation)
                .persistent(false)
                .execute(&mut *tx)
                .await?;
            let ready: bool = sqlx::query_scalar(include_str!("schema.sql"))
                .persistent(false)
                .fetch_one(&mut *tx)
                .await?;
            assert!(!ready, "incompatible migrated schema accepted");
            tx.rollback().await?;
            database.readiness().await?;
        }
        database.close().await;
        assert_eq!(database.readiness().await, Err(ReadinessError::Unavailable));
        Ok(())
    }
}

impl Database {
    /// Lazy connection permits liveness during database outages; readiness fails closed.
    pub fn new(config: &DatabaseConfig) -> Result<Self, DatabaseError> {
        let pool = PgPoolOptions::new()
            .max_connections(config.max_connections)
            .min_connections(0)
            .acquire_timeout(config.acquire_timeout)
            .idle_timeout(Duration::from_secs(60))
            .max_lifetime(Duration::from_secs(300))
            .connect_lazy_with(config.options.clone());
        Ok(Self { pool })
    }

    pub fn pool(&self) -> &PgPool {
        &self.pool
    }

    pub async fn close(&self) {
        self.pool.close().await;
    }

    pub async fn readiness(&self) -> Result<(), ReadinessError> {
        let start = Instant::now();
        let mut tx = self
            .pool
            .begin()
            .await
            .map_err(|_| ReadinessError::Unavailable)?;
        tracing::debug!(
            pool_wait_ms = start.elapsed().as_millis() as u64,
            "readiness acquired database connection"
        );
        // Transaction-local settings work with PgBouncer transaction pooling.
        sqlx::query("SET TRANSACTION READ ONLY")
            .persistent(false)
            .execute(&mut *tx)
            .await
            .map_err(|_| ReadinessError::Unavailable)?;
        sqlx::query("SET LOCAL statement_timeout = '2000ms'")
            .persistent(false)
            .execute(&mut *tx)
            .await
            .map_err(|_| ReadinessError::Unavailable)?;
        let compatible: bool = sqlx::query_scalar(include_str!("schema.sql"))
            .persistent(false)
            .fetch_one(&mut *tx)
            .await
            .map_err(|_| ReadinessError::Schema)?;
        if !compatible {
            return Err(ReadinessError::Schema);
        }
        // Catalog visibility alone does not prove the runtime role can read the tables.
        sqlx::query("SELECT d.id, j.claim_token, l.seq FROM public.devices d CROSS JOIN public.device_jobs j CROSS JOIN public.device_job_logs l WHERE FALSE")
            .persistent(false).execute(&mut *tx).await.map_err(|_| ReadinessError::Schema)?;
        tx.commit().await.map_err(|_| ReadinessError::Unavailable)?;
        tracing::debug!(
            duration_ms = start.elapsed().as_millis() as u64,
            "readiness database transaction completed"
        );
        Ok(())
    }
}
