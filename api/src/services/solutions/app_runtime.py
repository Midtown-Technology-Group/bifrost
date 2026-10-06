"""Compiled V2 App evidence retained by the existing Solution deploy path."""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from uuid import UUID
from urllib.parse import unquote, urlsplit, urlunsplit

from bifrost.workspace_release import canonical_digest

from src.core.solution_delivery_policy import delivery_path

SCHEMA = "bifrost.solution-app-runtime-pin/v1"


class _IndexAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.entries: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        source, href = values.get("src"), values.get("href")
        if tag == "script" and values.get("type") == "module" and source:
            self.entries.append(source)
        if tag == "link" and values.get("rel") == "stylesheet" and href:
            self.stylesheets.append(href)


def _compiled_index_references(html: str) -> tuple[list[str], list[str]]:
    parser = _IndexAssetParser()
    parser.feed(html)

    def normalize(reference: str) -> str:
        parsed = urlsplit(reference)
        # Only the pathname carries a serving prefix. Query/fragment contents
        # may themselves contain /dist/ and must retain their URL semantics.
        if (parsed.scheme or parsed.netloc) and "/dist/" not in parsed.path:
            return reference
        path = parsed.path.split("/dist/")[-1].lstrip("/").removeprefix("./")
        return urlunsplit(("", "", path, parsed.query, parsed.fragment))

    return (
        [normalize(ref) for ref in parser.entries],
        [normalize(ref) for ref in parser.stylesheets],
    )


def compiled_index_assets(html: str) -> tuple[str | None, str | None]:
    """Keep the loader's last entry/CSS selection and complete URL semantics."""
    entries, stylesheets = _compiled_index_references(html)
    return (entries[-1] if entries else None, stylesheets[-1] if stylesheets else None)


def source_archive_sha256(source: bytes | Path) -> str:
    if isinstance(source, bytes):
        return hashlib.sha256(source).hexdigest()
    with source.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compiled_app_runtime_pin(
    *,
    solution_id: UUID,
    application_id: UUID,
    deployment_id: UUID,
    source_sha256: str,
    outputs: dict[str, bytes],
    source_built: bool,
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
        raise ValueError("App build requires the exact source archive digest")
    if "index.html" not in outputs:
        raise ValueError("Compiled App is missing index.html")
    entries, stylesheets = _compiled_index_references(
        outputs["index.html"].decode("utf-8")
    )
    # Prove every index reference, including tags the legacy loader does not
    # select. Otherwise an earlier missing asset can hide behind the last tag.
    for reference in (*entries, *stylesheets):
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc or unquote(parsed.path) not in outputs:
            raise ValueError("Compiled App entry or CSS dependency is missing")
    for path in outputs:
        delivery_path(path)
    proof = {
        "schema_version": SCHEMA,
        "solution_id": str(solution_id),
        "application_id": str(application_id),
        "deployment_id": str(deployment_id),
        "source_artifact_sha256": source_sha256,
        "build_mode": "source" if source_built else "prebuilt",
        "output_hashes": {
            path: hashlib.sha256(raw).hexdigest()
            for path, raw in sorted(outputs.items())
        },
    }
    proof["runtime_pin_hash"] = canonical_digest(proof)
    return proof


def verify_compiled_app_runtime_pin(
    proof: Any,
    *,
    solution_id: UUID,
    application_id: UUID,
    deployment_id: UUID,
    source_sha256: str,
    outputs: dict[str, bytes],
    source_built: bool,
) -> dict[str, Any]:
    expected = compiled_app_runtime_pin(
        solution_id=solution_id,
        application_id=application_id,
        deployment_id=deployment_id,
        source_sha256=source_sha256,
        outputs=outputs,
        source_built=source_built,
    )
    if proof != expected:
        raise ValueError(
            "Active compiled App differs from the reviewed source/runtime pin"
        )
    return expected
