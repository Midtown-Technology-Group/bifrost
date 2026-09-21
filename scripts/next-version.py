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

The highest bump across all PRs wins. Exits 3 when there are no PRs (nothing to
release); other non-zero codes are real errors. ``--since`` drops PRs merged at
or before an exact ISO 8601 tag boundary.

Stdlib only and side-effect free, so ``scripts/release-check.sh`` and the
release-draft workflow share one decision.
"""

from __future__ import annotations

import argparse
import json
import re
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
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
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


def next_version(base: str, entries: list[dict], default: str = "patch") -> str:
    """Next version after ``base`` for the given PR entries."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", base.strip())
    if not match:
        raise ValueError(f"base must be MAJOR.MINOR.PATCH, got {base!r}")
    major, minor, patch = (int(part) for part in match.groups())
    level = 0
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
    parser.add_argument(
        "--json", help="PR array as a string; defaults to stdin"
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
            print("next-version: --since must be an ISO 8601 timestamp", file=sys.stderr)
            return 2
        entries = [
            entry
            for entry in entries
            if (_parse_iso(entry.get("mergedAt")) or boundary) > boundary
        ]

    if not entries:
        print("next-version: no merged pull requests since the last release", file=sys.stderr)
        return NO_RELEASE
    try:
        print(next_version(args.base, entries, args.default))
    except ValueError as exc:
        print(f"next-version: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
