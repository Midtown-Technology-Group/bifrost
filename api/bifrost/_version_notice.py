"""Dedupe helper for the soft 'update available' notice (Gate 2).

When the CLI is contract-compatible but on a different build version than the
server, we show a one-line nudge — but only once per (API URL, server version),
so it doesn't fire on every command.

The marker lives in Bifrost's per-user cache directory, keyed by a STABLE digest
of the URL. We deliberately avoid Python's built-in ``hash()`` here: it is
per-process randomized (``PYTHONHASHSEED``), so two CLI invocations would key to
different files and dedupe would never work. Writes use a private temporary file
and atomic replacement so even a pre-existing symlink is replaced rather than
followed.

All I/O is best-effort: a permission/collision/stale-marker problem can only
cause a missed-or-extra notice, never a wrong block — callers must keep this
isolated from the hard-gate decision.
"""

from __future__ import annotations

import tempfile
import zlib
from pathlib import Path

from platformdirs import user_cache_path


def _marker_path(api_url: str) -> Path:
    # Derive a stable, filesystem-safe per-URL key for the marker filename. We use
    # a non-cryptographic checksum (CRC32) deliberately: this is a dedupe key, not
    # security, and crypto hashes here trip CodeQL's sensitive-data-hashing taint
    # rule (the URL is tainted via credentials._resolve_url). CRC32 is fixed-length,
    # collision-resistant enough to distinguish API URLs, and unambiguously not a
    # security primitive.
    key = format(zlib.crc32(api_url.rstrip("/").encode()) & 0xFFFFFFFF, "08x")
    return user_cache_path("bifrost") / f"bifrost-vnotice-{key}"


def should_notify(api_url: str, server_version: str) -> bool:
    """True if we haven't already shown a notice for this (url, version)."""
    try:
        marker = _marker_path(api_url)
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == server_version:
            return False
    except OSError:
        # Can't read the marker — fall through and notify (extra notice at worst).
        return True
    return True


def mark_notified(api_url: str, server_version: str) -> None:
    """Record that the notice for (url, version) has been shown."""
    temporary_marker: Path | None = None
    try:
        marker = _marker_path(api_url)
        marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=marker.parent,
            prefix=f".{marker.name}.",
            delete=False,
        ) as handle:
            temporary_marker = Path(handle.name)
            handle.write(server_version)
        temporary_marker.replace(marker)
    except OSError:
        pass  # best-effort: failing to record just means the notice may re-show
    finally:
        if temporary_marker is not None:
            try:
                temporary_marker.unlink(missing_ok=True)
            except OSError:
                pass
