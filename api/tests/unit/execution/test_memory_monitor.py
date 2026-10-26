"""
Unit tests for memory_monitor module.
"""

from unittest.mock import mock_open, patch

from src.services.execution.memory_monitor import (
    get_available_memory_mb,
    get_cgroup_memory,
    has_sufficient_memory,
    has_sufficient_memory_cgroup,
)


class TestGetAvailableMemoryMb:
    """Tests for get_available_memory_mb function."""

    def test_reads_mem_available_from_proc_meminfo(self):
        """Should parse MemAvailable from /proc/meminfo correctly."""
        mock_meminfo = """MemTotal:       16384000 kB
MemFree:         1234567 kB
MemAvailable:    8192000 kB
Buffers:          123456 kB
"""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=mock_meminfo)):
                result = get_available_memory_mb()
                # 8192000 kB = 8000 MB
                assert result == 8000

    def test_returns_negative_when_proc_meminfo_missing(self):
        """Should return -1 when /proc/meminfo doesn't exist (macOS)."""
        with patch("pathlib.Path.exists", return_value=False):
            result = get_available_memory_mb()
            assert result == -1

    def test_returns_negative_when_mem_available_not_found(self):
        """Should return -1 when MemAvailable line is missing."""
        mock_meminfo = """MemTotal:       16384000 kB
MemFree:         1234567 kB
Buffers:          123456 kB
"""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=mock_meminfo)):
                result = get_available_memory_mb()
                assert result == -1

    def test_handles_read_error_gracefully(self):
        """Should return -1 when file read fails."""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", side_effect=OSError("Permission denied")):
                result = get_available_memory_mb()
                assert result == -1

    def test_handles_malformed_mem_available_line(self):
        """Should return -1 when MemAvailable line is malformed."""
        mock_meminfo = """MemTotal:       16384000 kB
MemAvailable:
Buffers:          123456 kB
"""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=mock_meminfo)):
                result = get_available_memory_mb()
                assert result == -1


class TestHasSufficientMemory:
    """Tests for has_sufficient_memory function."""

    def test_returns_true_when_memory_above_threshold(self):
        """Should return True when available memory exceeds threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_available_memory_mb",
            return_value=500
        ):
            assert has_sufficient_memory(300) is True

    def test_returns_false_when_memory_below_threshold(self):
        """Should return False when available memory is below threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_available_memory_mb",
            return_value=200
        ):
            assert has_sufficient_memory(300) is False

    def test_returns_true_when_memory_equals_threshold(self):
        """Should return True when available memory equals threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_available_memory_mb",
            return_value=300
        ):
            assert has_sufficient_memory(300) is True

    def test_returns_true_when_memory_check_unavailable(self):
        """Should return True when memory check returns -1 (macOS)."""
        with patch(
            "src.services.execution.memory_monitor.get_available_memory_mb",
            return_value=-1
        ):
            # Should not block execution on macOS/dev environments
            assert has_sufficient_memory(300) is True

    def test_uses_default_threshold_of_300mb(self):
        """Should use 300MB as default threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_available_memory_mb",
            return_value=250
        ):
            # 250 < 300 (default threshold)
            assert has_sufficient_memory() is False

        with patch(
            "src.services.execution.memory_monitor.get_available_memory_mb",
            return_value=350
        ):
            # 350 > 300 (default threshold)
            assert has_sufficient_memory() is True


class TestGetCgroupMemory:
    """Tests for cgroup v2 memory reading (working-set semantics)."""

    # Realistic memory.stat excerpt — order intentionally interleaved.
    STAT_DATA = (
        "anon 400000000\n"
        "file 500000000\n"
        "kernel_stack 1048576\n"
        "pagetables 10000000\n"
        "sock 10000000\n"
        "slab 30000000\n"
        "active_anon 0\n"
        "inactive_anon 400000000\n"
        "active_file 100000000\n"
        "inactive_file 400000000\n"
    )

    def test_returns_current_minus_inactive_file_as_working_set(self):
        """Working set retains all charged memory except inactive file cache."""
        with patch("builtins.open", side_effect=[
            mock_open(read_data="950000000\n")(),  # memory.current
            mock_open(read_data=self.STAT_DATA)(),
            mock_open(read_data="1073741824\n")(),   # memory.max = 1GB
        ]), patch("pathlib.Path.exists", return_value=True):
            current, limit = get_cgroup_memory()
            # 950MB total - 400MB inactive file = 550MB working set.
            # anon + active_file would undercount this as 500MB by omitting
            # the other 50MB of charged kernel/socket/page-table memory.
            assert current == 550_000_000
            assert limit == 1073741824

    def test_returns_negative_when_cgroup_files_missing(self):
        """Should return (-1, -1) when cgroup files don't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            current, limit = get_cgroup_memory()
            assert current == -1
            assert limit == -1

    def test_returns_current_with_negative_limit_when_memory_max_is_max(self):
        """Should return (working_set, -1) when memory.max is 'max' (no limit set)."""
        with patch("builtins.open", side_effect=[
            mock_open(read_data="950000000\n")(),
            mock_open(read_data=self.STAT_DATA)(),
            mock_open(read_data="max\n")(),
        ]), patch("pathlib.Path.exists", return_value=True):
            current, limit = get_cgroup_memory()
            assert current == 550_000_000
            assert limit == -1

    def test_handles_read_error_gracefully(self):
        """Should return (-1, -1) on read failure."""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", side_effect=OSError("Permission denied")):
                current, limit = get_cgroup_memory()
                assert current == -1
                assert limit == -1

    def test_returns_negative_when_stat_missing_required_keys(self):
        """Should return (-1, -1) when memory.stat lacks inactive_file."""
        bad_stat = "anon 400000000\nfile 500000000\n"
        with patch("builtins.open", side_effect=[
            mock_open(read_data="950000000\n")(),
            mock_open(read_data=bad_stat)(),
        ]), patch("pathlib.Path.exists", return_value=True):
            current, limit = get_cgroup_memory()
            assert current == -1
            assert limit == -1

    def test_clamps_working_set_when_counters_race(self):
        """A racy inactive-file sample cannot produce negative admission usage."""
        stat_data = "inactive_file 200\n"
        with patch("builtins.open", side_effect=[
            mock_open(read_data="100\n")(),
            mock_open(read_data=stat_data)(),
            mock_open(read_data="1000\n")(),
        ]), patch("pathlib.Path.exists", return_value=True):
            current, limit = get_cgroup_memory()
            assert current == 0
            assert limit == 1000


class TestHasSufficientMemoryCgroup:
    """Tests for cgroup-aware memory pressure check."""

    def test_rejects_when_nonreclaimable_charges_cross_threshold(self):
        """Kernel and other charged memory must contribute to admission."""
        stat_data = (
            "anon 700000000\n"
            "active_file 100000000\n"
            "inactive_file 20000000\n"
            "slab 50000000\n"
            "sock 30000000\n"
        )
        with patch("builtins.open", side_effect=[
            mock_open(read_data="900000000\n")(),
            mock_open(read_data=stat_data)(),
            mock_open(read_data="1000000000\n")(),
        ]), patch("pathlib.Path.exists", return_value=True):
            # 900MB current - 20MB inactive file = 880MB, above the 85% cap.
            # The old anon + active_file approximation reported only 800MB.
            assert has_sufficient_memory_cgroup(threshold=0.85) is False

    def test_returns_true_when_below_threshold(self):
        """Should allow fork when memory usage is below threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_cgroup_memory",
            return_value=(500_000_000, 1_000_000_000),  # 50% usage
        ):
            assert has_sufficient_memory_cgroup(threshold=0.85) is True

    def test_returns_false_when_above_threshold(self):
        """Should reject fork when memory usage exceeds threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_cgroup_memory",
            return_value=(900_000_000, 1_000_000_000),  # 90% usage
        ):
            assert has_sufficient_memory_cgroup(threshold=0.85) is False

    def test_returns_true_when_cgroup_unavailable(self):
        """Should be permissive when cgroup files can't be read."""
        with patch(
            "src.services.execution.memory_monitor.get_cgroup_memory",
            return_value=(-1, -1),
        ):
            assert has_sufficient_memory_cgroup(threshold=0.85) is True

    def test_returns_true_when_limit_unknown_but_current_known(self):
        """Should be permissive when max is unset (current readable, limit=-1)."""
        with patch(
            "src.services.execution.memory_monitor.get_cgroup_memory",
            return_value=(500_000_000, -1),
        ):
            assert has_sufficient_memory_cgroup(threshold=0.85) is True

    def test_returns_true_at_exact_threshold(self):
        """Should allow fork when exactly at threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_cgroup_memory",
            return_value=(850_000_000, 1_000_000_000),  # exactly 85%
        ):
            assert has_sufficient_memory_cgroup(threshold=0.85) is True
