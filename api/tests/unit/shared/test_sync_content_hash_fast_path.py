"""Normalization stays identical for the shared helper and its CLI equivalent."""

from collections.abc import Callable

import pytest

from bifrost.cli import _normalize_line_endings
from shared.sync_content_hash import normalize_line_endings


@pytest.mark.parametrize("normalize", [normalize_line_endings, _normalize_line_endings])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"", b""),
        (b"line\n" * 100_000, b"line\n" * 100_000),
        (b"x" * 100_000 + b"\r\n", b"x" * 100_000 + b"\n"),
        (b"x" * 8191 + b"\0\r\n", b"x" * 8191 + b"\0\r\n"),
        (b"x" * 8192 + b"\0\r\n", b"x" * 8192 + b"\0\n"),
        (b"a\rb\n", b"a\rb\n"),
    ],
    ids=["empty", "lf-only", "late-crlf", "binary-boundary", "late-nul", "lone-cr"],
)
def test_normalization_fast_path_preserves_bytes(
    normalize: Callable[[bytes], bytes], raw: bytes, expected: bytes
) -> None:
    assert normalize(raw) == expected
