"""Retain measured repository Python only; audit runtime-generated filenames.

Workflow tests compile virtual user programs with /app filenames. They are not
repository source and cannot be exported by coverage.py after the test ends.
Never ignore a missing tracked file or broadly suppress coverage parser errors.
"""

import argparse
import hashlib
import json
from pathlib import Path

import coverage


def tracked_python(root: Path, listing: Path) -> tuple[set[str], set[str]]:
    raw = listing.read_bytes()
    if not raw or not raw.endswith(b"\0"):
        raise ValueError("Tracked-file inventory must be a nonempty NUL-delimited list")
    paths = raw.decode("utf-8").split("\0")[:-1]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate tracked-file inventory entry")
    result = set()
    for name in paths:
        path = Path(name)
        if (path.is_absolute() or ".." in path.parts or "\\" in name
                or not name or any(ord(char) < 32 for char in name)):
            raise ValueError("Unsafe tracked-file inventory entry")
        if path.suffix != ".py":
            continue
        target = root / path
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root):
            raise ValueError(f"Missing or unsafe tracked Python source: {name}")
        result.add(name)
    if not result:
        raise ValueError("No tracked Python source")
    return result, set(paths)


def repository_path(filename: str, root: Path, api: Path) -> str | None:
    path = Path(filename)
    if not path.is_absolute():
        raise ValueError("Collection must use absolute coverage filenames")
    # Prefer canonical repository paths, then specific /app aliases, then API.
    for mounted, prefix in (
        (root, ""),
        (api / "doc_renderer_service", "doc_renderer_service"),
        (api / ".claude", ".claude"),
        (api, "api"),
    ):
        if path.is_relative_to(mounted):
            relative = path.relative_to(mounted)
            if ".." in relative.parts:
                raise ValueError("Measured source path traverses outside its mount")
            return (Path(prefix) / relative).as_posix()
    return None


def filter_data(root: Path, api: Path, listing: Path, source: Path, output: Path) -> dict:
    root, api = root.resolve(), api.resolve()
    tracked, all_tracked = tracked_python(root, listing)
    if not source.is_file() or source.is_symlink() or not source.stat().st_size:
        raise ValueError("Coverage data is missing, empty or unsafe")
    if source.resolve() == output.resolve() or output.exists() or output.is_symlink():
        raise ValueError("Filtered output must be a fresh file separate from input")
    measured = coverage.CoverageData(basename=str(source))
    measured.read()
    if not measured.has_arcs() or not measured.measured_files():
        raise ValueError("Nonempty branch coverage is required")
    arcs: dict[str, set[tuple[int, int]]] = {}
    discarded = []
    outside = 0
    for filename in sorted(measured.measured_files()):
        canonical = repository_path(filename, root, api)
        if canonical is None:
            outside += 1
            continue
        if canonical not in tracked:
            if canonical in all_tracked:
                raise ValueError("Measured filename refers to tracked non-Python source")
            discarded.append(canonical)
            continue
        if measured.file_tracer(filename):
            raise ValueError("Custom Python file tracers require separate review")
        target = str(root / canonical)
        arcs.setdefault(target, set()).update(measured.arcs(filename) or [])
    if not arcs:
        raise ValueError("No measured tracked Python source remains")
    filtered = coverage.CoverageData(basename=str(output))
    filtered.add_arcs(arcs)
    # add_arcs does not retain empty arc sets. Keep zero-hit source entries so
    # filtering virtual programs cannot shrink the repository denominator.
    filtered.touch_files(arcs)
    filtered.write()
    return {
        "measured_files": len(measured.measured_files()),
        "retained_canonical_files": len(arcs),
        "discarded_runtime_only_files": sorted(set(discarded)),
        "discarded_outside_source_roots": outside,
        "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "tracked_inventory_sha256": hashlib.sha256(listing.read_bytes()).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--tracked-files", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args(argv)
    result = filter_data(args.repository_root, args.api_root, args.tracked_files,
                         args.input, args.output)
    args.audit.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Retained {result['retained_canonical_files']} measured repository Python files; "
          f"audited {len(result['discarded_runtime_only_files'])} runtime-only filenames.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
