//! Internal operational contracts. Public device DTOs remain Python-owned in W0.

use serde::Serialize;

#[derive(Serialize)]
pub struct HealthResponse {
    pub status: &'static str,
}
