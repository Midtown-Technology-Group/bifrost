"""The downloaded compiler catches unsupported transitions without executing source."""

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest

from bifrost.solution_delivery_review import (
    WorkflowRecipeError, review_solution_recipe,
)
from shared.cli_artifact import build_cli_artifact


def test_body_only_source_review_rejects_changed_named_parameter_default():
    recipe = {"schema_version": "bifrost.solution-source-delivery/v1", "solution_id": str(uuid4()),
        "files": {"run.py": "run.py"}}
    source = b"from bifrost import workflow\nDEFAULT_LIMIT = 10\n@workflow\nasync def run(limit: int = DEFAULT_LIMIT):\n return limit\n"
    with pytest.raises(WorkflowRecipeError, match="registration or signatures"):
        review_solution_recipe(recipe, {"run.py": source.replace(b"DEFAULT_LIMIT = 10", b"DEFAULT_LIMIT = 20")}, {},
            previous_recipe_value=recipe, previous_files={"run.py": source})


def fixture():
    recipe = {"schema_version": "bifrost.solution-workflow-delivery/v1", "solution_id": str(uuid4()),
        "files": {"run.py": "solutions/demo/run.py"}, "resources": {"rates.json": "data/rates.json"},
        "workflows": [{"id": str(uuid4()), "path": "run.py", "function_name": "run",
            "organization_id": None, "controls": {}, "runtime_bounds": {
                "max_duration_seconds": 20, "max_external_calls": 10,
                "max_records_read": 100, "max_output_bytes": 4096}}]}
    code = b'from bifrost import workflow, resources\n@workflow\nasync def run(user: str = "root"):\n return await resources.read("rates.json")\n'
    return recipe, {"run.py": code}, {"rates.json": b"{}"}


def test_org_root_binding_requires_all_recipe_workflows_global_or_in_its_scope():
    from bifrost.solution_delivery_review import ReviewedWorkflowRecipe

    value, _files, _resources = fixture()
    org_id = str(uuid4())
    value["workflows"][0]["organization_id"] = org_id
    value["shared_tables"] = {"ticket_matches": {
        "table_id": str(uuid4()), "metadata_hash": "sha256:" + "a" * 64,
        "organization_id": org_id, "access": "read-write",
    }}
    assert ReviewedWorkflowRecipe.model_validate(value).shared_tables["ticket_matches"].organization_id is not None
    value["workflows"][0]["organization_id"] = None
    assert ReviewedWorkflowRecipe.model_validate(value).shared_tables["ticket_matches"].organization_id is not None
    value["workflows"][0]["organization_id"] = str(uuid4())
    with pytest.raises(ValueError, match="every workflow"):
        ReviewedWorkflowRecipe.model_validate(value)


def test_review_allows_defaults_optional_args_and_resource_updates_without_live_claims():
    old, files, resources = fixture()
    source = files["run.py"].replace(b'"root"', b'"system"').replace(b'user: str = "system"', b'user: str = "system", *, count: int = 1')
    result = review_solution_recipe(old, {"run.py": source}, {"rates.json": b'{"version":2}'},
        previous_recipe_value=old, previous_files=files, previous_resources=resources)
    assert result["previous_recipe_checked"] is True
    assert result["live_state_verified"] is False and result["runtime_verified"] is False


def test_review_allows_nullable_parameter_without_rejecting_existing_string_callers():
    recipe, old_files, resources = fixture()
    nullable = {"run.py": old_files["run.py"].replace(b'user: str = "root"', b'user: str | None = None')}
    reviewed = review_solution_recipe(recipe, nullable, resources,
        previous_recipe_value=recipe, previous_files=old_files, previous_resources=resources)
    assert reviewed["previous_recipe_checked"] is True
    with pytest.raises(WorkflowRecipeError, match="caller reconciliation"):
        review_solution_recipe(recipe, old_files, resources,
            previous_recipe_value=recipe, previous_files=nullable, previous_resources=resources)


@pytest.mark.parametrize("damage", ["rename", "break_type", "required", "remove", "expose", "missing_resource", "extra_source"])
def test_known_unsupported_transitions_fail_before_merge(damage):
    old, files, resources = fixture()
    desired = json.loads(json.dumps(old))
    sources, contents = dict(files), dict(resources)
    if damage == "rename":
        sources["run.py"] = files["run.py"].replace(b"@workflow", b'@workflow(name="Renamed")')
    elif damage == "break_type":
        sources["run.py"] = files["run.py"].replace(b"user: str", b"user: int")
    elif damage == "required":
        sources["run.py"] = files["run.py"].replace(b' = "root"', b"")
    elif damage == "remove":
        desired["workflows"][0]["id"] = str(uuid4())
    elif damage == "expose":
        desired["workflows"][0]["controls"] = {"endpoint_enabled": True}
    elif damage == "missing_resource":
        desired["resources"] = {}
        contents = {}
    else:
        desired["files"]["unused.py"] = "unused.py"
        sources["unused.py"] = b"x = 1"
    with pytest.raises(ValueError):
        review_solution_recipe(desired, sources, contents, previous_recipe_value=old,
            previous_files=files, previous_resources=resources)


def test_legacy_source_adapter_allows_body_only_and_rejects_declaration_change():
    value = {"schema_version": "bifrost.solution-source-delivery/v1", "solution_id": str(uuid4()),
        "files": {"run.py": "run.py"}}
    old = {"run.py": b"from bifrost import workflow\n@workflow\nasync def run(count: int = 1):\n return 1\n"}
    changed = {"run.py": old["run.py"].replace(b"return 1", b"return 2")}
    assert review_solution_recipe(value, changed, {}, previous_recipe_value=value, previous_files=old)["entrypoints"] == ["run.py::run"]
    changed["run.py"] = changed["run.py"].replace(b"count: int = 1", b"count: int = 2")
    with pytest.raises(WorkflowRecipeError, match="registration or signatures"):
        review_solution_recipe(value, changed, {}, previous_recipe_value=value, previous_files=old)


def test_legacy_review_rejects_rebound_decorator_before_source_activation():
    value = {"schema_version": "bifrost.solution-source-delivery/v1", "solution_id": str(uuid4()),
        "files": {"run.py": "run.py"}}
    code = b"from bifrost import workflow\nfrom functools import cache as workflow\n@workflow\nasync def run():\n return 1\n"
    with pytest.raises(WorkflowRecipeError, match="shadowed"):
        review_solution_recipe(value, {"run.py": code}, {})


@pytest.mark.parametrize("legacy", [False, True])
def test_workspace_star_import_cannot_replace_reviewed_decorator(legacy):
    value, _files, resources = fixture()
    value["files"]["helper.py"] = "helper.py"
    if legacy:
        value = {"schema_version": "bifrost.solution-source-delivery/v1",
            "solution_id": value["solution_id"], "files": value["files"]}
        resources = {}
    files = {"run.py": b"from bifrost import workflow\nfrom helper import *\n@workflow\nasync def run():\n return 1\n",
        "helper.py": b"def workflow(fn):\n return fn\n"}
    with pytest.raises(WorkflowRecipeError, match="Star imports"):
        review_solution_recipe(value, files, resources)


@pytest.mark.parametrize("module", ["bifrost", "bifrost as sdk"])
def test_resource_verifier_rejects_hidden_module_namespace(module):
    from bifrost.solution_delivery_review import _verify_resource_calls

    name = "sdk" if " as " in module else "bifrost"
    code = f"import {module}\nasync def run(path):\n return await {name}.resources.read(path)\n".encode()
    with pytest.raises(WorkflowRecipeError, match="explicitly"):
        _verify_resource_calls("run.py", code, {"rates.json"})


@pytest.mark.parametrize("legacy", [False, True])
def test_global_rebinding_in_helper_cannot_replace_executable_decorator(legacy):
    value, files, resources = fixture()
    code = b"from bifrost import workflow\ndef replace():\n global workflow\n workflow = object()\nreplace()\n@workflow\nasync def run():\n return 1\n"
    if legacy:
        value = {"schema_version": "bifrost.solution-source-delivery/v1",
            "solution_id": value["solution_id"], "files": value["files"]}
        resources = {}
    files["run.py"] = code
    with pytest.raises(WorkflowRecipeError, match="shadowed"):
        review_solution_recipe(value, files, resources)


def test_downloaded_artifact_reviews_source_without_platform_or_source_execution(tmp_path):
    artifact = build_cli_artifact(Path("bifrost"), tmp_path / "artifacts", "2.2.1-dev.999")
    destination = tmp_path / "standalone"
    destination.mkdir()
    with tarfile.open(artifact) as archive:
        archive.extractall(destination, filter="data")
    recipe, files, resources = fixture()
    files["run.py"] = b'raise RuntimeError("carried source must never execute")\n' + files["run.py"]
    payload = tmp_path / "input.json"
    payload.write_text(json.dumps({"recipe": recipe, "files": {k: v.decode() for k, v in files.items()},
        "resources": {k: v.decode() for k, v in resources.items()}}))
    program = '''import json,sys
sys.path.insert(0, sys.argv[1])
from bifrost.solution_delivery_review import review_solution_recipe
v=json.load(open(sys.argv[2]))
r=review_solution_recipe(v['recipe'],{k:v.encode() for k,v in v['files'].items()},{k:v.encode() for k,v in v['resources'].items()})
assert not any(name == 'src' or name.startswith('src.') or name.startswith('sqlalchemy') for name in sys.modules)
print(json.dumps(r))
'''
    result = subprocess.run([sys.executable, "-I", "-c", program, str(destination), str(payload)],
        cwd=tmp_path, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["workflow_ids"] == [recipe["workflows"][0]["id"]]
