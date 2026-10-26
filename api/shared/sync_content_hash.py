"""Byte-exact content hash used by sync/pull/watch comparisons."""

from __future__ import annotations

import hashlib


def normalize_line_endings(data: bytes) -> bytes:
    """Compatibility shim that preserves the exact uploaded bytes."""
    return data


def compute_sync_content_hash(raw_bytes: bytes) -> str:
    """Return the byte-exact MD5 content fingerprint used by CLI sync."""
    return hashlib.md5(raw_bytes).hexdigest()  # NOSONAR - content fingerprint, not crypto
