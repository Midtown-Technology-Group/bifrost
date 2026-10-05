"""Filter fixed CI coverage artifacts without accepting filesystem paths on CLI.

Runtime-generated programs are audited separately from repository Python. A
missing tracked source or a parser error must never be silently ignored.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

import coverage


REPOSITORY_ROOT = Path("/repo")
API_ROOT = Path("/app")
ARTIFACT_ROOT = Path("/tmp/bifrost")
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
INVENTORY_NAME = ".sonar-evidence/raw/tracked-files.nul"
INPUT_NAME = ".coverage.sonar"
OUTPUT_NAME = ".coverage.sonar-tracked"
AUDIT_NAME = "sonar-runtime-coverage.json"
# Jinja compiles this authored template using its filename. Generated Python
# lines are not template-source coverage. The template stays in source inventory
# and .j2 changes require human review; other tracked non-Python records fail.
COMPILED_TEMPLATE_PATHS = frozenset({"api/src/services/templates/sdk.py.j2"})


def relative_parts(name: str) -> tuple[str, ...]:
    path = Path(name)
    if (path.is_absolute() or ".." in path.parts or "\\" in name
            or not name or any(ord(char) < 32 for char in name)):
        raise ValueError("Unsafe repository-relative artifact or inventory path")
    return path.parts


def open_bounded(root: Path, name: str, flags: int, mode: int = 0o600) -> int:
    """Reject symlinks at/below a fixed root; its OS ancestors are trusted."""
    parts = relative_parts(name)
    if not parts:
        raise ValueError("An artifact filename is required")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                       mode, dir_fd=directory)
    finally:
        os.close(directory)


def read_bounded(root: Path, name: str) -> bytes:
    descriptor = open_bounded(root, name, os.O_RDONLY)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARTIFACT_BYTES:
            raise ValueError("Artifact must be a nonempty, bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(raw) != info.st_size:
            raise ValueError("Artifact size changed while reading")
        return raw
    finally:
        os.close(descriptor)


def write_fresh(root: Path, name: str, raw: bytes, mode: int = 0o600) -> None:
    with os.fdopen(open_bounded(root, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "wb") as stream:
        # Published reports cross the container/host UID boundary. Set the mode
        # on this newly-created descriptor rather than relying on process umask.
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)


def tracked_python(root: Path, raw: bytes) -> tuple[set[str], set[str]]:
    if not raw or not raw.endswith(b"\0"):
        raise ValueError("Tracked-file inventory must be a nonempty NUL-delimited list")
    paths = raw.decode("utf-8").split("\0")[:-1]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate tracked-file inventory entry")
    result = set()
    for name in paths:
        relative_parts(name)
        if Path(name).suffix == ".py":
            target = root / name
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


def compiled_template_hash(root: Path, canonical: str, tracer: str | None) -> str:
    if canonical not in COMPILED_TEMPLATE_PATHS:
        raise ValueError("Measured filename refers to tracked non-Python source")
    if tracer:
        raise ValueError("Custom template file tracers require separate review")
    return hashlib.sha256(read_bounded(root, canonical)).hexdigest()


def filter_data(root: Path, api: Path, inventory: bytes,
                measured: coverage.CoverageData) -> tuple[dict, dict]:
    tracked, all_tracked = tracked_python(root, inventory)
    if not measured.has_arcs() or not measured.measured_files():
        raise ValueError("Nonempty branch coverage is required")
    arcs: dict[str, set[tuple[int, int]]] = {}
    discarded, templates, outside = [], {}, 0
    for filename in sorted(measured.measured_files()):
        canonical = repository_path(filename, root, api)
        if canonical is None:
            outside += 1
        elif canonical in tracked:
            if measured.file_tracer(filename):
                raise ValueError("Custom Python file tracers require separate review")
            arcs.setdefault(str(root / canonical), set()).update(measured.arcs(filename) or [])
        elif canonical in all_tracked:
            templates[canonical] = compiled_template_hash(root, canonical, measured.file_tracer(filename))
        else:
            discarded.append(canonical)
    if not arcs:
        raise ValueError("No measured tracked Python source remains")
    return arcs, {
        "measured_files": len(measured.measured_files()),
        "retained_canonical_files": len(arcs),
        "discarded_runtime_only_files": sorted(set(discarded)),
        "discarded_compiled_template_source_sha256": templates,
        "discarded_outside_source_roots": outside,
    }


def convert_database(root: Path, api: Path, inventory: bytes, database: bytes) -> tuple[bytes, dict]:
    # CoverageData.loads accepts dumps() serialization, not raw SQLite files.
    # Work only on snapshots in a private directory, never a caller-selected DB.
    with tempfile.TemporaryDirectory(prefix="sonar-coverage-") as directory:
        stage = Path(directory)
        write_fresh(stage, "input", database)
        measured = coverage.CoverageData(basename=str(stage / "input"))
        measured.read()
        arcs, audit = filter_data(root, api, inventory, measured)
        filtered = coverage.CoverageData(basename=str(stage / "output"))
        filtered.add_arcs(arcs)
        # add_arcs drops empty sets. Preserve every zero-hit source record.
        filtered.touch_files(arcs)
        filtered.write()
        return read_bounded(stage, "output"), audit


def run_fixed_layout(root: Path, api: Path, artifacts: Path) -> dict:
    """Library seam for fixtures; production roots are fixed constants below."""
    inventory = read_bounded(root, INVENTORY_NAME)
    database = read_bounded(artifacts, INPUT_NAME)
    output, audit = convert_database(root, api, inventory, database)
    audit.update(input_sha256=hashlib.sha256(database).hexdigest(),
                 output_sha256=hashlib.sha256(output).hexdigest(),
                 tracked_inventory_sha256=hashlib.sha256(inventory).hexdigest())
    write_fresh(artifacts, OUTPUT_NAME, output, mode=0o644)
    write_fresh(artifacts, AUDIT_NAME, (json.dumps(audit, indent=2, sort_keys=True) + "\n").encode(), mode=0o644)
    return audit


def main(argv: list[str] | None = None) -> int:
    # No path arguments or environment overrides: LLM/caller-supplied arguments
    # cannot redirect the production inventory, database, output, or audit I/O.
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    result = run_fixed_layout(REPOSITORY_ROOT, API_ROOT, ARTIFACT_ROOT)
    print(f"Retained {result['retained_canonical_files']} measured repository Python files; "
          f"audited {len(result['discarded_runtime_only_files'])} runtime-only filenames and "
          f"{len(result['discarded_compiled_template_source_sha256'])} compiled templates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
