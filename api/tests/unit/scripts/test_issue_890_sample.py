"""The lab sampler reads cgroup counters without Docker's polling overhead."""

from scripts.issue_890_sample import read_usage


def test_read_usage_reads_cpu_and_memory_counters(tmp_path):
    (tmp_path / "cpu.stat").write_text("usage_usec 12345\nuser_usec 12000\n")
    (tmp_path / "memory.current").write_text("45678\n")

    assert read_usage(tmp_path) == (12345, 45678)
