#!/usr/bin/env python3
"""Create the stable MTG package identity from exact Git and build outputs.

Stdlib only. This manifest describes packages, never a production deployment.
The publication workflow attests it only after exact-digest content acceptance.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path

REPOSITORY = "Midtown-Technology-Group/bifrost"
WORKFLOW = ".github/workflows/ci.yml"
STABLE = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REVISION = re.compile(r"[A-Za-z0-9_]+")
COMPONENTS = ("api", "client", "worker")
RELEASE_PATHS = {
    ".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
    "plugins/bifrost/.codex-plugin/plugin.json", "release/candidate.json", "release/notes.md",
}
CONTRACT_PATHS = ("api/shared/contract_version.py", "api/bifrost/contract_version.py")
MIGRATIONS = "api/alembic/versions/"
RETIREMENT = "20261003_workflow_retirement"
ROLLBACK_REASON = (
    "Automatic rollback is not certified. Preserve terminal workflow retirement "
    "evidence and admission safeguards during application rollback. Database "
    "downgrade past 20261003_workflow_retirement refuses once retirement evidence "
    "exists; use a reviewed recovery plan, not an automatic schema downgrade."
)
ACCEPTANCE = {
    "kind": "container-content-smoke",
    "platform": "linux/amd64",
    "components": list(COMPONENTS),
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def literal(text: str, name: str) -> object:
    values = []
    for node in ast.parse(text).body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            values.append(ast.literal_eval(node.value))
    if len(values) != 1:
        raise ValueError(f"{name} must have exactly one literal assignment")
    return values[0]


def contract(ref: str) -> int:
    values = [literal(git("show", f"{ref}:{path}"), "CONTRACT_VERSION") for path in CONTRACT_PATHS]
    if any(type(value) is not int or value < 1 for value in values) or values[0] != values[1]:
        raise ValueError("server and CLI contracts must be matching positive integer literals")
    return values[0]


def migration_graph(ref: str) -> dict[str, tuple[str, ...]]:
    paths = git("ls-tree", "-r", "--name-only", ref, "--", MIGRATIONS).splitlines()
    graph = {}
    for path in paths:
        if not path.endswith(".py") or Path(path).name == "__init__.py":
            continue
        text = git("show", f"{ref}:{path}")
        revision = literal(text, "revision")
        down = literal(text, "down_revision")
        parents = () if down is None else ((down,) if isinstance(down, str) else down)
        if not isinstance(revision, str) or not REVISION.fullmatch(revision) or revision in graph:
            raise ValueError(f"invalid or duplicate migration revision: {path}")
        if not isinstance(parents, (tuple, list)) or any(
            not isinstance(parent, str) or not REVISION.fullmatch(parent) for parent in parents
        ):
            raise ValueError(f"invalid migration parents: {path}")
        graph[revision] = tuple(parents)
    if not graph:
        raise ValueError("migration graph is empty")
    visited, visiting = set(), set()

    def visit(revision: str) -> None:
        if revision in visiting:
            raise ValueError("migration graph contains a cycle")
        if revision not in graph:
            raise ValueError(f"migration parent missing: {revision}")
        if revision in visited:
            return
        visiting.add(revision)
        for parent in graph[revision]:
            visit(parent)
        visiting.remove(revision)
        visited.add(revision)

    for revision in graph:
        visit(revision)
    return graph


def heads(graph: dict[str, tuple[str, ...]]) -> list[str]:
    return sorted(set(graph) - {parent for parents in graph.values() for parent in parents})


def compatibility(source: str, base: str | None) -> dict:
    current_graph = migration_graph(source)
    prior_graph = migration_graph(base) if base else None
    return {
        "cli_contract": {"current": contract(source), "previous": contract(base) if base else None},
        "database": {
            "migration_heads": heads(current_graph),
            "upgrade_required": prior_graph != current_graph,
        },
        "rollback": {
            "supported": False,
            "reason": ROLLBACK_REASON if RETIREMENT in current_graph else (
                "Automatic rollback is not certified. Review migration downgrade safety "
                "and use a reviewed recovery plan before rolling back."
            ),
        },
    }


def upgrade_notes(source: str, base: str | None) -> list[str]:
    # Do not require a migration graph for historical/bootstrap test fixtures.
    current = contract(source)
    previous = contract(base) if base else None
    transition = f"{previous} -> {current}" if previous is not None else str(current)
    notes = [
        f"CLI/server contract: {transition}. Install the CLI bundled with this release; "
        "mismatched contracts are rejected.",
        "Before upgrading, back up the database and review the exact release's migration "
        "heads. Apply migrations using the protected infrastructure migration lane.",
    ]
    paths = git("ls-tree", "-r", "--name-only", source, "--", MIGRATIONS).splitlines()
    retirement_path = MIGRATIONS + "20261003_workflow_registration_retirement.py"
    if retirement_path in paths:
        text = git("show", f"{source}:{retirement_path}")
        if literal(text, "revision") != RETIREMENT:
            raise ValueError("terminal-retirement migration identity changed; review release warnings")
        notes.extend([
            ROLLBACK_REASON,
            "Before enabling terminal Live retirement, review actual production caller/byte "
            "evidence. The native read-only census does not prove absence of Python, "
            "operational file, App Source/dist, or external callers. Coordinate owners "
            "and retain the exact reviewed evidence digest before authorizing retirement.",
            "Read back retirement inventory to verify retained row/evidence IDs. An "
            "uncertain outcome requires readback before any exact retry; do not replay "
            "with fresh targets. Keep the forward schema during application-image rollback.",
            f"Follow the [terminal-retirement runbook](https://github.com/{REPOSITORY}/blob/"
            f"{source}/docs/architecture/rapid-workspace-promotion.md"
            "#obsolete-registrations-in-the-same-cutover).",
        ])
    else:
        notes.append("Automatic rollback is not certified; review schema compatibility before application rollback.")
    return notes


def build(tag: str, source: str, run_id: int, run_attempt: int, digests: dict[str, str]) -> dict:
    if not STABLE.fullmatch(tag) or not SHA.fullmatch(source):
        raise ValueError("stable tag and exact source commit are required")
    if type(run_id) is not int or type(run_attempt) is not int or min(run_id, run_attempt) < 1:
        raise ValueError("positive build run and attempt are required")
    if set(digests) != set(COMPONENTS) or any(not DIGEST.fullmatch(value) for value in digests.values()):
        raise ValueError("all three exact sha256 image digests are required")
    if git("rev-parse", "HEAD") != source or git("rev-parse", f"refs/tags/{tag}^{{commit}}") != source:
        raise ValueError("checkout, tag and source commit differ")
    candidate = json.loads(git("show", f"{source}:release/candidate.json"))
    if not isinstance(candidate, dict) or (type(candidate.get("schema")) is not int or candidate["schema"] != 1) or candidate.get("repository") != REPOSITORY or candidate.get("version") != tag:
        raise ValueError("stable release candidate does not match the package")
    previous = candidate.get("source_commit")
    if not isinstance(previous, str) or not SHA.fullmatch(previous):
        raise ValueError("release candidate has no exact pre-release source")
    git("merge-base", "--is-ancestor", previous, source)
    if candidate.get("source_tree") != git("rev-parse", f"{previous}^{{tree}}"):
        raise ValueError("release candidate source tree differs")
    changes = set(git("diff", "--name-only", previous, source).splitlines())
    if "release/candidate.json" not in changes or not changes <= RELEASE_PATHS:
        raise ValueError("release contains source changes outside its reviewed candidate")
    if "base_tag" not in candidate:
        raise ValueError("release candidate has no baseline declaration")
    base = candidate["base_tag"]
    if base is not None:
        if not isinstance(base, str) or not STABLE.fullmatch(base) or base == tag:
            raise ValueError("invalid previous stable tag")
        git("merge-base", "--is-ancestor", f"refs/tags/{base}^{{commit}}", source)
    return {
        "schema_version": 1,
        "product": "bifrost",
        "version": tag[1:],
        "tag": tag,
        "source": {"repository": REPOSITORY, "commit": source},
        "images": {name: {"repository": f"ghcr.io/{REPOSITORY.lower()}-{name}", "digest": digests[name]} for name in COMPONENTS},
        "compatibility": compatibility(source, base),
        "build": {"workflow": WORKFLOW, "run_id": run_id, "run_attempt": run_attempt},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    for name in COMPONENTS:
        parser.add_argument(f"--{name}-digest", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = build(args.tag, args.source, args.run_id, args.run_attempt, {name: getattr(args, name + "_digest") for name in COMPONENTS})
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
