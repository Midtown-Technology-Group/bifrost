"""The shared compatibility helper preserves exact bytes."""

import pytest

from shared.sync_content_hash import normalize_line_endings


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"line\n" * 100_000,
        b"x" * 100_000 + b"\r\n",
        b"x" * 8191 + b"\0\r\n",
        b"x" * 8192 + b"\0\r\n",
        b"a\rb\n",
    ],
    ids=["empty", "lf-only", "late-crlf", "binary-boundary", "late-nul", "lone-cr"],
)
def test_normalization_compatibility_shim_preserves_bytes(raw: bytes) -> None:
    assert normalize_line_endings(raw) == raw
