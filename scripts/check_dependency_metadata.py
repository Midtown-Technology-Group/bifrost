#!/usr/bin/env python3
"""Check recorded PyPI constraints for a modeled Linux/Python 3.14 closure.

This is metadata evidence, not a substitute for pip check in a running image.
Uses the platform's existing packaging dependency; never installs packages.
"""
import argparse
import gzip
import json
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def activated_requirements(package, extras, env):
    for declaration in package["requires_dist"] or []:
        requirement = Requirement(declaration)
        if requirement.marker and not any(
            requirement.marker.evaluate({**env, "extra": extra}) for extra in extras | {""}
        ):
            continue
        yield declaration, requirement


def satisfies_lock(requirement, metadata):
    child = canonicalize_name(requirement.name)
    return child in metadata and (
        not requirement.specifier
        or requirement.specifier.contains(metadata[child]["version"], prereleases=True)
    )


def root_closure(declarations, metadata, env):
    active, todo, violations = {}, [], []
    for declaration, requirement in activated_requirements({"requires_dist": declarations}, set(), env):
        name = canonicalize_name(requirement.name)
        if name in metadata and not satisfies_lock(requirement, metadata):
            violations.append({"parent": "pyproject.toml", "requirement": declaration,
                               "locked": metadata[name]["version"]})
        active.setdefault(name, set()).update(requirement.extras)
        todo.append(name)
    return active, todo, violations


def root_declarations(root, baseline):
    if not baseline:
        return tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    inventory = json.loads(gzip.decompress((root / "docs/architecture/dependency-evidence/baseline-02d1ca3.json.gz").read_bytes()))
    return [item["declaration"] for item in inventory["dependencies"]
            if item["manifest"] == "pyproject.toml" and item["group"] == "dependencies"]


def check_metadata(root: Path, *, baseline: bool = False) -> dict:
    metadata = json.loads(gzip.decompress((root / "docs/architecture/dependency-evidence/locked-metadata.json.gz").read_bytes()))
    env = {"os_name": "posix", "implementation_name": "cpython",
           "platform_python_implementation": "CPython", "python_version": "3.14", "python_full_version": "3.14.0",
           "implementation_version": "3.14.0", "sys_platform": "linux", "platform_system": "Linux",
           "platform_machine": "x86_64", "platform_release": "", "platform_version": ""}
    declarations = root_declarations(root, baseline)
    active, todo, violations = root_closure(declarations, metadata, env)
    seen = set()
    while todo:
        name = todo.pop()
        key = (name, tuple(sorted(active[name])))
        if key in seen or name not in metadata:
            continue
        seen.add(key)
        for declaration, requirement in activated_requirements(metadata[name], active[name], env):
            child = canonicalize_name(requirement.name)
            if not satisfies_lock(requirement, metadata):
                violations.append({"parent": name, "requirement": declaration,
                                   "locked": metadata.get(child, {}).get("version")})
            if child not in active or not requirement.extras.issubset(active[child]):
                active.setdefault(child, set()).update(requirement.extras)
                todo.append(child)
    return {"kind": "modeled-metadata-only", "environment": env,
            "active_packages": len(active), "unresolved": sorted(set(active) - set(metadata)),
            "violations": violations}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true", help="Use root declarations from the immutable baseline inventory")
    args = parser.parse_args()
    print(json.dumps(check_metadata(Path(__file__).resolve().parents[1], baseline=args.baseline), indent=2, sort_keys=True))
