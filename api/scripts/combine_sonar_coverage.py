"""Combine actual Sonar unit and server-process hits; reject absent runtime data."""

from pathlib import Path

import coverage

RUNTIME_DIR = Path("/coverage")
DATA_FILE = Path("/bifrost-results/.coverage.sonar")
RC_FILE = Path("/app/.coveragerc.sonar")


def main() -> int:
    if not DATA_FILE.is_file() or DATA_FILE.is_symlink():
        raise ValueError("Missing actual unit/tool coverage data")
    reports = list(RUNTIME_DIR.glob(".coverage.sonar.*"))
    if not reports or any(not p.is_file() or p.is_symlink() or not p.stat().st_size for p in reports):
        raise ValueError("Missing or unsafe actual runtime coverage data")
    collector = coverage.Coverage(config_file=str(RC_FILE), data_file=str(DATA_FILE))
    collector.load()
    unit_data = collector.get_data()
    for report in reports:
        runtime_data = coverage.CoverageData(basename=str(report))
        runtime_data.read()
        if not runtime_data.measured_files():
            raise ValueError("Empty actual runtime coverage data")
        if runtime_data.has_arcs() != unit_data.has_arcs():
            raise ValueError("Incompatible unit and runtime branch coverage data")
    collector.combine(data_paths=[str(RUNTIME_DIR)], strict=True, keep=True)
    collector.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
