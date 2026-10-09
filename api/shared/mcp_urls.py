"""Validation helpers for outbound MCP HTTP endpoints."""

from urllib.parse import urlsplit


def validate_mcp_http_url(value: str) -> str:
    """Require an absolute HTTP(S) URL for an external MCP server."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("MCP server URLs must be absolute HTTP or HTTPS URLs")
    return value
