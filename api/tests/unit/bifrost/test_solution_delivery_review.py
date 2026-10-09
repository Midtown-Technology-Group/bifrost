"""The downloaded compiler catches unsupported transitions without executing source."""

import json
from copy import deepcopy
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from uuid import uuid4
from typing import Any

import pytest

from bifrost.solution_delivery_review import (
    WorkflowRecipeError, require_adoption_parameters, review_solution_recipe,
)
from shared.cli_artifact import build_cli_artifact


@pytest.mark.parametrize("damage", [None, "required", "old_default", "default_type", "old_type", "removed",
                                   "kwargs", "missing_default", "modern_registry"])
def test_adoption_optional_additions_keep_old_parameters_and_admission_closed(damage):
    old = {"type": "object", "properties": {"apply": {"type": "boolean", "default": False}},
        "additionalProperties": False}
    new = deepcopy(old)
    new["properties"]["approved_id"] = {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None}
    if damage == "required":
        new["required"] = ["approved_id"]
    elif damage == "old_default":
        new["properties"]["apply"]["default"] = True
    elif damage == "default_type":
        new["properties"]["apply"]["default"] = 0
    elif damage == "old_type":
        new["properties"]["apply"]["type"] = "integer"
    elif damage == "removed":
        del new["properties"]["apply"]
    elif damage == "kwargs":
        new["additionalProperties"] = True
    elif damage == "missing_default":
        del new["properties"]["approved_id"]["default"]
    if damage is not None:
        with pytest.raises(WorkflowRecipeError, match="source parameter contract"):
            require_adoption_parameters(old, new, attested_legacy_list=damage != "modern_registry")
        return
    require_adoption_parameters(old, new, attested_legacy_list=True)


def test_adoption_does_not_coerce_existing_boolean_default_to_integer():
    old = {"type": "object", "properties": {"apply": {"type": "boolean", "default": False}}}
    new = deepcopy(old)
    new["properties"]["apply"]["default"] = 0
    with pytest.raises(WorkflowRecipeError, match="source parameter contract"):
        require_adoption_parameters(old, new, attested_legacy_list=True)


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


def test_owned_table_context_reviews_effect_successor_without_grant_or_live_claim():
    value, _files, _resources = fixture()
    value["resources"] = {}
    old = b'from bifrost import workflow, tables\n@workflow(effects=[])\nasync def run(user: str = "root"):\n return await tables.get("existing", user)\n'
    new = old.replace(b"effects=[]", b'effects=[{"kind":"integration.read","target":"microsoft_csp"}]')
    with pytest.raises(ValueError, match="table/file resource bindings"):
        review_solution_recipe(value, {"run.py": new}, {})
    table_ids = (uuid4(), uuid4(), uuid4())
    reviewed = review_solution_recipe(value, {"run.py": new}, {}, owned_table_ids=table_ids,
        previous_recipe_value=value, previous_files={"run.py": old})
    assert reviewed["owned_table_review_ids"] == sorted(str(identity) for identity in table_ids)
    assert reviewed["previous_recipe_checked"] is True
    assert reviewed["live_state_verified"] is False and reviewed["runtime_verified"] is False
    assert value.get("shared_tables", {}) == {}


@pytest.mark.parametrize("invalid", ["not-a-uuid", "duplicate"])
def test_owned_table_review_rejects_invalid_context(invalid):
    value, files, resources = fixture()
    identity = uuid4()
    ids = (identity, identity) if invalid == "duplicate" else (invalid,)
    with pytest.raises(WorkflowRecipeError, match="Owned-table review context"):
        review_solution_recipe(value, files, resources, owned_table_ids=ids)


def test_owned_table_context_cannot_enable_body_only_decorator_transition():
    value = {"schema_version": "bifrost.solution-source-delivery/v1", "solution_id": str(uuid4()),
        "files": {"run.py": "run.py"}}
    with pytest.raises(WorkflowRecipeError, match="reviewed workflow adapter"):
        review_solution_recipe(value, {"run.py": b""}, {}, owned_table_ids=(uuid4(),))


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
    result = review_solution_recipe(value, changed, {}, previous_recipe_value=value, previous_files=old)
    assert result["entrypoints"] == ["run.py::run"]
    assert result["workflow_removal_evidence_verified"] is False
    assert result["removed_workflow_ids"] == [] and result["workflow_removal_evidence_digest"] is None
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


def test_identical_installed_baseline_compiles_once_and_still_reports_checked(monkeypatch):
    from bifrost import solution_delivery_review as review
    recipe, files, resources = fixture()
    compiled = {"calls": 0}
    original = review.compile_workflow_registrations
    def counting(value, sources, indexer=None):
        compiled["calls"] += 1
        return original(value, sources, indexer)
    monkeypatch.setattr(review, "compile_workflow_registrations", counting)
    reviewed = review_solution_recipe(deepcopy(recipe), dict(files), dict(resources),
        previous_recipe_value=deepcopy(recipe), previous_files=dict(files), previous_resources=dict(resources))
    assert compiled["calls"] == 1
    assert reviewed["previous_recipe_checked"] is True
    assert reviewed == {**review_solution_recipe(recipe, files, resources), "previous_recipe_checked": True}


def test_float_substituted_baseline_is_not_treated_as_current():
    recipe, files, resources = fixture()
    previous = deepcopy(recipe)
    bounds = previous["workflows"][0]["runtime_bounds"]
    bounds["max_duration_seconds"] = float(bounds["max_duration_seconds"])
    assert previous == recipe  # plain dict equality hides the scalar type substitution
    with pytest.raises(ValueError, match="max_duration_seconds"):
        review_solution_recipe(recipe, files, resources,
            previous_recipe_value=previous, previous_files=dict(files), previous_resources=dict(resources))


def test_integer_substituted_boolean_baseline_is_not_treated_as_current():
    recipe, files, resources = fixture()
    recipe["workflows"][0]["controls"] = {"endpoint_enabled": False}
    previous = deepcopy(recipe)
    previous["workflows"][0]["controls"]["endpoint_enabled"] = 0
    assert previous == recipe  # plain dict equality hides the scalar type substitution
    with pytest.raises(ValueError, match="endpoint_enabled"):
        review_solution_recipe(recipe, files, resources,
            previous_recipe_value=previous, previous_files=dict(files), previous_resources=dict(resources))


def test_changed_installed_baseline_still_compiles_twice_and_enforces_workflow_identity(monkeypatch):
    from bifrost import solution_delivery_review as review
    recipe, files, resources = fixture()
    previous = deepcopy(recipe)
    previous["workflows"][0]["id"] = str(uuid4())
    compiled = {"calls": 0}
    original = review.compile_workflow_registrations
    def counting(value, sources, indexer=None):
        compiled["calls"] += 1
        return original(value, sources, indexer)
    monkeypatch.setattr(review, "compile_workflow_registrations", counting)
    with pytest.raises(WorkflowRecipeError, match="Workflow removal requires"):
        review_solution_recipe(recipe, files, resources,
            previous_recipe_value=previous, previous_files=dict(files), previous_resources=dict(resources))
    assert compiled["calls"] == 2


def test_identical_legacy_baseline_reports_checked_without_second_source_pass(monkeypatch):
    from bifrost import solution_delivery_review as review
    recipe = {"schema_version": "bifrost.solution-source-delivery/v1", "solution_id": str(uuid4()),
        "files": {"run.py": "run.py"}}
    source = {"run.py": b"from bifrost import workflow\n@workflow\nasync def run(count: int = 1):\n return 1\n"}
    parsed = {"calls": 0}
    original = review.require_executable_bindings
    def counting(tree):
        parsed["calls"] += 1
        return original(tree)
    monkeypatch.setattr(review, "require_executable_bindings", counting)
    reviewed = review_solution_recipe(recipe, dict(source), {},
        previous_recipe_value=recipe, previous_files=dict(source), previous_resources={})
    assert parsed["calls"] == 1
    assert reviewed["previous_recipe_checked"] is True


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


def removal_fixture():
    from datetime import datetime, timedelta, timezone
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from bifrost.workflow_removal_evidence import (
        WorkflowRemovalReviewContext, export_workflow_removal_evidence, removal_binding,
    )

    candidate, files, resources = fixture()
    base = deepcopy(candidate)
    retired = deepcopy(base["workflows"][0])
    retired.update(id=str(uuid4()), path="retired.py", function_name="retired")
    base["workflows"].append(retired)
    base["files"]["retired.py"] = "solutions/demo/retired.py"
    base_files = {**files, "retired.py": b"from bifrost import workflow\n@workflow\nasync def retired():\n return 1\n"}
    private = Ed25519PrivateKey.generate()
    producer = {"contract": "bifrost.workflow-removal-observer/v1", "issuer": "test-observer",
                "key_id": "test-key", "build_digest": "sha256:" + "a" * 64, "run_id": "scan-1"}
    context = WorkflowRemovalReviewContext(solution_id=candidate["solution_id"],
        instance_origin="https://test.example", recipe_path="config/demo.json", base_sha="b" * 40,
        trusted_producers=[{**{k: producer[k] for k in ("issuer", "key_id", "build_digest")},
            "public_key": base64.b64encode(private.public_key().public_bytes_raw()).decode()}])
    binding = removal_binding(solution_id=context.solution_id, instance_origin=context.instance_origin,
        recipe_path=context.recipe_path, base_sha=context.base_sha, base_recipe=base,
        candidate_recipe=candidate, base_files=base_files, candidate_files=files,
        base_resources=resources, candidate_resources=resources)
    now = datetime.now(timezone.utc)
    inventory = {"snapshot_revision": "revision-1", "complete": True,
                 "pagination_exhausted": True, "includes_disabled": True,
                 "remaining_references": [], "unresolved_references": []}
    payload = {"schema_version": "bifrost.workflow-removal-evidence/v1", "binding": binding.model_dump(),
        "producer": producer, "issued_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "observations": [{"workflow_id": retired["id"], "phase": "post-reconciliation", "snapshot_revision": "revision-1",
            "observed_at": (now - timedelta(seconds=2)).isoformat(),
            **{name: deepcopy(inventory) for name in
               ("callers_dependencies", "event_sources", "subscriptions", "schedules")}}]}
    receipt = export_workflow_removal_evidence(payload, private_key=private.private_bytes_raw())
    args: dict[str, Any] = dict(previous_recipe_value=base, previous_files=base_files, previous_resources=resources,
                workflow_removal_context=context, workflow_removal_evidence=receipt)
    return candidate, files, resources, args, private


@pytest.mark.parametrize("reviewer", ["workflow", "solution"])
def test_removal_requires_evidence_and_verifies_exact_signed_observations(reviewer):
    from bifrost.solution_delivery_review import review_workflow_recipe
    from bifrost.workflow_removal_evidence import digest

    review = review_workflow_recipe if reviewer == "workflow" else review_solution_recipe
    candidate, files, resources, args, _key = removal_fixture()
    with pytest.raises(WorkflowRecipeError, match="^Workflow removal requires verified live caller and trigger reconciliation$"):
        review(candidate, files, resources, **{**args, "workflow_removal_evidence": None})
    result = review(candidate, files, resources, **args)
    assert result["workflow_removal_evidence_verified"] is True
    assert result["removed_workflow_ids"] == [args["workflow_removal_evidence"]["observations"][0]["workflow_id"]]
    assert result["workflow_removal_evidence_digest"] == digest(args["workflow_removal_evidence"])
    assert result["live_state_verified"] is False and result["runtime_verified"] is False


@pytest.mark.parametrize("damage", ["solution_id", "instance_origin", "recipe_path", "base_sha",
    "base_recipe_digest", "candidate_recipe_digest", "base_sources_digest", "candidate_sources_digest",
    "base_resources_digest", "candidate_resources_digest", "missing_id", "extra_id", "duplicate_id",
    "expired", "stale", "future", "naive_time", "snapshot", "issuer", "key", "build", "tampered",
    "unknown_field", "unknown_schema", "missing_context", "wrong_context_solution", "untrusted_key",
    "no_baseline", "noncanonical_signature"])
def test_removal_evidence_fails_closed_for_wrong_stale_or_tampered_receipts(damage):
    import base64
    from bifrost.workflow_removal_evidence import canonical_bytes
    candidate, files, resources, args, key = removal_fixture()
    receipt = args["workflow_removal_evidence"]
    if damage in receipt["binding"]:
        receipt["binding"][damage] = (str(uuid4()) if damage == "solution_id" else
            "https://other.example" if damage == "instance_origin" else
            "config/other.json" if damage == "recipe_path" else "c" * 40 if damage == "base_sha" else
            "sha256:" + "c" * 64)
    elif damage == "missing_id":
        receipt["observations"] = []
    elif damage in {"extra_id", "duplicate_id"}:
        receipt["observations"].append(deepcopy(receipt["observations"][0]))
        if damage == "extra_id":
            receipt["observations"][-1]["workflow_id"] = str(uuid4())
    elif damage == "expired":
        receipt["expires_at"] = "2000-01-01T00:00:00Z"
    elif damage == "stale":
        receipt["observations"][0]["observed_at"] = "2000-01-01T00:00:00Z"
    elif damage == "future":
        receipt["observations"][0]["observed_at"] = "2999-01-01T00:00:00Z"
    elif damage == "naive_time":
        receipt["issued_at"] = "2026-01-01T00:00:00"
    elif damage == "snapshot":
        receipt["observations"][0]["schedules"]["snapshot_revision"] = "other"
    elif damage in {"issuer", "key", "build"}:
        field = {"key": "key_id", "build": "build_digest"}.get(damage, damage)
        receipt["producer"][field] = "sha256:" + "c" * 64 if damage == "build" else "other"
    elif damage == "unknown_field":
        receipt["verified"] = True
    elif damage == "unknown_schema":
        receipt["schema_version"] = "bifrost.workflow-removal-evidence/v99"
    elif damage == "missing_context":
        args["workflow_removal_context"] = None
    elif damage == "wrong_context_solution":
        args["workflow_removal_context"] = args["workflow_removal_context"].model_copy(update={"solution_id": str(uuid4())})
    elif damage == "untrusted_key":
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
    elif damage == "no_baseline":
        args["previous_recipe_value"] = None
    # Sign damaged semantics too: valid authentication must not excuse wrong bindings/scans.
    if damage == "noncanonical_signature":
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        signature = receipt["signature"]
        receipt["signature"] = signature[:85] + alphabet[alphabet.index(signature[85]) + 1] + "=="
    elif damage != "tampered":
        receipt["signature"] = base64.b64encode(key.sign(canonical_bytes(
            {k: v for k, v in receipt.items() if k != "signature"}))).decode()
    else:
        receipt["producer"]["run_id"] = "edited-after-signing"
    with pytest.raises(WorkflowRecipeError):
        review_solution_recipe(candidate, files, resources, **args)


@pytest.mark.parametrize("inventory", ["callers_dependencies", "event_sources", "subscriptions", "schedules"])
@pytest.mark.parametrize("damage", ["missing", "incomplete", "pagination", "disabled", "remaining", "dynamic", "boolean_integer"])
def test_every_removal_inventory_must_be_complete_and_empty(inventory, damage):
    import base64
    from bifrost.workflow_removal_evidence import canonical_bytes
    candidate, files, resources, args, key = removal_fixture()
    receipt = args["workflow_removal_evidence"]
    observation = receipt["observations"][0]
    if damage == "missing":
        del observation[inventory]
    else:
        field, value = {"incomplete": ("complete", False), "pagination": ("pagination_exhausted", False),
            "disabled": ("includes_disabled", False), "remaining": ("remaining_references", ["disabled-object"]),
            "dynamic": ("unresolved_references", ["dynamic-caller"]), "boolean_integer": ("complete", 1)}[damage]
        observation[inventory][field] = value
    receipt["signature"] = base64.b64encode(key.sign(canonical_bytes(
        {k: v for k, v in receipt.items() if k != "signature"}))).decode()
    with pytest.raises(WorkflowRecipeError):
        review_solution_recipe(candidate, files, resources, **args)


def test_no_removal_keeps_existing_transition_controls_and_has_no_evidence_claim():
    recipe, files, resources = fixture()
    result = review_solution_recipe(recipe, files, resources, previous_recipe_value=recipe,
                                   previous_files=files, previous_resources=resources)
    assert result["workflow_removal_evidence_verified"] is False
    assert result["removed_workflow_ids"] == []
    assert result["workflow_removal_evidence_digest"] is None
    candidate, files, resources, args, _key = removal_fixture()
    candidate["workflows"][0]["controls"] = {"endpoint_enabled": True}
    # A receipt cannot bypass protected controls even when it binds this candidate.
    from bifrost.workflow_removal_evidence import canonical_bytes, removal_binding
    import base64
    args["workflow_removal_evidence"]["binding"] = removal_binding(
        solution_id=candidate["solution_id"], instance_origin="https://test.example",
        recipe_path="config/demo.json", base_sha="b" * 40,
        base_recipe=args["previous_recipe_value"], candidate_recipe=candidate,
        base_files=args["previous_files"], candidate_files=files,
        base_resources=resources, candidate_resources=resources).model_dump()
    receipt = args["workflow_removal_evidence"]
    receipt["signature"] = base64.b64encode(_key.sign(canonical_bytes(
        {k: v for k, v in receipt.items() if k != "signature"}))).decode()
    with pytest.raises(WorkflowRecipeError, match="caller/control-plane adapter"):
        review_solution_recipe(candidate, files, resources, **args)


@pytest.mark.parametrize("content", ["base_source", "candidate_source", "base_resource", "candidate_resource"])
def test_receipt_is_bound_to_exact_carried_content(content):
    candidate, files, resources, args, _key = removal_fixture()
    if content == "base_source":
        args["previous_files"]["retired.py"] += b"\n# changed base\n"
    elif content == "candidate_source":
        files["run.py"] += b"\n# changed candidate\n"
    elif content == "base_resource":
        args["previous_resources"] = {"rates.json": b'{"changed": true}'}
    else:
        resources["rates.json"] = b'{"changed": true}'
    with pytest.raises(WorkflowRecipeError, match="digests mismatch"):
        review_solution_recipe(candidate, files, resources, **args)


def test_constructed_evidence_models_cannot_bypass_strict_validation():
    from bifrost.workflow_removal_evidence import WorkflowRemovalEvidence
    candidate, files, resources, args, _key = removal_fixture()
    valid = WorkflowRemovalEvidence.model_validate(args["workflow_removal_evidence"])
    args["workflow_removal_evidence"] = valid.model_copy(update={"schema_version": "untrusted"})
    with pytest.raises(WorkflowRecipeError):
        review_solution_recipe(candidate, files, resources, **args)


def test_evidence_cannot_be_embedded_in_recipe_or_used_by_legacy_adapter():
    recipe, files, resources = fixture()
    recipe["workflow_removal_evidence"] = {}
    with pytest.raises(ValueError, match="Extra inputs"):
        review_solution_recipe(recipe, files, resources)
    candidate, files, _resources, args, _key = removal_fixture()
    legacy = {k: candidate[k] for k in ("solution_id", "files")}
    legacy["schema_version"] = "bifrost.solution-source-delivery/v1"
    with pytest.raises(WorkflowRecipeError, match="reviewed workflow adapter"):
        review_solution_recipe(legacy, files, {}, workflow_removal_evidence=args["workflow_removal_evidence"])


def test_nested_constructed_inventory_cannot_bypass_completeness_validation():
    import base64
    from bifrost.workflow_removal_evidence import RemovalInventory, canonical_bytes
    candidate, files, resources, args, key = removal_fixture()
    receipt = args["workflow_removal_evidence"]
    inventory = receipt["observations"][0]["schedules"]
    inventory["complete"] = False
    receipt["signature"] = base64.b64encode(key.sign(canonical_bytes(
        {k: v for k, v in receipt.items() if k != "signature"}))).decode()
    receipt["observations"][0]["schedules"] = RemovalInventory.model_construct(**inventory)
    with pytest.raises(WorkflowRecipeError):
        review_solution_recipe(candidate, files, resources, **args)
