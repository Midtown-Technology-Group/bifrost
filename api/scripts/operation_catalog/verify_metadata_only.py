#!/usr/bin/env python3
"""Compare route modules to a git base after removing only catalog metadata.

This is a source proof, independent of application imports or external services.
The entire module AST must be identical, including route order, decorators,
handler signatures (Annotated/Depends aliases), and handler authorization logic.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_MODULE = "src.services.operation_catalog"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}


class WithoutCatalogMetadata(ast.NodeTransformer):
    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom | None:
        if node.module == CATALOG_MODULE:
            node.names = [alias for alias in node.names if alias.name != "operation_route"]
            return node if node.names else None
        return node

    def visit_Call(self, node: ast.Call) -> ast.Call:
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr in HTTP_METHODS:
            node.keywords = [
                keyword for keyword in node.keywords
                if keyword.arg != "operation_id" and not (
                    keyword.arg is None
                    and isinstance(keyword.value, ast.Call)
                    and isinstance(keyword.value.func, ast.Name)
                    and keyword.value.func.id == "operation_route"
                )
            ]
        return node


def normalized(source: str) -> str:
    return ast.dump(WithoutCatalogMetadata().visit(ast.parse(source)), include_attributes=False)


def route_dependencies(source: str) -> list[dict[str, object]]:
    """Dump route->signature/decorators mapping, preserving source order."""
    tree = WithoutCatalogMetadata().visit(ast.parse(source))
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = [
            decorator for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in HTTP_METHODS
        ]
        for route in routes:
            rows.append({
                "handler": node.name,
                "route": ast.unparse(route),
                "signature": ast.unparse(node.args),
                "decorators": [ast.unparse(d) for d in node.decorator_list],
            })
    return rows


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT).decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    base_sha = git("rev-parse", args.base_ref).strip()
    files = git("ls-tree", "-r", "--name-only", base_sha, "api/src/routers", "api/shared", "api/src/main.py").splitlines()
    proof_files: list[dict[str, object]] = []
    proof: dict[str, object] = {"base_sha": base_sha, "files": proof_files}
    failures = []
    for relative in files:
        if not relative.endswith(".py"):
            continue
        before = git("show", f"{base_sha}:{relative}").replace("\r\n", "\n")
        path = REPO_ROOT / relative
        after = path.read_text(encoding="utf-8") if path.is_file() else ""
        if before == after:
            continue
        before_ast, after_ast = normalized(before), normalized(after)
        before_routes, after_routes = route_dependencies(before), route_dependencies(after)
        row = {
            "path": relative,
            "normalized_before_sha256": hashlib.sha256(before_ast.encode()).hexdigest(),
            "normalized_after_sha256": hashlib.sha256(after_ast.encode()).hexdigest(),
            "before_route_dependencies": before_routes,
            "after_route_dependencies": after_routes,
            "identical": before_ast == after_ast and before_routes == after_routes,
        }
        proof_files.append(row)
        if not row["identical"]:
            failures.append(relative)
    proof["result"] = "FAIL" if failures else "PASS"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(proof, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n",
        )
    print(f"{proof['result']}: {len(proof_files)} changed modules; dependency/handler diff: {failures}")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
