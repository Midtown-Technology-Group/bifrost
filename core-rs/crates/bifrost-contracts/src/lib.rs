//! Internal operational contracts. Public device DTOs remain Python-owned in W0.

use serde::Serialize;

pub mod runtime;

#[derive(Serialize)]
pub struct HealthResponse {
    pub status: &'static str,
}
