"""Bounded immutable input for an isolated inline App build.

This captures bytes, not Git authority. Protected producer admission must
independently attest their repository/commit/tree before publication.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType

MAX_INLINE_SOURCE_FILES = 256
MAX_INLINE_SOURCE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class InlineAppSourceSnapshot:
    files: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if not 1 <= len(self.files) <= MAX_INLINE_SOURCE_FILES or "app.yaml" not in self.files:
            raise ValueError("Captured App source requires a bounded complete file map and app.yaml")
        copied: dict[str, bytes] = {}
        for path, content in self.files.items():
            if (not isinstance(path, str) or not path or path.startswith("/")
                    or any(char in path for char in ("\\", ":", "\0"))
                    or any(part in {"", ".", "..", "node_modules", ".git"} for part in path.split("/"))
                    or ".tmp." in path or path in {"_entry.tsx", "__bifrost_tailwind.css"}):
                raise ValueError("Captured App path is unsafe or belongs to generated/editor state")
            if not isinstance(content, bytes):
                raise ValueError("Captured App source must contain immutable bytes")
            copied[path] = content
        if sum(len(content) for content in copied.values()) > MAX_INLINE_SOURCE_BYTES:
            raise ValueError("Captured App source exceeds its byte bound")
        object.__setattr__(self, "files", MappingProxyType(dict(sorted(copied.items()))))

    def hashes(self) -> dict[str, str]:
        return {path: "sha256:" + hashlib.sha256(content).hexdigest()
                for path, content in self.files.items()}

    def materialize(self, root: Path) -> list[str]:
        """Write compiler inputs only; app.yaml is attested but never applied."""
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise ValueError("Captured App source requires an empty private build directory")
        paths = []
        for path, content in self.files.items():
            if path == "app.yaml":
                continue
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            paths.append(path)
        return paths
