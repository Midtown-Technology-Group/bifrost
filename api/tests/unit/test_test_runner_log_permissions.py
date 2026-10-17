"""Repeated local test commands must replace container-owned runner logs."""

import os
from pathlib import Path
import subprocess


def test_pytest_runner_replaces_non_writable_previous_log(tmp_path):
    candidates = (Path("/app/test.sh"), Path(__file__).resolve().parents[3] / "test.sh")
    script = next(path for path in candidates if path.exists()).read_text()
    start = script.index("run_pytest() {")
    end = script.index("\n# `unit`", start)
    log = tmp_path / "test-runner.log"
    log.write_text("previous run\n")
    log.chmod(0o444)
    shell = """set -euo pipefail
require_stack_up() { :; }
prepare_test_state() { :; }
docker() {
    case "$1" in
        container) return 1 ;;
        compose) echo 'new pytest output' ;;
        rm) return 0 ;;
    esac
}
""" + script[start:end] + "\nrun_pytest tests/unit/example.py\n"
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "LOG_DIR": str(tmp_path),
            "TEST_LOCK_DIR": str(tmp_path),
            "COMPOSE_FILE": "docker-compose.test.yml",
            "COMPOSE_PROJECT_NAME": "log-permission-regression",
            "BIFROST_SKIP_BUILD": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text() == "new pytest output\n"
