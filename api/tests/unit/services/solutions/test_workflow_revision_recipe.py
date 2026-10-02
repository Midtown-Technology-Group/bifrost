"""Reviewed registration compilation never imports carried user source."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.workflow_revision import require_compatible_parameters
from src.services.solutions.workflow_revision_recipe import (
    WORKFLOW_RECIPE_SCHEMA, ReviewedWorkflowRecipe, WorkflowRecipeError, compile_workflow_registrations,
)

BOUNDS = {"max_duration_seconds": 30, "max_external_calls": 10, "max_records_read": 100, "max_output_bytes": 4096}


def recipe():
    return {"schema_version": WORKFLOW_RECIPE_SCHEMA, "solution_id": str(uuid4()),
        "files": {"demo.py": "solutions/demo.py"}, "shared_tables": {},
        "workflows": [{"id": str(uuid4()), "path": "demo.py", "function_name": "run",
            "organization_id": None, "runtime_bounds": BOUNDS, "controls": {}}]}


def compile_source(source, spec=None):
    return next(iter(compile_workflow_registrations(ReviewedWorkflowRecipe.model_validate(spec or recipe()),
        {"demo.py": source.encode()}, WorkflowIndexer(AsyncMock())).values()))


def test_compiler_carries_literal_defaults_and_new_keyword_schema_without_execution():
    entity = compile_source('''from bifrost import workflow as wf
raise RuntimeError("must never execute source")
@wf(name="Run task", description="Reviewed description", tags=["task"], effects=[])
async def run(user: str = "system", *, count: int = 3):
    return user
''')
    definition = entity.model_dump(mode="json")["definition"]
    assert definition["parameters_schema"]["properties"]["user"]["default"] == "system"
    assert definition["parameters_schema"]["properties"]["count"]["default"] == 3
    assert definition["name"] == "Run task"
    assert definition["timeout_seconds"] == 30
    assert definition["effects"] == []
    assert definition["endpoint_enabled"] is False
    assert definition["role_ids"] == []


@pytest.mark.parametrize("decorator,kind", [("workflow", "workflow"), ("tool", "tool"), ("data_provider", "data_provider")])
def test_compiler_requires_actual_supported_bifrost_decorators(decorator, kind):
    entity = compile_source(f"import bifrost as sdk\n@sdk.{decorator}(effects=[])\nasync def run():\n    return 1\n")
    assert entity.definition["type"] == kind


@pytest.mark.parametrize("source", [
    "from bifrost import workflow\n@workflow(name=get_name())\nasync def run(): pass",
    "from bifrost import workflow\n@workflow(**settings)\nasync def run(): pass",
    "from bifrost import workflow\n@workflow(timeout=60)\nasync def run(): pass",
    "from bifrost import workflow\n@workflow\nasync def run(user=os.getenv('USER')): pass",
    "from bifrost import workflow\n@workflow\nasync def run(user, /): pass",
    "from bifrost import workflow\n@workflow\nasync def run(*args): pass",
    "from bifrost import workflow\nworkflow = other\n@workflow\nasync def run(): pass",
    "from other import workflow\n@workflow\nasync def run(): pass",
    "from bifrost import workflow\n@wrapper\n@workflow\nasync def run(): pass",
    "from bifrost import service\n@service\nasync def run(): pass",
    "from bifrost import workflow\n@workflow\nasync def run(): pass\nasync def run(): pass",
])
def test_compiler_rejects_ambiguous_or_dynamic_registration(source):
    with pytest.raises(WorkflowRecipeError):
        compile_source(source)


def test_recipe_cannot_weaken_bounds_declared_by_source():
    with pytest.raises(WorkflowRecipeError, match="exceed"):
        compile_source('from bifrost import workflow\n@workflow(enforced_bounds={"max_duration_seconds": 10})\nasync def run(): pass')


@pytest.mark.parametrize("rebind", [
    "from other import workflow", "import other as workflow",
    "for workflow in values:\n pass", "with manager() as workflow:\n pass",
    "(workflow, other) = values", "if flag:\n from other import workflow",
    "del workflow", "match value:\n case {'x': workflow}: pass",
])
def test_module_import_and_control_block_rebinding_is_rejected(rebind):
    with pytest.raises(WorkflowRecipeError, match="shadowed"):
        compile_source(f"from bifrost import workflow\n{rebind}\n@workflow\nasync def run(): pass")


def test_function_local_bindings_do_not_shadow_the_module_decorator():
    compile_source("from bifrost import workflow\ndef helper():\n workflow = 'local'\n return workflow\n@workflow\nasync def run(): pass")


def test_named_literal_scalar_defaults_are_carried_without_importing_source():
    entity = compile_source('''from bifrost import workflow
DEFAULT_MAX_TENANTS: int = 10
DEFAULT_MODE = "inspect"
@workflow
async def run(max_tenants: int = DEFAULT_MAX_TENANTS, *, mode: str = DEFAULT_MODE):
    raise RuntimeError("must never execute")
''')
    schema = entity.model_dump(mode="json")["definition"]["parameters_schema"]
    assert schema["properties"]["max_tenants"]["default"] == 10
    assert schema["properties"]["mode"]["default"] == "inspect"
    assert "required" not in schema


@pytest.mark.parametrize("before,after", [
    ("DEFAULT_LIMIT = get_limit()", ""),
    ("DEFAULT_LIMIT = 10\nDEFAULT_LIMIT = 20", ""),
    ("if enabled:\n    DEFAULT_LIMIT = 10", ""),
    ("DEFAULT_LIMIT = [10]", ""),
    ("", "DEFAULT_LIMIT = 10"),
    ("DEFAULT_LIMIT = 10", "del DEFAULT_LIMIT"),
    ("from other import DEFAULT_LIMIT\nDEFAULT_LIMIT = 10", ""),
])
def test_default_constant_resolution_rejects_dynamic_mutable_or_ambiguous_bindings(before, after):
    with pytest.raises(WorkflowRecipeError):
        compile_source(f"from bifrost import workflow\n{before}\n@workflow\nasync def run(limit: int = DEFAULT_LIMIT): pass\n{after}")


def test_legacy_source_id_must_match_the_existing_reviewed_uuid():
    spec = recipe()
    identity = spec["workflows"][0]["id"]
    entity = compile_source(f'from bifrost import workflow\n@workflow(id="{identity}")\nasync def run(): pass', spec)
    assert str(entity.resolved_id) == identity
    with pytest.raises(WorkflowRecipeError, match="identity differs"):
        compile_source(f'from bifrost import workflow\n@workflow(id="{uuid4()}")\nasync def run(): pass', spec)


@pytest.mark.parametrize("mutation", ["duplicate_id", "duplicate_entrypoint", "unsafe_path", "missing_source", "bool_bound", "credential", "unknown_field"])
def test_recipe_rejects_incomplete_or_unsafe_full_install(mutation):
    spec = recipe()
    item = spec["workflows"][0]
    if mutation == "duplicate_id":
        spec["workflows"].append({**item, "function_name": "other"})
    elif mutation == "duplicate_entrypoint":
        spec["workflows"].append({**item, "id": str(uuid4())})
    elif mutation == "unsafe_path":
        item["path"] = "../demo.py"
    elif mutation == "missing_source":
        item["path"] = "other.py"
    elif mutation == "bool_bound":
        item["runtime_bounds"] = {**BOUNDS, "max_duration_seconds": True}
    elif mutation == "credential":
        item["controls"]["api_key_hash"] = "must not accept instance credentials"
    else:
        spec["execute_after_delivery"] = True
    with pytest.raises(ValidationError):
        ReviewedWorkflowRecipe.model_validate(spec)


def test_default_and_optional_parameter_updates_preserve_existing_callers():
    old = {"type": "object", "properties": {"user": {"type": "string", "default": "root"}}, "additionalProperties": False}
    new = {"type": "object", "properties": {"user": {"type": "string", "default": "system"}, "count": {"type": "integer", "default": 1}}, "additionalProperties": False}
    require_compatible_parameters(old, new)


@pytest.mark.parametrize("change", ["remove", "type", "required", "default_property"])
def test_breaking_parameter_updates_require_live_caller_reconciliation(change):
    old = {"properties": {"user": {"type": "string"}}, "required": ["user"], "additionalProperties": False}
    new = {"properties": {"user": {"type": "string"}}, "required": ["user"], "additionalProperties": False}
    if change == "remove":
        new["properties"] = {}
    elif change == "type":
        new["properties"]["user"] = {"type": "integer"}
    elif change == "required":
        new["properties"]["other"] = {"type": "string"}
        new["required"].append("other")
    else:
        old["properties"]["user"] = {"type": "object", "properties": {"default": {"type": "string"}}}
        new["properties"]["user"] = {"type": "object", "properties": {"default": {"type": "integer"}}}
    with pytest.raises(ValueError, match="caller reconciliation"):
        require_compatible_parameters(old, new)


@pytest.mark.parametrize("old_property", [
    {"type": "string"},
    {"type": "string", "enum": ["success", "failed"]},
    {"type": "object", "properties": {"default": {"type": "string"}}},
])
def test_nullable_widening_keeps_the_previous_parameter_branch_exact(old_property):
    old = {"properties": {"result": old_property}, "required": ["result"]}
    nullable = {"properties": {"result": {"anyOf": [old_property, {"type": "null"}]}}}
    require_compatible_parameters(old, nullable)
    with pytest.raises(ValueError, match="caller reconciliation"):
        require_compatible_parameters(nullable, old)


@pytest.mark.parametrize("new_property", [
    {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    {"anyOf": [{"type": "string", "enum": ["success", "failed"]}, {"type": "null"}], "maxLength": 2},
    {"anyOf": [{"type": "string", "enum": ["success"]}, {"type": "null"}]},
])
def test_nullable_union_cannot_hide_changed_or_narrower_type_constraints(new_property):
    old = {"properties": {"result": {"type": "string", "enum": ["success", "failed"]}}}
    desired = {"properties": {"result": new_property}}
    with pytest.raises(ValueError, match="caller reconciliation"):
        require_compatible_parameters(old, desired)
