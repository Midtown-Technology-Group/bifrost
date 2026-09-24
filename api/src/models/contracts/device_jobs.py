"""Pydantic contracts for the agent-facing device job protocol (M2.2 #834).

Shapes follow the M0 freeze (docs/architecture/device-control-plane.md and
docs/architecture/device-control-plane/*.schema.json). User-side job
contracts (create/list) land with #836 in this same module.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DeviceHeartbeatRequest(BaseModel):
    """POST /api/device/heartbeat body (M0: session-gated activity renewal)."""

    agent_session_id: UUID


class DeviceHeartbeatResponse(BaseModel):
    server_time: datetime
    last_seen_at: datetime | None
    poll_interval_seconds: int
    cancel_requested: bool = False


class DeviceClaimRequest(BaseModel):
    """Claim must bind the job to the agent session that will heartbeat it."""

    agent_session_id: UUID


class DeviceClaimResponse(BaseModel):
    """Frozen claim response (claim-response.schema.json)."""

    job_id: UUID
    script_name: str
    script_content: str
    params: dict | None = None
    timeout_seconds: int
    max_output_bytes: int
    claim_token: UUID
    claimed_at: datetime
    claim_lease_seconds: int = Field(default=60)


class DeviceLogEntry(BaseModel):
    seq: int = Field(..., ge=1)
    stream: Literal["stdout", "stderr"]
    text: str = Field(..., max_length=65536)
    ts: datetime | None = None


class DeviceLogBatch(BaseModel):
    """Frozen log batch (log-batch.schema.json)."""

    claim_token: UUID
    entries: list[DeviceLogEntry] = Field(..., min_length=1, max_length=512)


class DeviceResultRequest(BaseModel):
    """Frozen result payload (job-result.schema.json)."""

    claim_token: UUID
    status: Literal["succeeded", "failed", "timeout", "cancelled"]
    exit_code: int | None = None
    truncated: bool = False
    error: str | None = Field(default=None, max_length=4096)
    output: str | None = Field(default=None, max_length=65536)
    duration_ms: int | None = Field(default=None, ge=0)


class DeviceResultResponse(BaseModel):
    job_id: UUID
    status: str
