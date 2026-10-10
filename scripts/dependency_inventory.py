#!/usr/bin/env python3
"""Reproduce the repository dependency evidence; no application imports or installs.

Static consumers are evidence of use, never proof of non-use. Lock `via` edges
are resolver provenance, not wheel metadata or platform/extra activation proof.
"""
import argparse
import ast
import gzip
import hashlib
import json
import re
import subprocess
import tomllib
from collections import defaultdict
from pathlib import Path


ALIASES = {
    "pyyaml": ["yaml"], "pillow": ["PIL"], "pygithub": ["github"],
    "gitpython": ["git"], "python-dotenv": ["dotenv"], "pyjwt": ["jwt"],
    "python-multipart": ["multipart", "python_multipart"], "python-docx": ["docx"],
    "pydantic-ai-slim": ["pydantic_ai"], "pydantic-ai-harness": ["pydantic_ai_harness"],
    "opentelemetry-sdk": ["opentelemetry.sdk"],
    "opentelemetry-exporter-otlp-proto-grpc": ["opentelemetry.exporter.otlp.proto.grpc"],
    "types-aiobotocore": ["types_aiobotocore_s3"], "email-validator": ["email_validator"],
    "sentry-sdk": ["sentry_sdk"], "cairosvg": ["cairosvg"],
    "azure-identity": ["azure.identity"], "azure-storage-blob": ["azure.storage.blob"],
}


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def scope(path):
    if "/tests/" in path or path.startswith("tests/"):
        return "test"
    if path.startswith("api/bifrost/"):
        return "sdk-cli"
    if path.startswith(("api/src/", "api/shared/")):
        return "runtime"
    return "tooling-or-other-runtime"


def record_imports(tree, name, imports):
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                bindings[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
                imports.append({"path": name, "line": node.lineno, "module": item.name,
                                "symbol": None, "scope": scope(name)})
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for item in node.names:
                module = node.module or ""
                bindings[item.asname or item.name] = module + "." + item.name
                imports.append({"path": name, "line": node.lineno, "module": module,
                                "symbol": item.name, "scope": scope(name)})
    return bindings


def record_calls(tree, name, bindings, imports, dynamic):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        expr = ast.unparse(node.func)
        first, _, rest = expr.partition(".")
        resolved = bindings.get(first, first) + ("." + rest if rest else "")
        if any(token in resolved for token in
               ("import_module", "__import__", "entry_points", "spec_from_file_location")):
            argument = ast.unparse(node.args[0]) if node.args else None
            dynamic.append({"path": name, "line": node.lineno, "call": resolved,
                            "argument": argument, "resolution": "requires-runtime-review"})
        if first in bindings:
            imports.append({"path": name, "line": node.lineno, "module": bindings[first],
                            "symbol": resolved, "scope": scope(name), "kind": "call"})


def record_commands(path, name, commands):
    for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if re.search(r"pip[ -]|pipx|uvicorn|watchmedo|pytest|ruff|pyright|debugpy|requirements|entry.point", line):
            commands.append({"path": name, "line": lineno, "text": line.strip()})


def scan_consumers(root, files):
    imports, dynamic, errors, commands = [], [], [], []
    for name in files:
        path = root / name
        if not path.is_file():
            continue
        if path.suffix == ".py":
            try:
                source = path.read_text()
                tree = ast.parse(source)
            except (SyntaxError, UnicodeError) as exc:
                errors.append({"path": name, "error": str(exc)})
                continue
            bindings = record_imports(tree, name, imports)
            record_calls(tree, name, bindings, imports, dynamic)
        elif path.suffix in {".sh", ".yml", ".yaml", ".toml"} or "Dockerfile" in path.name:
            record_commands(path, name, commands)
    return imports, dynamic, errors, commands


def lock_packages(text):
    packages, current, in_via = {}, None, False
    for line in text.splitlines():
        match = re.match(r"([\w.-]+)(?:\[[^]]+\])?==([^\s\\;]+)", line)
        if match:
            current = normalize(match[1])
            in_via = False
            packages[current] = {"version": match[2], "via": []}
        elif current and line.strip().startswith("# via"):
            in_via = True
            via = line.strip().removeprefix("# via").strip()
            if via:
                packages[current]["via"].append(via)
        elif current and in_via and line.strip().startswith("#"):
            packages[current]["via"].append(line.strip().removeprefix("#").strip())
    return packages


def read_locks(root, files):
    locks = {}
    for name in files:
        if not name.endswith(".lock") or "package" in name:
            continue
        text = (root / name).read_text()
        packages = lock_packages(text)
        locks[name] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "packages": packages}
    return locks


def dependency_record(manifest, group, declaration, imports, locks):
    package = normalize(re.match(r"[\w.-]+", declaration)[0])
    roots = ALIASES.get(package, [package.replace("-", "_")])
    consumers = [index for index, item in enumerate(imports) if any(
        item["module"] == mod or item["module"].startswith(mod + ".") for mod in roots)]
    return {"manifest": manifest, "group": group, "name": package,
        "declaration": declaration, "resolved": {lock: data["packages"][package]["version"]
            for lock, data in locks.items() if package in data["packages"]},
        "module_roots": roots, "consumer_sites": consumers, "assessment": "static-use" if consumers else
        "unknown: review dynamic/framework/command/extra consumers"}


def project_dependencies(root, files, imports, locks):
    dependencies, entrypoints = [], []
    for name in files:
        if not name.endswith("pyproject.toml"):
            continue
        project = tomllib.loads((root / name).read_text()).get("project", {})
        for group, values in {"dependencies": project.get("dependencies", []),
                              **project.get("optional-dependencies", {})}.items():
            for declaration in values:
                dependencies.append(dependency_record(name, group, declaration, imports, locks))
        for script, target in project.get("scripts", {}).items():
            entrypoints.append({"manifest": name, "script": script, "target": target})
    return dependencies, entrypoints


def requirements_dependencies(root, files, imports, locks):
    dependencies = []
    for name in files:
        if not (name.endswith(("requirements.txt", ".in"))):
            continue
        for line in (root / name).read_text().splitlines():
            declaration = line.split("#", 1)[0].strip()
            if not declaration or declaration.startswith("-"):
                continue
            match = re.match(r"[\w.-]+", declaration)
            if match is None:
                continue
            dependencies.append(dependency_record(name, "requirements-source", declaration, imports, locks))
    return dependencies


def provenance_graph(locks):
    graph = defaultdict(list)
    for package, data in locks["requirements.lock"]["packages"].items():
        for parent in data["via"]:
            parent = normalize(parent.split("[")[0])
            if parent in locks["requirements.lock"]["packages"]:
                graph[parent].append(package)
    return graph


def record_descendants(dependencies, graph):
    for dep in dependencies:
        seen, todo = set(), list(graph[dep["name"]])
        while todo:
            child = todo.pop()
            if child not in seen:
                seen.add(child)
                todo.extend(graph[child])
        dep["lock_provenance_descendants"] = sorted(seen - {dep["name"]})


def inventory(root):
    files = subprocess.check_output(["git", "ls-files"], cwd=root, text=True).splitlines()
    imports, dynamic, errors, commands = scan_consumers(root, files)
    locks = read_locks(root, files)
    dependencies, entrypoints = project_dependencies(root, files, imports, locks)
    dependencies.extend(requirements_dependencies(root, files, imports, locks))
    record_descendants(dependencies, provenance_graph(locks))
    return {"schema_version": 1, "source_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "declared_python": ">=3.11 API; >=3.10 published SDK; locked/container Python 3.14",
        "limitations": ["AST cannot prove dynamic reachability or installed distribution ownership",
            "Call symbols resolve local aliases, not instance types or reexports",
            "Lock via graph overapproximates activated extras; no wheel size/runtime conflict proof",
            "Runtime-installed workspace packages are mutable and not covered by the platform lock"],
        "dependencies": dependencies, "locks": locks, "dynamic_loading": dynamic,
        "entrypoints": entrypoints, "commands": commands, "parse_errors": errors,
        "imports": imports}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = (json.dumps(inventory(args.root), sort_keys=True, separators=(",", ":")) + "\n").encode()
    if args.output.suffix == ".gz":
        args.output.write_bytes(gzip.compress(payload, mtime=0))
    else:
        args.output.write_bytes(payload)
