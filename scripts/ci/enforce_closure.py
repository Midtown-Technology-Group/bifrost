#!/usr/bin/env python3
"""Report (or enforce) ownership of the CI enforcement dependency boundary.

The gate that keeps agents honest is itself code: workflows, the plans that
select tests, the coverage producers, the scanner configuration. If those files
can be edited without an owner's review, every other control is advisory.

This script audits a declared manifest of enforcement-chain paths against
CODEOWNERS and, optionally, against the paths a change touches.

Modes
-----
report   Always exit 0. Print the audit and write ``--out`` when given. This is
         the mode CI runs today, because ``.github/CODEOWNERS`` is deliberately
         empty on this fork until the P0 trust boundary lands.
enforce  Exit 1 when any manifest path is not covered by CODEOWNERS, or when a
         changed path inside the manifest scope is unowned. This becomes the
         blocking mode once P0 (owner entries + required owner approval) is
         enabled; the proposal is explicit that the guard is advisory until then.

Matching is gitignore/CODEOWNERS-shaped (``*``, ``**``, ``?``, leading ``/``,
trailing ``/`` for directories, bare patterns matching at any depth) but is not
a byte-for-byte reimplementation of gitignore; it is deliberately conservative:
an unmatched or ambiguous pattern is reported as uncovered, never as owned.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT_MARKERS = (".github", "scripts")
DEFAULT_MANIFEST = Path("scripts/ci/enforcement-deps.txt")
DEFAULT_CODEOWNERS = Path(".github/CODEOWNERS")


def load_patterns(path: Path) -> list[str]:
    """Read a CODEOWNERS-style file and return non-comment patterns."""
    if not path.exists():
        return []
    patterns: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # CODEOWNERS lines are "<pattern> <owner...>"; keep only the pattern.
        patterns.append(line.split()[0])
    return patterns


def _glob_to_regex(pattern: str, *, anchored: bool) -> re.Pattern[str]:
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    body = "".join(out)
    # Bare patterns (no slash) match at any depth; the prefix must be part of
    # the regex itself because Pattern.match always starts at position 0.
    prefix = "^" if anchored else r"(?:.*/)?"
    return re.compile(f"{prefix}{body}$")


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one CODEOWNERS-style pattern to a path matcher."""
    trimmed = pattern[1:] if pattern.startswith("/") else pattern
    if not trimmed:
        trimmed = "**"
    if trimmed.endswith("/"):
        # Directory pattern: everything below it matches too.
        inner = _glob_to_regex(trimmed.rstrip("/"), anchored=True).pattern
        body = inner[1:-1] if inner.startswith("^") and inner.endswith("$") else inner
        return re.compile(f"^{body}(?:/.*)?$")
    anchored = "/" in trimmed
    return _glob_to_regex(trimmed, anchored=anchored)


def matches_any(patterns: list[str], path: str) -> bool:
    """True when any pattern matches ``path`` (POSIX, repo-root relative)."""
    # Only strip an explicit "./" prefix and surrounding slashes: lstrip("./")
    # would also eat the leading dot of paths like ".github/workflows/ci.yml".
    normalized = path[2:] if path.startswith("./") else path
    normalized = normalized.strip("/")
    return any(compile_pattern(pattern).match(normalized) for pattern in patterns)


def changed_in_scope(manifest: list[str], changed: list[str]) -> list[str]:
    return [path for path in changed if matches_any(manifest, path)]


def audit(
    manifest: list[str], codeowners: list[str], changed: list[str] | None = None
) -> dict[str, object]:
    manifest_uncovered = [entry for entry in manifest if not matches_any(codeowners, entry)]
    report: dict[str, object] = {
        "codeowners_patterns": len(codeowners),
        "manifest_patterns": len(manifest),
        "manifest_covered": len(manifest) - len(manifest_uncovered),
        "manifest_uncovered": manifest_uncovered,
        "owns_enforcement_chain": not manifest_uncovered,
    }
    if changed is not None:
        in_scope = changed_in_scope(manifest, changed)
        unowned = [path for path in in_scope if not matches_any(codeowners, path)]
        report.update(
            {
                "changed_total": len(changed),
                "changed_in_scope": len(in_scope),
                "changed_in_scope_unowned": unowned,
            }
        )
    return report


def load_manifest(path: Path) -> list[str]:
    return load_patterns(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("report", "enforce"), default="report")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--codeowners", type=Path, default=DEFAULT_CODEOWNERS)
    parser.add_argument(
        "--changed-files",
        type=Path,
        help="optional file listing repo-root-relative changed paths, one per line",
    )
    parser.add_argument("--out", type=Path, help="optional JSON report path")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    if not manifest:
        print(f"enforce_closure: empty or missing manifest {args.manifest}", file=sys.stderr)
        return 2

    codeowners = load_patterns(args.codeowners)
    changed: list[str] | None = None
    if args.changed_files and args.changed_files.exists():
        changed = [
            line.strip()
            for line in args.changed_files.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    report = audit(manifest, codeowners, changed)
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")

    print("enforce_closure: enforcement dependency boundary audit")
    print(f"  CODEOWNERS patterns : {report['codeowners_patterns']}")
    print(f"  manifest patterns   : {report['manifest_patterns']}")
    print(f"  manifest covered    : {report['manifest_covered']}")
    uncovered = list(report["manifest_uncovered"])  # type: ignore[arg-type]
    if uncovered:
        print("  uncovered (advisory until P0):")
        for entry in uncovered:
            print(f"    - {entry}")
    if changed is not None:
        print(f"  changed paths       : {report['changed_total']}")
        print(f"  changed in scope    : {report['changed_in_scope']}")
        unowned = list(report.get("changed_in_scope_unowned", []))  # type: ignore[arg-type]
        if unowned:
            print("  changed in scope without an owner:")
            for entry in unowned:
                print(f"    - {entry}")

    if args.mode == "report":
        return 0

    if not report["owns_enforcement_chain"]:
        print(
            "enforce_closure: FAIL - enforcement-chain paths lack CODEOWNERS coverage "
            "(enable P0 ownership before enforcing).",
            file=sys.stderr,
        )
        return 1
    if changed is not None and report.get("changed_in_scope_unowned"):
        print(
            "enforce_closure: FAIL - changed enforcement paths are not owned.",
            file=sys.stderr,
        )
        return 1
    print("enforce_closure: OK - enforcement chain is owned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
