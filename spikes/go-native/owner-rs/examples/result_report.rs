//! Private synthetic Result probe; no workload, issuer or launch authority.
use bifrost_isolated_owner_spike::{ObserveError, SessionFence, accept_result};
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use std::{
    io::{self, Read},
    str::FromStr,
    time::Duration,
};

fn read_request(reader: impl Read) -> Result<(SessionFence, String, Vec<u8>), ()> {
    const MAX: usize = 16 * 1024 * 1024 + 4096;
    let mut bytes = Vec::new();
    reader
        .take((MAX + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| ())?;
    if bytes.len() > MAX {
        return Err(());
    }
    let mut parts = bytes.splitn(12, |b| *b == b'\n');
    let mut fields = Vec::new();
    for _ in 0..11 {
        let raw = parts.next().ok_or(())?;
        if raw.len() > 64 {
            return Err(());
        }
        fields.push(std::str::from_utf8(raw).map_err(|_| ())?.to_owned());
    }
    let payload = parts.next().ok_or(())?.to_vec();
    Ok((
        SessionFence {
            execution_id: fields[0].clone(),
            owner_incarnation_id: fields[1].clone(),
            attempt_id: fields[2].clone(),
            claim_token: fields[3].clone(),
            worker_incarnation_id: fields[4].clone(),
            session_id: fields[5].clone(),
            supervisor_incarnation_id: fields[6].clone(),
            runtime_incarnation_id: fields[7].clone(),
            binding_sha256: fields[8].clone(),
            channel_custody_sha256: fields[9].clone(),
        },
        fields[10].clone(),
        payload,
    ))
}

async fn run() -> String {
    if std::env::args().count() != 1
        || std::env::var("BIFROST_ISOLATED_OWNER_TEST").as_deref() != Ok("1")
    {
        return "rejected".into();
    }
    let Ok((fence, decision, payload)) = read_request(io::stdin().lock()) else {
        return "rejected".into();
    };
    let Ok(url) = std::env::var("BIFROST_OWNER_TEST_DATABASE_URL") else {
        return "rejected".into();
    };
    let Ok(options) = PgConnectOptions::from_str(&url) else {
        return "rejected".into();
    };
    let pool = match PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(5))
        .connect_with(options.statement_cache_capacity(0).extra_float_digits(None))
        .await
    {
        Ok(pool) => pool,
        Err(_) => return "database_failure".into(),
    };
    let result = tokio::time::timeout(
        Duration::from_secs(5),
        accept_result(&pool, &fence, &payload, &decision),
    )
    .await;
    if matches!(&result, Ok(Ok(_)))
        && std::env::var("BIFROST_OWNER_TEST_EXIT_AFTER_RESULT_COMMIT").as_deref() == Ok("1")
    {
        // Exact post-commit/pre-receipt process-loss test window only.
        std::process::exit(73);
    }
    pool.close().await;
    match result {
        Ok(Ok(decision)) => decision.receipt_body().to_string(),
        Ok(Err(ObserveError::InvalidFence | ObserveError::Rejected)) => "rejected".into(),
        Ok(Err(ObserveError::UncertainCommit)) | Err(_) => "uncertain_commit".into(),
        Ok(Err(ObserveError::Database(sqlx::Error::Database(error))))
            if error.code().as_deref() == Some("55P03") =>
        {
            "lock_contention".into()
        }
        Ok(Err(ObserveError::Database(_))) => "database_failure".into(),
    }
}

#[tokio::main]
async fn main() {
    println!("{}", run().await);
}

#[cfg(test)]
mod tests {
    use super::read_request;
    #[test]
    fn rejects_truncated_or_non_utf8_identity_before_connect() {
        assert!(read_request(&b"one\ntwo\n"[..]).is_err());
        assert!(read_request(&[0xff, b'\n'][..]).is_err());
    }
}
