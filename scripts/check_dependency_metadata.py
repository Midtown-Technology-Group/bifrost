#!/usr/bin/env python3
"""Check recorded PyPI constraints for a modeled Linux/Python 3.14 closure.

This is metadata evidence, not a substitute for pip check in a running image.
Uses the platform's existing packaging dependency; never installs packages.
"""
import gzip
import json
import tomllib
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

def check_metadata(root: Path) -> dict:
    metadata = json.loads(gzip.decompress((root / "docs/architecture/dependency-evidence/locked-metadata.json.gz").read_bytes()))
    env = {**default_environment(), "python_version": "3.14", "python_full_version": "3.14.0",
           "implementation_version": "3.14.0", "sys_platform": "linux", "platform_system": "Linux",
           "platform_machine": "x86_64", "platform_release": "", "platform_version": ""}
    active, todo, seen, violations = {}, [], set(), []
    for declaration in tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]:
        requirement = Requirement(declaration)
        name = canonicalize_name(requirement.name)
        active.setdefault(name, set()).update(requirement.extras)
        todo.append(name)
    while todo:
        name = todo.pop()
        key = (name, tuple(sorted(active[name])))
        if key in seen or name not in metadata:
            continue
        seen.add(key)
        for declaration in metadata[name]["requires_dist"] or []:
            requirement = Requirement(declaration)
            if requirement.marker and not any(requirement.marker.evaluate({**env, "extra": extra})
                                              for extra in active[name] | {""}):
                continue
            child = canonicalize_name(requirement.name)
            if child not in metadata or (requirement.specifier and not requirement.specifier.contains(
                    metadata[child]["version"], prereleases=True)):
                violations.append({"parent": name, "requirement": declaration,
                                   "locked": metadata.get(child, {}).get("version")})
            if child not in active or not requirement.extras.issubset(active[child]):
                active.setdefault(child, set()).update(requirement.extras)
                todo.append(child)
    return {"kind": "modeled-metadata-only", "environment": env,
            "active_packages": len(active), "unresolved": sorted(set(active) - set(metadata)),
            "violations": violations}


if __name__ == "__main__":
    print(json.dumps(check_metadata(Path(__file__).resolve().parents[1]), indent=2, sort_keys=True))
