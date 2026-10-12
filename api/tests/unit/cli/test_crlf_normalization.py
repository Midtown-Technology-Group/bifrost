"""Tests for byte-exact file collection in bifrost sync."""

import base64
import pathlib
from unittest.mock import patch, MagicMock

from bifrost.cli import _collect_push_files


def _mock_file_filter(*args, **kwargs):
    """Return a mock PathSpec that never matches (skips nothing)."""
    spec = MagicMock()
    spec.match_file.return_value = False
    return spec


class TestCollectFilesByteExact:
    @patch("bifrost.cli._build_file_filter", side_effect=_mock_file_filter)
    def test_crlf_and_lf_remain_distinct(self, _mock_filter, tmp_path: pathlib.Path):
        text = "hello\nworld\n"
        crlf_dir = tmp_path / "crlf"
        lf_dir = tmp_path / "lf"
        crlf_dir.mkdir()
        lf_dir.mkdir()

        (crlf_dir / "file.txt").write_bytes(text.replace("\n", "\r\n").encode())
        (lf_dir / "file.txt").write_bytes(text.encode())

        crlf_files, _ = _collect_push_files(crlf_dir, "")
        lf_files, _ = _collect_push_files(lf_dir, "")

        assert crlf_files["file.txt"] != lf_files["file.txt"]
        assert base64.b64decode(crlf_files["file.txt"]) == text.replace(
            "\n", "\r\n"
        ).encode()

    @patch("bifrost.cli._build_file_filter", side_effect=_mock_file_filter)
    def test_binary_without_early_nul_is_preserved(self, _mock_filter, tmp_path):
        payload = b"%PDF-byte-exact\r\n" + b"A" * 9000 + b"\x00tail\r\n"
        (tmp_path / "document.pdf").write_bytes(payload)

        files, _ = _collect_push_files(tmp_path, "")

        assert base64.b64decode(files["document.pdf"]) == payload
