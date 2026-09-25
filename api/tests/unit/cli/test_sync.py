"""Tests for bifrost git commands (formerly sync)."""
from __future__ import annotations

import pytest

from bifrost import git_commands
from bifrost.git_commands import (
    EXIT_CLEAN,
    EXIT_CONFLICTS,
    EXIT_ERROR,
    RESOLUTION_MAP,
    _format_sync_result,
)


class TestFormatSyncResult:
    """Test sync result output formatting."""

    def test_success_no_changes(self):
        """Should report no changes on success with zero counts."""
        result = {"status": "success", "pulled": 0, "pushed_commits": 0, "commit_sha": None}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "no changes" in text.lower()

    def test_success_with_pulled_and_pushed(self):
        """Should summarize pull/push counts on success."""
        result = {
            "status": "success",
            "pulled": 3,
            "pushed_commits": 1,
            "commit_sha": "abc1234def5678",
        }
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "pulled 3" in text
        assert "pushed 1" in text
        assert "abc1234" in text

    def test_success_completed_status(self):
        """Should also accept 'completed' as a success status."""
        result = {"status": "completed", "pulled": 1, "pushed_commits": 0, "commit_sha": None}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "Sync complete" in text

    def test_conflicts_shown(self):
        """Should list each conflict with path and resolve command."""
        result = {
            "status": "conflict",
            "conflicts": [
                {
                    "path": "workflows/billing.py",
                    "display_name": "billing",
                    "entity_type": "workflow",
                },
            ],
        }
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "workflows/billing.py" in text
        assert "bifrost git resolve" in text
        assert "keep_remote" in text
        assert "keep_local" in text

    def test_conflict_resolution_commands_quote_paths(self):
        """Suggested resolve commands must be safe to copy into a shell."""
        result = {
            "status": "conflict",
            "conflicts": [
                {
                    "path": "workflows/billing.py; echo owned",
                    "display_name": "billing",
                    "entity_type": "workflow",
                },
            ],
        }

        lines = _format_sync_result(result)
        text = "\n".join(lines)

        assert "bifrost git resolve 'workflows/billing.py; echo owned=keep_remote'" in text
        assert "bifrost git resolve workflows/billing.py; echo owned=keep_remote" not in text

    def test_multiple_conflicts(self):
        """Should list all conflicts."""
        result = {
            "status": "conflict",
            "conflicts": [
                {"path": "workflows/a.py", "display_name": "a", "entity_type": "workflow"},
                {"path": "workflows/b.py", "display_name": "b", "entity_type": "workflow"},
            ],
        }
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "2 conflicts" in text
        assert "workflows/a.py" in text
        assert "workflows/b.py" in text

    def test_failed_with_error(self):
        """Should show error message on failure."""
        result = {"status": "failed", "error": "Authentication failed"}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "Authentication failed" in text
        assert "failed" in text.lower()

    def test_failed_unknown_error(self):
        """Should show fallback message when no error provided."""
        result = {"status": "failed"}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "Unknown error" in text


class TestResolutionMap:
    """Test CLI-to-API resolution mapping."""

    def test_keep_local_maps_to_ours(self):
        assert RESOLUTION_MAP["keep_local"] == "ours"

    def test_keep_remote_maps_to_theirs(self):
        assert RESOLUTION_MAP["keep_remote"] == "theirs"


class TestFormatHelpers:
    def test_format_changed_files_prints_symbols(self, capsys: pytest.CaptureFixture[str]):
        git_commands._format_changed_files(
            {
                "changed_files": [
                    {"change_type": "added", "path": "new.py"},
                    {"change_type": "modified", "path": "changed.py"},
                    {"change_type": "deleted", "path": "old.py"},
                    {"change_type": "renamed", "path": "move.py"},
                    {"change_type": "other", "path": "mystery.py"},
                    {},
                ]
            }
        )

        output = capsys.readouterr().out
        assert "6 changed file(s)" in output
        assert "+ new.py" in output
        assert "~ changed.py" in output
        assert "- old.py" in output
        assert "R move.py" in output
        assert "? mystery.py" in output
        assert "? unknown" in output

    def test_format_changed_files_prints_empty_message(
        self, capsys: pytest.CaptureFixture[str]
    ):
        git_commands._format_changed_files({"changed_files": []})

        assert "No changed files" in capsys.readouterr().out

    def test_format_ahead_behind_prints_counts(self, capsys: pytest.CaptureFixture[str]):
        git_commands._format_ahead_behind({"commits_ahead": 2, "commits_behind": 3})

        assert "2 ahead, 3 behind" in capsys.readouterr().out

    def test_format_sync_result_entity_changes_from_data(self):
        lines = _format_sync_result(
            {
                "status": "success",
                "data": {
                    "entity_changes": [
                        {"action": "added", "entity_type": "workflow", "name": "A"},
                        {
                            "action": "updated",
                            "entity_type": "form",
                            "name": "B",
                            "reason": "changed",
                        },
                        {"action": "removed", "entity_type": "table", "name": "C"},
                        {"action": "unknown", "entity_type": "app", "name": "D"},
                    ]
                },
            }
        )

        text = "\n".join(lines)
        assert "4 entity change(s): 1 added, 1 updated, 1 removed" in text
        assert "+ workflow" in text
        assert "~ form" in text
        assert "(changed)" in text
        assert "- table" in text
        assert "? app" in text

    def test_format_sync_result_conflict_defaults_unknown_values(self):
        lines = _format_sync_result({"status": "conflict", "conflicts": [{}]})

        text = "\n".join(lines)
        assert "unknown (file: unknown)" in text
        assert "unknown=keep_remote" in text

    def test_format_sync_result_failed_uses_message_fallback(self):
        lines = _format_sync_result({"status": "failed", "message": "no token"})

        assert lines == ["Sync failed: no token"]
