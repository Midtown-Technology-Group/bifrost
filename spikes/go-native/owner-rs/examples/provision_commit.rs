//! Private finite provision probe; no signer, delivery or physical launch.
use bifrost_isolated_owner_spike::{
    ObserveError, ProvisionRequest, SessionFence, record_provision_candidate,
};
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use std::{
    io::{self, Read},
    str::FromStr,
    time::Duration,
};

fn read_request(reader: impl Read) -> Result<(SessionFence, ProvisionRequest), ()> {
    let mut raw = String::new();
    reader
        .take(65537)
        .read_to_string(&mut raw)
        .map_err(|_| ())?;
    if raw.len() > 65536 {
        return Err(());
    }
    let mut lines = raw.splitn(11, '\n');
    let fields: Vec<_> = (0..10)
        .map(|_| lines.next().ok_or(()))
        .collect::<Result<_, _>>()?;
    if fields.iter().any(|value| value.len() > 64) {
        return Err(());
    }
    let value: serde_json::Value = serde_json::from_str(lines.next().ok_or(())?).map_err(|_| ())?;
    let object = value.as_object().ok_or(())?;
    let keys = [
        "snapshot",
        "grant_digest",
        "integration_name",
        "provision_id",
        "delivery_id",
        "frontier_sha256",
    ];
    if object.len() != keys.len() || keys.iter().any(|key| !object.contains_key(*key)) {
        return Err(());
    }
    let string = |key: &str| object[key].as_str().map(str::to_owned).ok_or(());
    Ok((
        SessionFence {
            execution_id: fields[0].into(),
            owner_incarnation_id: fields[1].into(),
            attempt_id: fields[2].into(),
            claim_token: fields[3].into(),
            worker_incarnation_id: fields[4].into(),
            session_id: fields[5].into(),
            supervisor_incarnation_id: fields[6].into(),
            runtime_incarnation_id: fields[7].into(),
            binding_sha256: fields[8].into(),
            channel_custody_sha256: fields[9].into(),
        },
        ProvisionRequest {
            snapshot: object["snapshot"].clone(),
            grant_digest: string("grant_digest")?,
            integration_name: string("integration_name")?,
            provision_id: string("provision_id")?,
            delivery_id: string("delivery_id")?,
            frontier_sha256: string("frontier_sha256")?,
        },
    ))
}

async fn run() -> &'static str {
    if std::env::args().count() != 1
        || std::env::var("BIFROST_ISOLATED_OWNER_TEST").as_deref() != Ok("1")
    {
        return "rejected";
    }
    let Ok((fence, request)) = read_request(io::stdin().lock()) else {
        return "rejected";
    };
    let Ok(url) = std::env::var("BIFROST_OWNER_TEST_DATABASE_URL") else {
        return "rejected";
    };
    let Ok(options) = PgConnectOptions::from_str(&url) else {
        return "rejected";
    };
    let pool = match PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(5))
        .connect_with(options.statement_cache_capacity(0).extra_float_digits(None))
        .await
    {
        Ok(pool) => pool,
        Err(_) => return "database_failure",
    };
    let result = tokio::time::timeout(
        Duration::from_secs(5),
        record_provision_candidate(&pool, &fence, &request),
    )
    .await;
    pool.close().await;
    match result {
        Ok(Ok(_)) => "newly_committed",
        Ok(Err(ObserveError::InvalidFence | ObserveError::Rejected)) => "rejected",
        Ok(Err(ObserveError::UncertainCommit)) | Err(_) => "uncertain_commit",
        Ok(Err(ObserveError::Database(sqlx::Error::Database(error))))
            if error.code().as_deref() == Some("55P03") =>
        {
            "lock_contention"
        }
        Ok(Err(ObserveError::Database(_))) => "database_failure",
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
    fn rejects_wrong_field_count_and_utf8_before_connect() {
        assert!(read_request(&b"one\ntwo\n"[..]).is_err());
        assert!(read_request(&[0xff, b'\n'][..]).is_err());
    }
}
