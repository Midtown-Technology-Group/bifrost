"""The narrowly scoped, Platform Admin-only MTG bridge-grant contract."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

BridgePermission = Literal["devices.readwrite", "devices.execute", "agents.promote"]


class RoleBridgeGrantsResponse(BaseModel):
    role_id: UUID
    is_builtin: bool
    permissions: list[BridgePermission]
    version: str


class RoleBridgeGrantsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permissions: list[BridgePermission]
    expected_permissions: list[BridgePermission]
    expected_version: str = Field(
        min_length=64, max_length=64, pattern="^[0-9a-f]{64}$"
    )
