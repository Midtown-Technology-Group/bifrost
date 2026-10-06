"""Compiled V2 App evidence retained by the existing Solution deploy path."""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from uuid import UUID

from bifrost.workspace_release import canonical_digest

from src.core.solution_delivery_policy import delivery_path

SCHEMA = "bifrost.solution-app-runtime-pin/v1"


def compiled_index_assets(html: str) -> tuple[str | None, str | None]:
    """Use the same entry/CSS references for serving and accounting."""

    class IndexAssetParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.entry: str | None = None
            self.css: str | None = None

        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            values = dict(attrs)
            if tag == "script" and values.get("type") == "module" and values.get("src"):
                self.entry = values["src"]
            if (
                tag == "link"
                and values.get("rel") == "stylesheet"
                and values.get("href")
            ):
                self.css = values["href"]

    parser = IndexAssetParser()
    parser.feed(html)
    # Vite's base="./" emits ./assets/...; the browser resolves that to the
    # same deployment-relative object as assets/.... Preserve that identity
    # when comparing against the compiled output inventory.
    entry = (
        parser.entry.split("/dist/")[-1].lstrip("/").removeprefix("./")
        if parser.entry
        else None
    )
    css = (
        parser.css.split("/dist/")[-1].lstrip("/").removeprefix("./")
        if parser.css
        else None
    )
    return entry, css


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
    entry, css = compiled_index_assets(outputs["index.html"].decode("utf-8"))
    if (
        entry is not None
        and entry not in outputs
        or css is not None
        and css not in outputs
    ):
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
