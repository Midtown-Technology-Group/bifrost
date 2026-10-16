"""Workflow contracts for bounded, fail-closed candidate attestation recovery.

Action outcomes are injected here; these tests do not publish attestations or
claim to reproduce the GitHub runner or a provider rate limit.
"""

import subprocess

import pytest
import yaml
from scripts.check_mtg_ci_boundaries import _repo_root


def _candidate_steps():
    workflow = _repo_root() / ".github/workflows/ci.yml"
    jobs = yaml.load(workflow.read_text(), Loader=yaml.BaseLoader)["jobs"]
    return jobs["build-candidate-images"]["steps"], jobs["candidate-images"]


def test_retry_preserves_the_attested_subject_and_required_gate():
    steps, gate = _candidate_steps()
    first = next(s for s in steps if s["name"] == "Attest candidate build provenance")
    backoff = next(s for s in steps if s["name"] == "Back off before attestation retry")
    retry = next(s for s in steps if s["name"] == "Retry candidate build provenance attestation")
    verify = next(s for s in steps if s["name"] == "Verify candidate identity and signature")

    assert first["id"] == "attest"
    assert first["uses"] == retry["uses"] == (
        "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"
    )
    assert first["with"] == retry["with"] == {
        "subject-name": "ghcr.io/${{ matrix.image }}",
        "subject-digest": "${{ steps.build.outputs.digest }}",
        "push-to-registry": "true",
    }
    assert backoff["run"] == "sleep 180"
    assert steps.index(first) < steps.index(backoff) < steps.index(retry) < steps.index(verify)
    assert gate["needs"] == "build-candidate-images"
    require = next(s for s in gate["steps"] if s["name"] == "Require exact signed candidates")
    assert require["env"]["BUILD_RESULT"] == "${{ needs.build-candidate-images.result }}"


@pytest.mark.parametrize(
    ("first_outcome", "retry_outcome", "expected_attempts", "expected_result"),
    [
        ("success", None, 1, "success"),
        ("failure", "success", 2, "success"),
        ("failure", "failure", 2, "failure"),
    ],
)
def test_injected_attestation_outcomes_fail_closed(
    first_outcome, retry_outcome, expected_attempts, expected_result
):
    steps, _ = _candidate_steps()
    first = next(s for s in steps if s["name"] == "Attest candidate build provenance")
    backoff = next(s for s in steps if s["name"] == "Back off before attestation retry")
    retry = next(s for s in steps if s["name"] == "Retry candidate build provenance attestation")

    # Pin the runner policy we model: a tolerated first failure preserves its
    # outcome, and both recovery steps run only for that outcome. A retry
    # failure must remain fatal rather than allowing Candidate Images through.
    assert first["continue-on-error"] == "true"
    assert "if" not in first
    assert backoff["if"] == retry["if"] == "steps.attest.outcome == 'failure'"
    assert retry.get("continue-on-error", "false") == "false"
    assert backoff.get("continue-on-error", "false") == "false"
    actions = [s for s in steps if s.get("uses") == first["uses"]]
    assert actions == [first, retry]
    assert expected_attempts == (1 if first_outcome == "success" else len(actions))

    # Execute the real downstream gate with the injected builder result. This
    # covers recovered and unrecovered failures without publishing artifacts.
    _, gate = _candidate_steps()
    require = next(s for s in gate["steps"] if s["name"] == "Require exact signed candidates")
    result = subprocess.run(
        ["bash", "-eu", "-c", require["run"]],
        env={"BUILD_RESULT": retry_outcome or first_outcome},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (0 if expected_result == "success" else 1)
