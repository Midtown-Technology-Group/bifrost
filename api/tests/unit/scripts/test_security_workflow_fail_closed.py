from datetime import datetime, timezone
from pathlib import Path

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github/workflows/snyk.yml").is_file() and (
            parent / ".snyk"
        ).is_file():
            return parent
    raise RuntimeError("Could not locate repository security policy root")


REPO_ROOT = _repo_root()


def test_snyk_scan_failures_are_not_tolerated() -> None:
    workflow = (REPO_ROOT / ".github/workflows/snyk.yml").read_text(encoding="utf-8")

    assert "continue-on-error" not in workflow
    assert "SNYK_TOKEN is required" in workflow
    assert "exit 1" in workflow
    assert '      - "requirements*.lock"' in workflow
    assert '      - "k8s/**"' in workflow
    assert "--policy-path=.snyk" in workflow


def test_snyk_policy_allows_only_the_approved_dated_authlib_exception() -> None:
    policy = yaml.safe_load((REPO_ROOT / ".snyk").read_text(encoding="utf-8"))

    assert set(policy) <= {"version", "ignore"}
    exceptions = policy.get("ignore") or {}
    assert set(exceptions) <= {"SNYK-PYTHON-AUTHLIB-20257410"}
    if not exceptions:
        return
    # Operator approval on 2026-09-30 covers this finding only. Removing
    # the exception after an upstream fix needs no replacement allowance.
    entries = exceptions["SNYK-PYTHON-AUTHLIB-20257410"]
    assert len(entries) == 1 and set(entries[0]) == {"*"}
    exception = entries[0]["*"]
    assert set(exception) == {"reason", "expires"}
    assert "CVE-2026-96760" in exception["reason"]
    assert exception["expires"] == "2026-10-14T00:00:00.000Z"
    assert datetime.now(timezone.utc) < datetime.fromisoformat(exception["expires"])
