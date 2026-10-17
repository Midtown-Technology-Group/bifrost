from pathlib import Path

import pytest
from scripts.check_mtg_ci_boundaries import (
    REQUIRED_CI_JOB_NAMES,
    _repo_root,
    _resolve_workflow_path,
    check_ci_image_token_boundaries,
    check_ci_workflow,
    check_codeowners,
)


def _mutate_ci_image_publisher(original: str, old: str, new: str) -> str:
    start = original.index("  publish-ci-test-images:\n")
    end = original.index("\n  lint:\n", start)
    publisher = original[start:end]
    assert old in publisher
    return original[:start] + publisher.replace(old, new, 1) + original[end:]


def test_resolve_workflow_path_rejects_paths_outside_repo():
    outside = _repo_root().parent / "outside.yml"
    with pytest.raises(ValueError, match="must stay within"):
        _resolve_workflow_path(outside)


def test_repo_root_never_resolves_to_filesystem_root():
    root = _repo_root()
    assert root != root.parent


def test_required_ci_check_identities_are_preserved():
    workflow = _repo_root() / ".github/workflows/ci.yml"

    assert check_ci_workflow(workflow) == []


def test_ci_image_tokens_are_removed_before_checkout():
    root = _repo_root()

    assert (
        check_ci_image_token_boundaries(
            root / ".github/workflows/ci.yml",
            root / ".github/workflows/sonar-coverage.yml",
        )
        == []
    )


def test_ci_image_token_after_checkout_is_rejected(tmp_path: Path):
    root = _repo_root()
    original = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        original.replace(
            "          API_IMAGE_CHANGED: ${{ steps.test-image-inputs.outputs.api_changed }}",
            "          GHCR_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
            "          API_IMAGE_CHANGED: ${{ steps.test-image-inputs.outputs.api_changed }}",
            1,
        ),
        encoding="utf-8",
    )

    violations = check_ci_image_token_boundaries(
        mutated,
        root / ".github/workflows/sonar-coverage.yml",
    )

    assert any("outside the pre-checkout cache pull" in item for item in violations)


def test_ci_image_checkout_credentials_must_not_persist(tmp_path: Path):
    root = _repo_root()
    original = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        original.replace(
            "        if: needs.affected-test-plan.outputs.api_unit_mode != 'skip'\n"
            "        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
            "        with:\n"
            "          persist-credentials: false\n",
            "        if: needs.affected-test-plan.outputs.api_unit_mode != 'skip'\n"
            "        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n",
            1,
        ),
        encoding="utf-8",
    )

    violations = check_ci_image_token_boundaries(
        mutated,
        root / ".github/workflows/sonar-coverage.yml",
    )

    assert any("persist-credentials: false" in item for item in violations)


def test_ci_image_publisher_checkout_credentials_must_not_persist(tmp_path: Path):
    root = _repo_root()
    original = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        _mutate_ci_image_publisher(
            original,
            "        with:\n          persist-credentials: false\n",
            "",
        ),
        encoding="utf-8",
    )

    violations = check_ci_image_token_boundaries(
        mutated,
        root / ".github/workflows/sonar-coverage.yml",
    )

    assert any("checkout must set persist-credentials: false" in item for item in violations)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    (
        (
            "          load: true\n          push: false\n",
            "          load: true\n          push: true\n",
            "must load locally and must not push",
        ),
        (
            "      - name: Build API CI test image without registry credentials\n",
            "      - name: Build API CI test image without registry credentials\n"
            "        env:\n"
            "          GHCR_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n",
            "package credentials must not reach untrusted image builds",
        ),
        (
            "          GHCR_TOKEN: ${{ secrets.GHCR_TOKEN || secrets.GITHUB_TOKEN }}",
            "          GHCR_TOKEN: unavailable",
            "only the final fixed-reference publish step may receive the package token",
        ),
        (
            "          trap 'docker logout \"$REGISTRY\" >/dev/null 2>&1 || true' EXIT\n",
            "",
            "must login, push, and logout inline",
        ),
    ),
)
def test_ci_image_publisher_rejects_credential_and_order_regressions(
    tmp_path: Path,
    old: str,
    new: str,
    expected: str,
):
    root = _repo_root()
    original = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        _mutate_ci_image_publisher(original, old, new),
        encoding="utf-8",
    )

    violations = check_ci_image_token_boundaries(
        mutated,
        root / ".github/workflows/sonar-coverage.yml",
    )

    assert any(expected in item for item in violations)


def test_ci_image_publisher_must_be_the_final_job_step(tmp_path: Path):
    root = _repo_root()
    original = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        _mutate_ci_image_publisher(
            original,
            "          done\n",
            "          done\n\n      - name: Run checked-out code after publication\n"
            "        run: bash api/scripts/unsafe.sh\n",
        ),
        encoding="utf-8",
    )

    violations = check_ci_image_token_boundaries(
        mutated,
        root / ".github/workflows/sonar-coverage.yml",
    )

    assert any("no step may run after" in item for item in violations)


def test_ci_image_authentication_must_precede_checkout(tmp_path: Path):
    root = _repo_root()
    original = (root / ".github/workflows/sonar-coverage.yml").read_text(
        encoding="utf-8"
    )
    start = original.index("      - name: Pull CI image caches before checkout\n")
    end = original.index("\n      - name: Checkout exact analysis source\n", start)
    pull_block = original[start:end]
    without_pull = original[:start] + original[end:]
    insertion = without_pull.index("\n      - name: Bind analysis context\n")
    mutated = tmp_path / "sonar-coverage.yml"
    mutated.write_text(
        without_pull[:insertion] + "\n" + pull_block + without_pull[insertion:],
        encoding="utf-8",
    )

    violations = check_ci_image_token_boundaries(
        root / ".github/workflows/ci.yml",
        mutated,
    )

    assert any(
        "authentication must finish before checkout" in item for item in violations
    )


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    (
        (
            "          GHCR_TOKEN: ${{ github.token }}",
            "          GHCR_TOKEN: unavailable",
            "must receive the read-only GitHub token",
        ),
        (
            '            docker pull "$ref"',
            '            docker inspect "$ref"',
            "must login, pull, and logout inline",
        ),
        (
            (
                "          trap 'docker logout \"$REGISTRY\" >/dev/null 2>&1 || true' EXIT\n"
                "          printf '%s' \"$GHCR_TOKEN\" | docker login"
            ),
            (
                "          printf '%s' \"$GHCR_TOKEN\" | docker login\n"
                "          trap 'docker logout \"$REGISTRY\" >/dev/null 2>&1 || true' EXIT\n"
                "          # preserve command suffix\n"
                "          true #"
            ),
            "logout cleanup must be armed before registry login",
        ),
        (
            "run: bash api/scripts/ci/prepare-test-images.sh api client",
            "run: bash untrusted-image-preparation.sh api client",
            "must use the credential-free repository helper",
        ),
    ),
)
def test_ci_image_boundary_rejects_malformed_trusted_steps(
    tmp_path: Path,
    old: str,
    new: str,
    expected: str,
):
    root = _repo_root()
    original = (root / ".github/workflows/sonar-coverage.yml").read_text(
        encoding="utf-8"
    )
    assert old in original
    mutated = tmp_path / "sonar-coverage.yml"
    mutated.write_text(original.replace(old, new, 1), encoding="utf-8")

    violations = check_ci_image_token_boundaries(
        root / ".github/workflows/ci.yml", mutated
    )

    assert any(expected in item for item in violations)


def test_ci_image_boundary_requires_files_jobs_and_named_steps(tmp_path: Path):
    root = _repo_root()
    missing = tmp_path / "missing.yml"
    assert any(
        "workflow file does not exist" in item
        for item in check_ci_image_token_boundaries(
            root / ".github/workflows/ci.yml", missing
        )
    )

    missing.write_text("jobs:\n  unrelated:\n    steps: []\n", encoding="utf-8")
    assert any(
        "required image consumer job is missing" in item
        for item in check_ci_image_token_boundaries(
            root / ".github/workflows/ci.yml", missing
        )
    )

    original = (root / ".github/workflows/sonar-coverage.yml").read_text(
        encoding="utf-8"
    )
    start = original.index("      - name: Pull CI image caches before checkout\n")
    end = original.index("\n      - name: Checkout exact analysis source\n", start)
    missing.write_text(original[:start] + original[end:], encoding="utf-8")
    assert any(
        "require a trusted cache pull" in item
        for item in check_ci_image_token_boundaries(
            root / ".github/workflows/ci.yml", missing
        )
    )


def test_required_ci_check_identity_change_is_rejected(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/ci.yml"
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        workflow.read_text(encoding="utf-8").replace(
            f"name: {REQUIRED_CI_JOB_NAMES['test-unit']}",
            "name: Renamed Unit Gate",
            1,
        ),
        encoding="utf-8",
    )

    violations = check_ci_workflow(mutated)

    assert any("required CI check identity changed" in item for item in violations)


def test_merge_group_trigger_is_required(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/ci.yml"
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "  merge_group:\n    types: [checks_requested]\n", "", 1
        ),
        encoding="utf-8",
    )

    assert any("merge_group trigger" in item for item in check_ci_workflow(mutated))


def test_merge_group_trigger_rejects_other_activity_types(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/ci.yml"
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "types: [checks_requested]", "types: [destroyed]", 1
        ),
        encoding="utf-8",
    )

    violations = check_ci_workflow(mutated)
    assert any("only checks_requested" in item for item in violations)


def test_zero_shard_sentinel_guard_is_required(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/ci.yml"
    original = workflow.read_text(encoding="utf-8")
    mutated = tmp_path / "ci.yml"
    sentinel_guard = (
        " && fromJSON(needs.affected-test-plan.outputs.api_e2e_matrix || "
        '\'{"include":[{"shard":0,"total":0}]}\').include[0].total != 0'
    )
    mutated.write_text(original.replace(sentinel_guard, "", 1), encoding="utf-8")
    assert mutated.read_text(encoding="utf-8") != original, (
        "sentinel guard not found in ci.yml"
    )

    violations = check_ci_workflow(mutated)

    assert any("admission guard changed" in item for item in violations)


def test_required_test_jobs_reject_preloaded_third_party_actions(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/ci.yml"
    original = workflow.read_text(encoding="utf-8")

    for replacement in (
        "uses: docker/setup-buildx-action@" + "a" * 40,
        "uses: codecov/codecov-action@" + "b" * 40,
    ):
        mutated = tmp_path / f"ci-{replacement.split('/')[0]}.yml"
        mutated.write_text(
            original.replace(
                "run: bash api/scripts/ci/prepare-test-images.sh api client",
                replacement,
                1,
            ),
            encoding="utf-8",
        )

        violations = check_ci_workflow(mutated)

        assert any("must not preload third-party action" in item for item in violations)


def test_codeowners_without_upstream_owners_passes():
    assert check_codeowners(_repo_root() / ".github/CODEOWNERS") == []


def test_codeowners_rejects_upstream_owner(tmp_path: Path):
    owners = tmp_path / "CODEOWNERS"
    owners.write_text(
        "# fork owners\n*.py @MTG-Thomas\n/api/src/ @jackmusick\n",
        encoding="utf-8",
    )

    violations = check_codeowners(owners)

    assert len(violations) == 1
    assert "@jackmusick" in violations[0]


def test_codeowners_rejects_email_owner(tmp_path: Path):
    owners = tmp_path / "CODEOWNERS"
    owners.write_text(
        "/api/ upstream-maintainer@example.com\n"
        "/docs/ invalid-owner-token\n",
        encoding="utf-8",
    )

    violations = check_codeowners(owners)

    assert len(violations) == 1
    assert "upstream-maintainer@example.com" in violations[0]
    assert "explicit MTG username or team" in violations[0]


def test_codeowners_ignores_comments_and_blanks(tmp_path: Path):
    owners = tmp_path / "CODEOWNERS"
    owners.write_text(
        "# @jackmusick used to own everything\n\n/docs/ @Midtown-Technology-Group/docs-team\n",
        encoding="utf-8",
    )

    assert check_codeowners(owners) == []


def test_codeowners_missing_file_passes(tmp_path: Path):
    assert check_codeowners(tmp_path / "CODEOWNERS") == []
