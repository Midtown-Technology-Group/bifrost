from __future__ import annotations

from pathlib import Path

from scripts import check_github_action_pins


def _workflow(*step_lines: str) -> str:
    return "\n".join(
        [
            "jobs:",
            "  test:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            *(f"      {line}" for line in step_lines),
        ]
    )


def test_flags_external_actions_without_full_sha(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        _workflow(
            "- uses: actions/checkout@v6",
            "- uses: owner/action@main",
            "- uses: owner/no-ref",
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert [violation.action for violation in violations] == [
        "actions/checkout@v6",
        "owner/action@main",
        "owner/no-ref",
    ]


def test_flags_yaml_uses_keys_with_alternate_spacing(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        _workflow(
            "- uses : actions/checkout@v6",
            '- "uses" : owner/action@main',
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert [(violation.line_number, violation.action) for violation in violations] == [
        (5, "actions/checkout@v6"),
        (6, "owner/action@main"),
    ]


def test_ignores_uses_text_inside_run_block(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        _workflow("- run: |", "    printf '%s\\n' 'uses: owner/action@main'"),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert violations == []


def test_ignores_uses_keys_outside_action_positions(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "\n".join(
            [
                "env:",
                "  uses: owner/workflow-env@main",
                "jobs:",
                "  test:",
                "    runs-on: ubuntu-latest",
                "    env:",
                "      uses: owner/job-env@main",
                "    steps:",
                "      - env:",
                "          uses: owner/step-env@main",
                "        run: echo safe",
            ]
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert violations == []


def test_flags_reusable_workflow_and_composite_action_uses(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "jobs:\n  delegated:\n    uses: owner/repo/.github/workflows/reuse.yml@main\n",
        encoding="utf-8",
    )
    action = tmp_path / "action.yml"
    action.write_text(
        "runs:\n  using: composite\n  steps:\n    - uses: owner/action@v1\n",
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow, action])

    assert [violation.action for violation in violations] == [
        "owner/action@v1",
        "owner/repo/.github/workflows/reuse.yml@main",
    ]


def test_flags_invalid_yaml_instead_of_skipping_scan(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text("steps:\n  - uses: [\n", encoding="utf-8")

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert len(violations) == 1
    assert violations[0].action == "<invalid YAML>"
    assert violations[0].reason == "workflow could not be parsed as YAML"


def test_flags_duplicate_mapping_keys_as_invalid_yaml(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        _workflow(
            "- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
            "  uses: owner/action@main",
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert len(violations) == 1
    assert violations[0].action == "<invalid YAML>"
    assert violations[0].line_number == 6


def test_scalar_alias_uses_alias_line_and_version_comment(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    sha = "a" * 40
    workflow.write_text(
        "\n".join(
            [
                "anchors:",
                f"  action: &action owner/action@{sha} # v1.0.0",
                "jobs:",
                "  test:",
                "    steps:",
                "      - uses: *action # v2.0.0",
            ]
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    violations = check_github_action_pins.find_mismatched_action_versions(
        [workflow],
        lambda repository, version: calls.append((repository, version)) or sha,
    )

    assert violations == []
    assert calls == [("owner/action", "v2.0.0")]


def test_allows_sha_pinned_local_and_docker_actions(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        _workflow(
            "- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
            "- uses: ./.github/actions/local-action",
            "- uses: docker://ghcr.io/example/image:latest",
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert violations == []


def test_flags_sha_that_does_not_match_version_comment(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        _workflow(
            "- uses: owner/action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.2.3"
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_mismatched_action_versions(
        [workflow],
        lambda repository, version: "b" * 40,
    )

    assert len(violations) == 1
    assert "owner/action@v1.2.3 resolves to" in violations[0].reason


def test_allows_sha_matching_version_comment_and_caches_resolution(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "workflow.yml"
    sha = "a" * 40
    workflow.write_text(
        _workflow(
            f"- uses: owner/action@{sha} # v1.2.3",
            f"- uses: owner/action/subpath@{sha} # v1.2.3",
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    def resolve(repository: str, version: str) -> str:
        calls.append((repository, version))
        return sha

    violations = check_github_action_pins.find_mismatched_action_versions(
        [workflow], resolve
    )

    assert violations == []
    assert calls == [("owner/action", "v1.2.3")]
