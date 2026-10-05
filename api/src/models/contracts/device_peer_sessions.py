"""Public-key-only requests for finite device troubleshooting leases."""

import base64
import ipaddress
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DevicePeerSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    operator_key: str = Field(min_length=44, max_length=44)
    target_key: str = Field(min_length=44, max_length=44)
    destination: str = Field(max_length=64)
    expires: datetime

    @field_validator("operator_key", "target_key")
    @classmethod
    def public_key(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except ValueError:
            raise ValueError("32-byte base64 public key required") from None
        if len(raw) != 32 or not any(raw):
            raise ValueError("32-byte base64 public key required")
        return base64.b64encode(raw).decode("ascii")

    @field_validator("destination")
    @classmethod
    def numeric_destination(cls, value: str) -> str:
        try:
            host, port = value.split(":")
            address = ipaddress.IPv4Address(host)
            number = int(port)
            if address.is_unspecified or address.is_multicast or not 1 <= number <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("numeric IPv4 TCP destination required") from None
        return f"{address}:{number}"

    @field_validator("expires")
    @classmethod
    def absolute_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone-aware absolute expiry required")
        return value.astimezone(timezone.utc)


class DevicePeerSessionPayload(DevicePeerSessionCreate):
    device_id: UUID


class DevicePeerRevokePayload(BaseModel):
    device_id: UUID
    session_job_id: UUID
