"""Reviewed source cannot hide dynamic or missing immutable resource reads."""

from io import BytesIO
from unittest.mock import AsyncMock
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pydantic import ValidationError

from src.services.solutions.deployment_manifest import DeploymentResolutionMap, RuntimeResourceResolution, sha256_digest
from src.services.solutions.live_handoff_source import source_archive, source_closure
from src.services.solutions.resource_delivery import read_deployment_resources, validate_resource_files
from src.services.solutions.source_revision import SolutionSourceRevisionError
from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe


def recipe():
    return ReviewedWorkflowRecipe.model_validate({"schema_version": "bifrost.solution-workflow-delivery/v1",
        "solution_id": str(uuid4()), "files": {"run.py": "run.py"},
        "resources": {"rates.json": "data/reviewed.json"}, "workflows": [{
            "id": str(uuid4()), "path": "run.py", "function_name": "run", "organization_id": None,
            "runtime_bounds": {"max_duration_seconds": 10, "max_external_calls": 10,
                "max_records_read": 10, "max_output_bytes": 1024}, "controls": {}}]})


@pytest.mark.parametrize("code", [
    'from bifrost import resources\nasync def run():\n return await resources.read("rates.json")\n',
    'from bifrost.resources import resources as reviewed\nRATE_PATH="rates.json"\nasync def run():\n return await reviewed.read_bytes(path=RATE_PATH)\n',
])
def test_explicit_literal_and_constant_reads_have_complete_sealed_closure(code):
    files = {"run.py": code.encode()}
    validate_resource_files(recipe(), {"rates.json": b"{}"}, files)
    assert source_closure(files, {"run.py"}, has_resource_bindings=True) == files


@pytest.mark.parametrize("code", [
    'from bifrost import resources\nasync def run():\n return await resources.read("missing.json")\n',
    'from bifrost import resources\nasync def run(path):\n return await resources.read(path)\n',
    'from bifrost import resources\nRATE_PATH="rates.json"\nRATE_PATH="other.json"\nasync def run():\n return await resources.read(RATE_PATH)\n',
    'from bifrost import resources\nRATE_PATH="rates.json"\nasync def run(RATE_PATH):\n return await resources.read(RATE_PATH)\n',
    'from bifrost import resources\nreader=resources.read\nasync def run():\n return await reader("rates.json")\n',
    'from bifrost import resources\nresources=object()\nasync def run():\n return await resources.read("rates.json")\n',
    'from bifrost import resources\nfrom other import resources\n',
    'import bifrost.resources as reviewed\n',
    'from bifrost import resources\nasync def run():\n return await resources.read("rates.json", solution="other")\n',
])
def test_unprovable_resource_reads_fail_before_staging(code):
    with pytest.raises(SolutionSourceRevisionError):
        validate_resource_files(recipe(), {"rates.json": b"{}"}, {"run.py": code.encode()})


@pytest.mark.parametrize("mapping", [{"rates.json": "../secret.json"}, {"rates.json": ".artifacts/state.json"},
    {"rates.json": "browser/storage-state.json"}, {"rates.json": "data/secret.env"}, {"code.py": "code.py"}])
def test_resource_recipe_rejects_hidden_auth_state_and_executable_python(mapping):
    value = recipe().model_dump(mode="json")
    value["resources"] = mapping
    with pytest.raises(ValidationError):
        ReviewedWorkflowRecipe.model_validate(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "extra_member", "changed_bytes", "compressed", "stale_object"])
async def test_resource_readback_verifies_archive_shape_and_actual_runtime_bytes(monkeypatch, damage):
    from src.services.solutions import resource_delivery
    files = {"rates.json": b'{"rate":1}'}
    original = files["rates.json"]
    contract = RuntimeResourceResolution(object_key="runtime/_resources/rates.json",
        content_hash=sha256_digest(original), size_bytes=len(original))
    resolution = DeploymentResolutionMap(resources={"rates.json": contract})
    if damage == "extra_member":
        files["unreviewed.json"] = b"{}"
    elif damage == "changed_bytes":
        files["rates.json"] = b'{"rate":2}'
    archive = source_archive(files)
    if damage == "compressed":
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
            bundle.writestr("rates.json", original)
        archive = output.getvalue()
    storage = AsyncMock()
    storage.read_resources_artifact.return_value = archive
    storage.read_resource.return_value = b'{"rate":9}' if damage == "stale_object" else original
    monkeypatch.setattr(resource_delivery, "SolutionDeploymentStorage", lambda *_: storage)
    if damage:
        with pytest.raises(SolutionSourceRevisionError):
            await read_deployment_resources(uuid4(), uuid4(), resolution)
    else:
        assert await read_deployment_resources(uuid4(), uuid4(), resolution) == files
