#!/usr/bin/env python3
"""Require external GitHub Actions to be pinned to full commit SHAs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

WORKFLOW_SUFFIXES = {".yml", ".yaml"}
VERSION_COMMENT_RE = re.compile(r"#\s*(?P<version>v[0-9][^\s#]*)\s*$")


@dataclass(frozen=True)
class Violation:
    path: Path
    line_number: int
    action: str
    reason: str

    def format(self) -> str:
        return f"{self.path}:{self.line_number}: {self.reason}: {self.action}"


@dataclass(frozen=True)
class ActionReference:
    path: Path
    line_number: int
    action: str
    source_line: str


def _action_references(path: Path) -> tuple[list[ActionReference], Violation | None]:
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    references: list[ActionReference] = []

    try:
        documents = list(yaml.compose_all(source, Loader=yaml.SafeLoader))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line_number = mark.line + 1 if mark is not None else 1
        return references, Violation(
            path,
            line_number,
            "<invalid YAML>",
            "workflow could not be parsed as YAML",
        )

    pending: list[Node] = [document for document in documents if document is not None]
    visited: set[int] = set()
    while pending:
        node = pending.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))

        if isinstance(node, MappingNode):
            for key_node, value_node in node.value:
                if isinstance(key_node, ScalarNode) and key_node.value == "uses":
                    line_number = value_node.start_mark.line + 1
                    if isinstance(value_node, ScalarNode):
                        source_line = (
                            source_lines[line_number - 1]
                            if line_number <= len(source_lines)
                            else ""
                        )
                        references.append(
                            ActionReference(
                                path,
                                line_number,
                                value_node.value.strip(),
                                source_line,
                            )
                        )
                pending.extend((key_node, value_node))
        elif isinstance(node, SequenceNode):
            pending.extend(node.value)

    return sorted(references, key=lambda reference: reference.line_number), None


def _is_full_sha(ref: str) -> bool:
    return len(ref) == 40 and all(char in "0123456789abcdefABCDEF" for char in ref)


def _is_local_or_non_github_action(action: str) -> bool:
    return action.startswith(("./", "../", "docker://"))


def find_unpinned_actions(paths: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_workflow_files(paths):
        references, parse_violation = _action_references(path)
        if parse_violation is not None:
            violations.append(parse_violation)
        for reference in references:
            action = reference.action
            if _is_local_or_non_github_action(action):
                continue

            if "@" not in action:
                violations.append(
                    Violation(
                        path,
                        reference.line_number,
                        action,
                        "external action is not pinned",
                    )
                )
                continue

            ref = action.rsplit("@", 1)[1]
            if not _is_full_sha(ref):
                violations.append(
                    Violation(
                        path,
                        reference.line_number,
                        action,
                        "external action must use a full 40-character commit SHA",
                    )
                )

    return violations


def find_mismatched_action_versions(
    paths: list[Path],
    resolve_version: Callable[[str, str], str],
) -> list[Violation]:
    """Verify each readable version comment resolves to the pinned commit."""
    violations: list[Violation] = []
    resolved: dict[tuple[str, str], str] = {}
    for path in _iter_workflow_files(paths):
        references, parse_violation = _action_references(path)
        if parse_violation is not None:
            violations.append(parse_violation)
        for reference in references:
            action = reference.action
            if _is_local_or_non_github_action(action) or "@" not in action:
                continue

            action_path, pinned_sha = action.rsplit("@", 1)
            if not _is_full_sha(pinned_sha):
                continue

            version_match = VERSION_COMMENT_RE.search(reference.source_line)
            if not version_match:
                violations.append(
                    Violation(
                        path,
                        reference.line_number,
                        action,
                        "SHA-pinned external action needs a readable version comment",
                    )
                )
                continue

            parts = action_path.split("/")
            if len(parts) < 2:
                continue
            repository = "/".join(parts[:2])
            version = version_match.group("version")
            key = (repository, version)
            try:
                if key not in resolved:
                    resolved[key] = resolve_version(repository, version)
                resolved_sha = resolved[key]
            except (HTTPError, URLError, RuntimeError, ValueError) as exc:
                violations.append(
                    Violation(
                        path,
                        reference.line_number,
                        action,
                        f"could not resolve {repository}@{version}: {exc}",
                    )
                )
                continue

            if resolved_sha.lower() != pinned_sha.lower():
                violations.append(
                    Violation(
                        path,
                        reference.line_number,
                        action,
                        (
                            f"{repository}@{version} resolves to {resolved_sha}, "
                            f"not {pinned_sha}"
                        ),
                    )
                )

    return violations


def resolve_github_action_version(repository: str, version: str) -> str:
    """Resolve an action tag through GitHub's commits API."""
    url = (
        "https://api.github.com/repos/"
        f"{quote(repository, safe='/')}/commits/{quote(version, safe='')}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "bifrost-action-pin-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with urlopen(Request(url, headers=headers), timeout=15) as response:  # noqa: S310
        payload = json.load(response)
    sha = payload.get("sha")
    if not isinstance(sha, str) or not _is_full_sha(sha):
        raise ValueError("GitHub returned no full commit SHA")
    return sha


def _iter_workflow_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix in WORKFLOW_SUFFIXES:
            files.append(path)
        elif path.is_dir():
            files.extend(
                child
                for child in path.rglob("*")
                if child.is_file() and child.suffix in WORKFLOW_SUFFIXES
            )
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that external GitHub Actions are pinned to full commit SHAs."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(".github/workflows"), Path(".github/actions")],
        help="Workflow files or directories to scan.",
    )
    parser.add_argument(
        "--verify-versions",
        action="store_true",
        help="Resolve readable version comments and verify their pinned SHAs.",
    )
    args = parser.parse_args(argv)

    violations = find_unpinned_actions(args.paths)
    if args.verify_versions:
        violations.extend(
            find_mismatched_action_versions(
                args.paths,
                resolve_github_action_version,
            )
        )
    violations = list(dict.fromkeys(violations))
    if not violations:
        return 0

    print("Found GitHub Actions that are not pinned to full commit SHAs:", file=sys.stderr)
    for violation in violations:
        print(f"  {violation.format()}", file=sys.stderr)
    print(
        "\nUse a full commit SHA and keep the readable version as a comment, for example:\n"
        "  uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
