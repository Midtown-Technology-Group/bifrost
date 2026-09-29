"""Exact dependency closure and deterministic archive for Live handoff source."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from bifrost.promotion import PromotionBundleError
from bifrost.workspace_impact import analyze_workspace_impact, transitive_distances


class LiveHandoffSourceError(ValueError):
    """The Live source closure cannot be proven as a sealed runtime."""


MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def source_closure(
    source_bytes: dict[str, bytes], entry_paths: set[str]
) -> dict[str, bytes]:
    """Reject any import edge that cannot be proven inside the Live snapshot."""
    try:
        analysis = analyze_workspace_impact(
            {path: raw for path, raw in source_bytes.items() if path.endswith(".py")}
        )
    except PromotionBundleError as exc:
        raise LiveHandoffSourceError("Live source import graph is invalid") from exc
    paths: set[str] = set()
    for entry in entry_paths:
        paths.update(transitive_distances(entry, analysis.edges))
    if not paths.issubset(source_bytes):
        raise LiveHandoffSourceError(
            "Live source import closure contains an ungoverned path"
        )
    for path in sorted(paths):
        if (
            analysis.unresolved_imports.get(path)
            or analysis.ambiguous_references.get(path)
            or path in analysis.dynamic_importers
            or path in analysis.dynamic_reference_importers
        ):
            raise LiveHandoffSourceError(
                f"Live source dependency closure cannot be proven: {path}"
            )
    return {path: source_bytes[path] for path in sorted(paths)}


def source_archive(files: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for path, content in sorted(files.items()):
            item = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = ZIP_STORED
            archive.writestr(item, content)
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise LiveHandoffSourceError("Live source closure is too large")
    return result
