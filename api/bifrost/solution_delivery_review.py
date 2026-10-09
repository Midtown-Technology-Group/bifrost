"""Compile reviewed workflow registrations from carried Git source, without imports.

Recipes describe a complete install, not a diff against an earlier CI run.
Instance credentials are deliberately absent from this contract.
"""

from __future__ import annotations

import ast
import json
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import UUID, uuid5

from bifrost.workflow_removal_evidence import (
    WorkflowRemovalEvidence, WorkflowRemovalReviewContext, removal_binding,
    verify_workflow_removal_evidence,
)

from bifrost.root_file_bindings import RootFileBinding, require_root_file_bindings
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bifrost.workspace_effects import normalize_workflow_bounds, normalize_workflow_effects
from bifrost.contracts.workflows import ExecutionRetryPolicy
from bifrost.workflow_parameters import WorkflowParameterCompiler

WORKFLOW_PARAMETERS_SCHEMA_CONTRACT = "bifrost.workflow-parameters-schema/v1"
DELIVERY_REVIEW_CONTRACT = "bifrost.solution-delivery-review/v2"
MAX_DEPLOYMENT_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_DEPLOYMENT_RESOURCES_BYTES = 10 * 1024 * 1024
PROTECTED_REGISTRATION_FIELDS = (
    "path", "function_name", "name", "type", "organization_id",
    "endpoint_enabled", "public_endpoint", "access_level", "role_ids",
    "allowed_methods", "disable_global_key", "execution_mode", "retry_policy", "cache_ttl_seconds",
)


def delivery_path(value: str) -> str:
    if (not value or value.startswith("/") or "\\" in value or ":" in value
            or "\0" in value or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Expected a normalized repository-relative delivery path")
    return value




class SharedRootTableGrant(BaseModel):
    """Reviewed access to an existing Root table without adopting its data."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    table_id: UUID
    metadata_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    access: Literal["read", "read-write"] = "read"
    organization_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)


class SharedRootTableBinding(SharedRootTableGrant):
    """Exact reviewed scopes for one alias, preserving legacy single-table bytes."""
    additional_scopes: tuple[SharedRootTableGrant, ...] | None = Field(
        default=None, max_length=99, exclude_if=lambda value: not value,
    )

    def grants(self) -> tuple[SharedRootTableGrant, ...]:
        return (self, *(self.additional_scopes or ()))

    def with_scope(self, grant: SharedRootTableGrant) -> SharedRootTableBinding:
        """Append an exact grant through the same canonical scope validation."""
        return SharedRootTableBinding(
            **self.model_dump(exclude={"additional_scopes"}),
            additional_scopes=(*(self.additional_scopes or ()), grant),
        )

    @model_validator(mode="after")
    def unique_scopes(self) -> SharedRootTableBinding:
        grants = self.grants()
        if len({grant.organization_id for grant in grants}) != len(grants):
            raise ValueError("Shared table organization scopes must be unique")
        if len({grant.table_id for grant in grants}) != len(grants):
            raise ValueError("Shared table IDs must be unique")
        return self


def require_shared_table_bindings(bindings: dict[str, SharedRootTableBinding]) -> None:
    """Bound every exact grant and reject duplicate identities across aliases."""
    grants = [grant for binding in bindings.values() for grant in binding.grants()]
    if len(grants) > 100:
        raise ValueError("Shared table grants exceed the install limit")
    if len({grant.table_id for grant in grants}) != len(grants):
        raise ValueError("Shared table IDs must be unique")
    if any(re.fullmatch(r"[a-z][a-z0-9_-]{0,254}", name) is None for name in bindings):
        raise ValueError("Shared table names must be canonical")


@dataclass(frozen=True)
class CompiledWorkflowRegistration:
    portable_ref: str
    resolved_id: UUID
    definition: dict[str, Any]
    source_ref: str
    source_hash: str


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
    resources: dict[str, str] = Field(default_factory=dict, max_length=256, exclude_if=lambda value: not value)
    root_file_bindings: dict[str, RootFileBinding] = Field(default_factory=dict, max_length=100, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def unique_install(self) -> ReviewedWorkflowRecipe:
        require_root_file_bindings(self.root_file_bindings)
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
        if set(self.resources) & set(self.files):
            raise ValueError("Resource paths cannot replace Python sources")
        for runtime_path, git_path in self.resources.items():
            for path in (runtime_path, git_path):
                delivery_path(path)
                if (len(path) > 1000 or not path.lower().endswith((".json", ".ps1"))
                        or any(part.startswith(".") for part in path.split("/"))
                        or path.rsplit("/", 1)[-1].lower() == "storage-state.json"):
                    raise ValueError("Resources require reviewed JSON or PowerShell source, without auth or hidden state paths")
        require_shared_table_bindings(self.shared_tables)
        if any(binding.organization_id is not None
               and any(item.organization_id not in {None, binding.organization_id} for item in self.workflows)
               for group in self.shared_tables.values() for binding in group.grants()):
            raise ValueError("Organization Root table bindings require every workflow to be global or in that organization")
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


def _entrypoint_parameters(source: bytes, tree: ast.Module,
                          node: ast.FunctionDef | ast.AsyncFunctionDef,
                          indexer: WorkflowParameterCompiler, path: str) -> dict[str, Any]:
    if node.args.posonlyargs or node.args.vararg:
        raise WorkflowRecipeError("Positional-only and variadic positional entrypoints are unsupported")
    positional_defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults)
    defaults = {arg.arg: _parameter_default(default, tree, node)
        for arg, default in [*zip(node.args.args, positional_defaults),
                             *zip(node.args.kwonlyargs, node.args.kw_defaults)] if default is not None}
    parameters = indexer.extract_parameters_from_source(source, node.name, path=path)
    if parameters is None:
        raise WorkflowRecipeError("Workflow parameter schema cannot be inferred")
    for parameter_name, value in defaults.items():
        if parameter_name in parameters["properties"]:
            parameters["properties"][parameter_name]["default"] = value
    return parameters


def compile_workflow_parameters(source: bytes, function_name: str, *, path: str,
                                indexer: WorkflowParameterCompiler | None = None) -> dict[str, Any]:
    """Resolve the same bounded literal defaults for legacy and reviewed source.

    Legacy decorators need not already be literal recipe declarations. Source
    is parsed only; importing or executing it is never part of this comparison.
    """
    try:
        tree = ast.parse(source.decode("utf-8"), filename=path)
    except (SyntaxError, UnicodeError) as exc:
        raise WorkflowRecipeError("Workflow source is missing or invalid UTF-8 Python") from exc
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
               and node.name == function_name]
    if len(matches) != 1:
        raise WorkflowRecipeError("Workflow entrypoint is missing or ambiguous")
    return _entrypoint_parameters(source, tree, matches[0], indexer or WorkflowParameterCompiler(), path)


def require_adoption_parameters(old: dict[str, Any], new: dict[str, Any], *,
                                attested_legacy_list: bool) -> None:
    """Preserve every old caller contract; permit reviewed optional additions.

    The legacy list stays sealed in the immutable entity rather than being
    rewritten in place. Complete modern registry schemas still require exact
    equality; adoption cannot silently project a different registration row.
    """
    def encoded(value: Any) -> str:
        # Python considers False == 0 and True == 1; parameter defaults must
        # retain their JSON type as well as their value.
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

    if encoded(old) == encoded(new):
        return
    old_properties, new_properties = old.get("properties"), new.get("properties")
    if (attested_legacy_list and isinstance(old_properties, dict) and isinstance(new_properties, dict)
            and set(old_properties) < set(new_properties)
            and encoded({key: value for key, value in old.items() if key != "properties"})
                == encoded({key: value for key, value in new.items() if key != "properties"})
            and all(encoded(new_properties[key]) == encoded(value) for key, value in old_properties.items())
            and all(isinstance(new_properties[key], dict) and "default" in new_properties[key]
                    for key in set(new_properties) - set(old_properties))):
        return
    raise WorkflowRecipeError("adoption cannot change the installed source parameter contract")


def require_executable_bindings(tree: ast.Module) -> tuple[dict[str, str], set[str]]:
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
    if any(isinstance(item, ast.Global) and protected.intersection(item.names) for item in ast.walk(tree)):
        raise WorkflowRecipeError("An executable decorator import is shadowed")
    # Inspect module bindings inside control blocks, while keeping function and
    # class locals separate. Import aliases and destructured for/with targets
    # must not replace the SDK name that the compiler recognized.
    pending: list[ast.AST] = [tree]
    imported = {name: 0 for name in protected}
    while pending:
        statement = pending.pop()
        if isinstance(statement, ast.Import | ast.ImportFrom):
            for alias in statement.names:
                if alias.name == "*":
                    raise WorkflowRecipeError("Star imports cannot preserve reviewed executable bindings")
                name = alias.asname or alias.name.split(".")[0]
                if name in imported:
                    imported[name] += 1
        if (isinstance(statement, ast.Name) and statement.id in protected
                and isinstance(statement.ctx, ast.Store | ast.Del)
                or isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler | ast.MatchAs | ast.MatchStar)
                    and statement.name in protected
                or isinstance(statement, ast.MatchMapping) and statement.rest in protected):
            raise WorkflowRecipeError("An executable decorator import is shadowed")
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            pending.extend(statement.decorator_list)
            pending.extend(statement.args.defaults)
            pending.extend(item for item in statement.args.kw_defaults if item is not None)
            pending.extend(item.annotation for item in [*statement.args.posonlyargs, *statement.args.args,
                *statement.args.kwonlyargs] if item.annotation is not None)
            if statement.returns is not None:
                pending.append(statement.returns)
        elif isinstance(statement, ast.ClassDef):
            pending.extend([*statement.decorator_list, *statement.bases, *statement.keywords])
        elif not isinstance(statement, ast.Lambda):
            pending.extend(ast.iter_child_nodes(statement))
    if any(count != 1 for count in imported.values()):
        raise WorkflowRecipeError("An executable decorator import is shadowed")
    return bindings, modules


def _decorator(tree: ast.Module, node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, dict[str, Any]]:
    bindings, modules = require_executable_bindings(tree)
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
    recipe: ReviewedWorkflowRecipe, files: dict[str, bytes], indexer: WorkflowParameterCompiler | None = None,
) -> dict[str, CompiledWorkflowRegistration]:
    """Use the same AST parameter indexer as normal registration, never exec()."""
    indexer = indexer or WorkflowParameterCompiler()
    if set(files) != set(recipe.files):
        raise WorkflowRecipeError("Recipe and carried source paths differ")
    entities: dict[str, CompiledWorkflowRegistration] = {}
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
        parameters = _entrypoint_parameters(files[item.path], tree, node, indexer, item.path)
        kind, declarations = _decorator(tree, node)
        if "id" in declarations:
            try:
                if not isinstance(declarations["id"], str):
                    raise ValueError("source identity must be a UUID string")
                declared_id = UUID(declarations["id"])
                # Captured bundles retain author-time UUIDs. Normal Solution
                # installation resolves them in this install's namespace;
                # reviewed delivery must preserve that same installed row.
                if item.id not in {declared_id, uuid5(recipe.solution_id, str(declared_id))}:
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
        entities[portable_ref] = CompiledWorkflowRegistration(portable_ref=portable_ref, resolved_id=item.id,
            definition=definition, source_ref=item.path, source_hash="sha256:" + hashlib.sha256(files[item.path]).hexdigest())
    return entities


def validate_resource_files(recipe: ReviewedWorkflowRecipe, resources: dict[str, bytes],
                            files: dict[str, bytes]) -> None:
    if (set(resources) != set(recipe.resources)
            or any(not 1 <= len(content) <= MAX_DEPLOYMENT_RESOURCE_BYTES for content in resources.values())
            or sum(map(len, resources.values())) > MAX_DEPLOYMENT_RESOURCES_BYTES):
        raise WorkflowRecipeError("Resources differ from the complete reviewed map or byte bounds")
    for path, raw in files.items():
        _verify_resource_calls(path, raw, set(resources))


def _constant_path(node: ast.expr, tree: ast.Module) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.Name) or re.fullmatch(r"[A-Z_][A-Z0-9_]*", node.id) is None:
        raise WorkflowRecipeError("Resource paths must be literal strings or immutable module constants")
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
        raise WorkflowRecipeError("Resource path constant is mutable or ambiguous")
    return values[0].value


def _verify_resource_calls(path: str, raw: bytes, resource_paths: set[str]) -> None:
    try:
        tree = ast.parse(raw, filename=path)
    except (SyntaxError, UnicodeError) as exc:
        raise WorkflowRecipeError("Source resource references cannot be parsed") from exc
    aliases = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"bifrost", "bifrost.resources"}:
            if any(item.name == "*" for item in node.names):
                raise WorkflowRecipeError("Import the resource SDK explicitly with from bifrost import resources")
            aliases.extend(item.asname or item.name for item in node.names if item.name == "resources")
        elif isinstance(node, ast.Import) and any(item.name == "bifrost" or item.name.startswith("bifrost.resources") for item in node.names):
            raise WorkflowRecipeError("Import the resource SDK explicitly with from bifrost import resources")
    if not aliases:
        return
    if len(aliases) != len(set(aliases)):
        raise WorkflowRecipeError("Resource SDK namespace is ambiguous")
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
            raise WorkflowRecipeError("Resource SDK namespace is rebound")
        if not isinstance(node, ast.Name) or node.id not in names or not isinstance(node.ctx, ast.Load):
            continue
        method = parents.get(node)
        call = parents.get(method) if isinstance(method, ast.Attribute) else None
        if (not isinstance(method, ast.Attribute) or method.attr not in {"read", "read_bytes"}
                or not isinstance(call, ast.Call) or call.func is not method):
            raise WorkflowRecipeError("Resource SDK reads must be explicit and statically reviewed")
        if len(call.args) == 1 and not call.keywords:
            argument = call.args[0]
        elif not call.args and len(call.keywords) == 1 and call.keywords[0].arg == "path":
            argument = call.keywords[0].value
        else:
            raise WorkflowRecipeError("Resource reads require one reviewed path")
        if _constant_path(argument, tree) not in resource_paths:
            raise WorkflowRecipeError("Source reads a resource absent from the reviewed map")




def _without_presentation(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: (
            {name: _without_presentation(schema) for name, schema in item.items()}
            if key == "properties" else item if key in {"enum", "const"} else _without_presentation(item)
        ) for key, item in value.items() if key not in {"title", "default"}}
    if isinstance(value, list | tuple):
        return [_without_presentation(item) for item in value]
    return value


def _accepts_previous_parameter_schema(old: dict, new: dict) -> bool:
    previous, desired = _without_presentation(old), _without_presentation(new)
    if previous == desired:
        return True
    # Adding null preserves every previously accepted value. Keep the original
    # branch exact and reject other schema changes or outer constraints; this
    # is deliberately narrower than general JSON Schema subsumption.
    return (isinstance(desired, dict) and set(desired) == {"anyOf"}
        and isinstance(desired["anyOf"], list) and len(desired["anyOf"]) == 2
        and previous in desired["anyOf"] and {"type": "null"} in desired["anyOf"])


def require_compatible_parameters(old: dict, new: dict) -> None:
    """Existing callers keep their accepted keyword set and type constraints."""
    old_properties, new_properties = old.get("properties", {}), new.get("properties", {})
    if (not set(old_properties).issubset(new_properties)
            or not set(new.get("required", [])).issubset(old.get("required", []))
            or old.get("additionalProperties") is True and new.get("additionalProperties") is not True
            or any(not _accepts_previous_parameter_schema(schema, new_properties[name])
                   for name, schema in old_properties.items())):
        raise WorkflowRecipeError("Breaking parameter changes require verified live caller reconciliation")



def _baseline_is_current(recipe_value: dict, files: dict[str, bytes], resources: dict[str, bytes],
                         previous_recipe_value: dict | None, previous_files: dict[str, bytes] | None,
                         previous_resources: dict[str, bytes] | None) -> bool:
    """An installed baseline with byte-identical inputs needs no second compilation.

    Every baseline comparison passes by construction against the identical inputs
    already compiled, so the equality proof replaces the duplicate work. Reviewing
    the same recipe twice is otherwise the dominant cost of the offline delivery
    review for unchanged installations.
    """
    if previous_recipe_value is None or (previous_files or {}) != files or (previous_resources or {}) != resources:
        return False
    # Recipe equality must keep scalar types: plain dict equality treats 20 == 20.0
    # and False == 0 as identical, which would skip the baseline's strict validation
    # and certify a schema-invalid installed recipe as checked. JSON text separates
    # those collisions while ignoring key order.
    return json.dumps(previous_recipe_value, sort_keys=True) == json.dumps(recipe_value, sort_keys=True)


def review_workflow_recipe(recipe_value: dict, files: dict[str, bytes], resources: dict[str, bytes], *,
                           previous_recipe_value: dict | None = None,
                           previous_files: dict[str, bytes] | None = None,
                           previous_resources: dict[str, bytes] | None = None,
                           owned_table_ids: tuple[UUID, ...] = (),
                           workflow_removal_evidence: dict[str, Any] | WorkflowRemovalEvidence | None = None,
                           workflow_removal_context: WorkflowRemovalReviewContext | None = None) -> dict[str, Any]:
    """Offline deterministic checks only; live ownership/callers remain unproved."""
    from bifrost.solution_source_closure import source_closure

    try:
        owned_ids = tuple(UUID(str(identity)) for identity in owned_table_ids)
    except (TypeError, ValueError) as exc:
        raise WorkflowRecipeError("Owned-table review context requires exact table UUIDs") from exc
    if len(set(owned_ids)) != len(owned_ids):
        raise WorkflowRecipeError("Owned-table review context contains duplicate UUIDs")

    def compile_complete(value, sources, resource_files):
        recipe = ReviewedWorkflowRecipe.model_validate(value)
        validate_resource_files(recipe, resource_files, sources)
        closure = source_closure(sources, {item.path for item in recipe.workflows},
            has_table_bindings=bool(recipe.shared_tables) or bool(owned_ids), has_resource_bindings=bool(recipe.resources),
            has_root_file_bindings=bool(recipe.root_file_bindings))
        if set(closure) != set(sources):
            raise WorkflowRecipeError("Recipe differs from the complete dependency closure")
        return recipe, compile_workflow_registrations(recipe, sources)

    recipe, desired = compile_complete(recipe_value, files, resources)
    removed_ids: set[str] = set()
    evidence_digest = None
    if previous_recipe_value is not None and not _baseline_is_current(
            recipe_value, files, resources, previous_recipe_value, previous_files, previous_resources):
        previous, old = compile_complete(previous_recipe_value, previous_files or {}, previous_resources or {})
        if previous.solution_id != recipe.solution_id:
            raise WorkflowRecipeError("An installed recipe cannot change its Solution identity")
        old_by_id = {item.resolved_id: item for item in old.values()}
        new_by_id = {item.resolved_id: item for item in desired.values()}
        removed_ids = {str(identity) for identity in old_by_id.keys() - new_by_id.keys()}
        if removed_ids:
            if workflow_removal_evidence is None:
                raise WorkflowRecipeError("Workflow removal requires verified live caller and trigger reconciliation")
            try:
                if workflow_removal_context is None:
                    raise ValueError("Workflow removal requires independent review context")
                context = WorkflowRemovalReviewContext.model_validate(workflow_removal_context.model_dump(mode="json"))
                if context.solution_id != str(recipe.solution_id):
                    raise ValueError("Workflow removal context Solution identity mismatch")
                binding = removal_binding(solution_id=context.solution_id,
                    instance_origin=context.instance_origin, recipe_path=context.recipe_path,
                    base_sha=context.base_sha, base_recipe=previous_recipe_value, candidate_recipe=recipe_value,
                    base_files=previous_files or {}, candidate_files=files,
                    base_resources=previous_resources or {}, candidate_resources=resources)
                evidence_digest = verify_workflow_removal_evidence(workflow_removal_evidence,
                    context=context, expected_binding=binding, removed_ids=removed_ids)
            except (ValueError, TypeError, AttributeError) as exc:
                raise WorkflowRecipeError(f"Workflow removal evidence rejected: {exc}") from exc
        for identity, item in new_by_id.items():
            definition = item.definition
            if identity in old_by_id:
                baseline = old_by_id[identity].definition
                if any(definition.get(key) != baseline.get(key) for key in PROTECTED_REGISTRATION_FIELDS):
                    raise WorkflowRecipeError("Rename, scope, type, access, endpoint, mode, cache or retry changes require a reviewed caller/control-plane adapter")
                require_compatible_parameters(baseline["parameters_schema"], definition["parameters_schema"])
            elif (definition["endpoint_enabled"] or definition["public_endpoint"]
                    or definition["access_level"] != "role_based"):
                raise WorkflowRecipeError("New workflows require role-based access and disabled endpoints")
    if workflow_removal_evidence is not None and not removed_ids:
        raise WorkflowRecipeError("Workflow removal evidence requires an exact removal baseline")
    return {"schema_version": DELIVERY_REVIEW_CONTRACT, "solution_id": str(recipe.solution_id),
        "workflow_removal_evidence_verified": evidence_digest is not None,
        "removed_workflow_ids": sorted(removed_ids), "workflow_removal_evidence_digest": evidence_digest,
        "source_paths": sorted(files), "resource_paths": sorted(resources),
        "workflow_ids": sorted(str(item.resolved_id) for item in desired.values()),
        **({"owned_table_review_ids": sorted(str(identity) for identity in owned_ids)} if owned_ids else {}),
        "previous_recipe_checked": previous_recipe_value is not None,
        "live_state_verified": False, "runtime_verified": False}


def review_solution_recipe(recipe_value: dict, files: dict[str, bytes], resources: dict[str, bytes], *,
                           previous_recipe_value: dict | None = None,
                           previous_files: dict[str, bytes] | None = None,
                           previous_resources: dict[str, bytes] | None = None,
                           owned_table_ids: tuple[UUID, ...] = (),
                           workflow_removal_evidence: dict[str, Any] | WorkflowRemovalEvidence | None = None,
                           workflow_removal_context: WorkflowRemovalReviewContext | None = None) -> dict[str, Any]:
    """Review workflow delivery or the legacy body-only source adapter offline."""
    from bifrost.solution_source_closure import source_closure
    if previous_recipe_value is not None and recipe_value.get("schema_version") != previous_recipe_value.get("schema_version"):
        raise WorkflowRecipeError("Changing an enabled recipe adapter requires a reconciled live baseline")
    if recipe_value.get("schema_version") == WORKFLOW_RECIPE_SCHEMA:
        return review_workflow_recipe(recipe_value, files, resources,
            previous_recipe_value=previous_recipe_value, previous_files=previous_files,
            previous_resources=previous_resources, owned_table_ids=owned_table_ids,
            workflow_removal_evidence=workflow_removal_evidence, workflow_removal_context=workflow_removal_context)
    if workflow_removal_evidence is not None or workflow_removal_context is not None:
        raise WorkflowRecipeError("Workflow removal evidence requires the reviewed workflow adapter")
    if owned_table_ids:
        raise WorkflowRecipeError("Owned-table review context requires the reviewed workflow adapter")

    def signatures(value, sources, resource_files):
        if (set(value) != {"schema_version", "solution_id", "files"}
                or value["schema_version"] != "bifrost.solution-source-delivery/v1"
                or resource_files or not isinstance(value["files"], dict)
                or not 1 <= len(value["files"]) <= 256 or set(sources) != set(value["files"])):
            raise WorkflowRecipeError("Legacy source delivery requires a complete Python-only recipe")
        UUID(value["solution_id"])
        result = {}
        for path, raw in sources.items():
            for filename in (path, value["files"][path]):
                delivery_path(filename)
                if not filename.endswith(".py"):
                    raise WorkflowRecipeError("Legacy sources must be Python files")
            tree = ast.parse(raw, filename=path)
            aliases, modules = require_executable_bindings(tree)
            for node in tree.body:
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                targets = [item.func if isinstance(item, ast.Call) else item for item in node.decorator_list]
                if not any(isinstance(item, ast.Name) and item.id in aliases
                        or isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name)
                            and item.value.id in modules and item.attr in {"workflow", "tool", "data_provider"}
                        for item in targets):
                    continue
                ref = f"{path}::{node.name}"
                if ref in result:
                    raise WorkflowRecipeError("Legacy source entrypoint is ambiguous")
                result[ref] = (ast.dump(node.args, include_attributes=False),
                    tuple(ast.dump(item, include_attributes=False) for item in node.decorator_list),
                    isinstance(node, ast.AsyncFunctionDef),
                    compile_workflow_parameters(raw, node.name, path=path))
        if not result:
            raise WorkflowRecipeError("Legacy source recipe has no explicit workflow entrypoints")
        # The legacy recipe does not describe its installed table grants.
        # Deployment still proves those grants against the immutable live base.
        closure = source_closure(sources, {ref.split("::")[0] for ref in result}, has_table_bindings=True)
        if set(closure) != set(sources):
            raise WorkflowRecipeError("Legacy recipe differs from the complete dependency closure")
        return result

    current = signatures(recipe_value, files, resources)
    if previous_recipe_value is not None and not _baseline_is_current(
            recipe_value, files, resources, previous_recipe_value, previous_files, previous_resources):
        if recipe_value["solution_id"] != previous_recipe_value.get("solution_id"):
            raise WorkflowRecipeError("An installed recipe cannot change its Solution identity")
        if current != signatures(previous_recipe_value, previous_files or {}, previous_resources or {}):
            raise WorkflowRecipeError("Legacy source delivery cannot revise workflow registration or signatures")
    return {"schema_version": DELIVERY_REVIEW_CONTRACT, "solution_id": recipe_value["solution_id"],
        "workflow_removal_evidence_verified": False,
        "removed_workflow_ids": [], "workflow_removal_evidence_digest": None,
        "source_paths": sorted(files), "resource_paths": [], "entrypoints": sorted(current),
        "previous_recipe_checked": previous_recipe_value is not None,
        "live_state_verified": False, "runtime_verified": False}
