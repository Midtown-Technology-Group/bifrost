"""Test-only startup gate: run closed pytest argv after host custody release."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

CUSTODY = Path("/app/reference-custody")
WAIT_SECONDS = 30
CASE = "tests/e2e/platform/agent_reference_cases.py"
UNITS = (
    "tests/unit/test_agent_reference_fixture.py",
    "tests/unit/test_agent_reference_lane.py",
    "tests/unit/test_agent_reference_contract.py",
    "tests/unit/test_agent_reference_model_contract.py",
    "tests/unit/test_agent_reference_observer.py",
    "tests/unit/test_agent_reference_server.py",
)
SUFFIX = ["--durations=25", "--junitxml=/tmp/bifrost/test-results.xml"]
BINDING_FIELDS = {
    "version",
    "source",
    "project",
    "container_name",
    "nonce",
    "argv",
    "image_id",
}


def validate_binding(binding: dict) -> None:
    if (
        set(binding) != BINDING_FIELDS
        or binding["version"] != 1
        or re.fullmatch(r"[a-f0-9]{40}", binding["source"]) is None
        or re.fullmatch(r"bifrost-agent-reference-[a-f0-9]{8}", binding["project"])
        is None
        or binding["container_name"] != f"{binding['project']}-pytest-runner"
        or re.fullmatch(r"[a-f0-9]{32}", binding["nonce"]) is None
        or re.fullmatch(r"sha256:[a-f0-9]{64}", binding["image_id"]) is None
        or binding["argv"]
        not in [
            ["pytest", *UNITS, "-v", *SUFFIX],
            ["pytest", CASE, "-v", *SUFFIX],
        ]
    ):
        raise ValueError("invalid binding")


def validate_release(binding: dict, receipt: dict, hostname: str) -> None:
    validate_binding(binding)
    if (
        set(receipt) != BINDING_FIELDS | {"container_id"}
        or any(receipt[key] != binding[key] for key in BINDING_FIELDS)
        or re.fullmatch(r"[a-f0-9]{64}", receipt["container_id"]) is None
        or hostname != receipt["container_id"][:12]
    ):
        raise ValueError("invalid release")


def await_release(custody: Path, hostname: str) -> list[str]:
    binding = json.loads((custody / "binding.json").read_text())
    validate_binding(binding)
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            receipt = json.loads((custody / "release.json").read_text())
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise ValueError("release missing") from None
            time.sleep(0.1)
            continue
        validate_release(binding, receipt, hostname)
        return binding["argv"]


def main() -> None:
    try:
        if len(sys.argv) != 1:
            raise ValueError("runner arguments forbidden")
        argv = await_release(CUSTODY, os.uname().nodename)
    except (ValueError, KeyError, TypeError, OSError):
        sys.exit("Agent reference custody release failed; pytest did not start.")
    if argv == ["pytest", CASE, "-v", *SUFFIX]:
        import logging

        logging.disable(logging.CRITICAL)
        import pytest

        raise SystemExit(pytest.main(argv[1:]))
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
