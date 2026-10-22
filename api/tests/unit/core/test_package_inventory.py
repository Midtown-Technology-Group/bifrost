"""Package inventory contracts shared by the API, worker and package manager."""

import json
import subprocess
import sys

import pytest

from src.core import package_inventory


def test_inventory_uses_fresh_current_interpreter_without_caching(monkeypatch):
    calls = []
    payloads = [[{"name": "Alpha", "version": "1.0"}],
                [{"name": "Alpha", "version": "2.0"}]]

    def run(command, **kwargs):
        assert command == [sys.executable, "-m", "pip", "list", "--format=json"]
        assert kwargs == dict(capture_output=True, text=True, timeout=30, check=True)
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payloads[len(calls) - 1]))

    monkeypatch.setattr(package_inventory.subprocess, "run", run)
    assert package_inventory.get_installed_packages() == payloads[0]
    assert package_inventory.get_installed_packages() == payloads[1]


@pytest.mark.parametrize("error", [OSError("pip unavailable"),
                                  subprocess.TimeoutExpired("pip", 30),
                                  subprocess.CalledProcessError(1, "pip"),
                                  ValueError("invalid JSON")])
def test_inventory_failure_is_logged_and_returns_empty(monkeypatch, caplog, error):
    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(package_inventory.subprocess, "run", unavailable)
    assert package_inventory.get_installed_packages() == []
    assert "Failed to read installed package inventory" in caplog.text


def test_worker_registration_uses_shared_inventory():
    from src.services.execution.process_pool import _get_installed_packages

    assert _get_installed_packages is package_inventory.get_installed_packages
