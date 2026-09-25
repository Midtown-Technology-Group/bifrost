"""The lab sampler reads cgroup counters without Docker's polling overhead."""

import sys

import pytest
from scripts.issue_890_sample import main, read_usage


def test_read_usage_reads_cpu_and_memory_counters(tmp_path):
    (tmp_path / "cpu.stat").write_text("usage_usec 12345\nuser_usec 12000\n")
    (tmp_path / "memory.current").write_text("45678\n")

    assert read_usage(tmp_path) == (12345, 45678)


def test_sampler_rejects_project_option_injection(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["issue_890_sample.py", "--project=--evil"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
