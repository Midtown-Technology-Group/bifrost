"""Combine actual Sonar unit and server-process hits; reject absent runtime data."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import coverage

RUNTIME_DIR = Path("/coverage")
DATA_FILE = Path("/bifrost-results/.coverage.sonar")
RC_FILE = Path("/app/.coveragerc.sonar")


def main() -> int:
    if not DATA_FILE.is_file() or DATA_FILE.is_symlink():
        raise ValueError("Missing actual unit/tool coverage data")
    reports = list(RUNTIME_DIR.glob(".coverage.sonar.*"))
    if not reports or any(not p.is_file() or p.is_symlink() for p in reports):
        raise ValueError("Missing or unsafe actual runtime coverage data")
    collector = coverage.Coverage(config_file=str(RC_FILE), data_file=str(DATA_FILE))
    collector.load()
    unit_data = collector.get_data()
    try:
        for report in reports:
            if not report.stat().st_size:
                raise ValueError(f"Empty actual runtime coverage data: {report.name}")
            runtime_data = coverage.CoverageData(basename=str(report))
            runtime_data.read()
            if not runtime_data.measured_files():
                raise ValueError(f"Empty actual runtime coverage data: {report.name}")
            if runtime_data.has_arcs() != unit_data.has_arcs():
                raise ValueError(f"Incompatible unit and runtime branch coverage data: {report.name}")
    except (ValueError, coverage.exceptions.CoverageException) as error:
        # The runtime volume is removed during teardown. Keep the actual input
        # databases in the already-private result directory before failing.
        diagnostics = DATA_FILE.parent / "sonar-runtime-shards"
        diagnostics.mkdir(mode=0o700, exist_ok=False)
        manifest = {"rejected_report": report.name, "error_type": type(error).__name__, "shards": []}
        for report in reports:
            destination = diagnostics / report.name
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                with report.open("rb") as source:
                    shutil.copyfileobj(source, output)
            with destination.open("rb") as retained:
                digest = hashlib.file_digest(retained, "sha256").hexdigest()
            manifest["shards"].append({
                "filename": report.name, "bytes": destination.stat().st_size, "sha256": digest,
            })
        descriptor = os.open(diagnostics / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(manifest, output, indent=2)
            output.write("\n")
        raise
    collector.combine(data_paths=[str(RUNTIME_DIR)], strict=True, keep=True)
    collector.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
