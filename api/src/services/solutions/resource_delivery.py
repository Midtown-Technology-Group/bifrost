"""Complete reviewed resources, static SDK references and immutable byte readback."""

from __future__ import annotations

import ast
import re
from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZIP_STORED, ZipFile

from src.services.solutions.deployment_manifest import (
    MAX_DEPLOYMENT_RESOURCE_BYTES, MAX_DEPLOYMENT_RESOURCES_BYTES, DeploymentResolutionMap, sha256_digest,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.source_revision import SolutionSourceRevisionError
from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe


def validate_resource_files(recipe: ReviewedWorkflowRecipe, resources: dict[str, bytes],
                            files: dict[str, bytes]) -> None:
    if (set(resources) != set(recipe.resources)
            or any(not 1 <= len(content) <= MAX_DEPLOYMENT_RESOURCE_BYTES for content in resources.values())
            or sum(map(len, resources.values())) > MAX_DEPLOYMENT_RESOURCES_BYTES):
        raise SolutionSourceRevisionError("Resources differ from the complete reviewed map or byte bounds")
    for path, raw in files.items():
        _verify_resource_calls(path, raw, set(resources))


def _constant_path(node: ast.expr, tree: ast.Module) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.Name) or re.fullmatch(r"[A-Z_][A-Z0-9_]*", node.id) is None:
        raise SolutionSourceRevisionError("Resource paths must be literal strings or immutable module constants")
    name = node.id
    values = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign):
            target, value = statement.target, statement.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id == name:
            values.append(value)
    bindings = sum(isinstance(item, ast.Name) and item.id == name and isinstance(item.ctx, ast.Store | ast.Del)
        for item in ast.walk(tree))
    rebound = any(
        isinstance(item, ast.alias) and (item.asname or item.name.split(".")[0]) == name
        or isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler | ast.MatchAs | ast.MatchStar)
            and item.name == name
        or isinstance(item, ast.arg) and item.arg == name
        for item in ast.walk(tree)
    )
    if (len(values) != 1 or bindings != 1 or rebound
            or not isinstance(values[0], ast.Constant) or not isinstance(values[0].value, str)):
        raise SolutionSourceRevisionError("Resource path constant is mutable or ambiguous")
    return values[0].value


def _verify_resource_calls(path: str, raw: bytes, resource_paths: set[str]) -> None:
    try:
        tree = ast.parse(raw, filename=path)
    except (SyntaxError, UnicodeError) as exc:
        raise SolutionSourceRevisionError("Source resource references cannot be parsed") from exc
    aliases = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"bifrost", "bifrost.resources"}:
            aliases.extend(item.asname or item.name for item in node.names if item.name == "resources")
        elif isinstance(node, ast.Import) and any(item.name.startswith("bifrost.resources") for item in node.names):
            raise SolutionSourceRevisionError("Import the resource SDK explicitly with from bifrost import resources")
    if not aliases:
        return
    if len(aliases) != len(set(aliases)):
        raise SolutionSourceRevisionError("Resource SDK namespace is ambiguous")
    names = set(aliases)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        parent = parents.get(node)
        if (isinstance(node, ast.Name) and node.id in names and isinstance(node.ctx, ast.Store | ast.Del)
                or isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler | ast.MatchAs | ast.MatchStar)
                    and node.name in names
                or isinstance(node, ast.arg) and node.arg in names
                or isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) in names
                    and not (isinstance(parent, ast.ImportFrom)
                        and parent.module in {"bifrost", "bifrost.resources"} and node.name == "resources")):
            raise SolutionSourceRevisionError("Resource SDK namespace is rebound")
        if not isinstance(node, ast.Name) or node.id not in names or not isinstance(node.ctx, ast.Load):
            continue
        method = parents.get(node)
        call = parents.get(method) if isinstance(method, ast.Attribute) else None
        if (not isinstance(method, ast.Attribute) or method.attr not in {"read", "read_bytes"}
                or not isinstance(call, ast.Call) or call.func is not method):
            raise SolutionSourceRevisionError("Resource SDK reads must be explicit and statically reviewed")
        if len(call.args) == 1 and not call.keywords:
            argument = call.args[0]
        elif not call.args and len(call.keywords) == 1 and call.keywords[0].arg == "path":
            argument = call.keywords[0].value
        else:
            raise SolutionSourceRevisionError("Resource reads require one reviewed path")
        if _constant_path(argument, tree) not in resource_paths:
            raise SolutionSourceRevisionError("Source reads a resource absent from the reviewed map")


async def read_deployment_resources(solution_id: UUID, deployment_id: UUID,
                                    resolution: DeploymentResolutionMap) -> dict[str, bytes]:
    if not resolution.resources:
        return {}
    storage = SolutionDeploymentStorage(solution_id, deployment_id)
    archive = await storage.read_resources_artifact()
    if len(archive) > MAX_DEPLOYMENT_RESOURCES_BYTES + 2 * 1024 * 1024:
        raise SolutionSourceRevisionError("Resource archive exceeds its byte bound")
    try:
        with ZipFile(BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if (len(members) != len(resolution.resources)
                    or {item.filename for item in members} != set(resolution.resources)
                    or any(item.compress_type != ZIP_STORED or item.is_dir()
                        or item.file_size != resolution.resources[item.filename].size_bytes for item in members)):
                raise SolutionSourceRevisionError("Resource archive members differ from immutable contracts")
            files = {item.filename: bundle.read(item) for item in members}
    except (BadZipFile, RuntimeError) as exc:
        raise SolutionSourceRevisionError("Resource archive is invalid") from exc
    for path, content in files.items():
        contract = resolution.resources[path]
        if (len(content) != contract.size_bytes or sha256_digest(content) != contract.content_hash
                or await storage.read_resource(path, contract.size_bytes) != content):
            raise SolutionSourceRevisionError("Resource archive or runtime bytes differ from immutable evidence")
    return files
