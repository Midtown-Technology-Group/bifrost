"""Release jobs must survive intentional skipped CI ancestors and fail closed."""

from pathlib import Path

import yaml


REPO_ROOT = Path.cwd()


def test_tag_release_jobs_explicitly_gate_required_results() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]

    verify_condition = jobs["verify-release-manifest"]["if"]
    assert "always()" in verify_condition
    assert "startsWith(github.ref, 'refs/tags/v')" in verify_condition
    for required in ("test-unit", "test-e2e-gate", "lint", "mcp-conformance"):
        assert f"needs.{required}.result == 'success'" in verify_condition

    for build_job in ("build-api", "build-client", "build-worker"):
        condition = jobs[build_job]["if"]
        assert "always()" in condition
        assert "startsWith(github.ref, 'refs/tags/v')" in condition
        assert "needs.verify-release-manifest.result == 'success'" in condition

    release_condition = jobs["create-release"]["if"]
    assert "always()" in release_condition
    assert "startsWith(github.ref, 'refs/tags/v')" in release_condition
    for build_job in ("build-api", "build-client", "build-worker"):
        assert f"needs.{build_job}.result == 'success'" in release_condition


def test_mtg_pr_images_build_in_parallel_and_main_promotes_exact_digests() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    candidate_build = jobs["build-candidate-images"]
    candidate_gate = jobs["candidate-images"]
    main_publish = jobs["build-dev"]

    assert candidate_build["needs"] == "lint"
    assert "needs.lint.result == 'success'" in candidate_build["if"]
    assert candidate_gate["name"] == "Candidate Images"
    assert candidate_gate["needs"] == "build-candidate-images"
    assert set(candidate_build["strategy"]["matrix"]["include"][0]) >= {
        "component",
        "image",
        "dockerfile",
        "version_arg",
    }

    build_step = next(
        step
        for step in candidate_build["steps"]
        if step["name"] == "Build immutable candidate"
    )
    assert (
        "candidate-tree-${{ steps.identity.outputs.tree_sha }}"
        in build_step["with"]["tags"]
    )
    assert "com.midtowntg.bifrost.source-tree" in build_step["with"]["labels"]
    assert "com.midtowntg.bifrost.candidate=true" in build_step["with"]["labels"]

    version_step = next(
        step
        for step in main_publish["steps"]
        if step["name"] == "Compute candidate-compatible version"
    )
    assert (
        'publication_parent="$(git rev-parse "${GITHUB_SHA}^1")"'
        in version_step["run"]
    )
    assert (
        './scripts/compute-dev-version.sh --next-after "$publication_parent"'
        in version_step["run"]
    )

    promotion_steps = [
        step
        for step in main_publish["steps"]
        if step["name"].startswith("Promote tested")
    ]
    assert [step["name"] for step in promotion_steps] == [
        "Promote tested API candidate",
        "Promote tested worker candidate",
        "Promote tested client candidate",
    ]
    for step in promotion_steps:
        assert "api/scripts/ci/candidate_image.py promote" in step["run"]
        assert "--tree-sha" in step["run"]
        assert "--main-source-sha" in step["run"]



def test_stable_manifest_acceptance_attestation_and_upload_precede_publication() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    for component in ("api", "client", "worker"):
        assert jobs[f"build-{component}"]["outputs"]["digest"] == (
            "${{ steps.build-" + component + ".outputs.digest }}"
        )
    steps = jobs["create-release"]["steps"]
    by_name = {step["name"]: step for step in steps}
    required = [
        "Generate stable release manifest",
        "Accept exact stable image contents",
        "Attest stable release manifest",
        "Retain release manifest attestation bundle",
        "Upload accepted stable release manifest",
        "Create release",
        "Publish release",
    ]
    indices = [steps.index(by_name[name]) for name in required]
    assert indices == sorted(indices)
    for name in required[:5]:
        condition = by_name[name]["if"]
        assert "github.repository == 'Midtown-Technology-Group/bifrost'" in condition
        assert "prerelease == 'false'" in condition
    assert by_name["Attest stable release manifest"]["with"]["subject-path"] == "release-manifest.json"
    manifest_upload = by_name["Upload accepted stable release manifest"]["with"]
    assert manifest_upload["if-no-files-found"] == "error"
    # Failed publication can retry only create-release without rebuilding images;
    # a prior upload in this run must not collide or overwrite its evidence.
    assert manifest_upload["name"] == "release-manifest-${{ steps.get_version.outputs.version }}-attempt-${{ github.run_attempt }}"
    assert "overwrite" not in manifest_upload
    assert "bundle-path" in by_name["Retain release manifest attestation bundle"]["env"]["BUNDLE_PATH"]
    assert by_name["Create release"]["id"] == "release"
    assert by_name["Publish release"]["env"]["RELEASE_ID"] == "${{ steps.release.outputs.id }}"
    publication = by_name["Publish release"]["run"]
    assert 'endpoint="repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID"' in publication
    assert "releases/tags/" not in publication
    for name in ("release-manifest.json", "release-manifest.json.sha256", "release-manifest.json.sigstore.json"):
        assert name in by_name["Create release"]["with"]["files"]
        assert publication.index(name) < publication.index("gh api --method PATCH")
    assert '.state == "uploaded" and .size > 0' in publication


def test_manual_branch_ci_runs_clean_candidate_gate_without_tag_or_write_access() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["pre-pr-candidate"]
    assert job["needs"] == "lint"
    assert job["if"] == "${{ !cancelled() && needs.lint.result == 'success' && github.event_name == 'workflow_dispatch' && startsWith(github.ref, 'refs/heads/') }}"
    assert job["permissions"] == {"contents": "read"}
    assert job["steps"][0]["with"] == {"ref": "${{ github.sha }}", "fetch-depth": 0}
    assert any("bash test.sh pre-pr" in step.get("run", "") for step in job["steps"])
    assert next(step for step in job["steps"] if step["name"] == "Record cleanup evidence")["if"] == "always()"

