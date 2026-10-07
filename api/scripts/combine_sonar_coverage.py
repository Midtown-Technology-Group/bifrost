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
    collector.combine(data_paths=[str(RUNTIME_DIR)], strict=True, keep=True)
    collector.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
