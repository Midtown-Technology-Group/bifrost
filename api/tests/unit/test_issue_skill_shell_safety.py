from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_PATHS = (
    REPO_ROOT / ".claude/skills/bifrost-issues/SKILL.md",
    REPO_ROOT / ".agents/skills/bifrost-issues/SKILL.md",
)


def test_issue_skills_keep_untrusted_text_out_of_shell_source() -> None:
    for path in SKILL_PATHS:
        content = path.read_text(encoding="utf-8")

        assert "fixed-delimiter heredoc" in content
        assert 'gh issue list --search "$(<"$SEARCH_FILE")"' in content
        assert '--title "$(<"$TITLE_FILE")"' in content
        assert '--body-file "$BODY_FILE"' in content
        assert "<<'EOF'" not in content
        assert 'search_terms="<2-3 key terms>"' not in content
        assert 'gh issue create --title "..." --body "..."' not in content
        assert '--title "[bug]: <summary>"' not in content


def test_agent_issue_skill_mirror_matches_canonical() -> None:
    canonical = SKILL_PATHS[0].read_bytes()
    assert SKILL_PATHS[1].read_bytes() == canonical
