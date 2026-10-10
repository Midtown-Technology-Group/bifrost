//! Private synthetic test probe. This is not a workload API or admission path.
use bifrost_isolated_owner_spike::{
    CancelDecision, ObserveError, SessionFence, SessionObservation, observe_session,
    request_running_cancel,
};
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use std::{
    io::{self, Read},
    str::FromStr,
    time::Duration,
};

fn read_fence(reader: impl Read) -> Result<SessionFence, ()> {
    let mut bytes = Vec::new();
    reader.take(4097).read_to_end(&mut bytes).map_err(|_| ())?;
    if bytes.len() > 4096 {
        return Err(());
    }
    let text = std::str::from_utf8(&bytes).map_err(|_| ())?;
    let fields: Vec<_> = text.lines().collect();
    if fields.len() != 10 || fields.iter().any(|value| value.is_empty()) {
        return Err(());
    }
    Ok(SessionFence {
        execution_id: fields[0].to_owned(),
        owner_incarnation_id: fields[1].to_owned(),
        attempt_id: fields[2].to_owned(),
        claim_token: fields[3].to_owned(),
        worker_incarnation_id: fields[4].to_owned(),
        session_id: fields[5].to_owned(),
        supervisor_incarnation_id: fields[6].to_owned(),
        runtime_incarnation_id: fields[7].to_owned(),
        binding_sha256: fields[8].to_owned(),
        channel_custody_sha256: fields[9].to_owned(),
    })
}

async fn run() -> &'static str {
    if std::env::args().count() != 1
        || std::env::var("BIFROST_ISOLATED_OWNER_TEST").as_deref() != Ok("1")
    {
        return "rejected";
    }
    let Ok(fence) = read_fence(io::stdin().lock()) else {
        return "rejected";
    };
    let operation = std::env::var("BIFROST_OWNER_TEST_ACTION")
        .unwrap_or_else(|_| "observe".to_owned());
    if !matches!(operation.as_str(), "observe" | "request-running-cancel") {
        return "rejected";
    }
    let Ok(url) = std::env::var("BIFROST_OWNER_TEST_DATABASE_URL") else {
        return "rejected";
    };
    let Ok(options) = PgConnectOptions::from_str(&url) else {
        return "rejected";
    };
    // Independent backend sessions through the admitted transaction pool. No
    // prepared-statement or ambient connection/credential fallback is required.
    let options = options.statement_cache_capacity(0);
    let options = if std::env::var("BIFROST_OWNER_TEST_DEFAULT_FLOAT_DIGITS").as_deref() == Ok("1") {
        // Regression control: the exact SQLx default rejected by this pool.
        options
    } else {
        options.extra_float_digits(None)
    };
    let pool = match PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(5))
        .connect_with(options)
        .await
    {
        Ok(pool) => pool,
        Err(sqlx::Error::Database(error))
            if error.message().contains("startup parameter")
                && error.message().contains("extra_float_digits") =>
        {
            // Static classification only; no free-form error, DSN or secret.
            return "startup_parameter_rejected";
        }
        Err(_) => return "database_failure",
    };
    let result = tokio::time::timeout(Duration::from_secs(5), async {
        if operation == "request-running-cancel" {
            match request_running_cancel(&pool, &fence).await? {
                CancelDecision::Committed => Ok("cancel_committed"),
                CancelDecision::AlreadyCommitted => Ok("cancel_already_committed"),
            }
        } else {
            match observe_session(&pool, &fence).await? {
                SessionObservation::Open => Ok("open"),
                SessionObservation::Closed => Ok("closed"),
            }
        }
    })
    .await;
    pool.close().await;
    match result {
        Ok(Ok(outcome)) => outcome,
        Ok(Err(ObserveError::InvalidFence | ObserveError::Rejected)) => "rejected",
        Ok(Err(ObserveError::UncertainCommit)) => "uncertain_commit",
        Ok(Err(ObserveError::Database(sqlx::Error::Database(error))))
            if error.code().as_deref() == Some("55P03") =>
        {
            "lock_contention"
        }
        Err(_) if operation == "request-running-cancel" => "uncertain_commit",
        Ok(Err(ObserveError::Database(_))) | Err(_) => "database_failure",
    }
}

#[tokio::main]
async fn main() {
    // Bounded, credential-free output only; never print SQL/DSN/claim/payload.
    println!("{}", run().await);
}

#[cfg(test)]
mod tests {
    use super::read_fence;

    #[test]
    fn private_probe_rejects_oversize_and_invalid_utf8_before_connect() {
        assert!(read_fence(&vec![b'a'; 4097][..]).is_err());
        assert!(read_fence(&[0xff][..]).is_err());
        assert!(read_fence(&b"one\ntwo\n"[..]).is_err());
    }
}
