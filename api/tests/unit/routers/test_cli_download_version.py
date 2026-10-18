"""CLI build-time artifact generation."""

import os
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

from shared.cli_artifact import build_cli_artifact, cli_artifact_filename, to_pep440


def test_to_pep440_preserves_dev_release_versions() -> None:
    assert to_pep440("1.0.8-dev.11") == "1.0.8.dev11"
    assert to_pep440("v1.0.8-dev.11") == "1.0.8.dev11"


def test_to_pep440_preserves_dirty_dev_release_versions() -> None:
    assert to_pep440("1.0.8-dev.11-dirty") == "1.0.8.dev11+dirty"


def test_build_cli_artifact_stamps_and_filters_package(tmp_path: Path) -> None:
    source_dir = Path(__file__).resolve().parents[3] / "bifrost"

    artifact = build_cli_artifact(source_dir, tmp_path, "v1.2.3")

    assert artifact.name == cli_artifact_filename("v1.2.3")
    with tarfile.open(artifact, mode="r:gz") as archive:
        names = archive.getnames()
        pyproject = archive.extractfile("pyproject.toml")
        init = archive.extractfile("bifrost/__init__.py")
        assert pyproject is not None
        assert init is not None
        pyproject_text = pyproject.read().decode()
        assert 'version = "1.2.3"' in pyproject_text
        metadata = tomllib.loads(pyproject_text)
        assert any(
            requirement.startswith("packaging")
            for requirement in metadata["project"]["dependencies"]
        )
        assert any(
            requirement.startswith("psutil>=6.0.0")
            for requirement in metadata["project"]["dependencies"]
        )
        assert '__version__ = "v1.2.3"' in init.read().decode()

    assert "bifrost/lucide_icon_names.json" in names
    assert "_bifrost_workspace_effects.py" in names
    assert "bifrost/_write_buffer.py" not in names
    assert "bifrost/_logging.py" not in names


def test_build_cli_artifact_is_deterministic(tmp_path: Path) -> None:
    source_dir = Path(__file__).resolve().parents[3] / "bifrost"
    first = build_cli_artifact(source_dir, tmp_path / "first", "debug")
    second = build_cli_artifact(source_dir, tmp_path / "second", "debug")

    assert first.read_bytes() == second.read_bytes()


def test_built_cli_artifact_can_verify_vite_listener_ownership(
    tmp_path: Path,
) -> None:
    source_dir = Path(__file__).resolve().parents[3] / "bifrost"
    artifact = build_cli_artifact(source_dir, tmp_path / "artifacts", "v1.2.3")
    artifact_root = tmp_path / "artifact-root"
    with tarfile.open(artifact, mode="r:gz") as archive:
        archive.extractall(artifact_root, filter="data")

    smoke_test = r"""
import os
import socket
import sys

sys.path.insert(0, sys.argv[1])
from bifrost.commands.solution import _vite_process_owns_listener


class CurrentProcess:
    pid = os.getpid()


listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(1)
try:
    assert _vite_process_owns_listener(
        CurrentProcess(), listener.getsockname()[1]
    )
finally:
    listener.close()
"""
    smoke = subprocess.run(
        [sys.executable, "-I", "-c", smoke_test, str(artifact_root)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert smoke.returncode == 0, smoke.stderr
