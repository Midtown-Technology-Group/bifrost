#!/usr/bin/env python3
"""Decide the next MTG Bifrost release version from merged pull requests.

Reads a JSON array of merged pull requests from stdin, each shaped as
``{"title": "...", "labels": ["semver:minor", "bug"], "mergedAt": "..."}``,
and prints the next ``MAJOR.MINOR.PATCH`` (without the ``v``) after ``--base``.

The bump is decided per PR, label-first and conventional-commit-second:

- a ``semver:major`` / ``semver:minor`` / ``semver:patch`` label always wins;
- otherwise a ``!``/``BREAKING`` title marker is a major, a ``feat`` type is a
  minor, any other conventional type is a patch;
- otherwise the bump is ``--default`` (default: patch).

The highest bump across all PRs wins. With --contract-base, a changed CLI
contract imposes a major-version floor. Missing or inconsistent contracts fail
closed. Exits 3 when there are no PRs (nothing to
release); other non-zero codes are real errors. ``--since`` drops PRs merged at
or before an exact ISO 8601 tag boundary.

Stdlib only and side-effect free, so ``scripts/release-check.sh`` and the
release-draft workflow share one decision.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from datetime import datetime

_CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<bang>!)?:")
_BREAKING = re.compile(r"\bBREAKING\b|!:")

_MINOR_TYPES = frozenset({"feat"})
_KNOWN_TYPES = frozenset(
    {
        "feat",
        "fix",
        "perf",
        "refactor",
        "revert",
        "build",
        "ci",
        "chore",
        "docs",
        "style",
        "test",
    }
)

_RANK = {"patch": 1, "minor": 2, "major": 3}

# Distinct "nothing to release" status, so callers never confuse it with a
# genuine failure (bad args, gh error, invalid JSON).
NO_RELEASE = 3


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def bump_for(entry: dict) -> str | None:
    """Return ``major``/``minor``/``patch`` for one PR, or None if undecidable."""
    names = {str(label).strip().lower() for label in (entry.get("labels") or [])}
    title = str(entry.get("title") or "").strip()
    if "semver:major" in names:
        return "major"
    if "semver:minor" in names:
        return "minor"
    if "semver:patch" in names:
        return "patch"
    if _BREAKING.search(title):
        return "major"
    match = _CONVENTIONAL.match(title)
    if match:
        if match.group("type") in _MINOR_TYPES:
            return "minor"
        if match.group("type") in _KNOWN_TYPES:
            return "patch"
    return None


def contract_bump(base_ref: str, current_ref: str = "HEAD") -> str | None:
    """An exact CLI contract mismatch requires a major release, regardless of labels."""
    paths = ("api/shared/contract_version.py", "api/bifrost/contract_version.py")

    def read_contract(ref: str, path: str) -> int:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/~^{}-]*", ref) or ".." in ref:
            raise ValueError("release contract requires a Git ref, never a command option")
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ValueError(f"cannot read release contract at {ref}:{path}")
        values = []
        for node in ast.parse(result.stdout).body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "CONTRACT_VERSION" and isinstance(
                    node.value, ast.Constant
                ):
                    values.append(node.value.value)
            elif (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "CONTRACT_VERSION"
                    for target in node.targets
                )
                and isinstance(node.value, ast.Constant)
            ):
                values.append(node.value.value)
        if len(values) != 1 or type(values[0]) is not int or values[0] < 1:
            raise ValueError(
                f"release contract is not one positive literal at {ref}:{path}"
            )
        return values[0]

    previous = [read_contract(base_ref, path) for path in paths]
    current = [read_contract(current_ref, path) for path in paths]
    if previous[0] != previous[1] or current[0] != current[1]:
        raise ValueError("server and CLI release contracts disagree")
    return "major" if previous != current else None


def next_version(
    base: str,
    entries: list[dict],
    default: str = "patch",
    minimum_bump: str | None = None,
) -> str:
    """Next version after ``base`` for the given PR entries."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", base.strip())
    if not match:
        raise ValueError(f"base must be MAJOR.MINOR.PATCH, got {base!r}")
    major, minor, patch = (int(part) for part in match.groups())
    level = _RANK[minimum_bump] if minimum_bump else 0
    for entry in entries:
        level = max(level, _RANK[bump_for(entry) or default])
    if level == _RANK["major"]:
        return f"{major + 1}.0.0"
    if level == _RANK["minor"]:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", required=True, help="current version, MAJOR.MINOR.PATCH"
    )
    parser.add_argument(
        "--default",
        choices=sorted(_RANK),
        default="patch",
        help="bump for a PR with no label and no conventional type (default: patch)",
    )
    parser.add_argument(
        "--since",
        help="drop PRs merged at or before this ISO 8601 tag boundary",
    )
    parser.add_argument("--json", help="PR array as a string; defaults to stdin")
    parser.add_argument(
        "--contract-base", help="exact stable tag or commit to compare CLI contracts"
    )
    args = parser.parse_args(argv)

    raw = args.json if args.json is not None else sys.stdin.read()
    try:
        entries = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError as exc:
        print(f"next-version: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2
    if not isinstance(entries, list):
        print("next-version: expected a JSON array of pull requests", file=sys.stderr)
        return 2

    if args.since:
        boundary = _parse_iso(args.since)
        if boundary is None:
            print(
                "next-version: --since must be an ISO 8601 timestamp", file=sys.stderr
            )
            return 2
        entries = [
            entry
            for entry in entries
            if (_parse_iso(entry.get("mergedAt")) or boundary) > boundary
        ]

    if not entries:
        print(
            "next-version: no merged pull requests since the last release",
            file=sys.stderr,
        )
        return NO_RELEASE
    try:
        minimum_bump = contract_bump(args.contract_base) if args.contract_base else None
        print(next_version(args.base, entries, args.default, minimum_bump))
    except (ValueError, SyntaxError) as exc:
        print(f"next-version: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
