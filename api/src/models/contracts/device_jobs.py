"""Pydantic contracts for the agent-facing device job protocol (M2.2 #834).

Shapes follow the M0 freeze (docs/architecture/device-control-plane.md and
docs/architecture/device-control-plane/*.schema.json). User-side job
contracts (create/list) land with #836 in this same module.
"""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# ASCII control chars (C0 + DEL): never valid inside a version string.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class DeviceHeartbeatRequest(BaseModel):
    """POST /api/device/heartbeat body (M0: session-gated activity renewal).

    `agent_version` is an additive observability field (epic #818): when
    absent or empty the stored `devices.agent_version` is left untouched.
    """

    agent_session_id: UUID
    agent_version: str | None = Field(default=None, max_length=64)

    @field_validator("agent_version", mode="before")
    @classmethod
    def _strip_control_chars(cls, value: object) -> object:
        """Strip control characters; empty/whitespace-only means 'absent'."""
        if not isinstance(value, str):
            return value  # let pydantic produce the type error
        cleaned = _CONTROL_CHARS_RE.sub("", value).strip()
        return cleaned or None


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


class DeviceRunningRequest(BaseModel):
    """POST /api/device/jobs/{job_id}/running body.

    Additive route (feedback #1): the agent reports a **real spawn**
    (claimed → running) so the server can distinguish safe pre-spawn
    reclaim from post-spawn `lost`. Same fencing as every agent mutation.
    """

    claim_token: UUID
    agent_session_id: UUID


class DeviceRunningResponse(BaseModel):
    job_id: UUID
    status: str


class DeviceResultResponse(BaseModel):
    job_id: UUID
    status: str


# ---------------------------------------------------------------------------
# Device jobs — user/workspace side (M2.4 #836)
# ---------------------------------------------------------------------------


class DeviceJobCreate(BaseModel):
    """POST /api/devices/{device_id}/jobs body (M0 prose contract)."""

    script_name: str = Field(..., min_length=1, max_length=255)
    # Byte caps (256 KiB script / 32 KiB params) are enforced in the
    # service; pydantic bounds guard the cheap cases.
    script_content: str = Field(..., min_length=1, max_length=1_048_576)
    params: dict | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=900)
    max_output_bytes: int = Field(default=1_048_576, ge=1024, le=16_777_216)
    # Caller-asserted attribution (validated for org scope when present).
    workflow_id: UUID | None = None
    execution_id: UUID | None = None


class DeviceJobPublic(BaseModel):
    """Job list item — never exposes script bodies, params, or fencing
    secrets (claim_token stays agent-only)."""

    model_config = {"from_attributes": True}

    id: UUID
    organization_id: UUID
    device_id: UUID
    status: str
    script_name: str
    timeout_seconds: int
    max_output_bytes: int
    requested_by_user_id: UUID | None
    requested_by_api_key_id: UUID | None
    requested_by_workflow_id: UUID | None
    requested_by_execution_id: UUID | None
    claimed_at: datetime | None
    cancel_requested_at: datetime | None
    exit_code: int | None
    error: str | None
    log_sequence: int
    created_at: datetime
    updated_at: datetime


class DeviceJobDetail(DeviceJobPublic):
    """Full observation view: create-level authz may read body/params/result."""

    script_content: str
    params: dict | None = None
    result: str | None = None


class DeviceJobLogPublic(BaseModel):
    model_config = {"from_attributes": True}

    seq: int
    stream: str
    text: str
    ts: datetime | None = None
