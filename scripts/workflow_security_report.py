"""Summarize a successful offline Zizmor SARIF audit without gating findings."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path


def report(sarif: dict, head_sha: str, scanner_version: str) -> tuple[dict, str]:
    """Reject incomplete evidence; keep untrusted finding text out of Markdown."""
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ValueError("head must be a full Git commit SHA")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", scanner_version):
        raise ValueError("scanner version must be stable semver")
    if sarif.get("version") != "2.1.0":
        raise ValueError("expected SARIF 2.1.0")
    runs = sarif.get("runs")
    if not isinstance(runs, list) or len(runs) != 1:
        raise ValueError("expected one Zizmor run")
    run = runs[0]
    driver = run["tool"]["driver"]
    if driver["name"] != "zizmor" or driver["version"] != scanner_version:
        raise ValueError("scanner identity does not match the pinned version")
    for invocation in run.get("invocations", []):
        if invocation.get("executionSuccessful") is False:
            raise ValueError("scanner reported unsuccessful execution")
    results = run.get("results")
    if not isinstance(results, list):
        raise ValueError("missing audit results")
    levels = Counter(result.get("level", "warning") for result in results)
    if levels.keys() - {"error", "warning", "note", "none"}:
        raise ValueError("unknown SARIF result level")
    metadata = {
        "head_sha": head_sha,
        "scanner": "zizmor",
        "scanner_version": scanner_version,
        "mode": "offline",
        "collection": ["workflows", "actions"],
        "advisory": True,
        "findings": len(results),
        "levels": dict(sorted(levels.items())),
    }
    summary = (
        "## Zizmor advisory pilot\n\n"
        f"Source: `{head_sha}`; Zizmor `{scanner_version}`; offline audit.\n\n"
        f"Findings: **{len(results)}** "
        f"({levels['error']} error, {levels['warning']} warning, "
        f"{levels['note']} note, {levels['none']} none).\n\n"
        "Download the workflow-security artifact for SARIF, scanner diagnostics "
        "and source metadata. Findings require triage and do not gate merging. "
        "Online audits were not run; this is not a security certification.\n"
    )
    return metadata, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sarif", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--scanner-version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    metadata, summary = report(
        json.loads(args.sarif.read_text(encoding="utf-8")),
        args.head_sha,
        args.scanner_version,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "summary.md").write_text(summary, encoding="utf-8")


if __name__ == "__main__":
    main()
