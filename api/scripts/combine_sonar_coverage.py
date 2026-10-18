"""Combine actual Sonar unit and server-process hits; reject absent runtime data."""

import json
from pathlib import Path
import shutil

import coverage

RUNTIME_DIR = Path("/coverage")
DATA_FILE = Path("/bifrost-results/.coverage.sonar")
RC_FILE = Path("/app/.coveragerc.sonar")
AUDIT_FILE = Path("/bifrost-results/sonar-runtime-reports.json")


def main() -> int:
    if not DATA_FILE.is_file() or DATA_FILE.is_symlink():
        raise ValueError("Missing actual unit/tool coverage data")
    reports = list(RUNTIME_DIR.glob(".coverage.sonar.*"))
    if not reports or any(not p.is_file() or p.is_symlink() or not p.stat().st_size for p in reports):
        raise ValueError("Missing or unsafe actual runtime coverage data")
    collector = coverage.Coverage(config_file=str(RC_FILE), data_file=str(DATA_FILE))
    collector.load()
    unit_data = collector.get_data()
    audit = []
    empty = []
    incompatible = []
    for report in sorted(reports):
        runtime_data = coverage.CoverageData(basename=str(report))
        runtime_data.read()
        measured = len(runtime_data.measured_files())
        audit.append({"file": report.name, "bytes": report.stat().st_size,
                      "measured_files": measured, "has_arcs": runtime_data.has_arcs()})
        if not measured:
            empty.append(report.name)
        if runtime_data.has_arcs() != unit_data.has_arcs():
            incompatible.append(report.name)
    if AUDIT_FILE.is_symlink():
        raise ValueError("Unsafe runtime coverage audit path")
    AUDIT_FILE.write_text(json.dumps({"version": 1, "reports": audit}, indent=2) + "\n")
    if empty or incompatible:
        archive = AUDIT_FILE.parent / "sonar-runtime-data"
        if archive.is_symlink():
            raise ValueError("Unsafe runtime coverage archive path")
        archive.mkdir(exist_ok=True)
        for report in reports:
            destination = archive / report.name
            if destination.is_symlink():
                raise ValueError("Unsafe runtime coverage archive file")
            shutil.copyfile(report, destination)
    if empty:
        raise ValueError(f"Empty actual runtime coverage data: {empty!r}")
    if incompatible:
        raise ValueError(f"Incompatible unit and runtime branch coverage data: {incompatible!r}")
    collector.combine(data_paths=[str(RUNTIME_DIR)], strict=True, keep=True)
    collector.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
