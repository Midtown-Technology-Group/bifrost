"""Immutable grants for existing Root data used by reviewed Solution workflows."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BACKUP_PREFIX = ".artifacts/ninjaone-webhook-config-repairs"
MAX_ROOT_READ_BYTES = 128 * 1024 * 1024
MAX_ROOT_DOWNLOAD_BYTES = 256 * 1024 * 1024
RootFileOperation = Literal["exists", "read", "signed_get", "create"]


def root_data_path(value: str) -> str:
    """Require the exact canonical data path; never normalize an unsafe request."""
    if (
        not value or len(value) > 1000 or value != value.strip()
        or "\\" in value or "%" in value or "\x00" in value
        or value.startswith("/") or ":" in value
        or any(ord(char) < 32 for char in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Root data path must be canonical and relative")
    return value


class RootFileBinding(BaseModel):
    """An explicit data grant; uploads inherit only the durable execution scope."""
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    location: Literal["workspace", "uploads"]
    path: str
    directory_prefix: bool = False
    operations: tuple[RootFileOperation, ...] = Field(min_length=1, max_length=4)
    max_bytes: int = Field(gt=0, le=MAX_ROOT_DOWNLOAD_BYTES)
    max_url_ttl_seconds: int = Field(default=600, gt=0, le=600)
    expected_read_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("operations", mode="before")
    @classmethod
    def immutable_operations(cls, value):
        if not isinstance(value, list | tuple):
            raise ValueError("Root operations must be an explicit sequence")
        return tuple(value)

    @model_validator(mode="after")
    def require_data_grant(self) -> RootFileBinding:
        root_data_path(self.path)
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("Root file operations must be unique")
        if self.max_bytes > MAX_ROOT_READ_BYTES and (
            "signed_get" not in self.operations
            or not set(self.operations) <= {"exists", "signed_get"}
        ):
            raise ValueError("Large Root objects require a bounded download-only grant")
        if any(part.startswith(".") for part in self.path.split("/")):
            if self.path != BACKUP_PREFIX or self.operations != ("create",):
                raise ValueError("Hidden Root paths are unavailable")
        if "create" in self.operations:
            if (
                self.location != "workspace" or self.path != BACKUP_PREFIX
                or not self.directory_prefix or self.operations != ("create",)
                or self.max_bytes > 64 * 1024 or self.expected_read_sha256 is not None
            ):
                raise ValueError("Root backup grants require bounded create-only JSON")
        elif self.location == "workspace":
            if self.directory_prefix or not self.path.startswith("features/"):
                raise ValueError("Root workspace reads require an exact feature data path")
            if not self.path.endswith((".ps1", ".xml", ".json", ".zip", ".msi", ".exe")):
                raise ValueError("Root workspace bindings cannot grant source or authentication files")
            if (
                set(self.operations) & {"read", "signed_get"} and not self.path.endswith(".json")
                and self.expected_read_sha256 is None
            ):
                raise ValueError("Root workspace asset byte access requires an exact reviewed hash")
        if self.expected_read_sha256 is not None and not set(self.operations) & {"read", "signed_get"}:
            raise ValueError("Root content hashes require a read or download grant")
        if not re.fullmatch(r"[A-Za-z0-9_./ -]+", self.path):
            raise ValueError("Root data selector contains unsupported characters")
        return self

    def permits(self, path: str, operation: RootFileOperation) -> bool:
        root_data_path(path)
        if operation not in self.operations:
            return False
        if self.directory_prefix:
            if not path.startswith(self.path + "/"):
                return False
        elif path != self.path:
            return False
        if operation == "create":
            filename = path.removeprefix(self.path + "/")
            return bool(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12,32}\.json", filename))
        return not any(part.startswith(".") for part in path.split("/"))


def require_root_file_bindings(bindings: dict[str, RootFileBinding]) -> None:
    """Reject duplicate selectors and ambiguous grants before creating artifacts."""
    selectors = set()
    for name, binding in bindings.items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,99}", name):
            raise ValueError("Root file binding names must be canonical")
        selector = (binding.location, binding.path, binding.directory_prefix)
        if selector in selectors:
            raise ValueError("Root file selectors must be unique")
        selectors.add(selector)
        for other in bindings.values():
            if other is binding or other.location != binding.location:
                continue
            if binding.directory_prefix and other.path.startswith(binding.path + "/"):
                raise ValueError("Root file selectors must not overlap")
