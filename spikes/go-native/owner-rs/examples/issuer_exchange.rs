//! Isolated actual-SQL/private-issuer probe. No guardian or runtime acceptance.
use bifrost_isolated_owner_spike::{
    IntegrationGetRequest, SessionFence, authorize_finite_issuance_candidate,
    issuer::IssuerChannel, peer::OriginalPeer,
};
use serde_json::Value;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use std::{
    io::{self, Read, Write},
    path::Path,
    str::FromStr,
    time::Duration,
};

async fn run() -> Result<(), ()> {
    if std::env::args().count() != 1
        || std::env::var("BIFROST_ISOLATED_OWNER_TEST").as_deref() != Ok("1")
    {
        return Err(());
    }
    println!("ready");
    io::stdout().flush().map_err(|_| ())?;
    let mut raw = String::new();
    io::stdin()
        .lock()
        .take(16385)
        .read_to_string(&mut raw)
        .map_err(|_| ())?;
    if raw.len() > 16384 {
        return Err(());
    }
    let value: Value = serde_json::from_str(&raw).map_err(|_| ())?;
    let fields = value["fence"].as_array().ok_or(())?;
    if fields.len() != 10 {
        return Err(());
    }
    let field = |i: usize| -> Result<String, ()> { Ok(fields[i].as_str().ok_or(())?.into()) };
    let fence = SessionFence {
        execution_id: field(0)?,
        owner_incarnation_id: field(1)?,
        attempt_id: field(2)?,
        claim_token: field(3)?,
        worker_incarnation_id: field(4)?,
        session_id: field(5)?,
        supervisor_incarnation_id: field(6)?,
        runtime_incarnation_id: field(7)?,
        binding_sha256: field(8)?,
        channel_custody_sha256: field(9)?,
    };
    let fields = value["request"].as_array().ok_or(())?;
    if fields.len() != 5 {
        return Err(());
    }
    let field = |i: usize| -> Result<String, ()> { Ok(fields[i].as_str().ok_or(())?.into()) };
    let request = IntegrationGetRequest {
        grant_id: field(0)?,
        grant_digest: field(1)?,
        integration_name: field(2)?,
        organization_id: field(3)?,
        solution_id: field(4)?,
    };
    let config = &value["issuer"];
    let uid = u32::try_from(config["uid"].as_u64().ok_or(())?).map_err(|_| ())?;
    let peer = OriginalPeer::pin(
        u32::try_from(config["pid"].as_u64().ok_or(())?).map_err(|_| ())?,
        uid,
        config["ticks"].as_str().ok_or(())?,
    )
    .map_err(|_| ())?;
    let mut channel = IssuerChannel::pin(
        Path::new(config["path"].as_str().ok_or(())?),
        uid,
        peer,
        config["ca"].as_str().ok_or(())?.into(),
    )
    .map_err(|_| ())?;
    let url = std::env::var("BIFROST_OWNER_TEST_DATABASE_URL").map_err(|_| ())?;
    let options = PgConnectOptions::from_str(&url).map_err(|_| ())?;
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(5))
        .connect_with(options.statement_cache_capacity(0).extra_float_digits(None))
        .await
        .map_err(|_| ())?;
    let admission = authorize_finite_issuance_candidate(&pool, &fence, &request)
        .await
        .map_err(|_| ())?;
    let material = channel.issue_once(&admission).map_err(|_| ())?;
    if material.grant_id() != request.grant_id
        || material.expires_at() != admission.expires_at()
        || channel.issue_once(&admission).is_ok()
    {
        return Err(());
    }
    pool.close().await;
    Ok(())
}
#[tokio::main]
async fn main() {
    let result = tokio::time::timeout(Duration::from_secs(8), run()).await;
    println!(
        "{}",
        if matches!(result, Ok(Ok(()))) {
            "issuer_material_observed"
        } else {
            "rejected"
        }
    );
}
