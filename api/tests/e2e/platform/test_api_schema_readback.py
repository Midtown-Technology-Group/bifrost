"""Retain the actual public API schema and canonical digest for CI generation."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.e2e
def test_public_api_schema_readback_for_retirement_inventory(e2e_client):
    response = e2e_client.get("/openapi.json")
    assert response.status_code == 200, response.text
    schema = response.json()
    route = "/api/workspace-promotions/live/retirement-inventory"
    assert "get" in schema["paths"][route]
    assert "WorkspaceLiveRetirementInventory" in schema["components"]["schemas"]
    # No auth headers or entity values enter these public schema artifacts.
    output = Path("/bifrost-results")
    output.joinpath("openapi-current.json").write_text(json.dumps(schema))
    generator = Path(__file__).resolve().parents[3] / "scripts/skill-truth/generate.py"
    spec = importlib.util.spec_from_file_location("skill_truth_generator", generator)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output.joinpath("openapi-digest.md").write_text(module.gen_openapi_digest())
