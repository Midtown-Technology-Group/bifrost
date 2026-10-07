"""A shared registry keeps independent publication authority namespaces."""

import pytest

from src.core.solution_delivery_policy import reviewed_package_registry


def registry():
    return {"schema_version": "bifrost.package-delivery-installations/v1", "installations": [
        {"kind": "solution", "target": "production", "recipe": "config/solution-delivery/fixture.json"},
        {"kind": "inline_app", "target": "production", "recipe": "config/app-delivery/fixture.json"},
        {"kind": "solution_package", "target": "production", "recipe": "config/solution-package-delivery/fixture.json"},
    ]}


def test_package_registry_keeps_distinct_adapter_namespaces_and_legacy_defaults():
    assert reviewed_package_registry(registry()) == registry()["installations"]
    legacy = {"schema_version": "bifrost.solution-delivery-installations/v1",
        "installations": [{"target": "production", "recipe": "config/solution-delivery/fixture.json"}]}
    assert reviewed_package_registry(legacy) == [registry()["installations"][0]]


@pytest.mark.parametrize("kind", ["solution", "solution_package", "inline_app"])
@pytest.mark.parametrize("namespace", ["solution-delivery", "solution-package-delivery", "app-delivery"])
def test_each_adapter_accepts_only_its_recipe_namespace(kind, namespace):
    value = registry()
    value["installations"] = [{"kind": kind, "target": "production", "recipe": f"config/{namespace}/fixture.json"}]
    expected = {"solution": "solution-delivery", "solution_package": "solution-package-delivery",
        "inline_app": "app-delivery"}
    if expected[kind] == namespace:
        assert reviewed_package_registry(value) == value["installations"]
    else:
        with pytest.raises(ValueError, match="adapter namespace"):
            reviewed_package_registry(value)


@pytest.mark.parametrize("fault", ["unknown_kind", "wrong_namespace", "duplicate", "unknown_target", "missing_kind",
    "uploaded_control", "unsafe_path", "not_a_path"])
def test_shared_registry_cannot_widen_or_borrow_publication_authority(fault):
    value = registry()
    row = value["installations"][1]
    if fault == "unknown_kind":
        row["kind"] = "deploy_anything"
    elif fault == "wrong_namespace":
        row["recipe"] = "config/solution-delivery/fixture.json"
    elif fault == "duplicate":
        value["installations"].append(dict(row))
    elif fault == "unknown_target":
        row["target"] = "unreviewed"
    elif fault == "missing_kind":
        del row["kind"]
    elif fault == "uploaded_control":
        row["public_endpoint"] = True
    elif fault == "unsafe_path":
        row["recipe"] = "config/app-delivery/../fixture.json"
    else:
        row["recipe"] = None
    with pytest.raises(ValueError):
        reviewed_package_registry(value)
