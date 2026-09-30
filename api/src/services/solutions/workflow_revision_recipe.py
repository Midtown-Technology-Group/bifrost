"""Compile reviewed workflow registrations from carried Git source, without imports.

Recipes describe a complete install, not a diff against an earlier CI run.
Instance credentials are deliberately absent from this contract.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.workspace_effects import normalize_workflow_bounds, normalize_workflow_effects
from src.core.solution_delivery_policy import delivery_path
from src.models.contracts.base import ExecutionRetryPolicy
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.deployment_manifest import (
    WORKFLOW_PARAMETERS_SCHEMA_CONTRACT, RuntimeEntityDefinition, SharedRootTableBinding, sha256_digest,
)

WORKFLOW_RECIPE_SCHEMA = "bifrost.solution-workflow-delivery/v1"
WORKFLOW_REVISION_MARKER = "bifrost.solution-workflow-revision/v1"


class WorkflowRecipeError(ValueError):
    """A reviewed source declaration cannot be compiled faithfully."""


class ReviewedRuntimeBounds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    max_duration_seconds: int = Field(gt=0, le=86400)
    max_external_calls: int = Field(gt=0)
    max_records_read: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    max_records_written: int | None = Field(default=None, gt=0)
    max_output_rows: int | None = Field(default=None, gt=0)
    max_pages: int | None = Field(default=None, gt=0)


class WorkflowRegistrationControls(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    display_name: str | None = Field(default=None, max_length=255)
    execution_mode: Literal["sync", "async"] = "async"
    timeout_seconds: int = Field(default=1800, strict=True, gt=0, le=86400)
    cache_ttl_seconds: int = Field(default=0, strict=True, ge=0)
    time_saved: int = Field(default=0, strict=True, ge=0)
    value: float = Field(default=0, ge=0, allow_inf_nan=False)
    retry_policy: ExecutionRetryPolicy = Field(default_factory=ExecutionRetryPolicy)
    access_level: Literal["role_based", "authenticated", "everyone"] = "role_based"
    role_ids: list[UUID] = Field(default_factory=list, max_length=100)
    endpoint_enabled: bool = Field(default=False, strict=True)
    public_endpoint: bool = Field(default=False, strict=True)
    allowed_methods: list[Literal["GET", "POST", "PUT", "PATCH", "DELETE"]] = Field(default_factory=lambda: ["POST"], min_length=1, max_length=5)
    disable_global_key: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def unique_grants(self) -> WorkflowRegistrationControls:
        if len(self.role_ids) != len(set(self.role_ids)) or len(self.allowed_methods) != len(set(self.allowed_methods)):
            raise ValueError("Registration grants and methods must be unique")
        if self.public_endpoint and not self.endpoint_enabled:
            raise ValueError("A public endpoint must be enabled explicitly")
        return self


class ReviewedWorkflowRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    path: str
    function_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=255)
    organization_id: UUID | None
    runtime_bounds: ReviewedRuntimeBounds
    controls: WorkflowRegistrationControls

    @field_validator("path")
    @classmethod
    def safe_path(cls, path: str) -> str:
        delivery_path(path)
        if not path.endswith(".py") or len(path) > 1000:
            raise ValueError("A workflow requires a safe Python runtime path")
        return path


class ReviewedWorkflowRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["bifrost.solution-workflow-delivery/v1"]
    solution_id: UUID
    files: dict[str, str] = Field(min_length=1, max_length=256)
    workflows: list[ReviewedWorkflowRegistration] = Field(min_length=1, max_length=256)
    shared_tables: dict[str, SharedRootTableBinding] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def unique_install(self) -> ReviewedWorkflowRecipe:
        if len({item.id for item in self.workflows}) != len(self.workflows):
            raise ValueError("Workflow identities must be unique")
        if len({(item.path, item.function_name) for item in self.workflows}) != len(self.workflows):
            raise ValueError("Workflow entrypoints must be unique")
        for runtime_path, git_path in self.files.items():
            delivery_path(runtime_path)
            delivery_path(git_path)
            if not runtime_path.endswith(".py") or not git_path.endswith(".py"):
                raise ValueError("Recipe sources must be Python files")
        if any(item.path not in self.files for item in self.workflows):
            raise ValueError("Workflow entrypoint source is absent")
        if len({item.table_id for item in self.shared_tables.values()}) != len(self.shared_tables):
            raise ValueError("Shared table identities must be unique")
        if any(re.fullmatch(r"[a-z][a-z0-9_-]{0,254}", name) is None for name in self.shared_tables):
            raise ValueError("Shared table names must be canonical")
        return self


def _literal(node: ast.AST) -> Any:
    try:
        value = ast.literal_eval(node)
        # JSON serialization also rejects sets, bytes, non-finite values and
        # values whose runtime representation cannot be retained in a recipe.
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise WorkflowRecipeError("Registration declarations and defaults must be literal JSON values") from exc


def _parameter_default(default: ast.expr, tree: ast.Module,
                       entrypoint: ast.FunctionDef | ast.AsyncFunctionDef) -> Any:
    if not isinstance(default, ast.Name):
        return _literal(default)
    # Common authored defaults refer to a module-level scalar constant. Resolve
    # only one direct literal assignment before the function, with no other
    # binding or mutation anywhere in the source. Never import or evaluate it.
    name = default.id
    declarations = []
    for statement in tree.body[:tree.body.index(entrypoint)]:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets, value = [statement.target], statement.value
        else:
            continue
        if len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == name:
            if value is None:
                raise WorkflowRecipeError("Parameter default constant has no literal value")
            declarations.append(value)
    writes = sum(isinstance(node, ast.Name) and node.id == name
        and isinstance(node.ctx, ast.Store | ast.Del) for node in ast.walk(tree))
    rebound = any(
        isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) == name
        or isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.name == name
        or isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name == name
        for node in ast.walk(tree)
    )
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or len(declarations) != 1 or writes != 1 or rebound:
        raise WorkflowRecipeError("Parameter default constant is missing, mutable or ambiguous")
    value = _literal(declarations[0])
    if not isinstance(value, str | int | float | bool | type(None)):
        raise WorkflowRecipeError("Parameter default constant must be a literal scalar")
    return value


def _decorator(tree: ast.Module, node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, dict[str, Any]]:
    bindings: dict[str, str] = {}
    modules: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module in {"bifrost", "bifrost.decorators"}:
            for alias in statement.names:
                if alias.name in {"workflow", "tool", "data_provider"}:
                    bindings[alias.asname or alias.name] = alias.name
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name == "bifrost":
                    modules.add(alias.asname or alias.name)
    protected = set(bindings) | modules
    for statement in tree.body:
        targets = statement.targets if isinstance(statement, ast.Assign) else (
            [statement.target] if isinstance(statement, ast.AnnAssign | ast.AugAssign) else [])
        if any(isinstance(target, ast.Name) and target.id in protected for target in targets) or (
            isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and statement.name in protected
        ):
            raise WorkflowRecipeError("An executable decorator import is shadowed")
    if len(node.decorator_list) != 1:
        raise WorkflowRecipeError("Reviewed registration requires exactly one Bifrost decorator")
    decorator = node.decorator_list[0]
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = bindings.get(target.id) if isinstance(target, ast.Name) else None
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id in modules:
        name = target.attr if target.attr in {"workflow", "tool", "data_provider"} else None
    if name is None:
        raise WorkflowRecipeError("Reviewed registration requires a direct Bifrost workflow, tool or provider decorator")
    values: dict[str, Any] = {}
    if isinstance(decorator, ast.Call):
        if decorator.args:
            raise WorkflowRecipeError("Executable decorator positional arguments are unsupported")
        allowed = {"id", "name", "description", "category", "tags", "is_tool", "effects", "enforced_bounds", "requested_bounds"}
        for keyword in decorator.keywords:
            if keyword.arg is None or keyword.arg not in allowed or keyword.arg in values:
                raise WorkflowRecipeError("Executable decorator has an unknown or ambiguous declaration")
            values[keyword.arg] = _literal(keyword.value)
    if name != "workflow" and "is_tool" in values:
        raise WorkflowRecipeError("is_tool is supported only by @workflow")
    if type(values.get("is_tool", False)) is not bool:
        raise WorkflowRecipeError("is_tool must be a literal boolean")
    return name, values


def compile_workflow_registrations(
    recipe: ReviewedWorkflowRecipe, files: dict[str, bytes], indexer: WorkflowIndexer,
) -> dict[str, RuntimeEntityDefinition]:
    """Use the same AST parameter indexer as normal registration, never exec()."""
    if set(files) != set(recipe.files):
        raise WorkflowRecipeError("Recipe and carried source paths differ")
    entities: dict[str, RuntimeEntityDefinition] = {}
    trees: dict[str, ast.Module] = {}
    for item in recipe.workflows:
        if item.path not in trees:
            try:
                trees[item.path] = ast.parse(files[item.path].decode("utf-8"), filename=item.path)
            except (KeyError, SyntaxError, UnicodeError) as exc:
                raise WorkflowRecipeError("Workflow source is missing or invalid UTF-8 Python") from exc
        tree = trees[item.path]
        matches = [node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                   and node.name == item.function_name]
        if len(matches) != 1:
            raise WorkflowRecipeError("Workflow entrypoint is missing or ambiguous")
        node = matches[0]
        if node.args.posonlyargs or node.args.vararg:
            raise WorkflowRecipeError("Positional-only and variadic positional entrypoints are unsupported")
        positional_defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults)
        defaults = {arg.arg: _parameter_default(default, tree, node)
            for arg, default in [*zip(node.args.args, positional_defaults),
                                 *zip(node.args.kwonlyargs, node.args.kw_defaults)] if default is not None}
        kind, declarations = _decorator(tree, node)
        if "id" in declarations:
            try:
                if not isinstance(declarations["id"], str) or UUID(declarations["id"]) != item.id:
                    raise WorkflowRecipeError("Legacy source identity differs from the reviewed registration UUID")
            except ValueError as exc:
                raise WorkflowRecipeError("Legacy source identity differs from the reviewed registration UUID") from exc
        name = declarations.get("name") or node.name
        description = declarations.get("description")
        if description is None:
            description = (ast.get_docstring(node) or "").split("\n")[0].strip()
        category = declarations.get("category", "General")
        tags = declarations.get("tags") or []
        if (not isinstance(name, str) or not 1 <= len(name) <= 255
                or not isinstance(description, str) or not isinstance(category, str) or not 1 <= len(category) <= 100
                or not isinstance(tags, list) or len(tags) > 100 or any(not isinstance(tag, str) for tag in tags)):
            raise WorkflowRecipeError("Executable identity declarations have invalid types or lengths")
        try:
            effects = normalize_workflow_effects(declarations.get("effects"))
            enforced = normalize_workflow_bounds(declarations.get("enforced_bounds"), field_name="enforced_bounds")
            requested = normalize_workflow_bounds(declarations.get("requested_bounds"), field_name="requested_bounds")
        except (ValueError, TypeError) as exc:
            raise WorkflowRecipeError("Executable effect or bound declarations are invalid") from exc
        bounds = item.runtime_bounds.model_dump(exclude_none=True)
        # Recipe limits may be stricter than source declarations. A source
        # declaration can never be silently weakened by installation metadata.
        for declaration in (enforced, requested):
            if declaration:
                for key, limit in asdict(declaration).items():
                    if limit is not None and (key not in bounds or bounds[key] > limit):
                        raise WorkflowRecipeError("Reviewed runtime bounds exceed a source declaration")
        parameters = indexer.extract_parameters_from_source(files[item.path], item.function_name, path=item.path)
        if parameters is None:
            raise WorkflowRecipeError("Workflow parameter schema cannot be inferred")
        for parameter_name, value in defaults.items():
            if parameter_name in parameters["properties"]:
                parameters["properties"][parameter_name]["default"] = value
        controls = item.controls.model_dump(mode="json")
        controls["role_ids"] = sorted(controls["role_ids"])
        controls["allowed_methods"] = sorted(controls["allowed_methods"])
        controls["timeout_seconds"] = min(item.controls.timeout_seconds, bounds["max_duration_seconds"])
        workflow_type = "tool" if declarations.get("is_tool") else kind
        definition = {**controls, "path": item.path, "function_name": item.function_name,
            "name": name, "description": description, "category": category, "tags": tags,
            "type": workflow_type, "tool_description": description if workflow_type == "tool" else None,
            "organization_id": str(item.organization_id) if item.organization_id else None,
            "parameters_schema": parameters, "runtime_bounds": bounds,
            "parameters_schema_contract": WORKFLOW_PARAMETERS_SCHEMA_CONTRACT,
            "effects": [asdict(effect) for effect in effects] if effects is not None else None,
            "source_enforced_bounds": asdict(enforced) if enforced else None,
            "source_requested_bounds": asdict(requested) if requested else None}
        portable_ref = f"{item.path}::{item.function_name}"
        entities[portable_ref] = RuntimeEntityDefinition(portable_ref=portable_ref, resolved_id=item.id,
            definition=definition, source_ref=item.path, source_hash=sha256_digest(files[item.path]))
    return entities
