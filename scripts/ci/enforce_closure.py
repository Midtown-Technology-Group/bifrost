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
    """Read a pattern file and return non-comment patterns (no ownership)."""
    return [pattern for pattern, _ in load_rules(path)]


# A CODEOWNERS rule: (pattern, has_owner). Order matters - see is_owned().
Rule = tuple[str, bool]


def load_rules(path: Path) -> list[Rule]:
    """Read a CODEOWNERS-style file into ordered rules with owner presence.

    Lines are ``<pattern> [owner...]``. ``#`` terminates a line. Owner
    presence is recorded rather than discarded, because CODEOWNERS semantics
    are last-match: a trailing rule without owners *unowns* what an earlier
    rule owned.
    """
    if not path.exists():
        return []
    rules: list[Rule] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens: list[str] = []
        for token in line.split():
            if token.startswith("#"):
                break
            tokens.append(token)
        if not tokens:
            continue
        rules.append((tokens[0], any(owner.startswith("@") for owner in tokens[1:])))
    return rules


def _normalize(path: str) -> str:
    # Only strip an explicit "./" prefix and surrounding slashes: lstrip("./")
    # would also eat the leading dot of paths like ".github/workflows/ci.yml".
    normalized = path[2:] if path.startswith("./") else path
    return normalized.strip("/")


def is_owned(rules: list[Rule], path: str) -> bool:
    """CODEOWNERS last-match semantics: only the last matching rule decides."""
    normalized = _normalize(path)
    decision: bool | None = None
    for pattern, has_owner in rules:
        if compile_pattern(pattern).match(normalized):
            decision = has_owner
    return bool(decision)


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
    """True when any pattern matches ``path`` (POSIX, repo-root relative).

    Used for boundary membership (the manifest is a flat path list), not for
    ownership - ownership uses CODEOWNERS last-match rules via is_owned().
    """
    normalized = _normalize(path)
    return any(compile_pattern(pattern).match(normalized) for pattern in patterns)


def changed_in_scope(manifest: list[str], changed: list[str]) -> list[str]:
    return [path for path in changed if matches_any(manifest, path)]


def audit(
    manifest: list[str], rules: list[Rule], changed: list[str] | None = None
) -> dict[str, object]:
    manifest_uncovered = [entry for entry in manifest if not is_owned(rules, entry)]
    report: dict[str, object] = {
        "codeowners_patterns": len(rules),
        "manifest_patterns": len(manifest),
        "manifest_covered": len(manifest) - len(manifest_uncovered),
        "manifest_uncovered": manifest_uncovered,
        "owns_enforcement_chain": not manifest_uncovered,
    }
    if changed is not None:
        in_scope = changed_in_scope(manifest, changed)
        unowned = [path for path in in_scope if not is_owned(rules, path)]
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


def _contain(base: Path, raw: Path, what: str) -> Path:
    """Resolve a CLI-supplied path under ``base`` and reject traversal.

    Sonar S8707 (path traversal via faulty LLM-supplied CLI arguments): a
    caller-supplied path must not escape the repository, whether through
    ``..`` segments or through an absolute path pointing elsewhere. Raises
    ``ValueError`` instead of silently reading or writing outside the base.
    """
    if ".." in Path(raw).parts:
        raise ValueError(f"{what} contains a traversal segment: {raw}")
    base_resolved = base.resolve()
    resolved = (raw if raw.is_absolute() else base_resolved / raw).resolve()
    if not resolved.is_relative_to(base_resolved):
        raise ValueError(f"{what} escapes {base_resolved}: {raw}")
    return resolved


def _read_changed_files(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"--changed-files path does not exist: {path}")
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _print_report(report: dict[str, object], changed_provided: bool) -> None:
    print("enforce_closure: enforcement dependency boundary audit")
    print(f"  CODEOWNERS patterns : {report['codeowners_patterns']}")
    print(f"  manifest patterns   : {report['manifest_patterns']}")
    print(f"  manifest covered    : {report['manifest_covered']}")
    uncovered = list(report["manifest_uncovered"])  # type: ignore[arg-type]
    if uncovered:
        print("  uncovered (advisory until P0):")
        for entry in uncovered:
            print(f"    - {entry}")
    if not changed_provided:
        return
    print(f"  changed paths       : {report['changed_total']}")
    print(f"  changed in scope    : {report['changed_in_scope']}")
    unowned = list(report.get("changed_in_scope_unowned", []))  # type: ignore[arg-type]
    if unowned:
        print("  changed in scope without an owner:")
        for entry in unowned:
            print(f"    - {entry}")


def _enforce_result(report: dict[str, object]) -> int:
    if not report["owns_enforcement_chain"]:
        print(
            "enforce_closure: FAIL - enforcement-chain paths lack CODEOWNERS coverage "
            "(enable P0 ownership before enforcing).",
            file=sys.stderr,
        )
        return 1
    if report.get("changed_in_scope_unowned"):
        print(
            "enforce_closure: FAIL - changed enforcement paths are not owned.",
            file=sys.stderr,
        )
        return 1
    print("enforce_closure: OK - enforcement chain is owned.")
    return 0


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

    # Every path below is caller-supplied: contain it in the repository first
    # (S8707), and distinguish an omitted --changed-files from a missing one.
    try:
        root = Path.cwd()
        manifest = load_manifest(_contain(root, args.manifest, "--manifest"))
        if not manifest:
            print(
                f"enforce_closure: empty or missing manifest {args.manifest}",
                file=sys.stderr,
            )
            return 2
        rules = load_rules(_contain(root, args.codeowners, "--codeowners"))
        changed: list[str] | None = None
        if args.changed_files is not None:
            changed = _read_changed_files(
                _contain(root, args.changed_files, "--changed-files")
            )
        out_path = _contain(root, args.out, "--out") if args.out else None
    except (ValueError, FileNotFoundError) as error:
        print(f"enforce_closure: {error}", file=sys.stderr)
        return 2

    report = audit(manifest, rules, changed)
    if out_path is not None:
        out_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    _print_report(report, changed_provided=changed is not None)
    if args.mode == "report":
        return 0
    return _enforce_result(report)


if __name__ == "__main__":
    raise SystemExit(main())
