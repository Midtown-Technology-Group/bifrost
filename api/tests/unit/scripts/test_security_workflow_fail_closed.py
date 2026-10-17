from datetime import datetime, timezone
from pathlib import Path

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github/workflows/codeql.yml").is_file() and (
            parent / ".snyk"
        ).is_file():
            return parent
    raise RuntimeError("Could not locate repository security policy root")


REPO_ROOT = _repo_root()


def test_reviewed_exception_preserves_approved_scope_and_deadline() -> None:
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
