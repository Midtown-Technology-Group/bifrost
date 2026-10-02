"""Exercise release version decisions through their offline Git/CLI seam."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "script", ["test-next-version.sh", "test-next-release-version.sh"]
)
def test_release_version_decision_seams(script: str) -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
